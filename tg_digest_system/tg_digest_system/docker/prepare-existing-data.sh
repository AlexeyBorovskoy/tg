#!/bin/bash
# ==============================================================================
# Подготовка каталогов для деплоя с существующими данными
# ==============================================================================
# Создаёт структуру каталогов и копирует .env.example в .env при необходимости.
# Запуск из каталога docker/: ./prepare-existing-data.sh
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DATA_DIR="${EXISTING_DATA_DIR:-./data}"
CONFIG_SRC="${EXISTING_CONFIG_DIR:-../config}"
PROMPTS_SRC="${EXISTING_PROMPTS_DIR:-../prompts}"

echo "Подготовка данных для деплоя с существующими БД/промптами/настройками"
echo "  EXISTING_DATA_DIR (каталог данных): $DATA_DIR"
echo "  Конфиг (уже в репо):               $CONFIG_SRC"
echo "  Промпты (уже в репо):             $PROMPTS_SRC"
echo ""

mkdir -p "$DATA_DIR"/worker_data "$DATA_DIR"/logs "$DATA_DIR"/media
echo "Каталоги созданы: $DATA_DIR/worker_data, $DATA_DIR/logs, $DATA_DIR/media"

if [ ! -f "$DATA_DIR/worker_data/telethon.session" ]; then
  echo ""
  echo "Сессия Telethon не найдена: $DATA_DIR/worker_data/telethon.session"
  echo "После первого запуска выполните: docker compose -f docker-compose.yml -f docker-compose.existing.yml run --rm auth"
fi

if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    echo ""
    echo "Создан .env из .env.example. Заполните PGHOST (хост существующей БД), PGPASSWORD, TG_*, OPENAI_API_KEY"
  fi
fi

echo ""
echo "Готово. Деплой: ./deploy.sh --existing"
