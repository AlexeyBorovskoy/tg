#!/bin/bash
# ==============================================================================
# Деплой TG Digest System (Docker Compose)
# ==============================================================================
# Использование:
#   ./deploy.sh              — поднять все сервисы (postgres, migrate, web, worker)
#   ./deploy.sh --existing   — деплой с существующими БД, промптами и настройками
#   ./deploy.sh --build      — пересобрать образы и поднять
#   ./deploy.sh --migrate    — только выполнить миграции и выйти
#   ./deploy.sh --stop       — только остановить сервисы (данные сохраняются)
#
# Данные: при down volumes НЕ удаляются — БД, логи, сессия Telethon, медиа сохраняются.
# Не используйте «docker compose down -v» — это удалит все данные!
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

# .env
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    echo "Создаю .env из .env.example (заполните переменные!)."
    cp .env.example .env
  else
    echo "Создайте .env с переменными PGPASSWORD, TG_API_ID, TG_API_HASH, OPENAI_API_KEY и др."
    exit 1
  fi
fi

# secrets.env (опционально: OAuth, JWT и др.; если нет — создаём пустой из примера, чтобы compose не падал)
if [ ! -f secrets.env ]; then
  if [ -f secrets.env.example ]; then
    echo "Создаю secrets.env из secrets.env.example (при необходимости заполните YANDEX_OAUTH_*, BASE_URL, JWT_SECRET)."
    cp secrets.env.example secrets.env
  else
    touch secrets.env
  fi
fi

set -a
[ -f .env ] && source .env
[ -f secrets.env ] && source secrets.env
set +a

do_migrate_only=false
do_build=false
do_existing=false
do_stop_only=false
for arg in "$@"; do
  case "$arg" in
    --migrate)  do_migrate_only=true ;;
    --build)    do_build=true ;;
    --existing) do_existing=true ;;
    --stop)     do_stop_only=true ;;
  esac
done

# docker compose (v2) или docker-compose (v1)
COMPOSE_CMD="docker compose"
if ! docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
fi

COMPOSE_FILES="-f docker-compose.yml"
if [ "$do_existing" = true ]; then
  COMPOSE_FILES="-f docker-compose.yml -f docker-compose.existing.yml"
  if [ -z "${PGHOST}" ]; then
    echo "Для деплоя с существующими данными задайте в .env: PGHOST (хост существующей БД)"
    exit 1
  fi
  echo "Деплой с существующими данными: БД на $PGHOST, промпты и конфиг из репо"
  ./prepare-existing-data.sh 2>/dev/null || true
fi

if [ "$do_migrate_only" = true ]; then
  echo "Запуск только миграций..."
  $COMPOSE_CMD $COMPOSE_FILES run --rm migrate
  echo "Готово."
  exit 0
fi

if [ "$do_stop_only" = true ]; then
  echo "Останавливаем сервисы (данные в volumes сохраняются)..."
  $COMPOSE_CMD $COMPOSE_FILES stop
  echo "Готово. Для запуска снова: ./deploy.sh"
  exit 0
fi

echo "Деплой TG Digest System ($COMPOSE_CMD)"
echo "Root: $ROOT"
echo "Данные в volumes (postgres_data, worker_data, worker_logs, worker_media) сохраняются."
echo ""

if [ "$do_build" = true ]; then
  echo "Сборка образов..."
  $COMPOSE_CMD $COMPOSE_FILES build
fi

echo "Останавливаем контейнеры (down без -v, данные не удаляются)..."
$COMPOSE_CMD $COMPOSE_FILES down 2>/dev/null || true
echo "Запуск сервисов..."
$COMPOSE_CMD $COMPOSE_FILES up -d

echo ""
echo "Сервисы:"
$COMPOSE_CMD $COMPOSE_FILES ps

echo ""
echo "Веб-интерфейс: http://localhost:${WEB_PORT:-8000}"
echo "Логи: $COMPOSE_CMD $COMPOSE_FILES logs -f web   или  logs -f worker"
echo "Остановка: $COMPOSE_CMD $COMPOSE_FILES down"
