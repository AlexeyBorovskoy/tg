#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   echo '["строка 1","строка 2"]' | scripts/llm_gigachat_chat.sh
# Input: JSON array of strings (user messages) from stdin.
# Output: assistant text to stdout (raw text).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env.gigachat"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: env file not found: $ENV_FILE" >&2
  exit 2
fi

source "$ENV_FILE"

: "${GIGACHAT_AUTH_BASIC:?missing}"
: "${GIGACHAT_SCOPE:?missing}"
: "${GIGACHAT_MODEL:?missing}"
: "${GIGACHAT_CACERT:?missing}"
: "${GIGACHAT_TOKEN_JSON:?missing}"

OAUTH_URL="https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL="https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

mkdir -p "$(dirname "$GIGACHAT_TOKEN_JSON")"

need_token() {
  [[ ! -s "$GIGACHAT_TOKEN_JSON" ]] && return 1
  python3 - "$GIGACHAT_TOKEN_JSON" <<'PY' >/dev/null 2>&1 || return 1
import json,sys,time
p=sys.argv[1]
d=json.load(open(p,'r',encoding='utf-8'))
tok=d.get('access_token')
exp=d.get('expires_at')
if not tok or not exp:
    raise SystemExit(1)
now=int(time.time())
if exp > 10_000_000_000:
    exp//=1000
if exp - now < 60:
    raise SystemExit(1)
PY
}

fetch_token() {
  local rquid
  rquid="$(cat /proc/sys/kernel/random/uuid)"
  curl -sS --cacert "$GIGACHAT_CACERT" \
    -o "$GIGACHAT_TOKEN_JSON" \
    -X POST "$OAUTH_URL" \
    -H "Authorization: Basic $GIGACHAT_AUTH_BASIC" \
    -H "RqUID: $rquid" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data "scope=$GIGACHAT_SCOPE" >/dev/null
}

get_access_token() {
  python3 - "$GIGACHAT_TOKEN_JSON" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],'r',encoding='utf-8'))
print(d["access_token"])
PY
}

USER_JSON="$(cat)"

REQ_JSON="$(python3 - "$USER_JSON" "$GIGACHAT_MODEL" <<'PY'
import json,sys
user_json=sys.argv[1]
model=sys.argv[2]
arr=json.loads(user_json)
msgs=[{"role":"system","content":"Ты технический ассистент. Отвечай кратко, строго по делу."}]
for s in arr:
    msgs.append({"role":"user","content":str(s)})
req={"model":model,"temperature":0,"max_tokens":800,"messages":msgs}
print(json.dumps(req,ensure_ascii=False))
PY
)"

if ! need_token; then
  fetch_token
fi

TOKEN="$(get_access_token)"

RESP="$(curl -sS --cacert "$GIGACHAT_CACERT" \
  -X POST "$CHAT_URL" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary "$REQ_JSON")"

python3 - "$RESP" <<'PY'
import json,sys
d=json.loads(sys.argv[1])
print(d["choices"][0]["message"]["content"])
PY

