#!/usr/bin/env bash
# ==============================================================================
# Деплой TG Digest на Yandex VM (запуск с локальной машины)
# Копирует код в ~/tg_digest_deploy на сервере и при необходимости выполняет
# завершение деплоя (остановка tg_ingest, systemd, старт воркера).
#
# Ключи для .env на сервере берутся из папки pwr_key (по умолчанию ../pwr_key):
#   - OPENAI_API_KEY и OPENAI_BASE_URL из файла "доступ к api chatgpt.txt"
#   - TG_BOT_TOKEN из файла tg_bot_token.txt (одна строка — токен бота)
# Остальное (TG_API_ID, TG_API_HASH, PGPASSWORD) — из ~/tg_ingest/.env на сервере.
#
# Использование:
#   ./deploy_yandex.sh              # только синхронизация кода
#   ./deploy_yandex.sh --finish      # синхронизация + .env из pwr_key + systemd + старт
#   ./deploy_yandex.sh --finish --run-once  # то же + один запуск воркера (--once)
#   PWR_KEY=/path/to/keys ./deploy_yandex.sh --finish
# ==============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PWR_KEY="${PWR_KEY:-$(cd "$PROJECT_ROOT/.." && pwd)/pwr_key}"
SSH_USER="${SSH_USER:-ripas}"
SSH_HOST="${SSH_HOST:-158.160.19.253}"
REMOTE_DIR="/home/$SSH_USER/tg_digest_deploy"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"

FINISH=
RUN_ONCE=
for x in "$@"; do
  case "$x" in
    --finish)   FINISH=1 ;;
    --run-once) RUN_ONCE=1 ;;
  esac
done

TG_DIGEST_SYSTEM="${TG_DIGEST_SYSTEM:-$(cd "$PROJECT_ROOT/.." && pwd)/tg_digest_system/tg_digest_system}"

echo "=== Деплой на Yandex VM ==="
echo "Сервер: $SSH_USER@$SSH_HOST"
echo "Каталог на сервере: $REMOTE_DIR"
echo "Ключи (pwr_key): $PWR_KEY"
echo ""

echo "=== 1. Синхронизация кода ==="
# Сначала код воркера (scripts, prompts, db, config) из tg_digest_system
if [[ -d "$TG_DIGEST_SYSTEM" ]]; then
  echo "Копирую воркер и промпты из tg_digest_system..."
  rsync -avz --progress \
    -e "ssh $SSH_OPTS" \
    --exclude '.venv' \
    --exclude '.env' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'config/' \
    "$TG_DIGEST_SYSTEM/" \
    "${SSH_USER}@${SSH_HOST}:${REMOTE_DIR}/"
fi
# Затем new_project_01 (deploy, docs), не перезаписывая scripts/
rsync -avz --progress \
  -e "ssh $SSH_OPTS" \
  --exclude '.venv' \
  --exclude '.env' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.git' \
  --exclude 'incoming/' \
  --exclude 'scripts/' \
  --exclude 'config/' \
  "$PROJECT_ROOT/" \
  "${SSH_USER}@${SSH_HOST}:${REMOTE_DIR}/"
# scripts/ и config/ не перезаписываем (воркер из tg_digest_system)
# Убедиться, что на сервере есть config/channels.json для Yandex
if [[ -f "$SCRIPT_DIR/channels.yandex.json" ]]; then
  ssh $SSH_OPTS "${SSH_USER}@${SSH_HOST}" "mkdir -p $REMOTE_DIR/config"
  scp -q $SSH_OPTS "$SCRIPT_DIR/channels.yandex.json" "${SSH_USER}@${SSH_HOST}:${REMOTE_DIR}/config/channels.json"
  echo "config/channels.json на сервере обновлён (Yandex: канал -1002700886173, RIPAS_DialogKeeperBot)."
fi
echo "Файлы деплоя и воркера синхронизированы."

if [[ -n "$FINISH" ]]; then
  echo ""
  echo "=== 2. Сборка .env на сервере из pwr_key и серверного tg_ingest ==="
  # Переменные с сервера (tg_ingest или текущий tg_digest_deploy)
  SERVER_ENV=$(ssh $SSH_OPTS "${SSH_USER}@${SSH_HOST}" "grep -E '^TG_API_ID=|^TG_API_HASH=|^PGPASSWORD=' ~/tg_ingest/.env 2>/dev/null || grep -E '^TG_API_ID=|^TG_API_HASH=|^PGPASSWORD=' ~/tg_digest_deploy/.env 2>/dev/null || true" || true)
  TG_API_ID=; TG_API_HASH=; PGPASSWORD=
  while IFS= read -r line; do
    [[ $line =~ ^TG_API_ID= ]] && TG_API_ID="${line#TG_API_ID=}"
    [[ $line =~ ^TG_API_HASH= ]] && TG_API_HASH="${line#TG_API_HASH=}"
    [[ $line =~ ^PGPASSWORD= ]] && PGPASSWORD="${line#PGPASSWORD=}"
  done <<< "$SERVER_ENV"
  # Пароль БД tg_digest: везде один (all270174bae)
  PGPASSWORD=all270174bae

  # OpenAI из pwr_key
  OPENAI_API_KEY=; OPENAI_BASE_URL=
  CHATGPT_FILE="$PWR_KEY/доступ к api chatgpt.txt"
  if [[ -f "$CHATGPT_FILE" ]]; then
    OPENAI_API_KEY=$(grep -o 'sk-[A-Za-z0-9]*' "$CHATGPT_FILE" | head -1)
    OPENAI_BASE_URL=$(grep -oE 'https://[^[:space:]]+' "$CHATGPT_FILE" | head -1)
  fi

  # TG_BOT_TOKEN из pwr_key (одна строка в файле)
  TG_BOT_TOKEN=
  if [[ -f "$PWR_KEY/tg_bot_token.txt" ]]; then
    TG_BOT_TOKEN=$(tr -d '\n\r' < "$PWR_KEY/tg_bot_token.txt")
  fi

  ENV_FILE=$(mktemp)
  trap 'rm -f "$ENV_FILE"' EXIT
  cat > "$ENV_FILE" << ENVEOF
# TG Digest — собрано deploy_yandex.sh из pwr_key и серверного .env
TG_API_ID=${TG_API_ID:-}
TG_API_HASH=${TG_API_HASH:-}
TG_BOT_TOKEN=${TG_BOT_TOKEN:-}
TG_SESSION_FILE=$REMOTE_DIR/data/telethon.session

PGHOST=127.0.0.1
PGPORT=5432
PGDATABASE=tg_digest
PGUSER=tg_digest
PGPASSWORD=${PGPASSWORD:-all270174bae}

OPENAI_API_KEY=${OPENAI_API_KEY:-}
OPENAI_BASE_URL=${OPENAI_BASE_URL:-}
OPENAI_MODEL=${OPENAI_MODEL:-gpt-4o}
OPENAI_MAX_TOKENS=2000
OPENAI_TEMPERATURE=0.1

CONFIG_FILE=$REMOTE_DIR/config/channels.json
PROMPTS_DIR=$REMOTE_DIR/prompts
REPO_DIR=/home/$SSH_USER/analysis-methodology
MEDIA_DIR=$REMOTE_DIR/media
LOGS_DIR=$REMOTE_DIR/logs

GITLAB_ENABLED=1
GITLAB_REPO_URL=ssh://git@gitlab.ripas.ru:8611/analyzer/analysis-methodology.git
GITLAB_BRANCH=feature/tg-digest-yandex
GITLAB_SSH_KEY=

TZ=Europe/Moscow
DEBUG=0
ENVEOF

  scp -q $SSH_OPTS "$ENV_FILE" "${SSH_USER}@${SSH_HOST}:${REMOTE_DIR}/.env"
  echo ".env обновлён (OPENAI из pwr_key, TG_* и PG* с сервера или по умолчанию)."

  if [[ -z "$TG_BOT_TOKEN" ]]; then
    echo "ВНИМАНИЕ: TG_BOT_TOKEN не задан. Создайте файл $PWR_KEY/tg_bot_token.txt с одной строкой — токен бота."
  fi

  echo ""
  echo "=== 3. Завершение деплоя на сервере ==="
  RUN_ONCE_ARG=
  [[ -n "$RUN_ONCE" ]] && RUN_ONCE_ARG="--run-once"
  ssh $SSH_OPTS "${SSH_USER}@${SSH_HOST}" "bash $REMOTE_DIR/deploy/finish_deploy_yandex.sh $RUN_ONCE_ARG"
else
  echo ""
  echo "Код обновлён. Чтобы собрать .env из pwr_key и запустить воркер через systemd:"
  echo "  ./deploy_yandex.sh --finish"
  echo "Или на сервере: bash $REMOTE_DIR/deploy/finish_deploy_yandex.sh"
fi

echo ""
echo "Готово."
