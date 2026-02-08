#!/bin/bash
# Диагностика воркера TG Digest на сервере
# Запуск: ./check_worker.sh или bash check_worker.sh

set -e

echo "=========================================="
echo "Проверка воркера TG Digest"
echo "=========================================="
echo ""

# 1. Systemd сервис воркера
echo "1. Статус сервиса tg_digest_worker:"
if systemctl is-active --quiet tg_digest_worker 2>/dev/null; then
    echo "   ✅ Сервис запущен"
    systemctl status tg_digest_worker --no-pager -l 2>/dev/null | head -15
else
    echo "   ❌ Сервис не запущен или не найден"
    systemctl status tg_digest_worker --no-pager 2>&1 | head -10 || true
fi
echo ""

# 2. Процесс Python (воркер может быть запущен без systemd)
echo "2. Процессы digest_worker:"
if pgrep -af "digest_worker" 2>/dev/null; then
    echo "   ✅ Процесс воркера найден"
else
    echo "   ⚠️ Процесс digest_worker не найден"
fi
echo ""

# 3. Последние логи воркера
echo "3. Последние 25 строк логов (journalctl -u tg_digest_worker):"
journalctl -u tg_digest_worker -n 25 --no-pager 2>/dev/null || echo "   (сервис не найден или нет логов)"
echo ""

# 4. Cron (если воркер запускается по расписанию)
echo "4. Записи cron, связанные с digest/tg:"
crontab -l 2>/dev/null | grep -E "digest|tg_digest|worker" || true
if [ -f /etc/crontab ]; then
    grep -E "digest|tg_digest|worker" /etc/crontab 2>/dev/null || true
fi
echo ""

# 5. Веб-сервис (для полноты картины)
echo "5. Статус веб-интерфейса tg_digest_web:"
if systemctl is-active --quiet tg_digest_web 2>/dev/null; then
    echo "   ✅ Веб-сервис запущен"
else
    echo "   ⚠️ Веб-сервис не запущен"
fi
echo ""

# 6. Рекомендации
echo "=========================================="
echo "Рекомендации:"
echo "=========================================="
if ! systemctl is-active --quiet tg_digest_worker 2>/dev/null; then
    echo "• Запустить воркер: sudo systemctl start tg_digest_worker"
    echo "• Включить автозапуск: sudo systemctl enable tg_digest_worker"
    echo "• Полные логи: journalctl -u tg_digest_worker -n 100 -f"
fi
echo "• Ручной запуск одного цикла (из каталога проекта с .env):"
echo "  cd /home/ripas/tg_digest_system/tg_digest_system/scripts && source ../../.env 2>/dev/null; python digest_worker.py --once"
echo ""
