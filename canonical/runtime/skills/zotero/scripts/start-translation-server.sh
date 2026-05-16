#!/usr/bin/env bash
set -euo pipefail

# Legacy/optional path for this Windows Codex setup.
# The primary working generic-URL translation route now uses
# scripts/wsl_url_translate.sh and a WSL-local source checkout at
# ~/zotero-translation-server instead of relying on Windows->WSL localhost.

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"
docker compose up -d
echo "Translation Server starting on http://localhost:1969"
echo "Waiting for server to be ready..."
for i in $(seq 1 30); do
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:1969/ 2>/dev/null | grep -q "200\|404"; then
        echo "Translation Server is ready."
        exit 0
    fi
    sleep 2
done
echo "ERROR: Translation Server did not become ready in 60s"
exit 1
