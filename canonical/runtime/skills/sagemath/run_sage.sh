#!/usr/bin/env bash
set -euo pipefail

# SageMath execution via native Sage or WSL.
# Optional overrides:
#   AAS_SAGE_BIN        - native/WSL Sage executable, default sage
#   AAS_SAGE_WSL_DISTRO - WSL distro name, default Ubuntu-24.04

SAGE_BIN="${AAS_SAGE_BIN:-sage}"
SAGE_WSL_DISTRO="${AAS_SAGE_WSL_DISTRO:-Ubuntu-24.04}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEFAULT_WORKSPACE="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
WS="${AAS_RUNTIME_WORKSPACE:-${OPENCLAW_WORKSPACE:-$DEFAULT_WORKSPACE}}"
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

run_sage_code() {
  local code="$1"
  if command -v "$SAGE_BIN" >/dev/null 2>&1 || [[ -x "$SAGE_BIN" ]]; then
    run_with_timeout "$SAGE_BIN" -c "$code"
  elif command -v wsl.exe >/dev/null 2>&1; then
    wsl.exe -d "$SAGE_WSL_DISTRO" -- timeout "$TIMEOUT" "$SAGE_BIN" -c "$code"
  elif command -v wsl >/dev/null 2>&1; then
    wsl -d "$SAGE_WSL_DISTRO" -- timeout "$TIMEOUT" "$SAGE_BIN" -c "$code"
  else
    echo '{"status":"error","message":"SageMath not found; install sage or configure WSL"}'
    return 127
  fi
}

if [[ "$MODE" == "file" ]]; then
  run_sage_code "$(cat "$FILE_PATH")"
else
  run_sage_code "$CODE"
fi
