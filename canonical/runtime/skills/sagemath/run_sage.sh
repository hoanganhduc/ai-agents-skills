#!/usr/bin/env bash
set -euo pipefail

# SageMath execution via native Sage or WSL.
# Optional overrides:
#   AAS_SAGE_BIN        - native/WSL Sage executable, default sage
#   AAS_SAGE_WSL_DISTRO - WSL distro name, default Ubuntu-24.04

SAGE_BIN="${AAS_SAGE_BIN:-sage}"
SAGE_WSL_DISTRO="${AAS_SAGE_WSL_DISTRO:-Ubuntu-24.04}"
WS="${AAS_RUNTIME_WORKSPACE:-${OPENCLAW_WORKSPACE:-$HOME/.codex/runtime/workspace}}"
SAGE_DIR="$WS/data/research/sagemath"
SESSION_DIR="$SAGE_DIR/sessions"
TIMEOUT=300
MODE="code"
CODE=""
FILE_PATH=""
SESSION_NAME=""
CANCEL_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --file) MODE="file"; FILE_PATH="$2"; shift 2 ;;
    --plot) shift ;;
    --session) SESSION_NAME="$2"; shift 2 ;;
    --cancel) CANCEL_ID="$2"; shift 2 ;;
    *) CODE="$1"; shift ;;
  esac
done

if [[ -n "$CANCEL_ID" ]]; then
  echo '{"status":"ok","message":"Cancel not supported in direct mode"}'
  exit 0
fi
if [[ "$MODE" == "code" && -z "$CODE" ]]; then
  echo '{"status":"error","message":"No Sage code provided"}'
  exit 1
fi
if [[ "$MODE" == "file" && -z "$FILE_PATH" ]]; then
  echo '{"status":"error","message":"--file requires a path"}'
  exit 1
fi

mkdir -p "$SAGE_DIR" "$SESSION_DIR"

if [[ -n "$SESSION_NAME" && "$MODE" == "code" ]]; then
  SESSION_FILE="$SESSION_DIR/${SESSION_NAME}.sage"
  echo "$CODE" >> "$SESSION_FILE"
  MODE="file"
  FILE_PATH="$SESSION_FILE"
fi

run_with_timeout() {
  if command -v timeout >/dev/null 2>&1; then
    timeout "$TIMEOUT" "$@"
  else
    "$@"
  fi
}

wsl_path() {
  local p="$1"
  if [[ "$p" == ?:* ]]; then
    local drive="${p:0:1}"
    drive=$(printf '%s' "$drive" | tr 'A-Z' 'a-z')
    p="/mnt/$drive${p:2}"
    p="${p//\//}"
  elif [[ "$p" == /[a-zA-Z]/* ]]; then
    local drive="${p:1:1}"
    drive=$(printf '%s' "$drive" | tr 'A-Z' 'a-z')
    p="/mnt/$drive${p:2}"
  fi
  printf '%s
' "$p"
}

run_sage() {
  local path="$1"
  if command -v "$SAGE_BIN" >/dev/null 2>&1 || [[ -x "$SAGE_BIN" ]]; then
    run_with_timeout "$SAGE_BIN" "$path"
  elif command -v wsl.exe >/dev/null 2>&1; then
    wsl.exe -d "$SAGE_WSL_DISTRO" -- timeout "$TIMEOUT" "$SAGE_BIN" "$(wsl_path "$path")"
  elif command -v wsl >/dev/null 2>&1; then
    wsl -d "$SAGE_WSL_DISTRO" -- timeout "$TIMEOUT" "$SAGE_BIN" "$(wsl_path "$path")"
  else
    echo '{"status":"error","message":"SageMath not found; install sage or configure WSL"}'
    return 127
  fi
}

if [[ "$MODE" == "file" ]]; then
  run_sage "$FILE_PATH"
else
  TMPFILE="$SAGE_DIR/tmp_sage_$$.sage"
  echo "$CODE" > "$TMPFILE"
  run_sage "$TMPFILE"
  rm -f "$TMPFILE"
fi
