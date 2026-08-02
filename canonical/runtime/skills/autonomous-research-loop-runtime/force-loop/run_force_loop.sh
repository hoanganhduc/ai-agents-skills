#!/usr/bin/env bash
# Thin POSIX wrapper → force_loop_cli.py
set -euo pipefail
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${AAS_RUNTIME_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
  else
    echo "force-loop requires python3 or python on PATH" >&2
    exit 127
  fi
fi
exec "$PYTHON" "$SCRIPT_DIR/force_loop_cli.py" "$@"
