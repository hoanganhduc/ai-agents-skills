#!/usr/bin/env bash
set -euo pipefail

# Host-side worker that processes send-queue jobs.
# Watches /workspace/data/send-queue/ for .json job files,
# sends via openclaw message send, writes .result files back.
#
# Run as: systemctl --user start send-queue-worker
# Or manually: ./send_queue_worker.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_WORKSPACE="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE="${AAS_RUNTIME_WORKSPACE:-${OPENCLAW_WORKSPACE:-$DEFAULT_WORKSPACE}}"
QUEUE_DIR="$WORKSPACE/data/send-queue"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"

mkdir -p "$QUEUE_DIR"

log() { echo "[$(date -u +%H:%M:%S)] $*" >&2; }

process_job() {
  local job_file="$1"
  local job_id
  job_id=$(basename "$job_file" .json)
  local result_file="$QUEUE_DIR/${job_id}.result"

  # Parse job
  local channel target media caption
  channel=$(python3 -c "import json; print(json.load(open('$job_file'))['channel'])")
  target=$(python3 -c "import json; print(json.load(open('$job_file'))['target'])")
  media=$(python3 -c "import json; print(json.load(open('$job_file'))['media'])")
  caption=$(python3 -c "import json; print(json.load(open('$job_file')).get('caption',''))")

  # Convert sandbox path to host path
  local host_media="${media/\/workspace/$WORKSPACE}"

  if [[ ! -f "$host_media" ]]; then
    echo '{"status":"error","message":"File not found on host: '"$host_media"'"}' > "$result_file"
    log "FAIL $job_id: file not found $host_media"
    return
  fi

  log "SEND $job_id: $channel -> $target ($(basename "$host_media"))"

  # Build command
  local cmd=("$OPENCLAW_BIN" message send --channel "$channel" --target "$target" --media "$host_media")
  if [[ -n "$caption" ]]; then
    cmd+=(-m "$caption")
  fi

  # Execute
  local output
  if output=$("${cmd[@]}" 2>&1); then
    echo '{"status":"ok","channel":"'"$channel"'","target":"'"$target"'","file":"'"$(basename "$host_media")"'","output":"'"$(echo "$output" | head -1)"'"}' > "$result_file"
    log "OK   $job_id: sent"
  else
    echo '{"status":"error","channel":"'"$channel"'","message":"openclaw send failed","output":"'"$(echo "$output" | head -1)"'"}' > "$result_file"
    log "FAIL $job_id: $output"
  fi
}

log "Send queue worker started. Watching $QUEUE_DIR"

while true; do
  for job_file in "$QUEUE_DIR"/*.json; do
    [[ -f "$job_file" ]] || continue
    process_job "$job_file"
  done
  sleep 2
done
