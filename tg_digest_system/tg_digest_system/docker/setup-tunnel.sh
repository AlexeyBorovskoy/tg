#!/bin/bash
# Скрипт для настройки SSH туннеля к VPS серверу
# Использование: ./setup-tunnel.sh

VPS_HOST="45.95.2.49"
VPS_USER="sshadmin"  # Пользователь на VPS сервере
SOCKS_PORT="1080"
TUNNEL_PID_FILE="/tmp/ssh_tunnel_${SOCKS_PORT}.pid"

echo "=== Настройка SSH туннеля к VPS серверу ==="
echo "VPS: ${VPS_USER}@${VPS_HOST}"
echo "SOCKS5 порт: ${SOCKS_PORT}"
echo ""

# Проверяем, не запущен ли уже туннель
if [ -f "$TUNNEL_PID_FILE" ]; then
    OLD_PID=$(cat "$TUNNEL_PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "⚠️  Туннель уже запущен (PID: $OLD_PID)"
        echo "Остановить старый туннель? (y/n)"
        read -r answer
        if [ "$answer" = "y" ]; then
            kill "$OLD_PID" 2>/dev/null
            rm -f "$TUNNEL_PID_FILE"
            echo "Старый туннель остановлен"
        else
            echo "Используется существующий туннель"
            exit 0
        fi
    else
        rm -f "$TUNNEL_PID_FILE"
    fi
fi

# Проверяем доступность VPS сервера
echo "Проверка доступности VPS сервера..."
SSH_KEY="$HOME/.ssh/id_ed25519_vps"
if [ ! -f "$SSH_KEY" ]; then
    echo "❌ SSH ключ не найден: $SSH_KEY"
    exit 1
fi

if ! ssh -i "$SSH_KEY" -o ConnectTimeout=5 -o StrictHostKeyChecking=no "${VPS_USER}@${VPS_HOST}" "echo OK" > /dev/null 2>&1; then
    echo "❌ Не удалось подключиться к ${VPS_USER}@${VPS_HOST}"
    echo ""
    echo "Возможные причины:"
    echo "1. SSH ключ не настроен (нужно добавить публичный ключ на VPS)"
    echo "2. Неправильное имя пользователя (измените VPS_USER в скрипте)"
    echo "3. Файрвол блокирует подключение"
    echo ""
    echo "Для настройки SSH ключа:"
    echo "  ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_vps -N ''"
    echo "  ssh-copy-id -i ~/.ssh/id_ed25519_vps.pub ${VPS_USER}@${VPS_HOST}"
    exit 1
fi

echo "✅ Подключение к VPS успешно"
echo ""

# Запускаем туннель (слушаем на всех интерфейсах для доступа из Docker контейнеров)
echo "Запуск SSH туннеля..."
ssh -i "$SSH_KEY" -D "0.0.0.0:${SOCKS_PORT}" -f -N \
    -o ServerAliveInterval=60 \
    -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes \
    -o StrictHostKeyChecking=no \
    "${VPS_USER}@${VPS_HOST}"

if [ $? -eq 0 ]; then
    # Находим PID процесса
    TUNNEL_PID=$(ps aux | grep "ssh -D ${SOCKS_PORT}" | grep -v grep | awk '{print $2}' | head -1)
    if [ -n "$TUNNEL_PID" ]; then
        echo "$TUNNEL_PID" > "$TUNNEL_PID_FILE"
        echo "✅ Туннель запущен (PID: $TUNNEL_PID)"
        echo ""
        echo "Проверка туннеля:"
        ss -tlnp | grep "${SOCKS_PORT}" || echo "⚠️  Порт ${SOCKS_PORT} не слушается"
        echo ""
        echo "Для остановки туннеля:"
        echo "  kill $TUNNEL_PID"
        echo "  или: ./stop-tunnel.sh"
    else
        echo "⚠️  Туннель запущен, но не удалось определить PID"
    fi
else
    echo "❌ Не удалось запустить туннель"
    exit 1
fi
