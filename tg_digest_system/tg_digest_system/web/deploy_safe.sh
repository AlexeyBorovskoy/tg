#!/bin/bash
# Безопасный скрипт деплоя веб-интерфейса
# Использование: ./deploy_safe.sh

set -e  # Остановка при ошибке

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKUP_DIR="/home/ripas/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "=========================================="
echo "Безопасный деплой веб-интерфейса"
echo "=========================================="
echo ""

# Загружаем переменные окружения из .env
if [ -f "$PROJECT_ROOT/.env" ]; then
    echo "Загрузка переменных окружения из .env..."
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
    echo "✅ Переменные окружения загружены"
else
    echo "⚠️  Файл .env не найден, используем значения по умолчанию"
fi
echo ""

# Создаём директорию для бэкапов
mkdir -p "$BACKUP_DIR"

# Этап 1: Бэкапы
echo "Этап 1: Создание бэкапов..."
echo ""

echo "1.1. Бэкап БД..."
export PGHOST="${PGHOST:-localhost}"
export PGPORT="${PGPORT:-5432}"
export PGDATABASE="${PGDATABASE:-tg_digest}"
export PGUSER="${PGUSER:-tg_digest}"
pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -F c \
    -f "$BACKUP_DIR/tg_digest_db_backup_${TIMESTAMP}.dump"
echo "✅ Бэкап БД создан: $BACKUP_DIR/tg_digest_db_backup_${TIMESTAMP}.dump"
echo ""

echo "1.2. Бэкап инженерных документов..."
if [ -d "$PROJECT_ROOT/docs" ]; then
    tar -czf "$BACKUP_DIR/docs_backup_${TIMESTAMP}.tar.gz" \
        -C "$PROJECT_ROOT" docs/
    echo "✅ Бэкап документов создан: $BACKUP_DIR/docs_backup_${TIMESTAMP}.tar.gz"
else
    echo "⚠️  Директория docs не найдена, пропускаем"
fi
echo ""

echo "1.3. Бэкап конфигурации..."
if [ -f "$PROJECT_ROOT/tg_digest_system/config/channels.json" ]; then
    cp "$PROJECT_ROOT/tg_digest_system/config/channels.json" \
       "$PROJECT_ROOT/tg_digest_system/config/channels.json.backup_${TIMESTAMP}"
    echo "✅ Бэкап конфигурации создан"
else
    echo "⚠️  Файл channels.json не найден, пропускаем"
fi
echo ""

# Этап 2: Проверки
echo "Этап 2: Проверка текущего состояния..."
echo ""

echo "2.1. Проверка работы воркера..."
if systemctl is-active --quiet tg_digest_worker; then
    echo "✅ Воркер работает"
else
    echo "⚠️  Воркер не работает, но продолжаем"
fi
echo ""

echo "2.2. Проверка количества данных в БД..."
MESSAGE_COUNT=$(PGPASSWORD="$PGPASSWORD" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -t -c "SELECT COUNT(*) FROM tg.messages;" 2>/dev/null | xargs)
echo "   Сообщений в БД: $MESSAGE_COUNT"
echo ""

echo "2.3. Проверка инженерных документов..."
if [ -d "$PROJECT_ROOT/docs/reference" ]; then
    DOC_COUNT=$(ls -1 "$PROJECT_ROOT/docs/reference"/*.md 2>/dev/null | wc -l)
    echo "   Инженерных документов: $DOC_COUNT"
else
    echo "⚠️  Директория docs/reference не найдена"
fi
echo ""

# Этап 3: Миграции БД
echo "Этап 3: Применение миграций БД..."
echo ""

echo "3.1. Проверка существующих миграций..."
if PGPASSWORD="$PGPASSWORD" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -t -c "SELECT 1 FROM information_schema.tables WHERE table_name='users';" 2>/dev/null | grep -q 1; then
    echo "⚠️  Таблица users уже существует, пропускаем миграцию 001"
else
    echo "3.2. Применение миграции 001 (мультитенантность)..."
    cd "$PROJECT_ROOT/tg_digest_system/db/migrations"
    sudo -u postgres psql -d "$PGDATABASE" -f 001_add_user_id.sql
    echo "✅ Миграция 001 применена"
    
    # Проверка
    echo "3.3. Проверка данных после миграции..."
    NULL_COUNT=$(PGPASSWORD="$PGPASSWORD" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -t -c "SELECT COUNT(*) FROM tg.messages WHERE user_id IS NULL;" 2>/dev/null | xargs)
    if [ "$NULL_COUNT" = "0" ]; then
        echo "✅ Все сообщения получили user_id"
    else
        echo "⚠️  Внимание: $NULL_COUNT сообщений без user_id"
    fi
fi
echo ""

echo "3.4. Применение миграции 002 (сессии)..."
if PGPASSWORD="$PGPASSWORD" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -t -c "SELECT 1 FROM information_schema.tables WHERE table_name='user_sessions';" 2>/dev/null | grep -q 1; then
    echo "⚠️  Таблица user_sessions уже существует, пропускаем миграцию 002"
else
    cd "$PROJECT_ROOT/tg_digest_system/db/migrations"
    sudo -u postgres psql -d "$PGDATABASE" -f 002_add_user_sessions.sql
    echo "✅ Миграция 002 применена"
fi
echo ""

# Этап 4: Установка зависимостей
echo "Этап 4: Установка зависимостей веб-интерфейса..."
cd "$SCRIPT_DIR"
if [ -f "requirements.txt" ]; then
    # Используем виртуальное окружение если оно есть
    if [ -d "$PROJECT_ROOT/.venv" ]; then
        echo "Использование виртуального окружения..."
        "$PROJECT_ROOT/.venv/bin/pip" install -q -r requirements.txt
        echo "✅ Зависимости установлены в venv"
    else
        echo "⚠️  Виртуальное окружение не найдено, пропускаем установку зависимостей"
        echo "   Установите зависимости вручную: pip install -r requirements.txt"
    fi
else
    echo "⚠️  Файл requirements.txt не найден"
fi
echo ""

# Этап 5: Проверка конфигурации
echo "Этап 5: Проверка конфигурации..."
if [ -z "$PGHOST" ] || [ -z "$TG_API_ID" ]; then
    echo "⚠️  Некоторые переменные окружения не установлены"
else
    echo "✅ Переменные окружения настроены"
fi
echo ""

# Этап 6: Тестовый запуск
echo "Этап 6: Тестовый запуск веб-интерфейса..."
cd "$SCRIPT_DIR"
if [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
    if "$PROJECT_ROOT/.venv/bin/python" -c "import sys; sys.path.insert(0, '..'); import web_api" 2>/dev/null; then
        echo "✅ Модуль web_api импортируется успешно"
    else
        echo "⚠️  Ошибка импорта web_api (возможно зависимости не установлены)"
        echo "   Попробуйте запустить вручную для проверки"
    fi
else
    echo "⚠️  Виртуальное окружение не найдено, пропускаем проверку импорта"
fi
echo ""

# Итог
echo "=========================================="
echo "Деплой завершён успешно!"
echo "=========================================="
echo ""
echo "Следующие шаги:"
echo "1. Настроить nginx (если нужно):"
echo "   sudo cp nginx.conf.example /etc/nginx/sites-available/tg_digest_web"
echo ""
echo "2. Настроить systemd service:"
echo "   sudo cp tg_digest_web.service.example /etc/systemd/system/tg_digest_web.service"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable tg_digest_web"
echo "   sudo systemctl start tg_digest_web"
echo ""
echo "3. Проверить работу:"
echo "   curl http://localhost:8080/health"
echo ""
echo "Бэкапы сохранены в: $BACKUP_DIR"
echo ""
