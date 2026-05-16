#!/usr/bin/env bash
set -euo pipefail

# Send a file to a Telegram chat via Bot API.
# Usage: send_telegram.sh <chat_id> <file_path> [caption]

CHAT_ID="${1:?Usage: send_telegram.sh <chat_id> <file_path> [caption]}"
FILE_PATH="${2:?Usage: send_telegram.sh <chat_id> <file_path> [caption]}"
CAPTION="${3:-}"

# Read bot token from secrets
SECRETS_FILE="${OPENCLAW_SECRETS_FILE:-/workspace/.secrets.json}"
if [[ ! -f "$SECRETS_FILE" ]]; then
  echo '{"status":"error","message":"Secrets file not found: '"$SECRETS_FILE"'"}'
  exit 1
fi

BOT_TOKEN=$(python3 -c "import json; print(json.load(open('$SECRETS_FILE'))['TELEGRAM_BOT_TOKEN'])" 2>/dev/null)
if [[ -z "$BOT_TOKEN" ]]; then
  echo '{"status":"error","message":"TELEGRAM_BOT_TOKEN not found in secrets"}'
  exit 1
fi

if [[ ! -f "$FILE_PATH" ]]; then
  echo '{"status":"error","message":"File not found: '"$FILE_PATH"'"}'
  exit 1
fi

FILE_SIZE=$(stat -c %s "$FILE_PATH" 2>/dev/null || stat -f %z "$FILE_PATH" 2>/dev/null || echo 0)
MAX_SIZE=52428800  # 50MB Telegram limit

if [[ "$FILE_SIZE" -gt "$MAX_SIZE" ]]; then
  echo '{"status":"error","message":"File too large for Telegram ('"$FILE_SIZE"' bytes, max 50MB)","file_path":"'"$FILE_PATH"'","file_size":'"$FILE_SIZE"'}'
  exit 1
fi

# Send via Telegram Bot API
API_URL="https://api.telegram.org/bot${BOT_TOKEN}/sendDocument"

if [[ -n "$CAPTION" ]]; then
  RESPONSE=$(curl -s -X POST "$API_URL" \
    -F "chat_id=$CHAT_ID" \
    -F "document=@$FILE_PATH" \
    -F "caption=$CAPTION" \
    --max-time 120)
else
  RESPONSE=$(curl -s -X POST "$API_URL" \
    -F "chat_id=$CHAT_ID" \
    -F "document=@$FILE_PATH" \
    --max-time 120)
fi

# Parse response
OK=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok', False))" 2>/dev/null || echo "False")

if [[ "$OK" == "True" ]]; then
  echo '{"status":"ok","message":"File sent to Telegram","chat_id":"'"$CHAT_ID"'","file":"'"$(basename "$FILE_PATH")"'","size":'"$FILE_SIZE"'}'
else
  echo '{"status":"error","message":"Telegram API error","response":'"$RESPONSE"'}'
  exit 1
fi
