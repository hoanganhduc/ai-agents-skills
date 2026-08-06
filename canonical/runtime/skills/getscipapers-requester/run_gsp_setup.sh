#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
WORKSPACE="${AAS_RUNTIME_WORKSPACE:-${OPENCLAW_WORKSPACE:-$(cd "$SCRIPT_DIR/../.." && pwd -P)}}"
export OPENCLAW_WORKSPACE="${OPENCLAW_WORKSPACE:-$WORKSPACE}"
exec python3 "$SCRIPT_DIR/run_gsp_setup.py" "$@"
