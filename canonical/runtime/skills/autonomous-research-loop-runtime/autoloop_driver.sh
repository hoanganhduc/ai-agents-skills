#!/usr/bin/env bash
# Generic headless driver for ai-agents-skills autonomous loops.
#
# Runs one agent iteration command per loop until the runtime reports the loop
# is done (loops reached, credit/budget exhausted, goal resolved, or user stop),
# or until the iteration command fails too many times in a row. The driver is
# the sole enforcer in headless mode: it exports AUTOLOOP_DRIVER=1 so the
# interactive Stop hook stands down, and it derives "done" only from the runtime
# (never from the agent's own say-so). On any inability to determine state it
# fails SAFE (stops) rather than running unbounded.
set -uo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: autoloop_driver.sh --dir <loop_dir> --cmd <iteration_command>
                          [--root <project_root>] [--iteration-timeout <seconds>]
                          [--max-failures <n>] [--poll <seconds>]

The iteration command runs once per loop with AUTOLOOP_DRIVER=1, AUTOLOOP_DIR,
and AUTOLOOP_ROOT exported. It should perform exactly one iteration and append it
to the ledger through the runtime.
USAGE
}

DIR="" ROOT="" CMD="" ITER_TIMEOUT=1800 MAX_FAILURES=3 POLL=5
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="${2:-}"; shift 2 || true ;;
    --root) ROOT="${2:-}"; shift 2 || true ;;
    --cmd) CMD="${2:-}"; shift 2 || true ;;
    --iteration-timeout) ITER_TIMEOUT="${2:-}"; shift 2 || true ;;
    --max-failures) MAX_FAILURES="${2:-}"; shift 2 || true ;;
    --poll) POLL="${2:-}"; shift 2 || true ;;
    -h | --help) usage; exit 0 ;;
    *) echo "autoloop-driver: unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done
if [ -z "$DIR" ] || [ -z "$CMD" ]; then usage; exit 2; fi
[ -n "$ROOT" ] || ROOT="$PWD"

here="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
runtime="$here/autonomous_research_loop_runtime.py"
[ -f "$runtime" ] || { echo "autoloop-driver: runtime not found: $runtime" >&2; exit 127; }
PY="${AAS_RUNTIME_PYTHON:-}"
[ -n "$PY" ] || PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "autoloop-driver: python3 or python required" >&2; exit 127; }

rt() { "$PY" "$runtime" "$@"; }

rt arm --dir "$DIR" --root "$ROOT" >/dev/null 2>&1 || true
cleanup() { rt disarm --dir "$DIR" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

have_timeout=0
command -v timeout >/dev/null 2>&1 && have_timeout=1

failures=0
reason="unknown"
while true; do
  out="$(rt done --dir "$DIR" 2>/dev/null)"
  rc=$?
  if [ "$rc" -ne 0 ] || [ -z "$out" ]; then reason="runtime_error"; break; fi
  case "$out" in *'"done": true'*) reason="done"; break ;; esac
  case "$out" in *'"paused": true'*) sleep "$POLL"; continue ;; esac

  if [ "$have_timeout" -eq 1 ]; then
    AUTOLOOP_DRIVER=1 AUTOLOOP_DIR="$DIR" AUTOLOOP_ROOT="$ROOT" timeout "$ITER_TIMEOUT" bash -c "$CMD"
    rc=$?
  else
    AUTOLOOP_DRIVER=1 AUTOLOOP_DIR="$DIR" AUTOLOOP_ROOT="$ROOT" bash -c "$CMD"
    rc=$?
  fi
  if [ "$rc" -ne 0 ]; then
    failures=$((failures + 1))
    echo "autoloop-driver: iteration command failed (rc=$rc, ${failures}/${MAX_FAILURES})" >&2
    if [ "$failures" -ge "$MAX_FAILURES" ]; then reason="max_failures"; break; fi
  else
    failures=0
  fi
done

verdict="$(rt done --dir "$DIR" 2>/dev/null || echo '{}')"
echo "autoloop-driver: stopped (reason=${reason})"
echo "$verdict"
case "$reason" in
  max_failures) exit 3 ;;
  runtime_error) exit 4 ;;
  *) exit 0 ;;
esac
