#!/bin/bash
# Скрипт запуска веб-интерфейса

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

# Загружаем переменные окружения
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

# Активируем виртуальное окружение если есть
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Запускаем веб-сервер
cd "$SCRIPT_DIR"
exec python web_api.py
