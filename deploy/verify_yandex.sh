#!/usr/bin/env bash
# ==============================================================================
# Проверка «всё ли на сервере работает»: сервис, БД, heartbeat, ресурсы, прогресс.
# Запуск на сервере: bash ~/tg_digest_deploy/deploy/verify_yandex.sh
# Не меняет состояние сервера, только читает.
# ==============================================================================

set -e
DEPLOY="${TG_DIGEST_DEPLOY:-$HOME/tg_digest_deploy}"
CHANNEL_ID="${CHANNEL_ID:--1002700886173}"
PEER_TYPE="${PEER_TYPE:-channel}"

if [[ -f "$DEPLOY/.env" ]]; then
  set -a
  source "$DEPLOY/.env"
  set +a
fi

export PGPASSWORD
export PAGER=cat
PSQL_OPTS="-h ${PGHOST:-127.0.0.1} -U ${PGUSER} -d ${PGDATABASE} -t -A --no-psqlrc 2>/dev/null"

ok()  { echo "  OK   $1"; }
fail() { echo "  FAIL $1"; }
warn() { echo "  --   $1"; }

echo "=============================================="
echo " Проверка TG Digest на Yandex VM"
echo " $(date -Iseconds 2>/dev/null || date)"
echo "=============================================="
echo ""

# 1. Сервис tg_digest_worker
echo "1. Сервис tg_digest_worker"
if systemctl is-active --quiet tg_digest_worker 2>/dev/null; then
  ok "сервис активен (running)"
else
  fail "сервис не активен — выполните: systemctl status tg_digest_worker"
fi
echo ""

# 2. PostgreSQL
echo "2. PostgreSQL (подключение к БД)"
if [[ -n "${PGUSER}" ]] && [[ -n "${PGDATABASE}" ]]; then
  if psql $PSQL_OPTS -c "SELECT 1" 2>/dev/null | grep -q 1; then
    ok "подключение к БД успешно"
  else
    fail "не удалось подключиться к БД (проверьте PGHOST, PGUSER, PGPASSWORD, PGDATABASE в .env)"
  fi
else
  fail "PGUSER или PGDATABASE не заданы в .env"
fi
echo ""

# 3. Heartbeat (последний успешный цикл)
echo "3. Heartbeat (последний успешный цикл воркера)"
HEARTBEAT_FILE="$DEPLOY/logs/heartbeat.txt"
if [[ -f "$HEARTBEAT_FILE" ]]; then
  MTIME=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || echo 0)
  NOW=$(date +%s)
  AGE_H=$(( (NOW - MTIME) / 3600 ))
  if [[ $AGE_H -lt 25 ]]; then
    ok "heartbeat есть, возраст ~${AGE_H} ч"
  else
    warn "heartbeat старый (~${AGE_H} ч) — воркер давно не завершал цикл или первый проход ещё идёт"
  fi
else
  warn "файл heartbeat отсутствует (нормально при первом запуске до завершения первого цикла)"
fi
echo ""

# 4. Прогресс (сообщения в БД)
echo "4. Прогресс загрузки (канал $CHANNEL_ID)"
if [[ -n "${PGUSER}" ]] && [[ -n "${PGDATABASE}" ]]; then
  MSG_COUNT=$(psql $PSQL_OPTS -c "SELECT COUNT(*) FROM tg.messages WHERE peer_type = '$PEER_TYPE' AND peer_id = $CHANNEL_ID" 2>/dev/null || echo "?")
  LAST_MSG=$(psql $PSQL_OPTS -c "SELECT last_msg_id FROM rpt.report_state WHERE peer_type = '$PEER_TYPE' AND peer_id = $CHANNEL_ID" 2>/dev/null || echo "")
  echo "  Сообщений в БД: ${MSG_COUNT:-?}"
  if [[ -n "$LAST_MSG" ]]; then
    echo "  Последний msg_id (курсор): $LAST_MSG"
  else
    echo "  Курсор: ещё не создан (первый проход в процессе)"
  fi
  ok "данные по каналу получены"
else
  warn "БД недоступна — прогресс не проверен"
fi
echo ""

# 5. Память
echo "5. Ресурсы (память, диск)"
TOTAL=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}')
AVAIL=$(free -m 2>/dev/null | awk '/^Mem:/{print $7}')
if [[ -n "$TOTAL" ]] && [[ "$TOTAL" -gt 0 ]]; then
  FREE_PCT=$(( AVAIL * 100 / TOTAL ))
  if [[ $FREE_PCT -ge 10 ]]; then
    ok "память: свободно ~${FREE_PCT}%"
  else
    fail "память: свободно только ~${FREE_PCT}% (рекомендуется ≥10%)"
  fi
else
  warn "не удалось прочитать память"
fi
USE_PCT=$(df -P / 2>/dev/null | awk 'NR==2 {gsub(/%/,""); print $5}')
if [[ -n "$USE_PCT" ]]; then
  if [[ $USE_PCT -le 90 ]]; then
    ok "диск: занято ${USE_PCT}%"
  else
    fail "диск: занято ${USE_PCT}% (рекомендуется ≤90%)"
  fi
else
  warn "не удалось прочитать диск"
fi
echo ""

# 6. Процесс воркера
echo "6. Процесс воркера (python)"
if pgrep -f "digest_worker|run_worker" >/dev/null 2>&1; then
  ok "процесс воркера запущен"
else
  fail "процесс воркера не найден (при активном сервисе проверьте: journalctl -u tg_digest_worker -n 50)"
fi
echo ""

echo "=============================================="
echo " Конец проверки"
echo "=============================================="
echo ""
echo "При проблемах: journalctl -u tg_digest_worker -n 200 --no-pager"
