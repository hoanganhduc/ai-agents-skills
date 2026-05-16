#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd -P)"
exec python3 "$SCRIPT_DIR/research_digest.py" "$@"
