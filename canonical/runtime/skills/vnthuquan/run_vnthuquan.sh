#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
RUNTIME_ROOT="${AAS_RUNTIME_ROOT:-$(cd "$SCRIPT_DIR/../../.." && pwd -P)}"
RUNTIME_WORKSPACE="${AAS_RUNTIME_WORKSPACE:-$RUNTIME_ROOT/workspace}"

export PYTHONUTF8="${PYTHONUTF8:-1}"
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"
export VNTHUQUAN_TARGET="${VNTHUQUAN_TARGET:-posix-codex}"
export VNTHUQUAN_ASSISTANT_HOME="${VNTHUQUAN_ASSISTANT_HOME:-$RUNTIME_ROOT}"
export VNTHUQUAN_CALIBRE_RUNNER="${VNTHUQUAN_CALIBRE_RUNNER:-$RUNTIME_ROOT/run_skill.sh}"
export VNTHUQUAN_CALIBRE_SCRIPT="${VNTHUQUAN_CALIBRE_SCRIPT:-skills/calibre/run_cal.sh}"
export VNTHUQUAN_CALIBRE_CACHE_PATH="${VNTHUQUAN_CALIBRE_CACHE_PATH:-$RUNTIME_WORKSPACE/data/calibre/cache/library.json}"

exec python3 "$SCRIPT_DIR/vnthuquan_wrapper.py" "$@"
