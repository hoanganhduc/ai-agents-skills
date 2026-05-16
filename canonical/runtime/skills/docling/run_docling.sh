#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cmd="${1:-}"
if [[ -z "$cmd" ]]; then
  echo "usage: run_docling.sh <doctor|convert|extract|chunk> [args...]" >&2
  exit 1
fi
shift || true
case "$cmd" in
  doctor) exec python3 "$ROOT/doctor.py" "$@" ;;
  convert) exec python3 "$ROOT/docling_convert.py" "$@" ;;
  extract) exec python3 "$ROOT/docling_extract.py" "$@" ;;
  chunk) exec python3 "$ROOT/docling_chunk.py" "$@" ;;
  *) echo "unknown subcommand: $cmd" >&2; exit 1 ;;
esac
