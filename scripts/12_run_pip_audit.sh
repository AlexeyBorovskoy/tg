#!/usr/bin/env bash
set -euo pipefail
mkdir -p reports
rm -f reports/pip_audit.txt reports/pip_audit.json

PIPAUDIT=./venv/bin/pip-audit; [ -x "$PIPAUDIT" ] || PIPAUDIT=$(command -v pip-audit || echo "")
[ -n "$PIPAUDIT" ] || { echo "[pip-audit] не установлен"; exit 0; }

REQ=""
if [ -f requirements.txt ]; then
  REQ="requirements.txt"
elif ls **/requirements*.txt >/dev/null 2>&1; then
  REQ="$(ls **/requirements*.txt | head -n1)"
fi
[ -z "$REQ" ] && { echo "[pip-audit] Нет requirements*.txt — пропускаю."; exit 0; }

echo "[pip-audit] START: $(date)"
"$PIPAUDIT" -r "$REQ" | tee reports/pip_audit.txt || true
"$PIPAUDIT" -r "$REQ" -f json -o reports/pip_audit.json || true
echo "[pip-audit] SUMMARY: $(jq 'length' reports/pip_audit.json 2>/dev/null || echo 0)"
