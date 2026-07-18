#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
WORKSPACE_ROOT="$(cd -- "$ROOT/../.." && pwd)"

export CODEX_CALLER_CWD="${CODEX_CALLER_CWD:-${OLDPWD:-$PWD}}"
export PYTHONPATH="$WORKSPACE_ROOT:${PYTHONPATH:-}"

exec python3 "$ROOT/kaggle_research_compute.py" "$@"
