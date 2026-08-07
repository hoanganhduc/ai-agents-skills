"""Detached reaper for the Hetzner lane -- the durable billing-stopper (plan section 6, Arm 2).

A powered-off Hetzner server STILL BILLS; only DELETE stops it. Cloud-init's dead-man's-switch
(Arm 1) can only power a server off, and the in-session `oneshot` / `down --orphans` teardown
dies with the agent session. This reaper closes that gap: it lists the driver's labelled
servers and DELETES any that should no longer exist.

CRITICAL EXECUTION MODEL (the documented lesson from this host): the reaper must run DETACHED
-- a systemd timer or cron entry, never a session child -- because background children started
inside an agent session are killed when the session restarts, and a dead reaper is a server
that bills forever. The skill's reaper-deployment reference provides manual systemd and cron
templates. This module is only the worker each firing runs; installation is a gated,
deploy-time action performed outside this repo.

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
argv), and output redaction. Each successful delete independently attempts a redacted audit
record and reconciles the job's budget reservation; either post-delete failure is reported
without skipping later servers.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _bootstrap_path() -> None:
    """Put the broker workspace and this skill dir on sys.path so a detached invocation
    (`python3 hetzner_reaper.py`, `-m hetzner_reaper`, or a systemd ExecStart) can import
    research_compute and hetzner_driver without the shell wrapper. A no-op when the test
    harness or wrapper has already set the path. Installed runtimes keep the package at
    the runtime root beside ``skills/``; CSR's immutable exact-pin generation retains the
    canonical source layout, where the package lives one level deeper under
    ``workspace/``."""
    skill_dir = Path(__file__).resolve().parent
    workspace_root = skill_dir.parent.parent
    if not (workspace_root / "research_compute").is_dir():
        source_layout = workspace_root / "workspace"
        if (source_layout / "research_compute").is_dir():
            workspace_root = source_layout
    for entry in (str(workspace_root), str(skill_dir)):
        if entry not in sys.path:
            sys.path.insert(0, entry)


_bootstrap_path()

import hetzner_driver  # noqa: E402
import hetzner_research_compute  # noqa: E402
from research_compute import budget_ledger  # noqa: E402
from research_compute.config import default_config_path, load_config  # noqa: E402

# Hetzner server statuses that still bill but do no work -> DELETE, never "stop".
POWERED_OFF_STATES = {"off", "stopped", "stopping"}

# Default stale-heartbeat threshold (seconds). Overridable per invocation (--heartbeat-max-minutes).
DEFAULT_HEARTBEAT_MAX_SECONDS = 900.0

# Default reaper cadence for the self-looping daemon variant (seconds); the systemd .timer
# variant sets the cadence in the unit instead and runs a single pass per firing.
DEFAULT_INTERVAL_SECONDS = 120.0
PROJECT_WIDE_KILL_CONFIRMATION = "inventory-bound; run `kill --dry-run` to obtain it"


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

def list_managed_servers(config: Any) -> list[dict[str, Any]]:
    """List every AAS-managed server in the project, including old install scopes."""
    # Keep malformed local restore state fail-closed even though the safety selector itself is
    # stable across install-directory moves and reinstallations.
    hetzner_driver.install_scope(config)
    result = hetzner_driver.run_hcloud(
        ["server", "list", "--selector", hetzner_driver.managed_account_selector(), "-o", "json"])
    servers = hetzner_driver.parse_server_records(
        result.get("stdout"),
        context="hcloud reaper inventory",
    )
    return [
        server for server in servers
        if hetzner_driver.server_in_project_scope(server, config)
    ]


def write_reaper_lease(*, config: Any, scheduler_kind: str,
                       scheduler_id: str, now: float | None = None) -> dict[str, Any]:
    """Atomically publish short-lived scheduler evidence from a root-owned service/cron job."""
    if os.name != "posix" or os.geteuid() != 0:
        raise HetznerReaperError("reaper lease publication must run as root on POSIX")
    expected_scheduler = str(getattr(config, "hetzner_reaper_scheduler_id", None) or "")
    if scheduler_kind not in {"systemd", "cron"} or scheduler_id != expected_scheduler:
        raise HetznerReaperError("reaper scheduler identity does not match configuration")
    configured = str(getattr(config, "hetzner_reaper_lease_file", None) or "")
    if not configured or not Path(configured).is_absolute():
        raise HetznerReaperError("reaper_lease_file must be absolute")
    path = Path(configured)
    parent = path.parent
    hetzner_driver._require_root_protected_parent_chain(
        parent, label="reaper lease"
    )
    issued = time.time() if now is None else float(now)
    max_age = min(
        hetzner_driver.REAPER_LEASE_MAX_AGE_SECONDS,
        int(getattr(config, "hetzner_reaper_lease_max_age_seconds", 900) or 0),
    )
    if max_age <= 0:
        raise HetznerReaperError("reaper lease max age must be positive")
    payload = {
        "version": 1,
        "project_identity": hetzner_driver._project_identity(config),
        "install_scope": hetzner_driver.install_scope(config),
        "config_digest": hetzner_driver._reaper_config_digest(config),
        "scheduler": {"kind": scheduler_kind, "id": scheduler_id, "active": True},
        "issued_at": datetime.fromtimestamp(issued, tz=timezone.utc).isoformat(),
        "expires_at": datetime.fromtimestamp(issued + max_age, tz=timezone.utc).isoformat(),
    }
    temp = parent / f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(
        temp,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(fd, data[offset:])
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(temp, path)
        # The lease contains no credential material. It must be readable by the non-root
        # provisioner but writable only by root; the protected parent chain prevents
        # replacement through an ancestor.
        os.chmod(path, 0o644)
        directory_fd = os.open(
            parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    return payload


def _server_ident(server: dict[str, Any]) -> str | None:
    try:
        return hetzner_driver._exact_server_id(server)
    except hetzner_driver.HetznerDriverError:
        return None


def _refetch_managed_server(server: dict[str, Any], config: Any) -> dict[str, Any]:
    """Replace selector/list evidence with an exact provider-ID description."""
    ident = hetzner_driver._exact_server_id(server)
    return hetzner_driver.describe_server_exact(ident, config)


def _delete(server: dict[str, Any], *, reasons: list[str], event: str,
            state_root: Path | None, config: Any) -> dict[str, Any] | None:
    """DELETE one server, then independently audit and release its reservation."""
    from hetzner_audit import append as audit_append

    ident = _server_ident(server)
    if ident is None:
        return None
    labels = server.get("labels") or {}
    hetzner_driver.run_hcloud(["server", "delete", ident])
    errors: list[dict[str, str]] = []
    try:
        audit_append(state_root, {
            "event": event, "server": ident, "name": server.get("name"),
            "job_id": labels.get("job-id"), "labels": labels, "reasons": reasons,
            "reason": ",".join(reasons) or event, "real_eur": None,
        }, token=hetzner_driver._token())
    except Exception as exc:  # noqa: BLE001 - deletion already happened; reconciliation must run
        errors.append({
            "server": ident,
            "stage": "audit",
            "error": hetzner_driver._redact(str(exc)),
        })
    job_id = labels.get("job-id")
    if (
        state_root is not None
        and job_id
        and hetzner_driver.server_in_install_scope(server, config)
    ):
        try:
            budget_ledger.reconcile(Path(state_root), "hetzner", str(job_id), None)
        except Exception as exc:  # noqa: BLE001 - report but continue to later billable servers
            errors.append({
                "server": ident,
                "stage": "reconcile",
                "error": hetzner_driver._redact(str(exc)),
            })
    record = {"server": ident, "name": server.get("name"), "reasons": reasons}
    if errors:
        record["errors"] = errors
    return record


def _active_job_ids(state_root: Path | None) -> set[str] | None:
    if state_root is None:
        return None
    return budget_ledger.authoritative_reserved_job_ids(Path(state_root), "hetzner")


def _error_record(server: dict[str, Any], stage: str, exc: Exception) -> dict[str, str | None]:
    return {
        "server": _server_ident(server),
        "stage": stage,
        "error": hetzner_driver._redact(str(exc)),
    }


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
    # A server create is budget-reserved before it becomes visible. Listing first and reading
    # the ledger second means a just-created server cannot be judged against a stale pre-create
    # snapshot. We also re-read immediately before every deletion carrying the orphan reason.
    servers = list_managed_servers(config)
    active = _active_job_ids(state_root)
    deleted: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for server in servers:
        current_scope = hetzner_driver.server_in_install_scope(server, config)
        reasons = reap_reasons(
            server, now=now, active_job_ids=active if current_scope else None,
            default_ttl_hours=default_ttl, heartbeat_max_seconds=heartbeat_max_seconds)
        if not reasons:
            continue
        if dry_run:
            ident = _server_ident(server)
            planned.append({"server": ident, "name": server.get("name"), "reasons": reasons})
            continue
        try:
            latest = _refetch_managed_server(server, config)
            latest_scope = hetzner_driver.server_in_install_scope(latest, config)
            # Refresh the authoritative ledger after the exact provider refetch and use it
            # in the final predicate evaluation immediately before DELETE.
            latest_active = _active_job_ids(state_root) if latest_scope else None
            latest_reasons = reap_reasons(
                latest,
                now=now,
                active_job_ids=latest_active,
                default_ttl_hours=default_ttl,
                heartbeat_max_seconds=heartbeat_max_seconds,
            )
            if not latest_reasons:
                continue
            ident = _server_ident(latest)
            planned.append({
                "server": ident,
                "name": latest.get("name"),
                "reasons": latest_reasons,
            })
            record = _delete(
                latest,
                reasons=latest_reasons,
                event="reap",
                state_root=state_root,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 - one failure must not strand later servers
            errors.append(_error_record(server, "delete", exc))
            continue
        if record is not None:
            deleted.append(record)
            errors.extend(record.get("errors", []))

    return {
        "action": "reap", "scanned": len(servers), "dry_run": dry_run,
        "planned": planned, "deleted": deleted, "errors": errors,
        "kept": len(servers) - len(planned),
    }


def kill_switch(*, config: Any = None, state_root: Path | None = None,
                dry_run: bool = False,
                confirm_project_wide: str | None = None) -> dict[str, Any]:
    """Emergency kill switch (plan section 6, Arm 3): DELETE all AAS-managed servers,
    ignoring the reap predicate. The standalone peer of the driver's project-wide `down --all`,
    callable detached (systemd/cron/manual) without an agent session."""
    servers = list_managed_servers(config)
    project_identity = hetzner_driver._project_identity(config)
    target_count, target_digest = hetzner_driver._destruction_digest(
        servers, project_identity=project_identity
    )
    required_confirmation = hetzner_driver._project_delete_confirmation(
        project_identity, target_count, target_digest
    )
    if not dry_run and confirm_project_wide != required_confirmation:
        raise HetznerReaperError(
            "project-wide kill requires exact inventory-bound confirmation: "
            + required_confirmation
        )
    if not dry_run:
        # Broad deletion needs both inventory-bound human intent and current evidence that
        # the independent billing-stopper is really operating. A static environment string
        # or an agent-authored marker cannot satisfy this gate.
        hetzner_driver.REAPER_LEASE_VERIFIER(config)
    killed: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for server in servers:
        ident = _server_ident(server)
        if ident is None:
            continue
        planned.append({"server": ident, "name": server.get("name")})
        if not dry_run:
            try:
                latest = _refetch_managed_server(server, config)
                record = _delete(
                    latest, reasons=["kill_switch"], event="kill_switch",
                    state_root=state_root, config=config
                )
            except Exception as exc:  # noqa: BLE001 - continue through the scoped kill set
                errors.append(_error_record(server, "delete", exc))
                continue
            if record is not None:
                killed.append(record)
                errors.extend(record.get("errors", []))
    return {
        "action": "kill_switch", "scanned": len(servers), "dry_run": dry_run,
        "planned": planned, "killed": killed, "errors": errors,
        "project_identity": project_identity, "target_count": target_count,
        "target_digest": target_digest, "required_confirmation": required_confirmation,
    }


# --- config loading + CLI -----------------------------------------------------

def _load_config(config_path_arg: str | None) -> tuple[Any | None, Path | None]:
    root = hetzner_driver.runtime_workspace()
    config_path = Path(config_path_arg).expanduser().resolve() if config_path_arg else default_config_path(root)
    if not config_path.exists():
        raise HetznerReaperError(
            f"research-compute config not found at {config_path}; refusing unscoped reaper run"
        )
    config = load_config(config_path)
    hetzner_driver.install_scope(config)
    return config, config.state_root(root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hetzner-reaper",
        description="Detached Hetzner reaper: DELETE past-TTL / powered-off / stale / orphaned servers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Project-wide kill requires the exact phrase emitted by `kill --dry-run`."
        ),
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

    kill_p = sub.add_parser(
        "kill",
        help="Project-wide kill switch: DELETE every managed server in this Hetzner project",
    )
    kill_p.add_argument("--dry-run", action="store_true", help="List, delete nothing")
    kill_p.add_argument(
        "--confirm-project-wide",
        metavar="PHRASE",
        help="required for deletion; copy the exact phrase from `kill --dry-run`",
    )
    attest_p = sub.add_parser(
        "attest",
        help="publish a short-lived root-owned detached-scheduler lease",
    )
    attest_p.add_argument("--scheduler-kind", choices=("systemd", "cron"), required=True)
    attest_p.add_argument("--scheduler-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "reap"
    try:
        # A launcher running from an immutable generation exports its read-only
        # runtime root as the workspace override, which carries no broker
        # configuration; drop unusable overrides exactly like the lane entrypoint
        # so config and state resolve to the real broker data workspace.
        hetzner_research_compute._normalize_data_workspace_env()
        config, state_root = _load_config(args.config)
        if command == "attest":
            result = {
                "action": "attest",
                "lease": write_reaper_lease(
                    config=config,
                    scheduler_kind=args.scheduler_kind,
                    scheduler_id=args.scheduler_id,
                ),
            }
        elif command == "kill":
            result = kill_switch(
                config=config,
                state_root=state_root,
                dry_run=args.dry_run,
                confirm_project_wide=args.confirm_project_wide,
            )
        elif getattr(args, "loop", False):
            heartbeat_max = float(args.heartbeat_max_minutes) * 60.0
            while True:  # pragma: no cover - daemon loop; a single pass is tested directly
                result = reap(config=config, state_root=state_root, dry_run=args.dry_run,
                              heartbeat_max_seconds=heartbeat_max)
                print(json.dumps({"ok": not bool(result.get("errors")), **result}))
                sys.stdout.flush()
                time.sleep(max(1.0, float(args.interval)))
        else:
            heartbeat_max = float(args.heartbeat_max_minutes) * 60.0
            result = reap(config=config, state_root=state_root, dry_run=args.dry_run,
                          heartbeat_max_seconds=heartbeat_max)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": hetzner_driver._redact(str(exc))}, indent=2))
        return 1
    ok = not bool(result.get("errors"))
    print(json.dumps({"ok": ok, **result}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
