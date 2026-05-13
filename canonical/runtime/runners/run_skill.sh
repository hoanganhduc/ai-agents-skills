#!/usr/bin/env bash
set -euo pipefail

runtime_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
workspace="${AAS_RUNTIME_WORKSPACE:-$runtime_root/workspace}"

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

export AAS_RUNTIME_ROOT="${AAS_RUNTIME_ROOT:-$runtime_real}"
export AAS_RUNTIME_WORKSPACE="$workspace_real"
export AAS_SECRETS_FILE="${AAS_SECRETS_FILE:-$workspace_real/.secrets.json}"
export PYTHONUTF8="${PYTHONUTF8:-1}"
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"

# Compatibility for older runtime scripts. These are set by the managed runner
# instead of inherited blindly from the user's shell.
export OPENCLAW_WORKSPACE="$AAS_RUNTIME_WORKSPACE"
export OPENCLAW_SECRETS_FILE="$AAS_SECRETS_FILE"

exec "$command_path" "$@"
