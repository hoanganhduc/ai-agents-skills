#!/usr/bin/env bash
set -euo pipefail
script_path="${BASH_SOURCE[0]:-$0}"
runtime_command_fd="${AAS_RUNTIME_COMMAND_FD:-}"
if [[ "$runtime_command_fd" =~ ^[0-9]+$ ]] && \
   { [ "$script_path" = "/proc/self/fd/$runtime_command_fd" ] || [ "$script_path" = "/dev/fd/$runtime_command_fd" ]; }; then
  script_path="${AAS_RUNTIME_COMMAND_PATH:-$script_path}"
fi
unset AAS_RUNTIME_COMMAND_FD AAS_RUNTIME_COMMAND_PATH
SCRIPT_DIR="$(cd -- "$(dirname -- "$script_path")" && pwd -P)"
PYTHON="${AAS_RUNTIME_PYTHON:-python3}"

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

exec -a "$python_argv0" "$PYTHON" -I "$SCRIPT_DIR/research_digest.py" "$@"
