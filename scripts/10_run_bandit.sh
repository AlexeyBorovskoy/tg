#!/usr/bin/env bash
set -euo pipefail
mkdir -p reports
rm -f reports/bandit.txt reports/bandit.json

PY_LIST="reports/py_targets.txt"
grep -E '\.py$' reports/target_files.txt | grep -vE '(^|/)(venv|__pycache__)(/|$)' > "$PY_LIST" || true
if [ ! -s "$PY_LIST" ]; then
  echo "[Bandit] *.py не найдено — пропускаю."
  exit 0
fi

if [ -x ./venv/bin/bandit ]; then BANDIT=./venv/bin/bandit
elif command -v bandit >/dev/null 2>&1; then BANDIT=$(command -v bandit)
else echo "[Bandit] не установлен"; exit 0; fi

mapfile -t PY_FILES < "$PY_LIST"

echo "[Bandit] START: $(date)"
"$BANDIT" -ll -f txt  -o reports/bandit.txt  "${PY_FILES[@]}" || true
"$BANDIT" -q  -f json -o reports/bandit.json "${PY_FILES[@]" } || true

echo "[Bandit] SUMMARY: $(jq '.results|length' reports/bandit.json 2>/dev/null || echo 0)"
