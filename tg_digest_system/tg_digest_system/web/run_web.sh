#!/bin/bash
# Скрипт запуска веб-интерфейса

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

# Загружаем переменные окружения (.env и секреты)
set -a
[ -f "$PROJECT_ROOT/.env" ] && source "$PROJECT_ROOT/.env"
[ -f "$PROJECT_ROOT/docker/.env" ] && source "$PROJECT_ROOT/docker/.env"
[ -f "$PROJECT_ROOT/docker/secrets.env" ] && source "$PROJECT_ROOT/docker/secrets.env"
[ -f "$PROJECT_ROOT/secrets.env" ] && source "$PROJECT_ROOT/secrets.env"
set +a

# Активируем виртуальное окружение если есть
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Запускаем веб-сервер
cd "$SCRIPT_DIR"
exec python web_api.py
