#!/usr/bin/env bash
# Launch or replace the ARL drive supervisor for one loop directory.
#
# Modes:
#   start   (default) — acquire exclusive flock; refuse if lock held (exit 10)
#   replace — stop prior supervisor+drive for this loop, then start
#
# Usage:
#   LOOP_DIR=... PROJECT_ROOT=... bash LAUNCH_supervisor.sh [start|replace]
set -uo pipefail

MODE="${1:-start}"
SELF_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOOP_DIR="${LOOP_DIR:-}"
PROJECT_ROOT="${PROJECT_ROOT:-}"
SUPERVISOR="${SUPERVISOR:-$SELF_DIR/arl_drive_supervisor.sh}"

if [[ -z "${LOOP_DIR}" ]]; then
  echo "LAUNCH_supervisor: set LOOP_DIR" >&2
  exit 2
fi
LOOP_DIR="$(CDPATH= cd -- "$LOOP_DIR" && pwd)"
if [[ -z "${PROJECT_ROOT}" ]]; then
  PROJECT_ROOT="$(dirname "$LOOP_DIR")"
fi
PROJECT_ROOT="$(CDPATH= cd -- "$PROJECT_ROOT" && pwd)"

mkdir -p "$LOOP_DIR/driver" "$LOOP_DIR/driver_logs"
LOCK="$LOOP_DIR/driver/supervisor.lock"
LOG="$LOOP_DIR/driver_logs/supervisor.out"
PIDFILE="$LOOP_DIR/driver/supervisor.pid"

stop_prior() {
  # Kill prior supervisor for this loop (pidfile + cmdline scan).
  if [[ -f "$PIDFILE" ]]; then
    old="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "$old" ]] && kill -0 "$old" 2>/dev/null; then
      echo "LAUNCH_supervisor: stopping prior supervisor pid $old"
      kill -TERM "$old" 2>/dev/null || true
      sleep 2
      kill -KILL "$old" 2>/dev/null || true
    fi
    rm -f "$PIDFILE"
  fi
  # Kill drive processes for this loop only.
  LOOP_DIR="$LOOP_DIR" python3 - <<'PY'
import os, signal, time
from pathlib import Path
loop = os.environ["LOOP_DIR"]
needle = f"drive --dir {loop}"
pids = []
for proc in Path("/proc").iterdir():
    if not proc.name.isdigit():
        continue
    try:
        cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
    except Exception:
        continue
    if "autonomous_research_loop_runtime.py" in cmd and needle in cmd:
        pids.append(int(proc.name))
    if "arl_drive_supervisor.sh" in cmd and loop in cmd:
        pids.append(int(proc.name))
for pid in sorted(set(pids)):
    print(f"LAUNCH_supervisor: stopping pid {pid}")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
if pids:
    time.sleep(2)
    for pid in set(pids):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
PY
}

case "$MODE" in
  start) ;;
  replace)
    stop_prior
    # Wait briefly for lock release
    for _ in 1 2 3 4 5; do
      if ! fuser "$LOCK" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    ;;
  *)
    echo "LAUNCH_supervisor: unknown mode $MODE (use start|replace)" >&2
    exit 2
    ;;
esac

# Exclusive lock held by a long-lived wrapper so start refuses concurrent launch.
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "LAUNCH_supervisor: lock held at $LOCK (another supervisor running). Use replace." >&2
  exit 10
fi

export LOOP_DIR PROJECT_ROOT
nohup bash "$SUPERVISOR" >>"$LOG" 2>&1 &
sppid=$!
echo "$sppid" >"$PIDFILE"
# Keep flock for as long as supervisor lives: wait on it while holding fd 9.
echo "supervisor pid $sppid"
echo "log:    $LOG"
echo "lock:   $LOCK"
echo "stop:   touch $LOOP_DIR/STOP_REQUESTED"
wait "$sppid"
rc=$?
rm -f "$PIDFILE"
exit "$rc"
