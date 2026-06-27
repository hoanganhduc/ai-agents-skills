#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
select_python() {
  if [[ -n "${URL_TO_SCREENSHOT_PYTHON:-}" ]]; then printf '%s\n' "$URL_TO_SCREENSHOT_PYTHON"; return 0; fi
  if [[ -n "${AAS_RUNTIME_PYTHON:-}" ]]; then printf '%s\n' "$AAS_RUNTIME_PYTHON"; return 0; fi
  if command -v python3 >/dev/null 2>&1; then command -v python3; return 0; fi
  if command -v python >/dev/null 2>&1; then command -v python; return 0; fi
  return 1
}
PYTHON="$(select_python)" || {
  echo "no usable Python runtime found. Set URL_TO_SCREENSHOT_PYTHON or install Python 3." >&2
  exit 127
}
exec "$PYTHON" "$ROOT/url_to_screenshot_runtime.py" "$@"
