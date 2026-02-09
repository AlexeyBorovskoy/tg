#!/usr/bin/env bash
# ==============================================================================
# Деплой на уже существующую ВМ в Yandex (или любую с SSH).
# Самый простой способ, если ВМ уже есть и известен IP.
#
# Использование:
#   cd tg_digest_system/tg_digest_system/docker
#   PUBLIC_IP=93.77.185.71 ./yandex/deploy-to-existing-vm.sh
#
# Или из каталога yandex:
#   PUBLIC_IP=93.77.185.71 ./deploy-to-existing-vm.sh
#
# Переменные:
#   PUBLIC_IP  — обязательный, публичный IP ВМ
#   SSH_USER   — пользователь SSH (по умолчанию ubuntu, на yc часто yc-user)
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_TOP="$(cd "$SCRIPT_DIR/../../.." && pwd)"

SSH_USER="${SSH_USER:-ubuntu}"
REMOTE_DIR="/home/$SSH_USER/tg_digest_system"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o ServerAliveInterval=5"

if [[ -z "${PUBLIC_IP:-}" ]]; then
  echo "Задайте PUBLIC_IP (публичный IP вашей ВМ в Yandex):"
  echo "  PUBLIC_IP=93.77.185.71 $0"
  exit 1
fi

echo "=== Деплой TG Digest на ВМ $PUBLIC_IP ==="
echo "Пользователь SSH: $SSH_USER"
echo ""

# Проверка SSH
if ! ssh $SSH_OPTS -o ConnectTimeout=5 "${SSH_USER}@${PUBLIC_IP}" "echo ok" 2>/dev/null; then
  echo "Не удалось подключиться по SSH к ${SSH_USER}@${PUBLIC_IP}"
  echo "Проверьте: порт 22 открыт, ключ в ssh-agent или ~/.ssh."
  exit 1
fi
echo "SSH доступен."
echo ""

# Копирование кода (каталог tg_digest_system целиком)
echo "Копирование проекта на ВМ..."
ssh $SSH_OPTS "${SSH_USER}@${PUBLIC_IP}" "mkdir -p $REMOTE_DIR"
SRC_DIR="$DOCKER_DIR/.."
if command -v rsync &>/dev/null; then
  rsync -avz -e "ssh $SSH_OPTS" \
    --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' --exclude 'node_modules' \
    "$SRC_DIR/" "${SSH_USER}@${PUBLIC_IP}:${REMOTE_DIR}/"
else
  (cd "$SRC_DIR" && tar cf - .) | ssh $SSH_OPTS "${SSH_USER}@${PUBLIC_IP}" "cd $REMOTE_DIR && tar xf -"
fi
echo "Код скопирован."
echo ""

# Запуск установки и деплоя на ВМ (на ВМ в REMOTE_DIR лежат docker/, web/, db/ и т.д.)
REMOTE_DOCKER_DIR="$REMOTE_DIR/docker"
echo "Запуск установки и деплоя на ВМ..."
ssh $SSH_OPTS "${SSH_USER}@${PUBLIC_IP}" "cd $REMOTE_DOCKER_DIR && chmod +x deploy.sh yandex/setup-on-vm.sh 2>/dev/null; bash yandex/setup-on-vm.sh"
echo ""

echo "=== Готово ==="
echo ""
echo "Вход:  http://${PUBLIC_IP}:8000/login"
echo ""
echo "Если кнопки «Войти через Яндекс» нет — подключитесь по SSH и добавьте в secrets.env:"
echo "  ssh ${SSH_USER}@${PUBLIC_IP}"
echo "  cd $REMOTE_DOCKER_DIR"
echo "  nano secrets.env   # добавьте YANDEX_OAUTH_CLIENT_ID, YANDEX_OAUTH_CLIENT_SECRET, JWT_SECRET"
echo "  docker compose up -d --force-recreate web"
echo ""
