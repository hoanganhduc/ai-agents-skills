#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
select_python() {
  if [[ -n "${MMA_PYTHON:-}" ]]; then printf '%s\n' "$MMA_PYTHON"; return 0; fi
  local venv_python="${HOME:-}/.local/share/manim-math-animation-venv/bin/python"
  if [[ -x "$venv_python" ]]; then printf '%s\n' "$venv_python"; return 0; fi
  if [[ -n "${AAS_RUNTIME_PYTHON:-}" ]]; then printf '%s\n' "$AAS_RUNTIME_PYTHON"; return 0; fi
  if command -v python3 >/dev/null 2>&1; then command -v python3; return 0; fi
  if command -v python >/dev/null 2>&1; then command -v python; return 0; fi
  return 1
}
PYTHON="$(select_python)" || {
  echo "no usable Python runtime found. Set MMA_PYTHON or install Python 3." >&2
  exit 127
}
exec "$PYTHON" "$ROOT/manim_math_animation_runtime.py" "$@"
