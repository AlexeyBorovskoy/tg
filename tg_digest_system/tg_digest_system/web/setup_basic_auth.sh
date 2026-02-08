#!/bin/bash
# Скрипт настройки HTTP Basic Auth для веб-интерфейса
# Использование: ./setup_basic_auth.sh [username] [password]
# Если пароль не указан, будет запрошен интерактивно

set -e

USERNAME="${1:-admin}"
PASSWORD="${2:-}"
CONFIG_FILE="/etc/nginx/sites-available/tg_digest_web"
PASSWD_FILE="/etc/nginx/.htpasswd_tg_digest"

echo "=========================================="
echo "Настройка HTTP Basic Auth"
echo "=========================================="
echo ""

# Проверяем наличие htpasswd
if ! command -v htpasswd &> /dev/null; then
    echo "Установка apache2-utils для htpasswd..."
    sudo apt-get update -qq
    sudo apt-get install -y apache2-utils
fi

# Создаём файл с паролями
echo "Создание файла паролей для пользователя: $USERNAME"
if [ -n "$PASSWORD" ]; then
    # Пароль передан как параметр
    echo "$PASSWORD" | sudo htpasswd -ci "$PASSWD_FILE" "$USERNAME"
else
    # Запрашиваем пароль интерактивно
    echo "Введите пароль для пользователя $USERNAME:"
    sudo htpasswd -c "$PASSWD_FILE" "$USERNAME"
fi

echo ""
echo "✅ Файл паролей создан: $PASSWD_FILE"
echo ""

# Копируем конфигурацию nginx
echo "Копирование конфигурации nginx..."
sudo cp "$(dirname "$0")/nginx.conf.basic_auth" "$CONFIG_FILE"

# Создаём симлинк если его нет
if [ ! -L "/etc/nginx/sites-enabled/tg_digest_web" ]; then
    sudo ln -sf "$CONFIG_FILE" /etc/nginx/sites-enabled/tg_digest_web
fi

# Проверяем конфигурацию
echo "Проверка конфигурации nginx..."
sudo nginx -t

if [ $? -eq 0 ]; then
    echo "✅ Конфигурация корректна"
    echo ""
    echo "Перезагрузка nginx..."
    sudo systemctl reload nginx
    echo "✅ Nginx перезагружен"
else
    echo "❌ Ошибка в конфигурации nginx"
    exit 1
fi

echo ""
echo "=========================================="
echo "Настройка завершена!"
echo "=========================================="
echo ""
echo "Доступ к веб-интерфейсу:"
echo "  http://tg-digest.158.160.19.253.nip.io/"
echo "  или"
echo "  http://158.160.19.253/"
echo ""
echo "Логин: $USERNAME"
echo "Пароль: (тот что вы ввели)"
echo ""
echo "Для добавления дополнительных пользователей:"
echo "  sudo htpasswd $PASSWD_FILE username"
echo ""
