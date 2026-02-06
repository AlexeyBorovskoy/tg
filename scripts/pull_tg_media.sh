#!/usr/bin/env bash
set -euo pipefail

cd "${REPO_DIR:-$HOME/analysis-methodology}"

# venv for media
. .venv_media/bin/activate

# REQUIRED ENV:
# TG_API_ID, TG_API_HASH, TG_SESSION, TG_PEER
# MSG_ID_FROM, MSG_ID_TO
#
# OPTIONAL:
# PEER_TYPE, PEER_ID, MEDIA_ROOT, REPO_DIR, DRY_RUN=1

: "${TG_API_ID:?TG_API_ID required}"
: "${TG_API_HASH:?TG_API_HASH required}"
: "${TG_SESSION:?TG_SESSION required}"
: "${TG_PEER:?TG_PEER required}"
: "${MSG_ID_FROM:?MSG_ID_FROM required}"
: "${MSG_ID_TO:?MSG_ID_TO required}"

export REPO_DIR="${REPO_DIR:-$HOME/analysis-methodology}"
export MEDIA_ROOT="${MEDIA_ROOT:-$REPO_DIR/docs/media}"
mkdir -p "$MEDIA_ROOT" "$REPO_DIR/docs/media/_tmp"

python3 scripts/tg_media_pull.py
