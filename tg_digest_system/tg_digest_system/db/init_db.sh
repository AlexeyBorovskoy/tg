#!/bin/bash
# ==============================================================================
# init_db.sh — Инициализация базы данных
# ==============================================================================
set -e

echo "=== Инициализация базы данных TG Digest ==="

# Проверяем переменные
: "${PGHOST:?PGHOST не установлен}"
: "${PGPORT:?PGPORT не установлен}"
: "${PGDATABASE:?PGDATABASE не установлен}"
: "${PGUSER:?PGUSER не установлен}"
: "${PGPASSWORD:?PGPASSWORD не установлен}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Подключение к PostgreSQL: ${PGUSER}@${PGHOST}:${PGPORT}/${PGDATABASE}"

# Ждём готовности PostgreSQL
echo "Ожидание готовности PostgreSQL..."
until pg_isready -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -q; do
    echo "PostgreSQL недоступен, ждём 2 секунды..."
    sleep 2
done
echo "PostgreSQL готов!"

# Проверяем, есть ли уже схема
SCHEMA_EXISTS=$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -tAc \
    "SELECT EXISTS(SELECT 1 FROM information_schema.schemata WHERE schema_name = 'tg')")

if [ "$SCHEMA_EXISTS" = "t" ]; then
    echo "Схема tg уже существует. Пропускаем инициализацию."
    echo "Для пересоздания используйте: DROP SCHEMA tg CASCADE; DROP SCHEMA rpt CASCADE;"
else
    echo "Создаём схему базы данных..."
    psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -f "${SCRIPT_DIR}/schema.sql"
    echo "Схема создана успешно!"
fi

# Если есть дамп, восстанавливаем данные
if [ -f "${SCRIPT_DIR}/pg_rag.dump" ]; then
    echo "Найден дамп pg_rag.dump. Восстанавливаем данные..."
    
    # Проверяем, есть ли уже данные
    MSG_COUNT=$(psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -tAc \
        "SELECT COUNT(*) FROM tg.messages" 2>/dev/null || echo "0")
    
    if [ "$MSG_COUNT" = "0" ]; then
        pg_restore -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
            --no-owner --no-privileges --data-only \
            "${SCRIPT_DIR}/pg_rag.dump" 2>/dev/null || true
        echo "Данные восстановлены!"
    else
        echo "В базе уже есть данные (${MSG_COUNT} сообщений). Пропускаем восстановление."
    fi
fi

echo "=== Инициализация завершена ==="
