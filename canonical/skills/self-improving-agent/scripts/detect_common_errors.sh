#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  detect_common_errors.sh [FILE]
  some_command 2>&1 | detect_common_errors.sh

Behavior:
  - Reads FILE if provided
  - Otherwise reads stdin
  - Prints a reminder when common failure markers are detected
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -gt 1 ]]; then
  usage >&2
  exit 2
fi

if [[ $# -eq 1 ]]; then
  if [[ ! -f "$1" ]]; then
    echo "error: file not found: $1" >&2
    exit 2
  fi
  output="$(cat "$1")"
else
  output="$(cat)"
fi

pattern='(error:|Error:|FATAL|fatal:|Traceback|Exception|ModuleNotFoundError|TypeError|ImportError|Permission denied|No such file|command not found|npm ERR!|SyntaxError|NameError|KeyError|ValueError|FileNotFoundError)'

if echo "$output" | grep -qiE "$pattern"; then
  cat <<'EOF'
Potential failure markers detected.

Consider whether this should be logged with `self_improving_agent`:
- unexpected command failure
- recurring environment or path issue
- missing capability
- fix or workaround worth preserving

Useful next step:
  bash ~/.codex/skills/self_improving_agent/scripts/review_pending.sh
EOF
else
  echo "No common error markers detected."
fi
