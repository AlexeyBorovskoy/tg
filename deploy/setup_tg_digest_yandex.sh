#!/usr/bin/env bash
# ==============================================================================
# Настройка TG Digest на Yandex VM (запускать на сервере под ripas)
# Требует: tg_digest_deploy уже скопирован, analysis-methodology с веткой feature/tg-digest-yandex
# ==============================================================================

set -e
DEPLOY=~/tg_digest_deploy
INGEST=~/tg_ingest
REPO=~/analysis-methodology
TG_SESSION_NAME=telethon_session.session

echo "=== 1. Каталоги ==="
mkdir -p "$DEPLOY/data" "$DEPLOY/logs" "$DEPLOY/media"
mkdir -p "$DEPLOY/config" "$DEPLOY/prompts"

echo "=== 2. Сессия Telethon ==="
if [[ -f "$INGEST/$TG_SESSION_NAME" ]]; then
  cp "$INGEST/$TG_SESSION_NAME" "$DEPLOY/data/telethon.session"
  echo "Сессия скопирована в $DEPLOY/data/telethon.session"
else
  echo "ВНИМАНИЕ: $INGEST/$TG_SESSION_NAME не найден. Создайте сессию вручную."
fi

echo "=== 3. .env из tg_ingest (TG_*, пути) + новые переменные ==="
# Берём из tg_ingest: TG_API_ID, TG_API_HASH; пути подставляем под tg_digest_deploy
source "$INGEST/.env" 2>/dev/null || true
cat > "$DEPLOY/.env" << ENVEOF
# TG Digest — сформировано из tg_ingest и шаблона
TG_API_ID=${TG_API_ID:-}
TG_API_HASH=${TG_API_HASH:-}
TG_BOT_TOKEN=${TG_BOT_TOKEN:-}
TG_SESSION_FILE=$DEPLOY/data/telethon.session

PGHOST=127.0.0.1
PGPORT=5432
PGDATABASE=tg_digest
PGUSER=tg_digest
PGPASSWORD=${TG_DIGEST_PG_PASSWORD:-tg_digest_change_me}

OPENAI_API_KEY=${OPENAI_API_KEY:-}
OPENAI_MODEL=${OPENAI_MODEL:-gpt-4o}
OPENAI_MAX_TOKENS=2000
OPENAI_TEMPERATURE=0.1

CONFIG_FILE=$DEPLOY/config/channels.json
PROMPTS_DIR=$DEPLOY/prompts
REPO_DIR=$REPO
MEDIA_DIR=$DEPLOY/media
LOGS_DIR=$DEPLOY/logs

GITLAB_ENABLED=1
GITLAB_REPO_URL=ssh://git@gitlab.ripas.ru:8611/analyzer/analysis-methodology.git
GITLAB_BRANCH=feature/tg-digest-yandex
GITLAB_SSH_KEY=

TZ=Europe/Moscow
DEBUG=0
ENVEOF
echo ".env записан. Проверьте OPENAI_API_KEY и TG_BOT_TOKEN (при необходимости добавьте вручную)."

echo "=== 4. channels.json (один канал, RIPAS бот) ==="
cat > "$DEPLOY/config/channels.json" << 'JSONEOF'
{
  "channels": [
    {
      "id": -1002700886173,
      "name": "АСУДД Основной",
      "description": "Основной канал команды",
      "enabled": true,
      "peer_type": "channel",
      "prompt_file": "prompts/digest_management.md",
      "poll_interval_minutes": 60,
      "consolidated_doc_path": "docs/reference/asudd_engineering.md",
      "consolidated_doc_prompt_file": "prompts/consolidated_engineering.md",
      "recipients": [
        {
          "telegram_id": 8572555788,
          "name": "RIPAS_DialogKeeperBot",
          "role": "bot",
          "send_file": true,
          "send_text": true
        }
      ]
    }
  ],
  "defaults": {
    "poll_interval_minutes": 60,
    "llm_model": "gpt-4o",
    "ocr_enabled": true
  }
}
JSONEOF
echo "channels.json записан (канал -1002700886173, получатель 8572555788)."

echo "=== 5. Готово ==="
echo "Дальше: создать БД tg_digest и пользователя, применить schema.sql, установить venv и запустить воркер."
echo "Пароль БД задаётся: export TG_DIGEST_PG_PASSWORD=... перед повторным запуском этого скрипта или отредактируйте $DEPLOY/.env"
