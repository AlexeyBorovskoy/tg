#!/usr/bin/env bash
# ==============================================================================
# Установка и первый запуск TG Digest на ВМ (Yandex Cloud или любая Ubuntu).
# Запускать на сервере после клонирования репо в каталог с docker-compose.
#
# Использование (на ВМ, в каталоге docker):
#   cd /path/to/tg_digest_system/tg_digest_system/docker
#   bash yandex/setup-on-vm.sh
#
# Скрипт:
#   - проверяет/устанавливает Docker и Docker Compose
#   - создаёт .env и secrets.env из примеров, если их нет
#   - подставляет BASE_URL по публичному IP (если не задан)
#   - запускает ./deploy.sh --build
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Каталог с docker-compose и .env.example (родитель yandex/)
DOCKER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$DOCKER_DIR"

echo "=== TG Digest: установка на ВМ ==="
echo "Каталог: $DOCKER_DIR"
echo ""

# Docker
if ! command -v docker &>/dev/null; then
  echo "Установка Docker..."
  curl -fsSL https://get.docker.com | sh
  usermod -aG docker "$USER" 2>/dev/null || sudo usermod -aG docker "$USER"
  echo "Установлен. Перезайдите по SSH и запустите скрипт снова."
  exit 0
fi

if ! docker info &>/dev/null; then
  echo "Запустите скрипт после входа в группу docker: newgrp docker или перелогиньтесь."
  exit 1
fi

# Docker Compose (v1 или v2)
COMPOSE_CMD="docker compose"
if ! docker compose version &>/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
  if ! command -v docker-compose &>/dev/null; then
    echo "Установка docker-compose..."
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
  fi
fi
echo "Compose: $COMPOSE_CMD"

# .env
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Создан .env из примера. Отредактируйте: PGPASSWORD, WEB_PORT."
fi

# secrets.env
if [[ ! -f secrets.env ]]; then
  cp secrets.env.example secrets.env
  echo "Создан secrets.env из примера. Заполните: PGPASSWORD, TG_*, OPENAI_API_KEY, JWT_SECRET и др."
fi

# Единый PGPASSWORD для postgres и migrate (если не задан — ставим tg_digest_local, чтобы не было сбоя аутентификации)
if ! grep -q '^PGPASSWORD=.' .env 2>/dev/null && ! grep -q '^PGPASSWORD=.' secrets.env 2>/dev/null; then
  echo "PGPASSWORD=tg_digest_local" >> .env
  echo "В .env добавлен PGPASSWORD=tg_digest_local для совпадения с контейнером postgres."
fi

# BASE_URL по публичному IP, если не задан
if ! grep -q '^BASE_URL=' .env 2>/dev/null && ! grep -q '^BASE_URL=' secrets.env 2>/dev/null; then
  PUBLIC_IP=$(curl -s --max-time 3 ifconfig.me 2>/dev/null || curl -s --max-time 3 icanhazip.com 2>/dev/null || echo "")
  if [[ -n "$PUBLIC_IP" ]]; then
    WEB_PORT=$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2 || echo "8000")
    echo "BASE_URL=http://${PUBLIC_IP}:${WEB_PORT}" >> secrets.env
    echo "В secrets.env добавлен BASE_URL=http://${PUBLIC_IP}:${WEB_PORT}"
  fi
fi

# Включить авторизацию по умолчанию
if ! grep -q '^AUTH_OWN_ENABLED=' .env 2>/dev/null && ! grep -q '^AUTH_OWN_ENABLED=' secrets.env 2>/dev/null; then
  echo "AUTH_OWN_ENABLED=1" >> secrets.env
  echo "В secrets.env добавлен AUTH_OWN_ENABLED=1"
fi

echo ""
echo "Запуск деплоя..."
chmod +x deploy.sh
./deploy.sh --build

echo ""
echo "Готово. Проверка: curl -s http://localhost:8000/health"
echo "Вход: http://<ПУБЛИЧНЫЙ_IP>:8000/login"
