#!/usr/bin/env bash
# ==============================================================================
# Healthcheck TG Digest на Yandex VM: проверка работы решения и ресурсов сервера.
# При сбое — алерт в Telegram (бот @alexeyborovskoy_bot, chat_id из .env или 499412926).
#
# Запуск на сервере: bash ~/tg_digest_deploy/deploy/healthcheck_yandex.sh
# Cron (каждые 2 часа): 0 */2 * * * bash /home/ripas/tg_digest_deploy/deploy/healthcheck_yandex.sh
# ==============================================================================

set -e
DEPLOY="${TG_DIGEST_DEPLOY:-$HOME/tg_digest_deploy}"
# Если воркер работает из tg_digest_system, используем его путь для heartbeat
WORKER_DIR="${TG_DIGEST_SYSTEM:-$HOME/tg_digest_system}"
ALERT_CHAT_ID="${HEALTHCHECK_ALERT_CHAT_ID:-499412926}"
HEARTBEAT_MAX_AGE_HOURS="${HEARTBEAT_MAX_AGE_HOURS:-25}"
MEM_MIN_FREE_PERCENT="${MEM_MIN_FREE_PERCENT:-10}"
DISK_MAX_USE_PERCENT="${DISK_MAX_USE_PERCENT:-90}"

# Загружаем .env
if [[ -f "$DEPLOY/.env" ]]; then
  set -a
  source "$DEPLOY/.env"
  set +a
fi
# Если LOGS_DIR задан в .env, используем его, иначе проверяем оба места
HEARTBEAT_DIR="${LOGS_DIR:-$WORKER_DIR/logs}"

send_alert() {
  local text="⚠️ TG Digest: $1"
  if [[ -z "$TG_BOT_TOKEN" ]]; then
    echo "ALERT (no TG_BOT_TOKEN): $text"
    return
  fi
  curl -s -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${ALERT_CHAT_ID}" \
    --data-urlencode "text=${text}" >/dev/null || true
}

HAS_FAILURE=0
MSG=""

# 1. Сервис tg_digest_worker активен
if ! systemctl is-active --quiet tg_digest_worker 2>/dev/null; then
  HAS_FAILURE=1
  MSG="сервис tg_digest_worker не активен"
  send_alert "Сервис tg_digest_worker не активен. Проверьте: systemctl status tg_digest_worker"
fi

# 2. Подключение к PostgreSQL
if [[ $HAS_FAILURE -eq 0 ]] && [[ -n "$PGHOST" ]] && [[ -n "$PGUSER" ]] && [[ -n "$PGDATABASE" ]]; then
  if ! PGPASSWORD="${PGPASSWORD}" psql -h "${PGHOST:-127.0.0.1}" -U "$PGUSER" -d "$PGDATABASE" -t -c "SELECT 1" 2>/dev/null | grep -q 1; then
    HAS_FAILURE=1
    MSG="нет подключения к БД tg_digest"
    send_alert "Нет подключения к БД tg_digest. Проверьте PostgreSQL и .env"
  fi
fi

# 3. Heartbeat: последний успешный цикл воркера не старше N часов
HEARTBEAT_FILE="$HEARTBEAT_DIR/heartbeat.txt"
if [[ $HAS_FAILURE -eq 0 ]] && [[ -f "$HEARTBEAT_FILE" ]]; then
  HB_TS=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || echo 0)
  NOW=$(date +%s)
  MAX_AGE=$((HEARTBEAT_MAX_AGE_HOURS * 3600))
  if (( NOW - HB_TS > MAX_AGE )); then
    HAS_FAILURE=1
    MSG="heartbeat старше ${HEARTBEAT_MAX_AGE_HOURS} ч (воркер не завершал цикл)"
    send_alert "Heartbeat старше ${HEARTBEAT_MAX_AGE_HOURS} ч. Воркер мог упасть или зависнуть. Логи: journalctl -u tg_digest_worker -n 100"
  fi
elif [[ $HAS_FAILURE -eq 0 ]] && [[ ! -f "$HEARTBEAT_FILE" ]]; then
  # Файла ещё нет — первый запуск или воркер ни разу не завершил цикл
  echo "Heartbeat файл отсутствует (возможен первый запуск)"
fi

# 4. Память: свободно не менее N%
if [[ $HAS_FAILURE -eq 0 ]]; then
  TOTAL=$(free -m | awk '/^Mem:/{print $2}')
  AVAIL=$(free -m | awk '/^Mem:/{print $7}')
  if [[ -n "$TOTAL" ]] && [[ "$TOTAL" -gt 0 ]]; then
    FREE_PERCENT=$((AVAIL * 100 / TOTAL))
    if (( FREE_PERCENT < MEM_MIN_FREE_PERCENT )); then
      HAS_FAILURE=1
      MSG="мало свободной памяти: ${FREE_PERCENT}% (порог ${MEM_MIN_FREE_PERCENT}%)"
      send_alert "Сервер: свободной памяти ${FREE_PERCENT}%. Порог ${MEM_MIN_FREE_PERCENT}%. free -m"
    fi
  fi
fi

# 5. Диск: занято не более N%
if [[ $HAS_FAILURE -eq 0 ]]; then
  USE_PERCENT=$(df -P / | awk 'NR==2 {gsub(/%/,""); print $5}')
  if [[ -n "$USE_PERCENT" ]] && (( USE_PERCENT > DISK_MAX_USE_PERCENT )); then
    HAS_FAILURE=1
    MSG="диск заполнен на ${USE_PERCENT}% (порог ${DISK_MAX_USE_PERCENT}%)"
    send_alert "Сервер: диск заполнен на ${USE_PERCENT}%. Порог ${DISK_MAX_USE_PERCENT}%. df -h"
  fi
fi

if [[ $HAS_FAILURE -ne 0 ]]; then
  echo "HEALTHCHECK FAIL: $MSG"
  exit 1
fi

echo "HEALTHCHECK OK"
exit 0
