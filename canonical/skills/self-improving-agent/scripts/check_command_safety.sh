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

if echo "$cmd" | grep -qE 'rm[[:space:]]+(-[a-zA-Z]*f[a-zA-Z]*[[:space:]]+)?(-[a-zA-Z]*r[a-zA-Z]*[[:space:]]+)?(/|/home([[:space:]]|$)|~/?([[:space:]]|$))'; then
  echo "BLOCKED: destructive rm -rf targeting root or home directory." >&2
  exit 2
fi

if echo "$cmd" | grep -qE 'git[[:space:]]+push[[:space:]]+.*--force.*[[:space:]]+(origin[[:space:]]+)?(main|master)([^[:alnum:]_]|$)'; then
  echo "BLOCKED: force push to main/master." >&2
  exit 2
fi

if echo "$cmd" | grep -qE '(curl|wget)[[:space:]]+[^|]*\|[[:space:]]*(ba)?sh'; then
  echo "BLOCKED: pipe-to-shell pattern detected." >&2
  exit 2
fi

if echo "$cmd" | grep -qiE 'DROP[[:space:]]+(DATABASE|TABLE)[[:space:]]'; then
  echo "BLOCKED: DROP DATABASE/TABLE detected." >&2
  exit 2
fi

if echo "$cmd" | grep -qiE '(^|[^[:alnum:]_])(Remove-Item|rm|del|erase)([^[:alnum:]_]|$)'; then
  if echo "$cmd" | grep -qiE '(^|[[:space:]])(-Recurse|-r)([[:space:]]|$)' \
    && echo "$cmd" | grep -qiE '(^|[[:space:]])(-Force|-f)([[:space:]]|$)'; then
    echo "BLOCKED: PowerShell recursive forced deletion detected." >&2
    exit 2
  fi
fi

if echo "$cmd" | grep -qiE '(^|[^[:alnum:]_])(rmdir|rd)[[:space:]]+/s[[:space:]]+/q([^[:alnum:]_]|$)|(^|[^[:alnum:]_])(del|erase)[[:space:]]+/[sq]([^[:alnum:]_]|$)'; then
  echo "BLOCKED: CMD recursive deletion detected." >&2
  exit 2
fi

if echo "$cmd" | grep -qiE '(^|[^[:alnum:]_])(Format-Volume|format)([^[:alnum:]_]|$)'; then
  echo "BLOCKED: Windows volume formatting detected." >&2
  exit 2
fi

echo "ALLOW: no lightweight safety rule matched."
