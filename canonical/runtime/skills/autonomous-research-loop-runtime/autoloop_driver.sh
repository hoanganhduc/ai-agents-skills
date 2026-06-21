#!/usr/bin/env bash
# POSIX convenience shim for the ai-agents-skills headless autonomous-loop driver.
#
# The driver is implemented cross-platform inside the runtime as the `drive`
# subcommand; this shim only forwards its arguments there so existing POSIX call
# sites keep working. `drive` runs one iteration command per loop (with
# AUTOLOOP_DRIVER=1, AUTOLOOP_DIR, AUTOLOOP_ROOT exported) until the runtime reports
# the loop is done, the command fails too many times in a row, or the state cannot
# be read, and it fails safe (stops) on any inability to determine state. Exit
# codes: 3 = max failures, 4 = runtime error, 0 = stopped cleanly.
#
# Options (forwarded to `drive`): --dir <loop_dir> --cmd <iteration_command>
#   [--root <project_root>] [--iteration-timeout <seconds>] [--max-failures <n>]
#   [--poll <seconds>]
set -uo pipefail

here="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
runtime="$here/autonomous_research_loop_runtime.py"
[ -f "$runtime" ] || { echo "autoloop-driver: runtime not found: $runtime" >&2; exit 127; }
PY="${AAS_RUNTIME_PYTHON:-}"
[ -n "$PY" ] || PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "autoloop-driver: python3 or python required" >&2; exit 127; }

exec "$PY" "$runtime" drive "$@"
