#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

is_usable_python() {
  "$1" -c 'import sys, ssl, venv, pip; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

find_python() {
  if [ -n "${AAS_PYTHON:-}" ]; then
    if [ -x "$AAS_PYTHON" ]; then
      if is_usable_python "$AAS_PYTHON"; then
        printf '%s\n' "$AAS_PYTHON"
        return 0
      fi
    fi
    if command -v "$AAS_PYTHON" >/dev/null 2>&1; then
      resolved=$(command -v "$AAS_PYTHON")
      if is_usable_python "$resolved"; then
        printf '%s\n' "$resolved"
        return 0
      fi
    fi
  fi
  if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    if is_usable_python "$ROOT_DIR/.venv/bin/python"; then
      printf '%s\n' "$ROOT_DIR/.venv/bin/python"
      return 0
    fi
  fi
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      resolved=$(command -v "$candidate")
      if is_usable_python "$resolved"; then
        printf '%s\n' "$resolved"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON=$(find_python) || {
  printf 'error: no usable Python 3.10+ runtime with ssl, venv, and pip found. Set AAS_PYTHON to a compatible interpreter.\n' >&2
  exit 1
}

if [ "${1:-}" = "--print-python" ]; then
  printf '%s\n' "$PYTHON"
  exit 0
fi

if [ "${1:-}" = "--run-python" ]; then
  shift
  PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" exec "$PYTHON" "$@"
fi

PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" exec "$PYTHON" -m installer.ai_agents_skills "$@"
