#!/usr/bin/env bash
set -euo pipefail
mkdir -p reports
rm -f reports/cppcheck.txt

if ! grep -E '\.(cpp|h)$' reports/target_files.txt >/dev/null 2>&1; then
  echo "[cppcheck] Нет *.cpp/*.h — пропускаю."
  exit 0
fi
command -v cppcheck >/dev/null 2>&1 || { echo "[cppcheck] не установлен"; exit 0; }

echo "[cppcheck] START: $(date)"
cppcheck --enable=all --inconclusive --std=c++17 --quiet \
  --suppress=missingIncludeSystem \
  --template='{file}:{line}:{severity}:{message} [{id}]' \
  --error-exitcode=0 \
  $(grep -E '\.(cpp|h)$' reports/target_files.txt) 2> >(tee reports/cppcheck.txt >&2) || true

echo "[cppcheck] SUMMARY: $(grep -c '^[^:]\+:[0-9]\+:' reports/cppcheck.txt || echo 0)"
