#!/usr/bin/env bash
set -euo pipefail
mkdir -p reports
rm -f REPORT.md REPORT.docx full.txt full_*.zip

{
  echo "=== SUMMARY ==="
  [ -f reports/bandit.json ]    && echo "Bandit: $(jq '.results|length' reports/bandit.json)"      || echo "Bandit: 0"
  [ -f reports/ruff.json ]      && echo "Ruff: $(jq 'length' reports/ruff.json)"                    || echo "Ruff: 0"
  [ -f reports/pip_audit.json ] && echo "pip-audit: $(jq 'length' reports/pip_audit.json)"          || echo "pip-audit: 0"
  [ -f reports/cppcheck.txt ]   && echo "cppcheck: $(grep -c '^[^:]\+:[0-9]\+:' reports/cppcheck.txt || echo 0)" || echo "cppcheck: 0"
  [ -f reports/clang-tidy.txt ] && echo "clang-tidy: $(grep -c 'warning:' reports/clang-tidy.txt || echo 0)"     || echo "clang-tidy: 0"
  [ -f reports/gitleaks.sarif ] && echo "gitleaks: $(jq '.runs[0].results|length' reports/gitleaks.sarif)"       || echo "gitleaks: 0"
  [ -f reports/trivy_fs.json ]  && echo "Trivy (fs): $(jq '.Results|map(.Vulnerabilities // [])|flatten|length' reports/trivy_fs.json)" || echo "Trivy (fs): 0"
} | tee reports/summary.txt

FILES_TOTAL=$(wc -l < reports/target_files.txt 2>/dev/null | tr -d ' \n' || echo 0)
PY_COUNT=$(grep -E '\.py$' reports/target_files.txt 2>/dev/null | wc -l | tr -d ' \n' || echo 0)
CPP_COUNT=$(grep -E '\.(cpp|h)$' reports/target_files.txt 2>/dev/null | wc -l | tr -d ' \n' || echo 0)

BANDIT_COUNT=$(jq '.results|length' reports/bandit.json 2>/dev/null || echo 0)
RUFF_COUNT=$(jq 'length' reports/ruff.json 2>/dev/null || echo 0)
PIP_AUDIT_COUNT=$(jq 'length' reports/pip_audit.json 2>/dev/null || echo 0)
CPPCHECK_COUNT=$(grep -c '^[^:]\+:[0-9]\+:' reports/cppcheck.txt 2>/dev/null || echo 0)
CLANGTIDY_COUNT=$(grep -c 'warning:' reports/clang-tidy.txt 2>/dev/null || echo 0)
GITLEAKS_COUNT=$(jq '.runs[0].results|length' reports/gitleaks.sarif 2>/dev/null || echo 0)
TRIVY_COUNT=$(jq '.Results|map(.Vulnerabilities // [])|flatten|length' reports/trivy_fs.json 2>/dev/null || echo 0)

DATE_RANGE="$(date '+%Y-%m-%d %H:%M')"

cat > REPORT.md <<EOF
# Протокол контроля отсутствия уязвимостей

**Объект**: $(basename "$(pwd)")  
**Дата/время**: ${DATE_RANGE}  
**Среда**: см. reports/system_info.txt

## 1. Область и правила проверки
Включаем: *.py, *.cpp, *.h  
Исключаем: *.conf, .env, *.sh, *.json; каталоги: venv/, .git/, node_modules/, build/, dist/  
Целевых файлов: ${FILES_TOTAL} (Python: ${PY_COUNT}, C/C++: ${CPP_COUNT})

## 2. Методика и средства
Bandit (без venv), Ruff (--fix), pip-audit, cppcheck/clang-tidy, gitleaks, trivy.

## 3. Результаты (кол-во находок)
- Bandit: ${BANDIT_COUNT}
- Ruff: ${RUFF_COUNT}
- pip-audit: ${PIP_AUDIT_COUNT}
- cppcheck: ${CPPCHECK_COUNT}
- clang-tidy: ${CLANGTIDY_COUNT}
- gitleaks: ${GITLEAKS_COUNT}
- Trivy (fs): ${TRIVY_COUNT}

Подробности — в каталоге reports/.

## 4. Рекомендации
1) Bandit: убрать строковый SQL (B608), bind 0.0.0.0 → env/loopback, /tmp → tempfile/platformdirs.  
2) Ruff: автофиксы до нуля, вручную F821/F811.  
3) pip-audit: обновить уязвимые зависимости.  
4) Повторный прогон и актуализация протокола.
EOF

if [ -f templates/reference.docx ]; then
  pandoc REPORT.md --reference-doc=templates/reference.docx -o REPORT.docx || true
else
  pandoc REPORT.md -o REPORT.docx || true
fi

{
  cat reports/summary.txt
  echo
  echo "=== REPORT.md (inline) ==="
  cat REPORT.md
} > full.txt

TS=$(date +%Y%m%d_%H%M%S)
ZIP="full_${TS}.zip"
zip -r "$ZIP" REPORT.md REPORT.docx full.txt reports >/dev/null 2>&1 || true
echo "Готово: REPORT.md / REPORT.docx / $ZIP"
