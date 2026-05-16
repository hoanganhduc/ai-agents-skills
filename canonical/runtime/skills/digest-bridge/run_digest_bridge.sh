#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
WORKSPACE="${AAS_RUNTIME_WORKSPACE:-${OPENCLAW_WORKSPACE:-$(cd "$SCRIPT_DIR/../.." && pwd -P)}}"
export PYTHONPATH="$WORKSPACE/.local:${PYTHONPATH:-}"

exec python3 "$SCRIPT_DIR/digest_bridge.py" "$@"
