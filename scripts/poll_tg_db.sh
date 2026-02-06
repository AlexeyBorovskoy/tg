#!/usr/bin/env bash
set -euo pipefail

DB="${PGDATABASE:-rag}"
PEER_TYPE="${PEER_TYPE:-channel}"
PEER_ID="${PEER_ID:-2700886173}"

REPO_DIR="${REPO_DIR:-$HOME/analysis-methodology}"
BRANCH="${BRANCH:-master}"

DAY_UTC="$(date -u +%Y-%m-%d)"
OUT_DIR="${REPO_DIR}/docs/digests/${DAY_UTC}"
mkdir -p "${OUT_DIR}"

psqlq() { psql -X -qAt -d "${DB}" -c "$1"; }

# --- Telegram notify (Bot API) ---
tg_notify() {
  # Requires: TG_BOT_TOKEN, TG_CLIENT_CHAT_ID; optional: GITLAB_PROJECT_URL; TG_DEBUG
  if [[ -z "${TG_BOT_TOKEN:-}" || -z "${TG_CLIENT_CHAT_ID:-}" ]]; then
    return 0
  fi

  local caption msg
  caption="Increment digest: ${PEER_TYPE} ${PEER_ID} (${LAST_MSG_ID}->${MAX_MSG_ID})"
  msg="Digest updated and pushed.
Repo: ${GITLAB_PROJECT_URL:-}
File: ${OUT_FILE}"

  # Debug: if TG_DEBUG=1 then print JSON responses to journal (stderr)
  if [[ "${TG_DEBUG:-0}" == "1" ]]; then
    curl -sS --max-time 10 -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
      -d "chat_id=${TG_CLIENT_CHAT_ID}" \
      --data-urlencode "text=${msg}" | sed -n '1,200p' >&2 || true

    curl -sS --max-time 30 -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendDocument" \
      -F "chat_id=${TG_CLIENT_CHAT_ID}" \
      -F "caption=${caption}" \
      -F "document=@${OUT_FILE}" | sed -n '1,200p' >&2 || true
  else
    curl -sS --max-time 10 -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
      -d "chat_id=${TG_CLIENT_CHAT_ID}" \
      --data-urlencode "text=${msg}" >/dev/null || true

    curl -sS --max-time 30 -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendDocument" \
      -F "chat_id=${TG_CLIENT_CHAT_ID}" \
      -F "caption=${caption}" \
      -F "document=@${OUT_FILE}" >/dev/null || true
  fi
}

LAST_MSG_ID="$(psqlq "SELECT last_msg_id FROM rpt.report_state WHERE peer_type='${PEER_TYPE}' AND peer_id=${PEER_ID} LIMIT 1;")"
MAX_MSG_ID="$(psqlq "SELECT COALESCE(max(msg_id),0) FROM tg.messages WHERE peer_type='${PEER_TYPE}' AND peer_id=${PEER_ID};")"

# Валидация чисел
re='^[0-9]+$'
if [[ ! "${LAST_MSG_ID}" =~ ${re} ]]; then
  echo "ERROR: invalid LAST_MSG_ID='${LAST_MSG_ID}' (psql must be -X -qAt)" >&2
  exit 2
fi
if [[ ! "${MAX_MSG_ID}" =~ ${re} ]]; then
  echo "ERROR: invalid MAX_MSG_ID='${MAX_MSG_ID}' (psql must be -X -qAt)" >&2
  exit 2
fi

if (( MAX_MSG_ID <= LAST_MSG_ID )); then
  echo "NOOP: no new messages (last=${LAST_MSG_ID}, max=${MAX_MSG_ID})"
  exit 0
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="${OUT_DIR}/digest_${PEER_TYPE}_${PEER_ID}_from_${LAST_MSG_ID}_to_${MAX_MSG_ID}.md"

psql -X -qAt -P pager=off -d "${DB}" -v ON_ERROR_STOP=1 <<SQL > "${OUT_FILE}"
-- digest_increment
-- peer_type=${PEER_TYPE} peer_id=${PEER_ID}
-- from_msg_id=${LAST_MSG_ID} to_msg_id=${MAX_MSG_ID}
-- generated_utc=${TS}

WITH inc AS (
  SELECT dt, msg_id, sender_name, coalesce(nullif(text,''), '[EMPTY]') AS text
  FROM tg.messages
  WHERE peer_type='${PEER_TYPE}' AND peer_id=${PEER_ID}
    AND msg_id > ${LAST_MSG_ID}
    AND msg_id <= ${MAX_MSG_ID}
  ORDER BY dt ASC, msg_id ASC
)
SELECT
  '# Increment digest' || E'\n\n' ||
  'Peer: ${PEER_TYPE} ${PEER_ID}' || E'\n' ||
  'Window: msg_id (${LAST_MSG_ID}, ${MAX_MSG_ID}]' || E'\n' ||
  'Generated (UTC): ${TS}' || E'\n\n' ||
  string_agg(
    '- **' || to_char(dt, 'YYYY-MM-DD HH24:MI:SSOF') || '**'
    || ' `msg_id=' || msg_id || '` '
    || '**' || coalesce(sender_name,'[NO_SENDER]') || '**: '
    || replace(left(text, 1500), E'\n', ' ')
  , E'\n'
  )
FROM inc;
SQL

# LLM digest (GigaChat) поверх RAW-инкремента
RAW_FILE=""
export RAW_FILE
OUT_LLM_FILE="${OUT_DIR}/digest_llm_${PEER_TYPE}_${PEER_ID}_from_${LAST_MSG_ID}_to_${MAX_MSG_ID}.md"

LLM_TEXT=""

# --- MEDIA (OCR / captions) для prompt ---
MEDIA_SECTION="1000 1000 1001
  psql -X -qAt -P pager=off -d "${DB}" \
    -v peer_type="${PEER_TYPE}" \
    -v peer_id="${PEER_ID}" \
    -v msg_id_from="${LAST_MSG_ID}" \
    -v msg_id_to="${MAX_MSG_ID}" <<'SQL' 2>/dev/null || true
SELECT
  '- msg_id=' || mt.msg_id || ': ' ||
  left(regexp_replace(coalesce(mt.ocr_text,''), '\s+', ' ', 'g'), 400)
FROM tg.media_text mt
WHERE mt.peer_type = :'peer_type'
  AND mt.peer_id   = (:'peer_id')::bigint
  AND mt.msg_id >  (:'msg_id_from')::bigint
  AND mt.msg_id <= (:'msg_id_to')::bigint
  AND coalesce(mt.ocr_text,'') <> ''
ORDER BY mt.msg_id;
SQL
)"
export MEDIA_SECTION

if [[ -x "${REPO_DIR}/scripts/llm_gigachat_chat.sh" ]]; then
  PROMPT_JSON="$(python3 - <<'PY'
import json, os

raw_file = os.environ["RAW_FILE"]
media = os.environ.get("MEDIA_SECTION", "").strip()

raw = open(raw_file, "r", encoding="utf-8").read()

extra = ""
if media:
    extra = "\n\nMEDIA (OCR фрагменты):\n" + media + "\n"

prompt = (
  "Сформируй управленческий дайджест по инкременту сообщений.\n"
  "Требования:\n"
  "1) 5-10 буллетов по сути;\n"
  "2) секции: Решения/Задачи, Риски/Проблемы, Следующие шаги;\n"
  "3) без воды;\n"
  "4) при фактах сохраняй ссылку msg_id (как в RAW).\n"
  "5) формат ссылок строго: msg_id=<число> (пример: msg_id=1241).\n\n"
  "RAW-инкремент ниже:\n\n" + raw + extra
)
print(json.dumps([prompt], ensure_ascii=False))
PY
  )"
  LLM_TEXT="$(echo "${PROMPT_JSON}" | "${REPO_DIR}/scripts/llm_gigachat_chat.sh" 2>/dev/null || true)"

  # Нормализация LLM: удаляем "висячие" строки вида "- msg_id=123" или "msg_id=123"
  # Важно: передаём LLM_TEXT в окружение python-процесса, иначе будет пусто
  LLM_TEXT="$(LLM_TEXT="$LLM_TEXT" python3 - <<'PY2'
import os, re
t = os.environ.get('LLM_TEXT','')
t = t.replace('\r\n','\n').replace('\r','\n')

out = []
pat = re.compile(r"^(?:-\s*)?msg_id=\d+\s*:??\s*$")
for line in t.split('\n'):
    s = line.strip()
    # удаляем строки, содержащие только msg_id без содержания
    if pat.fullmatch(s):
        continue
    out.append(line)

# убираем пустые строки в конце
while out and out[-1].strip() == '':
    out.pop()

print('\n'.join(out))
PY2
  )"
fi

if [[ -n "${LLM_TEXT}" ]]; then
  {
    echo "# LLM digest"
    echo
    echo "Peer: ${PEER_TYPE} ${PEER_ID}"
    echo "Window: msg_id (${LAST_MSG_ID}, ${MAX_MSG_ID}]"
    echo "Generated (UTC): ${TS}"
    echo
    echo "${LLM_TEXT}"
  } > "${OUT_LLM_FILE}"
else
  OUT_LLM_FILE=""
fi

# Обновляем курсор
psql -X -qAt -P pager=off -d "${DB}" -v ON_ERROR_STOP=1 -c \
  "UPDATE rpt.report_state SET last_msg_id=${MAX_MSG_ID}, updated_at=now()
   WHERE peer_type='${PEER_TYPE}' AND peer_id=${PEER_ID};" >/dev/null

cd "${REPO_DIR}"
git pull --rebase origin "${BRANCH}" >/dev/null 2>&1 || true

git add "${RAW_FILE}" >/dev/null 2>&1 || true

if [[ -n "${OUT_LLM_FILE}" ]]; then
  git add "${OUT_LLM_FILE}" >/dev/null 2>&1 || true
fi

if git diff --cached --quiet; then
  echo "NOOP: nothing to commit"
  exit 0
fi

git commit -m "poll: add increment digest ${TS}" >/dev/null
git push origin "${BRANCH}" >/dev/null

OUT_FILE="${OUT_LLM_FILE:-${RAW_FILE}}"
tg_notify
echo "OK: committed and pushed ${OUT_FILE}"
