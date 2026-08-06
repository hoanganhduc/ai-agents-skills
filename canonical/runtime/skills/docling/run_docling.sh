#!/usr/bin/env bash
set -euo pipefail
script_path="${BASH_SOURCE[0]:-$0}"
runtime_command_fd="${AAS_RUNTIME_COMMAND_FD:-}"
if [[ "$runtime_command_fd" =~ ^[0-9]+$ ]] && \
   { [ "$script_path" = "/proc/self/fd/$runtime_command_fd" ] || [ "$script_path" = "/dev/fd/$runtime_command_fd" ]; }; then
  script_path="${AAS_RUNTIME_COMMAND_PATH:-$script_path}"
fi
unset AAS_RUNTIME_COMMAND_FD AAS_RUNTIME_COMMAND_PATH
ROOT="$(cd -- "$(dirname -- "$script_path")" && pwd)"
select_python() {
  if [[ -n "${DOCLING_PYTHON:-}" ]]; then
    printf '%s\n' "$DOCLING_PYTHON"
    return 0
  fi
  if [[ -n "${AAS_RUNTIME_PYTHON:-}" ]]; then
    printf '%s\n' "$AAS_RUNTIME_PYTHON"
    return 0
  fi
  local venv_python="${HOME:-}/.local/share/docling-venv/bin/python"
  if [[ -x "$venv_python" ]]; then
    printf '%s\n' "$venv_python"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi
  return 1
}
PYTHON="$(select_python)" || {
  echo "no usable Python runtime found. Set DOCLING_PYTHON or install Python 3." >&2
  exit 127
}
cmd="${1:-}"
if [[ -z "$cmd" ]]; then
  echo "usage: run_docling.sh <doctor|convert|extract|chunk|quality|ocrspace-smoke> [args...]" >&2
  exit 1
fi
shift || true
case "$cmd" in
  doctor) exec "$PYTHON" "$ROOT/doctor.py" "$@" ;;
  convert) exec "$PYTHON" "$ROOT/docling_convert.py" "$@" ;;
  extract) exec "$PYTHON" "$ROOT/docling_extract.py" "$@" ;;
  chunk) exec "$PYTHON" "$ROOT/docling_chunk.py" "$@" ;;
  quality) exec "$PYTHON" "$ROOT/docling_quality.py" "$@" ;;
  ocrspace-smoke) exec "$PYTHON" "$ROOT/docling_ocrspace_smoke.py" "$@" ;;
  *) echo "unknown subcommand: $cmd" >&2; exit 1 ;;
esac
