#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

find_python() {
  if [ -n "${AAS_PYTHON:-}" ] && command -v "$AAS_PYTHON" >/dev/null 2>&1; then
    printf '%s\n' "$AAS_PYTHON"
    return 0
  fi
  if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    printf '%s\n' "$ROOT_DIR/.venv/bin/python"
    return 0
  fi
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

PYTHON=$(find_python) || {
  printf 'error: no usable Python runtime found\n' >&2
  exit 1
}

PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" exec "$PYTHON" -m installer.ai_agents_skills "$@"
