#!/usr/bin/env bash
set -euo pipefail
mkdir -p reports
rm -f reports/clang-tidy.txt

if ! grep -E '\.cpp$' reports/target_files.txt >/dev/null 2>&1; then
  echo "[clang-tidy] Нет *.cpp — пропускаю."
  exit 0
fi
command -v clang-tidy >/dev/null 2>&1 || { echo "[clang-tidy] не установлен"; exit 0; }

: > reports/clang-tidy.txt
while read -r src; do
  echo "[clang-tidy] checking $src"
  clang-tidy "$src" --quiet -- -std=c++17 2>> reports/clang-tidy.txt || true
done < <(grep -E '\.cpp$' reports/target_files.txt)
echo "[clang-tidy] SUMMARY: $(grep -c 'warning:' reports/clang-tidy.txt || echo 0)"
