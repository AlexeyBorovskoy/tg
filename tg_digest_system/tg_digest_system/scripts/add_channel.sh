#!/bin/bash
# Обёртка для add_channel.py - упрощённое добавление чата

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_DIR"

# Активируем виртуальное окружение если есть
if [ -f "../.venv/bin/activate" ]; then
    source ../.venv/bin/activate
fi

# Загружаем переменные окружения
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

# Запускаем скрипт
python3 "$SCRIPT_DIR/add_channel.py" "$@"
