#!/bin/bash
# ==============================================================================
# quick_start.sh — Скрипт быстрого старта TG Digest System
# ==============================================================================
set -e

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║           TG Digest System — Быстрый старт                    ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Определяем директорию скрипта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Проверяем Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker не установлен!"
    echo "   Установите Docker: https://docs.docker.com/engine/install/"
    exit 1
fi

if ! command -v docker compose &> /dev/null; then
    echo "❌ Docker Compose не установлен!"
    exit 1
fi

echo "✅ Docker установлен: $(docker --version)"

# Проверяем .env
if [ ! -f ".env" ]; then
    echo ""
    echo "📝 Файл .env не найден. Создаём из шаблона..."
    cp .env.example .env
    echo ""
    echo "⚠️  ВАЖНО: Откройте файл .env и заполните все переменные!"
    echo "   nano .env"
    echo ""
    echo "   После заполнения запустите скрипт снова."
    exit 0
fi

# Проверяем заполненность .env
source .env 2>/dev/null || true

ERRORS=0

if [ -z "$TG_API_ID" ] || [ "$TG_API_ID" = "12345678" ]; then
    echo "❌ TG_API_ID не заполнен в .env"
    ERRORS=1
fi

if [ -z "$TG_API_HASH" ] || [ "$TG_API_HASH" = "your_api_hash_here" ]; then
    echo "❌ TG_API_HASH не заполнен в .env"
    ERRORS=1
fi

if [ -z "$TG_BOT_TOKEN" ] || [[ "$TG_BOT_TOKEN" == *"ABCdef"* ]]; then
    echo "❌ TG_BOT_TOKEN не заполнен в .env"
    ERRORS=1
fi

if [ -z "$OPENAI_API_KEY" ] || [[ "$OPENAI_API_KEY" == *"your-key"* ]]; then
    echo "❌ OPENAI_API_KEY не заполнен в .env"
    ERRORS=1
fi

if [ -z "$PGPASSWORD" ] || [ "$PGPASSWORD" = "CHANGE_ME_STRONG_PASSWORD_HERE" ]; then
    echo "❌ PGPASSWORD не заполнен в .env"
    ERRORS=1
fi

if [ $ERRORS -eq 1 ]; then
    echo ""
    echo "⚠️  Заполните указанные переменные в файле .env"
    echo "   nano .env"
    exit 1
fi

echo "✅ Файл .env заполнен"

# Проверяем channels.json
if [ ! -f "config/channels.json" ]; then
    echo "❌ Файл config/channels.json не найден"
    exit 1
fi

echo "✅ Файл config/channels.json найден"
echo ""

# Переходим в docker директорию
cd docker

# Проверяем, нужна ли авторизация
if [ ! -f "../data/telethon.session" ] && ! docker volume inspect tg_digest_worker_data &>/dev/null; then
    echo "🔐 Требуется авторизация в Telegram..."
    echo "   Сейчас запустится процесс авторизации."
    echo "   Введите номер телефона, затем код из Telegram."
    echo ""
    read -p "   Нажмите Enter для продолжения..."
    
    docker compose run --rm auth
    
    if [ $? -ne 0 ]; then
        echo "❌ Ошибка авторизации"
        exit 1
    fi
    echo "✅ Авторизация успешна!"
fi

# Запускаем систему
echo ""
echo "🚀 Запуск системы..."
docker compose up -d

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                    Система запущена!                          ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "📊 Статус контейнеров:"
docker compose ps
echo ""
echo "📝 Полезные команды:"
echo "   docker compose logs -f worker    # Просмотр логов"
echo "   docker compose down              # Остановка"
echo "   docker compose restart worker    # Перезапуск"
echo ""
echo "📖 Документация: docs/README.md"
