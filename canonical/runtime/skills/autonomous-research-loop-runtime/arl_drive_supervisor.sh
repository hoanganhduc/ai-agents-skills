#!/usr/bin/env bash
# Credit/auth failover supervisor around ARL headless drive.
#
# Stock drive is single --provider. This outer loop restarts drive under the
# next primary in failover.json primary_order on exit 5/6/7, session-excludes
# dead primaries (no infinite wrap thrash), and exits operationally when none
# remain. Secrets: inherit env only (never load token files here).
#
# Exit codes (supervisor):
#   0  loop done / STOP_REQUESTED / clean stop
#   2  configuration error
#  10  lock held (start refused) — set by LAUNCH_supervisor.sh
#  11  all_primaries_exhausted
#  12  restart_cap_reached
#  13  resource_cleanup_unverified (non-retryable safety stop)
#  14  candidate_quarantined (non-retryable safety stop)
#  15  quarantine_persistence_unverified (non-retryable safety stop)
set -uo pipefail

SUPERVISOR_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOOP_DIR="${LOOP_DIR:-}"
PROJECT_ROOT="${PROJECT_ROOT:-}"
FAILOVER_JSON="${FAILOVER_JSON:-}"
DRIVE_EXTRA_ARGS="${DRIVE_EXTRA_ARGS:-}"
RUNTIME_PY="${RUNTIME_PY:-}"
SYNC_PANEL_PY="${SYNC_PANEL_PY:-$SUPERVISOR_DIR/sync_panel_exclude.py}"

usage() {
  cat <<'EOF' >&2
Usage: LOOP_DIR=... PROJECT_ROOT=... arl_drive_supervisor.sh

Optional env:
  FAILOVER_JSON   path to failover.json (default: $LOOP_DIR/failover.json)
  RUNTIME_PY      path to autonomous_research_loop_runtime.py
  AAS_RUNTIME_ROOT  used to locate runtime if RUNTIME_PY unset
  DRIVE_EXTRA_ARGS  extra args appended to every drive invocation
EOF
}

if [[ -z "${LOOP_DIR}" ]]; then
  usage
  exit 2
fi
LOOP_DIR="$(CDPATH= cd -- "$LOOP_DIR" && pwd)"
if [[ -z "${PROJECT_ROOT}" ]]; then
  PROJECT_ROOT="$(dirname "$LOOP_DIR")"
fi
PROJECT_ROOT="$(CDPATH= cd -- "$PROJECT_ROOT" && pwd)"
FAILOVER_JSON="${FAILOVER_JSON:-$LOOP_DIR/failover.json}"

if [[ -z "${RUNTIME_PY}" ]]; then
  if [[ -n "${AAS_RUNTIME_ROOT:-}" && -f "${AAS_RUNTIME_ROOT}/workspace/skills/autonomous-research-loop-runtime/autonomous_research_loop_runtime.py" ]]; then
    RUNTIME_PY="${AAS_RUNTIME_ROOT}/workspace/skills/autonomous-research-loop-runtime/autonomous_research_loop_runtime.py"
  elif [[ -f "$SUPERVISOR_DIR/autonomous_research_loop_runtime.py" ]]; then
    RUNTIME_PY="$SUPERVISOR_DIR/autonomous_research_loop_runtime.py"
  else
    echo "supervisor: cannot locate autonomous_research_loop_runtime.py" >&2
    exit 2
  fi
fi

# Load failover config via Python (JSON + defaults).
_CFG_OUT="$(mktemp)"
if ! python3 - "$FAILOVER_JSON" "$LOOP_DIR" "$_CFG_OUT" <<'PY'
import json, shlex, sys
from pathlib import Path, PurePosixPath
path = Path(sys.argv[1])
loop = Path(sys.argv[2])
out = Path(sys.argv[3])
data = {}
if path.is_file():
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"supervisor: bad failover.json: {exc}", file=sys.stderr)
        sys.exit(2)
if not data.get("primary_order") and data.get("primary_fallback"):
    data["primary_order"] = data["primary_fallback"]
order = data.get("primary_order") or []
if not isinstance(order, list) or not order:
    print("supervisor: primary_order empty in failover.json", file=sys.stderr)
    sys.exit(2)
order = [str(x).strip() for x in order if str(x).strip()]
max_waits = int(data.get("max_quota_waits_per_primary", 3) or 0)
if len(order) > 1 and max_waits == 0:
    print("supervisor: refuse multi-primary with max_quota_waits_per_primary=0", file=sys.stderr)
    sys.exit(2)
dd = data.get("drive_defaults") if isinstance(data.get("drive_defaults"), dict) else {}
title = str(data.get("research_title") or data.get("notify_title") or data.get("display_name") or "").strip()
if not title:
    title = loop.name

def safe_state_path(value, approved):
    raw = str(value or approved)
    if (
        not raw
        or len(raw) > 240
        or raw.startswith("/")
        or "\\" in raw
        or any(ord(char) < 32 or ord(char) == 127 for char in raw)
    ):
        raise ValueError(f"unsafe supervisor state path: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} or len(part) > 96 for part in path.parts
    ):
        raise ValueError(f"unsafe supervisor state path: {raw!r}")
    if any(
        not all(char.isascii() and (char.isalnum() or char in "._-") for char in part)
        for part in path.parts
    ):
        raise ValueError(f"unsafe supervisor state path: {raw!r}")
    normalized = str(path)
    if normalized != approved:
        raise ValueError(
            f"supervisor state path must be the sanctioned path {approved!r}"
        )
    return normalized

try:
    primary_path = safe_state_path(data.get('write_active_primary_path'), 'driver/PRIMARY')
    excluded_path = safe_state_path(data.get('session_exclude_path'), 'driver/EXCLUDED')
except ValueError as exc:
    print(f"supervisor: {exc}", file=sys.stderr)
    sys.exit(2)
lines = [
    f"PRIMARY_ORDER=({' '.join(shlex.quote(x) for x in order)})",
    f"MAX_QUOTA_WAITS={max_waits}",
    f"MAX_RESTARTS={int(data.get('max_restarts', 50))}",
    f"FAILURES_BEFORE_ROTATE={int(data.get('failures_before_rotate', 3))}",
    f"RETRY_SLEEP_S={int(data.get('retry_sleep_s', 300))}",
    f"ROTATE_COOLDOWN_S={int(data.get('rotate_cooldown_s', 30))}",
    f"PRIMARY_PATH={shlex.quote(primary_path)}",
    f"EXCLUDED_PATH={shlex.quote(excluded_path)}",
    f"EXCLUDE_TTL_S={int(data.get('session_exclude_ttl_s', 21600))}",
    f"SYNC_PANEL={'1' if data.get('sync_panel_exclude_until_credit', True) else '0'}",
    f"PANEL={shlex.quote(str(dd.get('panel', 'on')))}",
    f"NOTIFY={shlex.quote(str(dd.get('notify', 'auto')))}",
    f"ITERATION_TIMEOUT={int(dd.get('iteration_timeout', 7200))}",
    f"MAX_FAILURES={int(dd.get('max_failures', 3))}",
    f"MAX_REVIEW_WAITS={int(dd.get('max_review_waits', 0))}",
    f"RESEARCH_TITLE={shlex.quote(title)}",
]
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
then
  rm -f "$_CFG_OUT"
  exit 2
fi
# shellcheck disable=SC1090
source "$_CFG_OUT"
rm -f "$_CFG_OUT"

safe_state_file() {
  local action="$1"
  local relative_path="$2"
  shift 2
  python3 - "$LOOP_DIR" "$relative_path" "$action" "$@" <<'PY'
import os, secrets, stat, sys
from pathlib import Path, PurePosixPath

loop = Path(sys.argv[1])
raw_relative = sys.argv[2]
action = sys.argv[3]
values = sys.argv[4:]

if not loop.is_absolute() or loop == Path(loop.anchor or os.sep):
    raise SystemExit("supervisor: loop directory must be an absolute non-root path")

relative = PurePosixPath(raw_relative)
if (
    not raw_relative
    or len(raw_relative) > 240
    or raw_relative.startswith("/")
    or "\\" in raw_relative
    or relative.is_absolute()
    or not relative.parts
    or any(part in {"", ".", ".."} or len(part) > 96 for part in relative.parts)
    or any(ord(char) < 32 or ord(char) == 127 for char in raw_relative)
):
    raise SystemExit("supervisor: unsafe state path")
if raw_relative not in {"driver/PRIMARY", "driver/EXCLUDED"}:
    raise SystemExit("supervisor: state path is not sanctioned")
if (
    (action == "write-raw" and raw_relative != "driver/PRIMARY")
    or (action in {"read", "write-lines"} and raw_relative != "driver/EXCLUDED")
):
    raise SystemExit("supervisor: state action/path combination is not sanctioned")

dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
fd = os.open(loop.anchor or os.sep, dir_flags)
try:
    for component in loop.parts[1:]:
        next_fd = os.open(component, dir_flags, dir_fd=fd)
        os.close(fd)
        fd = next_fd

    loop_info = os.fstat(fd)
    if not stat.S_ISDIR(loop_info.st_mode) or loop_info.st_uid != os.geteuid():
        raise OSError("supervisor loop directory has an unexpected owner or type")
    for component in relative.parts[:-1]:
        try:
            next_fd = os.open(component, dir_flags, dir_fd=fd)
        except FileNotFoundError:
            if action == "read":
                raise SystemExit(0)
            os.mkdir(component, 0o700, dir_fd=fd)
            next_fd = os.open(component, dir_flags, dir_fd=fd)
        os.close(fd)
        fd = next_fd

    leaf = relative.parts[-1]
    if action == "read":
        try:
            file_fd = os.open(
                leaf,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=fd,
            )
        except FileNotFoundError:
            raise SystemExit(0)
        try:
            info = os.fstat(file_fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != loop_info.st_uid
                or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                or info.st_size > 1_000_000
            ):
                raise OSError("supervisor state input is unsafe or oversized")
            payload = os.read(file_fd, 1_000_001)
            if len(payload) > 1_000_000:
                raise OSError("supervisor state input is oversized")
        finally:
            os.close(file_fd)
        sys.stdout.buffer.write(payload)
        raise SystemExit(0)

    if action == "write-raw":
        payload = (values[0] if values else "").encode("utf-8")
    elif action == "write-lines":
        payload = (("\n".join(values) + "\n") if values else "").encode("utf-8")
    else:
        raise OSError(f"unsupported state action: {action}")
    try:
        existing = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if (
            stat.S_ISLNK(existing.st_mode)
            or not stat.S_ISREG(existing.st_mode)
            or existing.st_nlink != 1
            or existing.st_uid != loop_info.st_uid
            or existing.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise OSError("supervisor state destination is not a regular file")
    temp = f".{leaf}.{os.getpid()}.{secrets.token_hex(12)}.tmp"
    temp_fd = os.open(
        temp,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=fd,
    )
    try:
        with os.fdopen(temp_fd, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(temp_fd)
    try:
        os.replace(temp, leaf, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(temp, dir_fd=fd)
        except FileNotFoundError:
            pass
finally:
    os.close(fd)
PY
}

notify() {
  # Do not echo the user-controlled title or message before Notify v2 has
  # applied recursive secret/PII redaction. Service logs receive only a fixed
  # operational marker.
  printf '%s supervisor: structured notification emitted\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >&2
  # Best-effort structured notification. The runtime resolves the stable title
  # and topic and always renders Goal / Completed / Current / Plan.
  python3 "$RUNTIME_PY" notify-event \
    --dir "$LOOP_DIR" \
    --event supervisor \
    --completed "$1" \
    --current "$1" \
    --plan "Continue under the supervisor's active failover and loop-control policy." \
    --iteration-status not_applicable \
    --provider "${provider:-}" \
    --driver-agent "${provider:-}" \
    --notify "$NOTIFY" \
    --quiet >/dev/null 2>&1 || true
}

loop_is_done() {
  python3 "$RUNTIME_PY" done --dir "$LOOP_DIR" 2>/dev/null | grep -q '"done": true'
}

write_primary() {
  local p="$1"
  safe_state_file write-raw "$PRIMARY_PATH" "$p" || {
    echo "supervisor: could not safely write active-primary state" >&2
    exit 2
  }
}

# Each line is `name` or `name<TAB>epoch`. EXCLUDED holds bare names, so every
# other helper is unchanged; EXCLUDED_AT holds the stamp at the same index.
load_excluded() {
  EXCLUDED=()
  EXCLUDED_AT=()
  local content line name stamp now
  now="$(date -u +%s)"
  content="$(safe_state_file read "$EXCLUDED_PATH")" || {
    echo "supervisor: could not safely read session-exclude state" >&2
    exit 2
  }
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line//$'\r'/}"
    [[ -z "$line" ]] && continue
    name="${line%%$'\t'*}"
    [[ -z "$name" ]] && continue
    stamp="${line#*$'\t'}"
    # A legacy bare-name file has no stamp: treat it as excluded from now, so
    # an existing file degrades to one more TTL window rather than to forever.
    if [[ "$stamp" == "$line" || ! "$stamp" =~ ^[0-9]+$ ]]; then
      stamp="$now"
    fi
    if (( EXCLUDE_TTL_S > 0 && now - stamp > EXCLUDE_TTL_S )); then
      continue
    fi
    EXCLUDED+=("$name")
    EXCLUDED_AT+=("$stamp")
  done <<<"$content"
}

save_excluded() {
  local i lines=()
  for i in "${!EXCLUDED[@]}"; do
    lines+=("${EXCLUDED[$i]}"$'\t'"${EXCLUDED_AT[$i]}")
  done
  safe_state_file write-lines "$EXCLUDED_PATH" "${lines[@]}" || {
    echo "supervisor: could not safely write session-exclude state" >&2
    exit 2
  }
}

is_excluded() {
  local p="$1" e
  for e in "${EXCLUDED[@]:-}"; do
    [[ "$e" == "$p" ]] && return 0
  done
  return 1
}

session_exclude() {
  local p="$1"
  if is_excluded "$p"; then
    return 0
  fi
  EXCLUDED+=("$p")
  EXCLUDED_AT+=("$(date -u +%s)")
  save_excluded
  if [[ "$SYNC_PANEL" == "1" && -f "$SYNC_PANEL_PY" ]]; then
    # Keep helper output suppressed — notify() is the pack's redaction-aware
    # channel — but do not let the one silent rotation-path failure stay silent.
    if ! python3 "$SYNC_PANEL_PY" --dir "$LOOP_DIR" --provider "$p" >/dev/null 2>&1; then
      notify "panel exclude sync failed for $p; panel dispatch may still invite this provider."
    fi
  fi
}

# First non-excluded entry in PRIMARY_ORDER (priority list order; no wrap-from-current).
# After a failure we session-exclude the dead primary, so the next pick is the
# first *available* name still in the list.
first_available_provider() {
  local cand
  local n=${#PRIMARY_ORDER[@]}
  (( n == 0 )) && return 1
  for cand in "${PRIMARY_ORDER[@]}"; do
    if ! is_excluded "$cand"; then
      printf '%s' "$cand"
      return 0
    fi
  done
  return 1
}

load_excluded
restarts=0
consecutive_failures=0
last_provider=""
child_pid=""

cleanup_child() {
  if [[ -n "${child_pid}" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    sleep 1
    kill -KILL "$child_pid" 2>/dev/null || true
  fi
}
trap 'cleanup_child; exit 130' INT TERM HUP

while :; do
  if [[ -f "$LOOP_DIR/STOP_REQUESTED" ]]; then
    notify "STOP_REQUESTED present; supervisor exiting."
    exit 0
  fi
  if loop_is_done; then
    notify "stop condition met; supervisor exiting."
    exit 0
  fi

  provider="$(first_available_provider || true)"
  if [[ -z "${provider}" ]]; then
    notify "all primaries exhausted (session-excluded: ${EXCLUDED[*]:-none})."
    exit 11
  fi
  # Soft-failure streak is per primary; reset when the active primary changes.
  if [[ "$provider" != "${last_provider:-}" ]]; then
    consecutive_failures=0
    last_provider="$provider"
  fi

  write_primary "$provider"
  # Do not remote-notify here: drive emits drive_start, which is the single
  # start-class remote event (avoids supervisor+drive_start double posts).

  # shellcheck disable=SC2086
  python3 "$RUNTIME_PY" drive \
    --dir "$LOOP_DIR" \
    --root "$PROJECT_ROOT" \
    --provider "$provider" \
    --panel "$PANEL" \
    --notify "$NOTIFY" \
    --iteration-timeout "$ITERATION_TIMEOUT" \
    --max-failures "$MAX_FAILURES" \
    --max-quota-waits "$MAX_QUOTA_WAITS" \
    --max-review-waits "$MAX_REVIEW_WAITS" \
    $DRIVE_EXTRA_ARGS &
  child_pid=$!
  wait "$child_pid"
  rc=$?
  child_pid=""

  if [[ -f "$LOOP_DIR/STOP_REQUESTED" ]]; then
    notify "STOP_REQUESTED present after drive (last primary: $provider)."
    exit 0
  fi
  if loop_is_done; then
    notify "stop condition met under primary $provider."
    exit 0
  fi

  case "$rc" in
    0)
      notify "driver exited 0 under $provider but loop not done; restarting same primary."
      consecutive_failures=0
      ;;
    5|6|7)
      # Exit 5 = quota_wait_exhausted after N consecutive quota signals (default N=3):
      # treat as temporary quota_or_credit exclude, then first available primary.
      case "$rc" in
        5) notify "quota/credit exhausted for $provider (N consecutive); exclude as quota_or_credit and switch to first available." ;;
        6) notify "provider $provider unavailable; exclude and use first available in list." ;;
        7) notify "auth/session dead for $provider; exclude and use first available in list." ;;
      esac
      session_exclude "$provider"
      consecutive_failures=0
      sleep "$ROTATE_COOLDOWN_S"
      ;;
    3)
      # Drive already hit max-failures (default 3 consecutive non-quota fails).
      # Treat as temporary exclude and switch immediately (one drive death = streak done).
      notify "driver max-failures under $provider (exit 3); exclude temporarily and switch to first available."
      session_exclude "$provider"
      consecutive_failures=0
      sleep "$ROTATE_COOLDOWN_S"
      ;;
    4)
      consecutive_failures=$((consecutive_failures + 1))
      notify "driver exit $rc under $provider (failure $consecutive_failures/$FAILURES_BEFORE_ROTATE)."
      if [[ "$consecutive_failures" -ge "$FAILURES_BEFORE_ROTATE" ]]; then
        notify "too many runtime errors under $provider; exclude and use first available in list."
        session_exclude "$provider"
        consecutive_failures=0
        sleep "$ROTATE_COOLDOWN_S"
      else
        sleep "$RETRY_SLEEP_S"
      fi
      ;;
    2)
      notify "driver configuration error (exit 2). Supervisor exiting."
      exit 2
      ;;
    8)
      notify "provider resource cleanup could not be verified; supervisor is stopping without retry or failover."
      exit 13
      ;;
    9)
      notify "a failed provider completion was quarantined; supervisor is stopping for explicit inspection."
      exit 14
      ;;
    10)
      notify "failed-completion quarantine could not be persisted; supervisor is stopping without retry or failover."
      exit 15
      ;;
    *)
      notify "driver exited $rc under $provider (unclassified); retrying in ${RETRY_SLEEP_S}s."
      sleep "$RETRY_SLEEP_S"
      ;;
  esac

  restarts=$((restarts + 1))
  if [[ "$restarts" -ge "$MAX_RESTARTS" ]]; then
    notify "restart cap ($MAX_RESTARTS) reached; supervisor exiting."
    exit 12
  fi
done
