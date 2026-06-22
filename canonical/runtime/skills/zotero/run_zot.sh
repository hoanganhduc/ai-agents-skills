#!/bin/bash
# Run zot.py
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEFAULT_WORKSPACE="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
WS="${AAS_RUNTIME_WORKSPACE:-${OPENCLAW_WORKSPACE:-$DEFAULT_WORKSPACE}}"
export PYTHONPATH="$WS:${PYTHONPATH:-}"
exec python3 "$SCRIPT_DIR/zot.py" "$@"
