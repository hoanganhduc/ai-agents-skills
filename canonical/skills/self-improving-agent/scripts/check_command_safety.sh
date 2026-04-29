#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  check_command_safety.sh "<command>"
  echo "<command>" | check_command_safety.sh

Exit codes:
  0  allowed by this lightweight checker
  2  blocked by a matched safety rule
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -gt 0 ]]; then
  cmd="$*"
else
  cmd="$(cat)"
fi

cmd="${cmd#"${cmd%%[![:space:]]*}"}"
cmd="${cmd%"${cmd##*[![:space:]]}"}"

if [[ -z "$cmd" ]]; then
  echo "No command provided." >&2
  usage >&2
  exit 2
fi

if echo "$cmd" | grep -qE 'rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?(-[a-zA-Z]*r[a-zA-Z]*\s+)?(/|/home(\s|$)|~/?(\s|$))'; then
  echo "BLOCKED: destructive rm -rf targeting root or home directory." >&2
  exit 2
fi

if echo "$cmd" | grep -qE 'git\s+push\s+.*--force.*\s+(origin\s+)?(main|master)\b'; then
  echo "BLOCKED: force push to main/master." >&2
  exit 2
fi

if echo "$cmd" | grep -qE '(curl|wget)\s+[^|]*\|\s*(ba)?sh'; then
  echo "BLOCKED: pipe-to-shell pattern detected." >&2
  exit 2
fi

if echo "$cmd" | grep -qiE 'DROP\s+(DATABASE|TABLE)\s'; then
  echo "BLOCKED: DROP DATABASE/TABLE detected." >&2
  exit 2
fi

echo "ALLOW: no lightweight safety rule matched."
