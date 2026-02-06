#!/usr/bin/env bash
set -euo pipefail
mkdir -p reports
rm -f reports/ruff.txt reports/ruff.json

PY_LIST="reports/py_targets.txt"
if [ ! -s "$PY_LIST" ]; then
  grep -E '\.py$' reports/target_files.txt | grep -vE '(^|/)(venv|__pycache__)(/|$)' > "$PY_LIST" || true
fi
if [ ! -s "$PY_LIST" ]; then
  echo "[Ruff] *.py не найдено — пропускаю."
  exit 0
fi

if [ -x ./venv/bin/ruff ]; then RUFF=./venv/bin/ruff
elif command -v ruff >/dev/null 2>&1; then RUFF=$(command -v ruff)
else echo "[Ruff] не установлен"; exit 0; fi

mapfile -t PY_FILES < "$PY_LIST"

echo "[Ruff] START: $(date)"
"$RUFF" check --fix "${PY_FILES[@]}" | tee reports/ruff.txt || true
"$RUFF" check --output-format=json "${PY_FILES[@]}" > reports/ruff.json || true
echo "[Ruff] SUMMARY: $(grep -E '^\s*[A-Z]\d{3}' -c reports/ruff.txt || echo 0)"
