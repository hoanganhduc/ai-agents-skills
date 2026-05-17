#!/usr/bin/env bash
set -euo pipefail

runtime_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
workspace="$runtime_root/workspace"
if [ "${AAS_ALLOW_EXTERNAL_RUNTIME_WORKSPACE:-}" = "1" ] && [ -n "${AAS_RUNTIME_WORKSPACE:-}" ]; then
  workspace="$AAS_RUNTIME_WORKSPACE"
fi

if [ "$#" -lt 1 ]; then
  printf 'usage: %s <runtime-relative-script> [args...]\n' "$0" >&2
  exit 2
fi

command_rel="$1"
shift

case "$command_rel" in
  /*|*..*|*\0*)
    printf 'refusing unsafe runtime command path: %s\n' "$command_rel" >&2
    exit 2
    ;;
esac

command_path="$workspace/$command_rel"
runtime_real="$(cd -- "$runtime_root" && pwd -P)"
workspace_real="$(cd -- "$workspace" && pwd -P)"
command_dir="$(dirname -- "$command_path")"

if [ ! -d "$command_dir" ]; then
  printf 'runtime command directory not found: %s\n' "$command_dir" >&2
  exit 127
fi

command_dir_real="$(cd -- "$command_dir" && pwd -P)"
case "$command_dir_real/" in
  "$workspace_real"/*) ;;
  *)
    printf 'refusing runtime command outside workspace: %s\n' "$command_path" >&2
    exit 2
    ;;
esac

if [ ! -f "$command_path" ]; then
  printf 'runtime command not found: %s\n' "$command_path" >&2
  exit 127
fi
if [ -L "$command_path" ]; then
  printf 'refusing symlinked runtime command: %s\n' "$command_path" >&2
  exit 2
fi

export AAS_RUNTIME_ROOT="${AAS_RUNTIME_ROOT:-$runtime_real}"
export AAS_RUNTIME_WORKSPACE="$workspace_real"
secrets_file="$workspace_real/.secrets.json"
if [ "${AAS_ALLOW_EXTERNAL_SECRETS_FILE:-}" = "1" ] && [ -n "${AAS_SECRETS_FILE:-}" ]; then
  secrets_dir="$(dirname -- "$AAS_SECRETS_FILE")"
  if [ ! -d "$secrets_dir" ]; then
    printf 'runtime secrets directory not found: %s\n' "$secrets_dir" >&2
    exit 2
  fi
  secrets_dir_real="$(cd -- "$secrets_dir" && pwd -P)"
  secrets_file="$secrets_dir_real/$(basename -- "$AAS_SECRETS_FILE")"
fi
export AAS_SECRETS_FILE="$secrets_file"
export PYTHONUTF8="${PYTHONUTF8:-1}"
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"

# Compatibility for older runtime scripts. These are set by the managed runner
# instead of inherited blindly from the user's shell.
export OPENCLAW_WORKSPACE="$AAS_RUNTIME_WORKSPACE"
export OPENCLAW_SECRETS_FILE="$AAS_SECRETS_FILE"

if command -v python3 >/dev/null 2>&1; then
  py_ver="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
  if [ -n "$py_ver" ]; then
    site_packages="$workspace_real/.local/lib/python${py_ver}/site-packages"
    dist_packages="$workspace_real/.local/local/lib/python${py_ver}/dist-packages"
    local_bin="$workspace_real/.local/bin"
    alt_bin="$workspace_real/.local/local/bin"
    mkdir -p "$site_packages" "$dist_packages" "$local_bin" "$alt_bin"
    python_path_entries="$site_packages:$dist_packages:$workspace_real/.local"
    path_entries="$local_bin:$alt_bin"
    if [ -n "${HOME:-}" ]; then
      for candidate in \
        "$HOME/.local/lib/python${py_ver}/site-packages" \
        "$HOME/.codex/runtime/workspace/.local/lib/python${py_ver}/site-packages" \
        "$HOME/.codex/runtime/workspace/.local/local/lib/python${py_ver}/dist-packages" \
        "$HOME/.codex/runtime/workspace/.local" \
        "$HOME/.claude/.local/lib/python${py_ver}/site-packages" \
        "$HOME/.claude/.local" \
        "$HOME/.deepseek/.local/lib/python${py_ver}/site-packages" \
        "$HOME/.local/share/docling-venv/lib/python${py_ver}/site-packages"; do
        if [ -d "$candidate" ]; then
          python_path_entries="$python_path_entries:$candidate"
        fi
      done
      for candidate in \
        "$HOME/.local/bin" \
        "$HOME/.codex/runtime/workspace/.local/bin" \
        "$HOME/.codex/runtime/workspace/.local/local/bin" \
        "$HOME/.local/share/docling-venv/bin"; do
        if [ -d "$candidate" ]; then
          path_entries="$candidate:$path_entries"
        fi
      done
    fi
    export PYTHONPATH="$python_path_entries:${PYTHONPATH:-}"
    export PATH="$path_entries:${PATH}"
  fi
fi

exec "$command_path" "$@"
