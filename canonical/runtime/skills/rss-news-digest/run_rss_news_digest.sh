#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd -P)"
exec python3 "$SCRIPT_DIR/rss_news_digest.py" "$@"
