#!/usr/bin/env bash
# Fail-open Stop hook for ai-agents-skills autonomous loops.
#
# Claude Code runs this on turn-end (the Stop event) with the hook JSON on
# stdin. It asks the runtime whether the active loop for this session's project
# is finished and exits 2 (block turn-end) ONLY when an active loop is not done.
# Any error, timeout, missing runtime, or kill switch exits 0 (allow turn-end):
# a broken hook must never trap a session.
set +e

payload=""
if [ ! -t 0 ]; then
  payload="$(cat 2>/dev/null)"
fi

# Re-entrancy: if Claude reports the stop hook is already active, allow, so a
# block can never build an infinite loop.
case "$payload" in
  *'"stop_hook_active":true'* | *'"stop_hook_active": true'*) exit 0 ;;
esac

# Kill switches, checked before any runtime call.
[ -n "${AUTOLOOP_DISABLE:-}" ] && exit 0
# Headless driver runs enforce the policy themselves; the interactive hook
# stands down so it never double-governs a driver iteration.
[ -n "${AUTOLOOP_DRIVER:-}" ] && exit 0

here="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
[ -n "$here" ] || exit 0
runtime="$here/autonomous_research_loop_runtime.py"
[ -f "$runtime" ] || exit 0

root="${CLAUDE_PROJECT_DIR:-$PWD}"

py="$(command -v python3 || command -v python)"
[ -n "$py" ] || exit 0

rc=0
if command -v timeout >/dev/null 2>&1; then
  timeout 10 "$py" "$runtime" hook-check --root "$root" >/dev/null 2>&1
  rc=$?
else
  "$py" "$runtime" hook-check --root "$root" >/dev/null 2>&1
  rc=$?
fi

# Only an explicit "active loop, not done" (runtime exit 2) blocks turn-end.
# A timeout (124), a missing interpreter (127), or any other failure allows it.
[ "$rc" -eq 2 ] && exit 2
exit 0
