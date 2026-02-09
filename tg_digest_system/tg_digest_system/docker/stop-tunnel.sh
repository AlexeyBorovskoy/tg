#!/bin/bash
# Остановка SSH туннеля

SOCKS_PORT="1080"
TUNNEL_PID_FILE="/tmp/ssh_tunnel_${SOCKS_PORT}.pid"

if [ -f "$TUNNEL_PID_FILE" ]; then
    PID=$(cat "$TUNNEL_PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        kill "$PID"
        rm -f "$TUNNEL_PID_FILE"
        echo "✅ Туннель остановлен (PID: $PID)"
    else
        rm -f "$TUNNEL_PID_FILE"
        echo "⚠️  Процесс не найден, файл PID удален"
    fi
else
    # Пытаемся найти процесс по порту
    PID=$(ps aux | grep "ssh -D ${SOCKS_PORT}" | grep -v grep | awk '{print $2}' | head -1)
    if [ -n "$PID" ]; then
        kill "$PID"
        echo "✅ Туннель остановлен (PID: $PID)"
    else
        echo "⚠️  Туннель не найден"
    fi
fi
