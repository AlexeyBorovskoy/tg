#!/usr/bin/env bash
# ==============================================================================
# deploy_to_server.sh — Развёртывание на сервер Нила (89.124.65.229)
# ==============================================================================
# Предварительно: ./setup_from_existing.sh
# Заполнить вручную: deploy/.env (TG_BOT_TOKEN, TG_CLIENT_CHAT_ID)
#                   deploy/channels.test.json (telegram_id получателя)
#
# Запуск: ./deploy_to_server.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJEKTT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SSH_KEY="$PROJEKTT/pwr_key/id_VPS_BAE_ed25519"
TARGET="root@89.124.65.229"
REMOTE_DIR="/opt/tg_digest"

echo "=== Развёртывание на сервер Нила ==="
echo "Сервер: $TARGET"
echo "Папка:  $REMOTE_DIR"
echo ""

# Проверки
if [[ ! -f "$SSH_KEY" ]]; then
  echo "Ошибка: ключ не найден: $SSH_KEY"
  exit 1
fi

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
  echo "Ошибка: сначала выполните ./setup_from_existing.sh"
  exit 1
fi

# Создать директорию на сервере
echo "Создаю $REMOTE_DIR на сервере..."
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$TARGET" "mkdir -p $REMOTE_DIR/config $REMOTE_DIR/docker"

# Копировать tg_digest_system
echo "Копирую tg_digest_system..."
rsync -avz --progress \
  -e "ssh -i $SSH_KEY" \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "$PROJEKTT/tg_digest_system/tg_digest_system/" \
  "$TARGET:$REMOTE_DIR/"

# Копировать наш .env и channels
echo "Копирую конфигурацию..."
scp -i "$SSH_KEY" "$SCRIPT_DIR/.env" "$TARGET:$REMOTE_DIR/"
scp -i "$SSH_KEY" "$SCRIPT_DIR/channels.test.json" "$TARGET:$REMOTE_DIR/config/channels.json"

echo ""
echo "Готово. Дальнейшие шаги на сервере:"
echo "  ssh -i $SSH_KEY $TARGET"
echo "  cd $REMOTE_DIR/docker"
echo "  docker compose run --rm auth   # первый раз — авторизация Telethon"
echo "  docker compose up -d"
echo ""
