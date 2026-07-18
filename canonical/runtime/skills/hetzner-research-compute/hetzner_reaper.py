"""Detached reaper for the Hetzner lane -- the durable billing-stopper (plan section 6, Arm 2).

A powered-off Hetzner server STILL BILLS; only DELETE stops it. Cloud-init's dead-man's-switch
(Arm 1) can only power a server off, and the in-session `oneshot` / `down --orphans` teardown
dies with the agent session. This reaper closes that gap: it lists the driver's labelled
servers and DELETES any that should no longer exist.

CRITICAL EXECUTION MODEL (the documented lesson from this host): the reaper must run DETACHED
-- a systemd timer or cron entry, never a session child -- because background children started
inside an agent session are killed when the session restarts, and a dead reaper is a server
that bills forever. The deploy templates in assets/ (hetzner-reaper.{service,timer,crontab})
and assets/reaper-install.md install it as a system-managed unit. This module is only the
worker each firing runs; installation is a gated, deploy-time action performed outside this
repo.

A server is reaped when ANY of these hold:
  * past-TTL         -- alive longer than its `ttl` label (fallback: max_server_hours).
  * powered-off      -- status is a stopped state; it bills but does no work, so DELETE it
                        (this is where cloud-init's power-off hands off to the reaper).
  * stale-heartbeat  -- carries a `heartbeat` label (epoch seconds) older than the threshold.
                        Absence of the label is not stale on its own; TTL / orphan cover that.
                        (The heartbeat WRITER -- a running job refreshing its label -- is an
                        optional future hook; the predicate is live now for servers that set it.)
  * orphaned         -- its `job-id` label is not in the local active-jobs ledger (its
                        controlling session finished, crashed, or died). Checked only when an
                        authoritative ledger is available.

Every hcloud call goes through hetzner_driver.run_hcloud, so it shares the driver's single
mockable COMMAND_RUNNER hook (offline tests never provision), env-only token handling (never
argv), and output redaction. Each delete writes a redacted audit record and reconciles the
job's budget reservation.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _bootstrap_path() -> None:
    """Put the broker workspace and this skill dir on sys.path so a detached invocation
    (`python3 hetzner_reaper.py`, `-m hetzner_reaper`, or a systemd ExecStart) can import
    research_compute and hetzner_driver without the shell wrapper. A no-op when the test
    harness or wrapper has already set the path."""
    skill_dir = Path(__file__).resolve().parent
    workspace_root = skill_dir.parent.parent
    for entry in (str(workspace_root), str(skill_dir)):
        if entry not in sys.path:
            sys.path.insert(0, entry)


_bootstrap_path()

import hetzner_driver  # noqa: E402
from research_compute import budget_ledger  # noqa: E402
from research_compute.config import default_config_path, load_config, workspace_root  # noqa: E402

MANAGED_BY = hetzner_driver.MANAGED_BY

# Hetzner server statuses that still bill but do no work -> DELETE, never "stop".
POWERED_OFF_STATES = {"off", "stopped", "stopping"}

# Default stale-heartbeat threshold (seconds). Overridable per invocation (--heartbeat-max-minutes).
DEFAULT_HEARTBEAT_MAX_SECONDS = 900.0

# Default reaper cadence for the self-looping daemon variant (seconds); the systemd .timer
# variant sets the cadence in the unit instead and runs a single pass per firing.
DEFAULT_INTERVAL_SECONDS = 120.0


class HetznerReaperError(RuntimeError):
    pass


# --- timestamp + label parsing (pure, deterministic) --------------------------

def _parse_ts(value: Any) -> float | None:
    """Parse an hcloud `created` timestamp (ISO 8601, possibly Z-suffixed) or a raw epoch
    number into epoch seconds. Returns None when it cannot be parsed."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _parse_ttl_hours(value: Any, default_hours: float) -> float:
    """Parse a `ttl` label into hours. Accepts `6h`, `90m`, `3600s`, or a bare number of
    hours; falls back to `default_hours` when absent or unparseable."""
    if value is None:
        return float(default_hours)
    text = str(value).strip().lower()
    if not text:
        return float(default_hours)
    try:
        if text.endswith("h"):
            return float(text[:-1])
        if text.endswith("m"):
            return float(text[:-1]) / 60.0
        if text.endswith("s"):
            return float(text[:-1]) / 3600.0
        return float(text)
    except ValueError:
        return float(default_hours)


def reap_reasons(server: dict[str, Any], *, now: float, active_job_ids: set[str] | None,
                 default_ttl_hours: float, heartbeat_max_seconds: float) -> list[str]:
    """Return the list of reasons this server must be deleted (empty = keep). Pure function
    over one hcloud server record, so the whole predicate is unit-testable without any call."""
    reasons: list[str] = []
    labels = server.get("labels") or {}
    status = str(server.get("status") or "").lower()

    # powered-off: still billing, doing nothing.
    if status in POWERED_OFF_STATES:
        reasons.append("powered_off")

    # past-TTL: alive longer than its labelled TTL (or the configured default).
    created = _parse_ts(server.get("created"))
    ttl_hours = _parse_ttl_hours(labels.get("ttl"), default_ttl_hours)
    if created is not None and ttl_hours > 0 and (now - created) > ttl_hours * 3600.0:
        reasons.append("past_ttl")

    # stale-heartbeat: only when the server actually carries a heartbeat label.
    heartbeat = _parse_ts(labels.get("heartbeat"))
    if heartbeat is not None and (now - heartbeat) > heartbeat_max_seconds:
        reasons.append("stale_heartbeat")

    # orphaned: job-id not in the authoritative active-jobs ledger.
    if active_job_ids is not None:
        job_id = labels.get("job-id")
        if not job_id or str(job_id) not in active_job_ids:
            reasons.append("orphaned")

    return reasons


# --- hcloud interaction (through the driver's mockable runner) -----------------

def list_managed_servers() -> list[dict[str, Any]]:
    """List every server carrying the managed-by label, as hcloud JSON records."""
    result = hetzner_driver.run_hcloud(
        ["server", "list", "--selector", f"managed-by={MANAGED_BY}", "-o", "json"])
    try:
        servers = json.loads(result.get("stdout") or "[]")
    except json.JSONDecodeError as exc:
        raise HetznerReaperError(f"could not parse hcloud server list: {exc}") from exc
    return list(servers) if isinstance(servers, list) else []


def _server_ident(server: dict[str, Any]) -> str | None:
    ident = server.get("id") or server.get("name")
    return str(ident) if ident else None


def _delete(server: dict[str, Any], *, reasons: list[str], event: str,
            state_root: Path | None) -> dict[str, Any] | None:
    """DELETE one server, write a redacted audit record, and release its budget reservation."""
    from hetzner_audit import append as audit_append

    ident = _server_ident(server)
    if ident is None:
        return None
    labels = server.get("labels") or {}
    hetzner_driver.run_hcloud(["server", "delete", ident])
    audit_append(state_root, {
        "event": event, "server": ident, "name": server.get("name"),
        "job_id": labels.get("job-id"), "labels": labels, "reasons": reasons,
        "reason": ",".join(reasons) or event, "real_eur": None,
    }, token=hetzner_driver._token())
    job_id = labels.get("job-id")
    if state_root is not None and job_id:
        try:
            budget_ledger.reconcile(Path(state_root), "hetzner", str(job_id), None)
        except Exception:  # noqa: BLE001 - reconciliation is best-effort at teardown
            pass
    return {"server": ident, "name": server.get("name"), "reasons": reasons}


# --- reap + kill-switch -------------------------------------------------------

def reap(*, config: Any = None, state_root: Path | None = None, dry_run: bool = False,
         now: float | None = None, heartbeat_max_seconds: float = DEFAULT_HEARTBEAT_MAX_SECONDS,
         ) -> dict[str, Any]:
    """One reap pass: list managed servers and DELETE every one matching the reap predicate.

    `dry_run` lists and evaluates but deletes nothing (it prints the plan). Orphan detection is
    active only when `state_root` points at a readable ledger; without it the reaper still
    enforces TTL, powered-off, and stale-heartbeat. Not gated on `hetzner_enabled`: the reaper
    is a safety net that must clean up even after the lane is turned off."""
    now = time.time() if now is None else float(now)
    default_ttl = float(getattr(config, "hetzner_max_server_hours", 6.0) or 6.0)
    active = budget_ledger.reserved_job_ids(Path(state_root), "hetzner") if state_root is not None else None

    servers = list_managed_servers()
    deleted: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    for server in servers:
        reasons = reap_reasons(
            server, now=now, active_job_ids=active,
            default_ttl_hours=default_ttl, heartbeat_max_seconds=heartbeat_max_seconds)
        if not reasons:
            continue
        ident = _server_ident(server)
        planned.append({"server": ident, "name": server.get("name"), "reasons": reasons})
        if not dry_run:
            record = _delete(server, reasons=reasons, event="reap", state_root=state_root)
            if record is not None:
                deleted.append(record)

    return {
        "action": "reap", "scanned": len(servers), "dry_run": dry_run,
        "planned": planned, "deleted": deleted, "kept": len(servers) - len(planned),
    }


def kill_switch(*, config: Any = None, state_root: Path | None = None,
                dry_run: bool = False) -> dict[str, Any]:
    """Emergency kill switch (plan section 6, Arm 3): DELETE every managed server immediately,
    ignoring the reap predicate. The standalone peer of the driver's `down --all`, callable
    detached (systemd/cron/manual) without an agent session."""
    servers = list_managed_servers()
    killed: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    for server in servers:
        ident = _server_ident(server)
        if ident is None:
            continue
        planned.append({"server": ident, "name": server.get("name")})
        if not dry_run:
            record = _delete(server, reasons=["kill_switch"], event="kill_switch", state_root=state_root)
            if record is not None:
                killed.append(record)
    return {"action": "kill_switch", "scanned": len(servers), "dry_run": dry_run,
            "planned": planned, "killed": killed}


# --- config loading + CLI -----------------------------------------------------

def _load_config(config_path_arg: str | None) -> tuple[Any | None, Path | None]:
    root = workspace_root()
    config_path = Path(config_path_arg).expanduser().resolve() if config_path_arg else default_config_path(root)
    if not config_path.exists():
        return None, None
    config = load_config(config_path)
    return config, config.state_root(root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hetzner-reaper",
        description="Detached Hetzner reaper: DELETE past-TTL / powered-off / stale / orphaned servers.",
    )
    parser.add_argument("--config", default=None, help="Path to research-compute.toml")
    sub = parser.add_subparsers(dest="command")

    reap_p = sub.add_parser("reap", help="One reap pass (default). Use --loop for the daemon variant.")
    reap_p.add_argument("--dry-run", action="store_true", help="List + evaluate, delete nothing")
    reap_p.add_argument("--loop", action="store_true",
                        help="Run forever, one pass per --interval (Type=simple/Restart=always variant)")
    reap_p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS,
                        help="Seconds between passes in --loop mode")
    reap_p.add_argument("--heartbeat-max-minutes", type=float,
                        default=DEFAULT_HEARTBEAT_MAX_SECONDS / 60.0,
                        help="Stale-heartbeat threshold in minutes")

    kill_p = sub.add_parser("kill", help="Kill switch: DELETE every managed server now")
    kill_p.add_argument("--dry-run", action="store_true", help="List, delete nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "reap"
    try:
        config, state_root = _load_config(args.config)
        if command == "kill":
            result = kill_switch(config=config, state_root=state_root, dry_run=args.dry_run)
        elif getattr(args, "loop", False):
            heartbeat_max = float(args.heartbeat_max_minutes) * 60.0
            while True:  # pragma: no cover - daemon loop; a single pass is tested directly
                result = reap(config=config, state_root=state_root, dry_run=args.dry_run,
                              heartbeat_max_seconds=heartbeat_max)
                print(json.dumps({"ok": True, **result}))
                sys.stdout.flush()
                time.sleep(max(1.0, float(args.interval)))
        else:
            heartbeat_max = float(args.heartbeat_max_minutes) * 60.0
            result = reap(config=config, state_root=state_root, dry_run=args.dry_run,
                          heartbeat_max_seconds=heartbeat_max)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": hetzner_driver._redact(str(exc))}, indent=2))
        return 1
    print(json.dumps({"ok": True, **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
