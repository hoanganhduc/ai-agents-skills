#!/usr/bin/env bash
# POSIX convenience shim for the ai-agents-skills autonomous-loop Stop hook.
#
# The installer wires the Stop hook to call the runtime's cross-platform hook-check
# directly (`python <runtime> hook-check`); this shim exists only for manual POSIX
# use. It locates the runtime and forwards the hook JSON (inherited on stdin) to
# hook-check, which reads stdin, honors AUTOLOOP_DISABLE / AUTOLOOP_DRIVER and the
# stop_hook_active re-entrancy payload, and resolves the project root from
# CLAUDE_PROJECT_DIR. The runtime fails open (exit 0) on any error or timeout; only
# an active, unfinished loop exits 2 (block turn-end). A broken hook never traps a
# session.
set +e

here="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
[ -n "$here" ] || exit 0
runtime="$here/autonomous_research_loop_runtime.py"
[ -f "$runtime" ] || exit 0

py="$(command -v python3 || command -v python)"
[ -n "$py" ] || exit 0

rc=0
if command -v timeout >/dev/null 2>&1; then
  timeout 10 "$py" "$runtime" hook-check
  rc=$?
else
  "$py" "$runtime" hook-check
  rc=$?
fi

# Only an explicit "active loop, not done" (runtime exit 2) blocks turn-end. A
# timeout (124), a missing interpreter, or any other failure allows it (fail open).
[ "$rc" -eq 2 ] && exit 2
exit 0
