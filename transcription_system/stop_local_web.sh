#!/usr/bin/env bash
set -euo pipefail

PID_FILE="/tmp/transcription_system_8081.pid"

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE" || true)"
  if [[ -n "${pid}" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi

pkill -f "uvicorn app.main:app --host 0.0.0.0 --port 8081" 2>/dev/null || true

echo "OK: stopped"
