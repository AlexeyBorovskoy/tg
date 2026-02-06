#!/usr/bin/env bash
set -euo pipefail
mkdir -p reports
rm -f reports/gitleaks.txt reports/gitleaks.sarif
command -v gitleaks >/dev/null 2>&1 || { echo "[gitleaks] не установлен — пропускаю."; exit 0; }

SCOPE_DIR="reports/_gitleaks_scope"
rm -rf "$SCOPE_DIR"
mkdir -p "$SCOPE_DIR"

while IFS= read -r f; do
  [ -f "$f" ] || continue
  dest="$SCOPE_DIR/$f"
  mkdir -p "$(dirname "$dest")"
  cp -f "$f" "$dest"
done < reports/target_files.txt

# используем локальную копию только целевых файлов
echo "[gitleaks] START: $(date)"
gitleaks detect --no-git --source "$SCOPE_DIR" \
  -f sarif -r reports/gitleaks.sarif | tee reports/gitleaks.txt || true

echo "[gitleaks] SUMMARY: $(jq '.runs[0].results|length' reports/gitleaks.sarif 2>/dev/null || echo 0)"
