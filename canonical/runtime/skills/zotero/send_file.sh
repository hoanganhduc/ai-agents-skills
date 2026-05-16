#!/usr/bin/env bash
set -euo pipefail

# Unified file sender for Telegram, WhatsApp, Google Chat, and Zulip.
# Usage: send_file.sh <channel> <target> <file_path> [caption]
#
# Telegram: sends directly via Bot API (fast).
# WhatsApp/Google Chat: writes a job to the send queue, waits for host worker.
# Zulip: uploads via /api/v1/user_uploads then sends message with attachment link.
#   Target format (Zulip):
#     bare-name               → look up correct capitalization in "Research" stream
#     stream:topic           → stream + topic (topic may contain colons)
#     user:123               → private message to user ID
#
#   The bare-name lookup normalizes to URL slug before comparing topics,
#   so both "$k$-Path Vertex Cover..." and "$k$-path-vertex-cover..." resolve correctly.

CHANNEL="${1:?Usage: send_file.sh <channel> <target> <file_path> [caption]}"
TARGET="${2:?Usage: send_file.sh <channel> <target> <file_path> [caption]}"
FILE_PATH="${3:?Usage: send_file.sh <channel> <target> <file_path> [caption]}"
CAPTION="${4:-}"

WS="${OPENCLAW_WORKSPACE:-/workspace}"
SECRETS_FILE="${OPENCLAW_SECRETS_FILE:-$WS/.secrets.json}"
QUEUE_DIR="$WS/data/send-queue"

if [[ ! -f "$FILE_PATH" ]]; then
  echo '{"status":"error","message":"File not found: '"$FILE_PATH"'"}'
  exit 1
fi

FILE_SIZE=$(stat -c %s "$FILE_PATH" 2>/dev/null || stat -f %z "$FILE_PATH" 2>/dev/null || echo 0)

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
if [[ "$CHANNEL" == "telegram" ]]; then
  MAX_SIZE=52428800
  if [[ "$FILE_SIZE" -gt "$MAX_SIZE" ]]; then
    echo '{"status":"error","message":"File too large for Telegram"}'
    exit 1
  fi
  BOT_TOKEN=$(python3 -c "import json; print(json.load(open('$SECRETS_FILE'))['TELEGRAM_BOT_TOKEN'])" 2>/dev/null)
  if [[ -z "$BOT_TOKEN" ]]; then
    echo '{"status":"error","message":"TELEGRAM_BOT_TOKEN not found"}'
    exit 1
  fi
  API_URL="https://api.telegram.org/bot${BOT_TOKEN}/sendDocument"
  if [[ -n "$CAPTION" ]]; then
    RESPONSE=$(curl -s -X POST "$API_URL" \
      -F "chat_id=$TARGET" -F "document=@$FILE_PATH" -F "caption=$CAPTION" --max-time 120)
  else
    RESPONSE=$(curl -s -X POST "$API_URL" \
      -F "chat_id=$TARGET" -F "document=@$FILE_PATH" --max-time 120)
  fi
  OK=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',False))" 2>/dev/null || echo "False")
  if [[ "$OK" == "True" ]]; then
    echo "{\"status\":\"ok\",\"channel\":\"telegram\",\"file\":\"$(basename "$FILE_PATH")\",\"size\":$FILE_SIZE}"
  else
    echo "{\"status\":\"error\",\"channel\":\"telegram\",\"response\":$RESPONSE}"
    exit 1
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# Zulip
# ---------------------------------------------------------------------------
if [[ "$CHANNEL" == "zulip" ]]; then

  ZULIP_ORG_URL=$(python3 -c "import json; print(json.load(open('$SECRETS_FILE'))['ZULIP_ORG_URL'])" 2>/dev/null)
  ZULIP_EMAIL=$(python3 -c "import json; print(json.load(open('$SECRETS_FILE'))['ZULIP_EMAIL'])" 2>/dev/null)
  ZULIP_API_KEY=$(python3 -c "import json; print(json.load(open('$SECRETS_FILE'))['ZULIP_API_KEY'])" 2>/dev/null)

  if [[ -z "$ZULIP_ORG_URL" || -z "$ZULIP_EMAIL" || -z "$ZULIP_API_KEY" ]]; then
    echo '{"status":"error","message":"Zulip credentials missing"}'
    exit 1
  fi
  ZULIP_ORG_URL="${ZULIP_ORG_URL%/}"
  AUTH="${ZULIP_EMAIL}:${ZULIP_API_KEY}"

  # --- Step 1: upload file ---
  UPLOAD_RESP=$(curl -s -X POST \
    "${ZULIP_ORG_URL}/api/v1/user_uploads" \
    -u "$AUTH" -F "file=@${FILE_PATH}" --max-time 120)
  URI=$(echo "$UPLOAD_RESP" | python3 -c \
    "import sys,json; print(json.load(sys.stdin).get('uri',''))" 2>/dev/null || echo "")
  if [[ -z "$URI" ]]; then
    echo "{\"status\":\"error\",\"channel\":\"zulip\",\"step\":\"upload\",\"response\":$UPLOAD_RESP}"
    exit 1
  fi

  # --- Build content ---
  BASENAME=$(basename "$FILE_PATH")
  FILE_LINK="[${BASENAME}](${URI})"
  if [[ -n "$CAPTION" ]]; then
    CONTENT="${CAPTION}"$'\n\n'"${FILE_LINK}"
  else
    CONTENT="${FILE_LINK}"
  fi

  # --- Parse target and resolve topic capitalization via Python ---
  # (Python avoids shell $ expansion on special topic chars)
  TARGET_JSON=$(python3 - "$TARGET" "$CONTENT" "$ZULIP_ORG_URL" "$AUTH" << 'PYEOF'
import sys, json, subprocess, re

target   = sys.argv[1]
content  = sys.argv[2]
org_url  = sys.argv[3]
auth     = sys.argv[4]

def curl_get(url, params):
    r = subprocess.run(
        ['curl', '-s', '-G', url, '-u', auth] + params,
        capture_output=True, text=True)
    return json.loads(r.stdout)

def slug(t):
    """Normalize topic to URL slug for comparison: keeps $...$ math as-is,
    lowercases the rest, removes punctuation, collapses spaces to hyphens."""
    def keep_math(m): return m.group(0)
    s = re.sub(r'\$[^$]*\$', keep_math, t.lower())
    s = re.sub(r'[^\$a-z0-9 _-]', '', s)
    s = re.sub(r'[\s_]+', '-', s).strip('-')
    return s

def resolve_topic(org_url, auth, hint):
    """Search Recent Converations in 'Research' stream for a topic whose
    slug matches the hint (case-insensitive + punctuation-insensitive)."""
    hint_slug = slug(hint)
    resp = curl_get(
        f'{org_url}/api/v1/messages',
        ['--data-urlencode', 'stream=Research',
         '--data-urlencode', 'anchor=newest',
         '--data-urlencode', 'num_before=200',
         '--data-urlencode', 'num_after=0',
         '--data-urlencode', 'apply_markdown=false'])
    for m in resp.get('messages', []):
        t = m.get('subject', '')
        if slug(t) == hint_slug:
            return t      # return correctly-capitalized topic
    return None           # no match found

# Parse target
msg_type = None; to_field = None; topic = None

if target.startswith('user:'):
    msg_type = 'private'; to_field = target[5:]

elif target.startswith('stream:'):
    rest = target[7:]
    msg_type = 'stream'
    if rest == ':':
        to_field, topic = 'general', ''
    elif rest.startswith(':'):
        to_field, topic = 'general', rest[1:]
    elif ':' in rest:
        to_field, topic = rest.rsplit(':', 1)
    else:
        to_field, topic = rest, None

else:
    # Bare name — try disambiguation
    msg_type = 'stream'
    if target == ':':
        to_field, topic = 'general', ''
    elif target.startswith(':'):
        to_field, topic = 'general', target[1:]
    elif ':' in target:
        # stream:topic  (last colon splits them)
        to_field, topic = target.rsplit(':', 1)
    else:
        # Bare name — look up correct capitalization in Research stream
        found = resolve_topic(org_url, auth, target)
        if found:
            to_field, topic = 'Research', found
        else:
            # No match — still use Research stream with the bare name as topic
            to_field, topic = 'Research', target

# Resolve empty topic
if msg_type == 'stream' and not topic:
    topic = 'file delivery'


result = {'type': msg_type, 'to': to_field, 'topic': topic, 'content': content}
print(json.dumps(result))
PYEOF
)

  MSG_TYPE=$(echo "$TARGET_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['type'])" 2>/dev/null)
  TO_FIELD=$(echo "$TARGET_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['to'])" 2>/dev/null)
  TOPIC=$(echo "$TARGET_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['topic'])" 2>/dev/null)

  # --- Step 2: send message ---
  if [[ "$MSG_TYPE" == "private" ]]; then
    MSG_RESP=$(curl -s -X POST "${ZULIP_ORG_URL}/api/v1/messages" \
      -u "$AUTH" \
      --data-urlencode "type=private" \
      --data-urlencode "to=${TO_FIELD}" \
      --data-urlencode "content=${CONTENT}" \
      --max-time 30)
  else
    MSG_RESP=$(curl -s -X POST "${ZULIP_ORG_URL}/api/v1/messages" \
      -u "$AUTH" \
      --data-urlencode "type=stream" \
      --data-urlencode "to=${TO_FIELD}" \
      --data-urlencode "topic=${TOPIC}" \
      --data-urlencode "content=${CONTENT}" \
      --max-time 30)
  fi

  MSG_ID=$(echo "$MSG_RESP" | python3 -c \
    "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")

  if [[ -n "$MSG_ID" ]]; then
    echo "{\"status\":\"ok\",\"channel\":\"zulip\",\"target\":\"$TARGET\",\"file\":\"$BASENAME\",\"topic\":\"$TOPIC\",\"uri\":\"$URI\",\"msg_id\":\"$MSG_ID\",\"size\":$FILE_SIZE}"
  else
    echo "{\"status\":\"error\",\"channel\":\"zulip\",\"step\":\"send_message\",\"response\":$MSG_RESP}"
    exit 1
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# Google Chat / Zalo: GDrive link fallback
# ---------------------------------------------------------------------------
if [[ "$CHANNEL" == "googlechat" || "$CHANNEL" == "zalo" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  BASENAME=$(basename "$FILE_PATH")
  LINK=$(python3 "$SCRIPT_DIR/zot.py" --json get --link "$BASENAME" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('link',''))" 2>/dev/null || echo "")
  if [[ -n "$LINK" ]]; then
    echo "{\"status\":\"ok\",\"channel\":\"$CHANNEL\",\"method\":\"gdrive_link\",\"link\":\"$LINK\",\"file\":\"$BASENAME\"}"
  else
    echo "{\"status\":\"error\",\"channel\":\"$CHANNEL\",\"message\":\"GDrive link failed\"}"
    exit 1
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# WhatsApp / unknown: send queue
# ---------------------------------------------------------------------------
mkdir -p "$QUEUE_DIR"
JOB_ID="$(date -u +%Y%m%dT%H%M%S)-$$"
JOB_FILE="$QUEUE_DIR/${JOB_ID}.json"
RESULT_FILE="$QUEUE_DIR/${JOB_ID}.result"

python3 -c "
import json
with open('$JOB_FILE', 'w') as f:
    json.dump({'id':'$JOB_ID','channel':'$CHANNEL','target':'$TARGET','media':'$FILE_PATH','caption':'$CAPTION','status':'pending'}, f)
"

WAITED=0
while [[ $WAITED -lt 60 ]]; do
  if [[ -f "$RESULT_FILE" ]]; then
    cat "$RESULT_FILE"; rm -f "$JOB_FILE" "$RESULT_FILE"
    exit 0
  fi
  sleep 2; WAITED=$((WAITED + 2))
done
rm -f "$JOB_FILE"
echo "{\"status\":\"error\",\"message\":\"Send queue timeout\",\"channel\":\"$CHANNEL\",\"job_id\":\"$JOB_ID\"}"
exit 1
