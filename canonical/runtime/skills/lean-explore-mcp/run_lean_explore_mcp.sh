#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
SCRIPT="$SCRIPT_DIR/lean_explore_mcp.py"

if [[ ! -f "$SCRIPT" ]]; then
  printf 'runtime helper not found: %s\n' "$SCRIPT" >&2
  exit 127
fi

if [[ -n "${AAS_RUNTIME_PYTHON:-}" ]]; then
  exec "$AAS_RUNTIME_PYTHON" "$SCRIPT" "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SCRIPT" "$@"
fi
if command -v python >/dev/null 2>&1; then
  exec python "$SCRIPT" "$@"
fi

printf 'error: no usable Python runtime found. Set AAS_RUNTIME_PYTHON or install Python 3.\n' >&2
exit 127
