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
import os, signal, subprocess, time
from pathlib import Path
loop = os.environ["LOOP_DIR"]
needle = f"drive --dir {loop}"


def cmdlines():
    """Return (pid, cmdline) pairs from /proc, or from ps where /proc is absent."""
    proc_root = Path("/proc")
    if proc_root.is_dir():
        pairs = []
        for proc in proc_root.iterdir():
            if not proc.name.isdigit():
                continue
            try:
                raw = (proc / "cmdline").read_bytes()
            except Exception:
                continue
            pairs.append((int(proc.name), raw.replace(b"\0", b" ").decode(errors="ignore")))
        return pairs
    # macOS/BSD and some containers carry no procfs.
    try:
        out = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception:
        return []
    pairs = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        head, _, rest = line.partition(" ")
        if not head.isdigit():
            continue
        pairs.append((int(head), rest.strip()))
    return pairs


pids = []
for pid, cmd in cmdlines():
    if "autonomous_research_loop_runtime.py" in cmd and needle in cmd:
        pids.append(pid)
    if "arl_drive_supervisor.sh" in cmd and loop in cmd:
        pids.append(pid)
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
    # Wait briefly for lock release. fuser(1) is util-linux/psmisc, so skip the
    # wait rather than treat "not installed" as "lock already free".
    if command -v fuser >/dev/null 2>&1; then
      for _ in 1 2 3 4 5; do
        if ! fuser "$LOCK" >/dev/null 2>&1; then
          break
        fi
        sleep 1
      done
    else
      sleep 2
    fi
    ;;
  *)
    echo "LAUNCH_supervisor: unknown mode $MODE (use start|replace)" >&2
    exit 2
    ;;
esac

# Exclusive lock held by a long-lived wrapper so start refuses concurrent launch.
exec 9>"$LOCK"
# No flock(1) on macOS/BSD: flock(2) on the inherited fd 9 locks the shared open
# file description, so the lock still belongs to this shell after python3 exits.
# A locking error that is not contention must not be reported as contention.
if command -v flock >/dev/null 2>&1; then
  flock -n 9
  lock_rc=$?
else
  python3 -c '
import errno, fcntl, sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError as exc:
    if exc.errno in (errno.EACCES, errno.EAGAIN):
        sys.exit(1)
    sys.stderr.write(f"LAUNCH_supervisor: cannot lock: {exc}\n")
    sys.exit(3)
'
  lock_rc=$?
fi
if [[ "$lock_rc" -eq 3 ]]; then
  exit 3
fi
if [[ "$lock_rc" -ne 0 ]]; then
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
