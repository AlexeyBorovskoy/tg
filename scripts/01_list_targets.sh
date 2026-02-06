#!/usr/bin/env bash
set -euo pipefail
mkdir -p reports
: > reports/target_files.txt

CODE_DIRS=("$@")
if [ "${#CODE_DIRS[@]}" -eq 0 ]; then
  for d in sp_pasp sp_mt src app tests; do [ -d "$d" ] && CODE_DIRS+=("$d"); done
  [ "${#CODE_DIRS[@]}" -eq 0 ] && CODE_DIRS+=(".")
fi

for d in "${CODE_DIRS[@]}"; do
  [ -d "$d" ] || continue
  find "$d" -type d \( -name .git -o -name venv -o -name __pycache__ -o -name node_modules -o -name build -o -name dist \) -prune -false -o \
    -type f \( -name "*.py" -o -name "*.cpp" -o -name "*.h" \) \
    ! -name "*.conf" ! -name ".env" ! -name "*.sh" ! -name "*.json" \
    >> reports/target_files.txt
done

sort -u -o reports/target_files.txt reports/target_files.txt

TOTAL=$(wc -l < reports/target_files.txt 2>/dev/null | tr -d ' \n' || echo 0)
PY=$(grep -E '\.py$' reports/target_files.txt 2>/dev/null | wc -l | tr -d ' \n' || echo 0)
CPP=$(grep -E '\.(cpp|h)$' reports/target_files.txt 2>/dev/null | wc -l | tr -d ' \n' || echo 0)

echo "Targets: TOTAL=$TOTAL | PY=$PY | CPP/H=$CPP"
echo "== FIRST 20 =="
head -n 20 reports/target_files.txt || true
