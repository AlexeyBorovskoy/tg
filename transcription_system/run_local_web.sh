#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="/tmp/transcription_system_8081.pid"
LOG_FILE="/tmp/transcription_system_8081.log"

cd "$ROOT_DIR"

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ -n "${old_pid}" ]] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" 2>/dev/null || true
    sleep 1
  fi
fi

pkill -f "uvicorn app.main:app --host 0.0.0.0 --port 8081" 2>/dev/null || true

nohup "$ROOT_DIR/.venv/bin/python" -m uvicorn app.main:app --host 0.0.0.0 --port 8081 >"$LOG_FILE" 2>&1 </dev/null &
echo $! > "$PID_FILE"
sleep 1

new_pid="$(cat "$PID_FILE")"
if kill -0 "$new_pid" 2>/dev/null; then
  echo "OK: started pid=$new_pid"
  echo "URL: http://127.0.0.1:8081/login"
  echo "URL: http://192.168.238.128:8081/login"
else
  echo "ERROR: start failed"
  tail -n 50 "$LOG_FILE" || true
  exit 1
fi
