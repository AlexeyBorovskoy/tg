#!/usr/bin/env bash
# ==============================================================================
# setup_from_existing.sh — Сбор настроек из исходных проектов для тестирования
# ==============================================================================
# Читает .env из telegram_pipeline и tg_watch, объединяет и создаёт .env для
# развёртывания tg_digest_system на сервере Нила.
#
# Запуск: из папки new_project_01/deploy/
#   ./setup_from_existing.sh
#
# Результат: deploy/.env (НЕ коммитить! уже в .gitignore)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJEKTT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_ENV="$SCRIPT_DIR/.env"

echo "=== Сбор настроек из исходных проектов ==="
echo "Проект: $PROJEKTT"
echo "Выход:  $OUT_ENV"
echo ""

# Источники
PIPELINE_ENV="$PROJEKTT/telegram_pipeline/analysis-methodology/.env"
TGWATCH_ENV="$PROJEKTT/tg server/tg_watch_snapshot_20260114_121246/tg_watch/.env"

# Собираем значения
TG_API_ID=""
TG_API_HASH=""
TG_TARGET_CHAT_ID=""
TG_SESSION_FILE=""
OPENAI_API_KEY=""
OPENAI_MODEL=""
TG_BOT_TOKEN=""
TG_CLIENT_CHAT_ID=""

# Функция: извлечь значение из .env
get_env() {
  local file="$1"
  local key="$2"
  if [[ -f "$file" ]]; then
    grep -E "^${key}=" "$file" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'
  fi
}

# Читаем из pipeline (приоритет)
if [[ -f "$PIPELINE_ENV" ]]; then
  echo "Читаю: $PIPELINE_ENV"
  TG_API_ID="$(get_env "$PIPELINE_ENV" TG_API_ID)"
  TG_API_HASH="$(get_env "$PIPELINE_ENV" TG_API_HASH)"
  TG_TARGET_CHAT_ID="$(get_env "$PIPELINE_ENV" TG_TARGET_CHAT_ID)"
  TG_SESSION_FILE="$(get_env "$PIPELINE_ENV" TG_SESSION_FILE)"
fi

# Читаем из tg_watch (дополнение: OpenAI, при необходимости — API)
if [[ -f "$TGWATCH_ENV" ]]; then
  echo "Читаю: $TGWATCH_ENV"
  [[ -z "$TG_API_ID" ]] && TG_API_ID="$(get_env "$TGWATCH_ENV" TG_API_ID)"
  [[ -z "$TG_API_HASH" ]] && TG_API_HASH="$(get_env "$TGWATCH_ENV" TG_API_HASH)"
  [[ -z "$TG_TARGET_CHAT_ID" ]] && TG_TARGET_CHAT_ID="$(get_env "$TGWATCH_ENV" TG_TARGET)"
  [[ -z "$TG_SESSION_FILE" ]] && TG_SESSION_FILE="$(get_env "$TGWATCH_ENV" TG_SESSION)"
  OPENAI_API_KEY="$(get_env "$TGWATCH_ENV" OPENAI_API_KEY)"
  OPENAI_MODEL="$(get_env "$TGWATCH_ENV" OPENAI_MODEL)"
fi

# TG_SESSION для Docker — путь внутри контейнера
TG_SESSION_FILE="/app/data/telethon.session"

# Генерируем пароль PostgreSQL если нет
PG_PASSWORD="${PGPASSWORD:-$(openssl rand -base64 24 2>/dev/null || echo "CHANGE_ME_STRONG_PASSWORD")}"

# Формируем .env
echo ""
echo "Создаю: $OUT_ENV"

cat > "$OUT_ENV" << EOF
# ==============================================================================
# TG Digest System — сгенерировано setup_from_existing.sh
# ВНИМАНИЕ: НЕ коммитить! Содержит секреты.
# ==============================================================================

# ------------------------------------------------------------------------------
# Telegram API (Telethon, чтение чатов)
# Источник: telegram_pipeline, tg_watch
# ------------------------------------------------------------------------------
TG_API_ID=${TG_API_ID:-12345678}
TG_API_HASH=${TG_API_HASH:-your_api_hash_here}
TG_SESSION_FILE=${TG_SESSION_FILE}

# ------------------------------------------------------------------------------
# Telegram Bot (рассылка дайджестов)
# Создать бота: https://t.me/BotFather
# TODO: Заполнить TG_BOT_TOKEN и TG_CLIENT_CHAT_ID (ваш ID из @userinfobot)
# ------------------------------------------------------------------------------
TG_BOT_TOKEN=${TG_BOT_TOKEN:-PUT_BOT_TOKEN_FROM_BOTFATHER}
TG_CLIENT_CHAT_ID=${TG_CLIENT_CHAT_ID:-PUT_YOUR_TELEGRAM_ID}

# ------------------------------------------------------------------------------
# PostgreSQL (для Docker: host=postgres)
# ------------------------------------------------------------------------------
PGHOST=postgres
PGPORT=5432
PGDATABASE=tg_digest
PGUSER=tg_digest
PGPASSWORD=${PG_PASSWORD}

# ------------------------------------------------------------------------------
# OpenAI API
# Источник: tg_watch (или pwr_key/доступ к api chatgpt.txt — Artemox)
# ------------------------------------------------------------------------------
OPENAI_API_KEY=${OPENAI_API_KEY:-sk-proj-your-key-here}
OPENAI_MODEL=${OPENAI_MODEL:-gpt-4o-mini}

# Для Artemox API (альтернатива):
# OPENAI_API_KEY=sk-JId7INsDQlyjabF41KyN1A
# OPENAI_BASE_URL=https://api.artemox.com/v1

# ------------------------------------------------------------------------------
# Прочее
# ------------------------------------------------------------------------------
TZ=Europe/Moscow
DEBUG=0
EOF

echo ""
echo "Готово. Проверьте $OUT_ENV"
echo ""
echo "Обязательно заполните вручную (если пусто):"
echo "  - TG_BOT_TOKEN    — токен от @BotFather"
echo "  - TG_CLIENT_CHAT_ID — ваш Telegram ID (получить у @userinfobot)"
echo ""
