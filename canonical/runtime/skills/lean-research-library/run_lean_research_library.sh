#!/usr/bin/env bash
set -euo pipefail

script_path="${BASH_SOURCE[0]:-$0}"
runtime_command_fd="${AAS_RUNTIME_COMMAND_FD:-}"
if [[ "$runtime_command_fd" =~ ^[0-9]+$ ]] && \
   { [ "$script_path" = "/proc/self/fd/$runtime_command_fd" ] || [ "$script_path" = "/dev/fd/$runtime_command_fd" ]; }; then
  script_path="${AAS_RUNTIME_COMMAND_PATH:-$script_path}"
fi
unset AAS_RUNTIME_COMMAND_FD AAS_RUNTIME_COMMAND_PATH
SCRIPT_DIR="$(cd "$(dirname "$script_path")" && pwd -P)"
SCRIPT="$SCRIPT_DIR/lean_research_library.py"

if [[ ! -f "$SCRIPT" ]]; then
  printf 'runtime helper not found: %s\n' "$SCRIPT" >&2
  exit 127
fi

if [[ -n "${AAS_RUNTIME_PYTHON:-}" ]]; then
  exec "$AAS_RUNTIME_PYTHON" "$SCRIPT" "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SCRIPT" "$@"
fi
if command -v python >/dev/null 2>&1; then
  exec python "$SCRIPT" "$@"
fi

printf 'error: no usable Python runtime found. Set AAS_RUNTIME_PYTHON or install Python 3.\n' >&2
exit 127
