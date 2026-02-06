#!/usr/bin/env bash
set -euo pipefail
mkdir -p reports
LOG=reports/bootstrap.log
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

echo "== [00] Bootstrap started: $(date)"

sudo apt-get update || true
sudo apt-get install -y python3-venv python3-pip jq zip curl wget gnupg lsb-release ca-certificates mousepad git || true
sudo apt-get install -y cppcheck clang-tidy || true

# gitleaks
if ! command -v gitleaks >/dev/null 2>&1; then
  if command -v snap >/dev/null 2>&1; then sudo snap install gitleaks || true; fi
  if ! command -v gitleaks >/dev/null 2>&1; then
    tmp=$(mktemp -d)
    cd "$tmp"
    curl -sSL https://raw.githubusercontent.com/gitleaks/gitleaks/master/install.sh | sudo bash -s -- -b /usr/local/bin || true
    cd - >/dev/null
  fi
fi

# trivy
if ! command -v trivy >/dev/null 2>&1; then
  sudo apt-get install -y trivy || true
fi

python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install bandit ruff pip-audit

{ uname -a; lsb_release -a 2>/dev/null || true; } > reports/system_info.txt
./venv/bin/pip freeze > reports/pip_freeze.txt

echo "== [00] Bootstrap done: $(date)"
