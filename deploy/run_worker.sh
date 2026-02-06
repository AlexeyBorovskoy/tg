#!/usr/bin/env bash
# Запуск TG Digest Worker (опрос каждый час)
set -e
cd /home/ripas/tg_digest_deploy
export PYTHONPATH=/home/ripas/tg_digest_deploy
exec /home/ripas/tg_digest_deploy/.venv/bin/python scripts/digest_worker.py --interval 60
