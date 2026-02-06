#!/usr/bin/env bash
# ==============================================================================
# Прогресс первого прохода: количество сообщений в БД и курсор (last_msg_id).
# Запуск на сервере: bash ~/tg_digest_deploy/deploy/check_first_pass_progress.sh
# Читает .env (PGHOST, PGUSER, PGPASSWORD, PGDATABASE). Не прерывает работу воркера.
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

if [[ -z "${PGDATABASE}" ]] || [[ -z "${PGUSER}" ]]; then
  echo "Ошибка: задайте PGDATABASE и PGUSER в .env или окружении."
  exit 1
fi

export PGPASSWORD
export PAGER=cat
PSQL_OPTS="-h ${PGHOST:-127.0.0.1} -U $PGUSER -d $PGDATABASE -t -A --no-psqlrc"

echo "========== Прогресс первого прохода (канал $CHANNEL_ID) =========="
echo ""

# Количество сообщений в БД по каналу
MSG_COUNT=$(psql $PSQL_OPTS -c "SELECT COUNT(*) FROM tg.messages WHERE peer_type = '$PEER_TYPE' AND peer_id = $CHANNEL_ID" 2>/dev/null || echo "0")
echo "Сообщений в БД: $MSG_COUNT"

# Курсор: последний обработанный msg_id
LAST_MSG=$(psql $PSQL_OPTS -c "SELECT last_msg_id FROM rpt.report_state WHERE peer_type = '$PEER_TYPE' AND peer_id = $CHANNEL_ID" 2>/dev/null || echo "")
if [[ -n "$LAST_MSG" ]]; then
  echo "Последний обработанный msg_id (курсор): $LAST_MSG"
else
  echo "Курсор (rpt.report_state): ещё не создан — первый проход в процессе."
fi

# Диапазон msg_id в БД (для ориентира)
RANGE=$(psql $PSQL_OPTS -c "SELECT COALESCE(MIN(msg_id),0), COALESCE(MAX(msg_id),0) FROM tg.messages WHERE peer_type = '$PEER_TYPE' AND peer_id = $CHANNEL_ID" 2>/dev/null || echo "")
if [[ -n "$RANGE" ]] && [[ "$RANGE" != "0	0" ]]; then
  echo "Диапазон msg_id в БД: $RANGE"
fi

echo ""
echo "Процент выполнения первого прохода неизвестен до завершения синхронизации (общее число сообщений в канале не хранится). По мере роста «Сообщений в БД» и «Последний msg_id» загрузка продолжается."
echo "========== Конец =========="
