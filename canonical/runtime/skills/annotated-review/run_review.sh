#!/bin/bash
WS="${OPENCLAW_WORKSPACE:-$HOME/.codex/runtime/workspace}"
export PYTHONPATH="$WS:$PYTHONPATH"
exec python3 "$(dirname "$0")/review.py" "$@"
