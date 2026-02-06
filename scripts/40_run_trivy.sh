#!/usr/bin/env bash
set -euo pipefail
mkdir -p reports
rm -f reports/trivy_fs.txt reports/trivy_fs.json

[ -f Dockerfile ] || { echo "[trivy] Dockerfile не найден — пропускаю."; exit 0; }
command -v trivy >/dev/null 2>&1 || { echo "[trivy] не установлен — пропускаю."; exit 0; }

echo "[trivy] START: $(date)"
trivy fs --scanners vuln,config --format json --output reports/trivy_fs.json . | tee reports/trivy_fs.txt || true
echo "[trivy] SUMMARY: $(jq '.Results|map(.Vulnerabilities // [])|flatten|length' reports/trivy_fs.json 2>/dev/null || echo 0)"
