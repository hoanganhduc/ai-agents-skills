#!/usr/bin/env bash
# Wrapper that sets PYTHONPATH and runs cal.py
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.codex/runtime/workspace}"

export PYTHONPATH="$SKILL_DIR:${PYTHONPATH:-}"
export OPENCLAW_WORKSPACE="$WORKSPACE"
export OPENCLAW_SECRETS_FILE="${OPENCLAW_SECRETS_FILE:-$WORKSPACE/.secrets.json}"

exec python3 "$SKILL_DIR/cal.py" "$@"
