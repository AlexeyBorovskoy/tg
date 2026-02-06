#!/usr/bin/env bash
set -euo pipefail
rm -rf reports/* REPORT.md REPORT.docx full.txt full_*.zip
mkdir -p reports

CODE_DIRS=("$@")

bash scripts/00_bootstrap.sh
bash scripts/01_list_targets.sh "${CODE_DIRS[@]:-}"

bash scripts/10_run_bandit.sh
bash scripts/11_run_ruff.sh
bash scripts/12_run_pip_audit.sh
bash scripts/20_run_cppcheck.sh
bash scripts/21_run_clang_tidy.sh
bash scripts/30_run_gitleaks.sh || true
bash scripts/40_run_trivy.sh   || true

bash scripts/90_build_report.sh
