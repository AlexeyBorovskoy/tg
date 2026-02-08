#!/bin/sh
# ==============================================================================
# Проверка порта 5433 и подключения к PostgreSQL (контейнер TG Digest)
# ==============================================================================
# Запуск из каталога docker/: ./check-port-5433.sh
# Требует: nc, опционально psql и .env с PGPASSWORD
# ==============================================================================
set -e

HOST="${POSTGRES_BIND_ADDRESS:-127.0.0.1}"
PORT="${POSTGRES_HOST_PORT:-5433}"
DB="${PGDATABASE:-tg_digest}"
USER="${PGUSER:-tg_digest}"

echo "Проверка порта ${HOST}:${PORT}..."
if ! command -v nc >/dev/null 2>&1; then
  echo "  nc не найден, проверка порта пропущена"
else
  if nc -zv "$HOST" "$PORT" 2>/dev/null; then
    echo "  Порт ${PORT} доступен."
  else
    echo "  Ошибка: порт ${PORT} недоступен (запустите контейнеры: ./deploy.sh)"
    exit 1
  fi
fi

echo "Проверка подключения к БД ${DB}..."
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
if command -v psql >/dev/null 2>&1 && [ -n "${PGPASSWORD}" ]; then
  if psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" -t -c 'SELECT 1' 2>/dev/null | grep -q 1; then
    echo "  Подключение к PostgreSQL успешно."
  else
    echo "  Ошибка: не удалось подключиться к БД (проверьте PGPASSWORD в .env)"
    exit 1
  fi
else
  echo "  psql или PGPASSWORD не заданы — проверка БД пропущена."
fi

echo "Готово: порт 5433 и сетевые настройки в порядке."
