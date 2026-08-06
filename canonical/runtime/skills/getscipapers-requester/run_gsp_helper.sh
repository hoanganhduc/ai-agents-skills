#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
WORKSPACE="${AAS_RUNTIME_WORKSPACE:-${OPENCLAW_WORKSPACE:-$(cd "$SCRIPT_DIR/../.." && pwd -P)}}"
export OPENCLAW_WORKSPACE="${OPENCLAW_WORKSPACE:-$WORKSPACE}"
export GETSCIPAPERS_SKILL_CONFIG="${GETSCIPAPERS_SKILL_CONFIG:-$WORKSPACE/data/research/getscipapers_bot/state/config.json}"
if [ -z "${GETSCIPAPERS_BIN:-}" ]; then
  gsp_venv_bin="${GETSCIPAPERS_VENV:-${HOME:-}/.getscipapers_venv}/bin/getscipapers"
  if [ -x "$gsp_venv_bin" ]; then
    export GETSCIPAPERS_BIN="$gsp_venv_bin"
  fi
fi
exec python3 "$SCRIPT_DIR/gsp_openclaw_helper.py" "$@"
