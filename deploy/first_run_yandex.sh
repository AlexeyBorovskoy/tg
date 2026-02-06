#!/usr/bin/env bash
# ==============================================================================
# Первый запуск на Yandex VM: полная загрузка сообщений в БД и формирование
# первоначального сводного инженерного документа (запускать НА СЕРВЕРЕ под ripas).
#
# Использование:
#   bash ~/tg_digest_deploy/deploy/first_run_yandex.sh
#   bash ~/tg_digest_deploy/deploy/first_run_yandex.sh --reset-cursor  # сбросить курсор и загрузить всё заново
# ==============================================================================

set -e
DEPLOY=~/tg_digest_deploy
RESET_CURSOR=

for x in "$@"; do
  case "$x" in
    --reset-cursor) RESET_CURSOR=1 ;;
  esac
done

cd "$DEPLOY"
set -a && source .env && set +a
source .venv/bin/activate
export PYTHONPATH="$DEPLOY/scripts"

if [[ -n "$RESET_CURSOR" ]]; then
  echo "=== Сброс курсора (last_msg_id = 0) для полной загрузки ==="
  psql -h "${PGHOST:-127.0.0.1}" -U "$PGUSER" -d "$PGDATABASE" -v ON_ERROR_STOP=1 -c "
    UPDATE rpt.report_state SET last_msg_id = 0 WHERE peer_type = 'channel' AND peer_id = -1002700886173;
  " 2>/dev/null || echo "Таблица report_state может ещё не существовать или канал другой — продолжаем."
fi

echo "=== Запуск воркера (один цикл: загрузка сообщений, OCR, дайджест, сводный документ, рассылка) ==="
python scripts/digest_worker.py --once

echo ""
echo "Готово. Дальше воркер работает по расписанию (systemd tg_digest_worker)."
