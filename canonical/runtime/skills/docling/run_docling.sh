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
# Skill Python venv: the launcher admitted AAS_RUNTIME_PYTHON_PREFIX (run_skill.sh
# skill_python_prefix).  Re-check the two facts this wrapper relies on, then run
# the attested binary under the venv's argv[0] so CPython reads <prefix>/pyvenv.cfg.
# The interpreter executed is still "$PYTHON"; the venv supplies argv[0], PATH and
# site-packages.
python_argv0="$PYTHON"
if [ -n "${AAS_RUNTIME_PYTHON_PREFIX:-}" ]; then
  prefix="$AAS_RUNTIME_PYTHON_PREFIX"
  case "$prefix" in /*) ;; *) prefix="" ;; esac
  if [ -z "$prefix" ] || [ -L "$prefix/pyvenv.cfg" ] || [ ! -f "$prefix/pyvenv.cfg" ] \
     || [ ! -L "$prefix/bin/python" ] || ! [ "$prefix/bin/python" -ef "$PYTHON" ]; then
    printf 'AAS_RUNTIME_PYTHON_PREFIX does not name a venv of the selected Python\n' >&2
    exit 127
  fi
  python_argv0="$prefix/bin/python"
  export PATH="$prefix/bin:$PATH"
fi

cmd="${1:-}"
if [[ -z "$cmd" ]]; then
  echo "usage: run_docling.sh <doctor|convert|extract|chunk|quality|ocrspace-smoke> [args...]" >&2
  exit 1
fi
shift || true
case "$cmd" in
  doctor) exec -a "$python_argv0" "$PYTHON" "$ROOT/doctor.py" "$@" ;;
  convert) exec -a "$python_argv0" "$PYTHON" "$ROOT/docling_convert.py" "$@" ;;
  extract) exec -a "$python_argv0" "$PYTHON" "$ROOT/docling_extract.py" "$@" ;;
  chunk) exec -a "$python_argv0" "$PYTHON" "$ROOT/docling_chunk.py" "$@" ;;
  quality) exec -a "$python_argv0" "$PYTHON" "$ROOT/docling_quality.py" "$@" ;;
  ocrspace-smoke) exec -a "$python_argv0" "$PYTHON" "$ROOT/docling_ocrspace_smoke.py" "$@" ;;
  --help|-h|help)
    echo "usage: run_docling.sh <doctor|convert|extract|chunk|quality|ocrspace-smoke> [args...]"
    exit 0
    ;;
  *) echo "unknown subcommand: $cmd" >&2; exit 1 ;;
esac
