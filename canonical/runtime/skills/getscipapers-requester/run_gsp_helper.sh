#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
WORKSPACE="${AAS_RUNTIME_WORKSPACE:-${OPENCLAW_WORKSPACE:-$(cd "$SCRIPT_DIR/../.." && pwd -P)}}"
export OPENCLAW_WORKSPACE="${OPENCLAW_WORKSPACE:-$WORKSPACE}"
export GETSCIPAPERS_SKILL_CONFIG="${GETSCIPAPERS_SKILL_CONFIG:-$WORKSPACE/data/research/getscipapers_bot/state/config.json}"
exec python3 "$SCRIPT_DIR/gsp_openclaw_helper.py" "$@"
