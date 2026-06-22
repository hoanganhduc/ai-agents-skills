#!/usr/bin/env bash
# Wrapper that sets PYTHONPATH and runs cal.py
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_WORKSPACE="$(cd -- "$SKILL_DIR/../.." && pwd -P)"
WORKSPACE="${AAS_RUNTIME_WORKSPACE:-${OPENCLAW_WORKSPACE:-$DEFAULT_WORKSPACE}}"

export PYTHONPATH="$SKILL_DIR:${PYTHONPATH:-}"
export OPENCLAW_WORKSPACE="$WORKSPACE"
export OPENCLAW_SECRETS_FILE="${OPENCLAW_SECRETS_FILE:-$WORKSPACE/.secrets.json}"

exec python3 "$SKILL_DIR/cal.py" "$@"
