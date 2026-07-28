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
from pathlib import Path
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
lines = [
    f"PRIMARY_ORDER=({' '.join(shlex.quote(x) for x in order)})",
    f"MAX_QUOTA_WAITS={max_waits}",
    f"MAX_RESTARTS={int(data.get('max_restarts', 50))}",
    f"FAILURES_BEFORE_ROTATE={int(data.get('failures_before_rotate', 3))}",
    f"RETRY_SLEEP_S={int(data.get('retry_sleep_s', 300))}",
    f"ROTATE_COOLDOWN_S={int(data.get('rotate_cooldown_s', 30))}",
    f"PRIMARY_PATH={shlex.quote(str(data.get('write_active_primary_path') or 'driver/PRIMARY'))}",
    f"EXCLUDED_PATH={shlex.quote(str(data.get('session_exclude_path') or 'driver/EXCLUDED'))}",
    f"SYNC_PANEL={'1' if data.get('sync_panel_exclude_until_credit', True) else '0'}",
    f"PANEL={shlex.quote(str(dd.get('panel', 'on')))}",
    f"NOTIFY={shlex.quote(str(dd.get('notify', 'auto')))}",
    f"ITERATION_TIMEOUT={int(dd.get('iteration_timeout', 7200))}",
    f"MAX_FAILURES={int(dd.get('max_failures', 10))}",
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

mkdir -p "$LOOP_DIR/driver" "$LOOP_DIR/driver_logs"

notify() {
  local msg="[$RESEARCH_TITLE] $1"
  printf '%s supervisor: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$msg" >&2
  # Best-effort remote-bridge when available; never fail the supervisor.
  if [[ -n "${AAS_RUNTIME_ROOT:-}" && -x "${AAS_RUNTIME_ROOT}/workspace/skills/remote-bridge/run_remote_bridge.sh" ]]; then
    bash "${AAS_RUNTIME_ROOT}/workspace/skills/remote-bridge/run_remote_bridge.sh" \
      send --channel both --text "$msg" >/dev/null 2>&1 || true
  fi
}

loop_is_done() {
  python3 "$RUNTIME_PY" done --dir "$LOOP_DIR" 2>/dev/null | grep -q '"done": true'
}

write_primary() {
  local p="$1"
  local path="$LOOP_DIR/$PRIMARY_PATH"
  mkdir -p "$(dirname "$path")"
  local tmp
  tmp="$(mktemp "${path}.XXXXXX")"
  printf '%s' "$p" >"$tmp"
  mv -f "$tmp" "$path"
}

load_excluded() {
  EXCLUDED=()
  local path="$LOOP_DIR/$EXCLUDED_PATH"
  if [[ -f "$path" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line//$'\r'/}"
      [[ -z "$line" ]] && continue
      EXCLUDED+=("$line")
    done <"$path"
  fi
}

save_excluded() {
  local path="$LOOP_DIR/$EXCLUDED_PATH"
  mkdir -p "$(dirname "$path")"
  local tmp
  tmp="$(mktemp "${path}.XXXXXX")"
  printf '%s\n' "${EXCLUDED[@]:-}" >"$tmp"
  mv -f "$tmp" "$path"
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
  save_excluded
  if [[ "$SYNC_PANEL" == "1" && -f "$SYNC_PANEL_PY" ]]; then
    python3 "$SYNC_PANEL_PY" --dir "$LOOP_DIR" --provider "$p" >/dev/null 2>&1 || true
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
  notify "driving with primary=$provider (first available; excluded: ${EXCLUDED[*]:-none})"

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
      case "$rc" in
        5) notify "quota/credit exhausted for $provider; exclude and use first available in list." ;;
        6) notify "provider $provider unavailable; exclude and use first available in list." ;;
        7) notify "auth/session dead for $provider; exclude and use first available in list." ;;
      esac
      session_exclude "$provider"
      consecutive_failures=0
      sleep "$ROTATE_COOLDOWN_S"
      ;;
    3|4)
      consecutive_failures=$((consecutive_failures + 1))
      notify "driver exit $rc under $provider (failure $consecutive_failures/$FAILURES_BEFORE_ROTATE)."
      if [[ "$consecutive_failures" -ge "$FAILURES_BEFORE_ROTATE" ]]; then
        notify "too many failures under $provider; exclude and use first available in list."
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
