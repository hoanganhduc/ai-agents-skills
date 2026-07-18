"""Hetzner Cloud lifecycle driver for the research-compute Hetzner lane.

This is the hcloud lifecycle CLI referenced by the hetzner-research-compute skill. It
rents a disposable server, runs a portable job bundle on it, fetches the results, and
DESTROYS the server. Planning verbs (bootstrap, doctor, preflight) are free and never
touch a server; lifecycle verbs (up, push, run, status, wait, fetch, down, oneshot) may
hold a paid server and require HCLOUD_TOKEN plus an explicit confirm.

Guardrails (treat the agent itself as the adversary):
  * HCLOUD_TOKEN is read from the environment and injected into the hcloud subprocess env,
    NEVER on argv (/proc/<pid>/cmdline is world-readable), NEVER logged, NEVER on a server,
    NEVER written to an `hcloud context` file. A redaction filter covers surfaced output.
  * Every server carries managed-by / job-id / owner / ttl labels so teardown can find it.
  * A fail-closed budget gate reserves the pessimistic worst case before any create
    (reuses research_compute.hetzner_backend.budget_gate).
  * `oneshot` guarantees teardown on every exit path (a finally block plus signal handlers,
    the equivalent of `trap 'down' EXIT INT TERM HUP`).

Offline safety: every external command goes through the module-level COMMAND_RUNNER hook,
which tests replace so no server is ever provisioned. `--dry-run` on up / down / oneshot
prints the exact planned hcloud command with no reservation and no provisioning.

Phase C guardrails now built (plan section 6): every `up` auto-attaches a cloud-init
dead-man's-switch (Arm 1) unless the operator supplies their own user-data, and enforces a
reconcile-before-create runaway-loop guard; `down --all` / `down --orphans` and the standalone
hetzner_reaper (Arms 2 and 3) delete servers; provision and destroy events are written to the
append-only redacted audit log.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import hetzner_audit
from research_compute import budget_ledger, hetzner_backend
from research_compute.config import default_config_path, load_config, workspace_root

MANAGED_BY = "ai-agents-skills"
REMOTE_DIR = "/root/job-bundle"
SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes"]


class HetznerDriverError(RuntimeError):
    pass


# --- token + redaction (env-only, never argv, never logged) -------------------

def _token() -> str | None:
    return os.environ.get("HCLOUD_TOKEN") or None


def token_present() -> bool:
    return bool(_token())


def _owner() -> str:
    """Best-effort owner label from the environment; never a hard-coded identity."""
    return os.environ.get("HETZNER_OWNER") or os.environ.get("AAS_OWNER") or MANAGED_BY


def _redact(text: str | None, token: str | None = None) -> str:
    token = token or _token()
    text = text or ""
    if token:
        text = text.replace(token, "<REDACTED_HCLOUD_TOKEN>")
    return text


# --- command runner (single mockable hook for hcloud + ssh/scp/rsync) ---------

def _default_command_runner(argv: list[str], *, env: dict[str, str], timeout: float) -> dict[str, Any]:  # pragma: no cover - real subprocess path is never exercised offline
    import subprocess

    proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=timeout)
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


# Tests replace this to guarantee no external command ever runs offline.
COMMAND_RUNNER: Callable[..., dict[str, Any]] = _default_command_runner


def _run(argv: list[str], *, timeout: float = 120.0, needs_token: bool = True) -> dict[str, Any]:
    """Run an external command through COMMAND_RUNNER. The token is passed only via the
    environment (already present in os.environ); argv never carries it, so argv is safe to
    surface. Output is redacted before it is returned."""
    if needs_token and not token_present():
        raise HetznerDriverError("HCLOUD_TOKEN is not set; refusing to run a Hetzner command")
    env = os.environ.copy()  # HCLOUD_TOKEN travels here, never on argv
    token = _token()
    result = COMMAND_RUNNER(list(argv), env=env, timeout=timeout)
    result["stdout"] = _redact(result.get("stdout", ""), token)
    result["stderr"] = _redact(result.get("stderr", ""), token)
    if int(result.get("returncode", 1)) != 0:
        raise HetznerDriverError(
            f"command failed ({' '.join(argv)}): {result['stderr'].strip() or result['stdout'].strip()}"
        )
    return result


def run_hcloud(args: list[str], **kwargs: Any) -> dict[str, Any]:
    return _run(["hcloud", *args], **kwargs)


# --- labels + selectors -------------------------------------------------------

def server_labels(job_id: str, ttl_hours: float) -> dict[str, str]:
    return {
        "managed-by": MANAGED_BY,
        "job-id": job_id,
        "owner": _owner(),
        "ttl": f"{int(max(1, round(ttl_hours)))}h",
    }


def _label_args(labels: dict[str, str]) -> list[str]:
    args: list[str] = []
    for key, value in labels.items():
        args += ["--label", f"{key}={value}"]
    return args


def _server_name(job_id: str) -> str:
    return f"{MANAGED_BY}-{job_id}"


def _new_job_id() -> str:
    return f"hz_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


# --- manifest + estimate ------------------------------------------------------

def _read_manifest(job_dir: str | Path) -> dict[str, Any]:
    path = Path(job_dir).expanduser() / "manifest.json"
    if not path.is_file():
        raise HetznerDriverError(f"job bundle manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def estimate_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Backend-agnostic estimate read from the portable job bundle manifest."""
    core_hours = manifest.get("core_hours") or manifest.get("est_core_hours") or 0.0
    parallelism = manifest.get("parallelism") or manifest.get("cores") or 1
    peak_ram_gb = manifest.get("peak_ram_gb")
    if peak_ram_gb in (None, 0, 0.0):
        peak_ram_gb = float(int(manifest.get("memory_mb", 0) or 0)) / 1024.0
    return {
        "core_hours": float(core_hours),
        "parallelism": max(1, int(parallelism)),
        "peak_ram_gb": float(peak_ram_gb or 0.0),
        "gpu": bool(manifest.get("gpu")),
        "arch": (str(manifest.get("arch") or "").lower() or None),
    }


def _check_allowlists(server_spec: dict[str, Any], location: str | None, config: Any) -> None:
    allowed_locations = list(getattr(config, "hetzner_allowed_locations", []) or [])
    if allowed_locations and location and location not in allowed_locations:
        raise HetznerDriverError(f"location '{location}' is not in allowed_locations {allowed_locations}")
    allowed_types = set(hetzner_backend.server_catalog(config))
    if server_spec.get("name") not in allowed_types:
        raise HetznerDriverError(
            f"server type '{server_spec.get('name')}' is not in the configured allow-list {sorted(allowed_types)}"
        )


# --- live datacenter availability + orderable placement (plan section 12) ------
#
# The durable fix for a stock-out: Hetzner ARM has been unorderable everywhere and individual
# regions run dry, so a hard-coded (type, location) can fail to provision. preflight and up
# query the live datacenter list through the mockable COMMAND_RUNNER, build a
# {location: [orderable type names]} map, and pick the cheapest adequate type in the
# most-preferred orderable region, falling back across the allow-list on a stock-out. Offline
# tests inject the map directly or through a fake runner; the real hcloud calls are read-only
# (they create no server), and the real subprocess path is gated in _default_command_runner.

def parse_availability(server_types_json: str, datacenters_json: str) -> dict[str, list[str]]:
    """Build {location: [orderable server-type names]} from `hcloud server-type list` (to map
    numeric ids to names) and `hcloud datacenter list` (per-datacenter available ids, unioned
    across the datacenters in a location). Pure and offline-testable."""
    try:
        server_types = json.loads(server_types_json or "[]")
        datacenters = json.loads(datacenters_json or "[]")
    except json.JSONDecodeError as exc:
        raise HetznerDriverError(f"could not parse hcloud availability output: {exc}") from exc
    id_to_name: dict[Any, str] = {}
    for entry in server_types if isinstance(server_types, list) else []:
        if isinstance(entry, dict) and entry.get("id") is not None and entry.get("name"):
            id_to_name[entry["id"]] = str(entry["name"])
    availability: dict[str, list[str]] = {}
    for datacenter in datacenters if isinstance(datacenters, list) else []:
        if not isinstance(datacenter, dict):
            continue
        location = ((datacenter.get("location") or {}).get("name")) or datacenter.get("name")
        available_ids = ((datacenter.get("server_types") or {}).get("available")) or []
        names = availability.setdefault(str(location), [])
        for sid in available_ids:
            name = id_to_name.get(sid)
            if name and name not in names:
                names.append(name)
    return availability


def fetch_availability(config: Any) -> dict[str, list[str]]:
    """Query the live Hetzner datacenter list and return a {location: [orderable type names]}
    map. Two read-only hcloud calls through the mockable COMMAND_RUNNER; free and provisions
    nothing. Requires HCLOUD_TOKEN (every hcloud API call is authenticated)."""
    server_types = run_hcloud(["server-type", "list", "-o", "json"])
    datacenters = run_hcloud(["datacenter", "list", "-o", "json"])
    return parse_availability(server_types["stdout"], datacenters["stdout"])


def resolve_placement(*, estimate: dict[str, Any], config: Any,
                      availability: dict[str, list[str]] | None = None,
                      location: str | None = None) -> tuple[dict[str, Any] | None, str | None, str]:
    """Resolve the (server_spec, location) to provision, availability-checked against the live
    datacenter list. An explicit `location` pins the region (the operator's choice wins) and
    only the cheapest adequate type is chosen; otherwise the cheapest adequate orderable
    (type, location) is picked from the allow-list, falling back on a stock-out. `availability`
    may be injected (offline tests); when omitted and a token is present it is fetched live. On
    no token or a fetch failure it degrades to the cheapest adequate type in the pinned/default
    location (no availability data). Returns (spec | None, location | None, reason)."""
    if location is not None:
        spec, reason = hetzner_backend.select_server_spec(estimate, config)
        return (spec, location, "operator_pinned_location") if spec is not None else (None, None, reason)
    if availability is None and token_present():
        try:
            availability = fetch_availability(config)
        except HetznerDriverError:
            availability = None
    if availability is not None:
        return hetzner_backend.select_orderable_placement(estimate, config=config, availability=availability)
    # No availability data (no token / fetch failed): cheapest adequate type, default region.
    spec, reason = hetzner_backend.select_server_spec(estimate, config)
    default_loc = getattr(config, "hetzner_location", None)
    if spec is None:
        return None, None, reason
    return spec, default_loc, "no_availability_data_using_default_location"


# --- cloud-init dead-man's-switch + reconcile guard + audit (plan section 6) ---

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def cloud_init_shutdown_minutes(ttl_hours: float) -> int:
    """Compute-cap for the dead-man's-switch: the server halts this many minutes after boot
    (boot-relative, so a wrong clock cannot defeat it). At least one minute."""
    return max(1, int(round(float(ttl_hours) * 60.0)))


def render_cloud_init(config: Any, ttl_hours: float) -> str:
    """Render the cloud-init dead-man's-switch (plan section 6, Arm 1) from
    assets/cloud-init.yaml: a boot-relative `shutdown -h +MAX` plus a systemd RuntimeMaxSec
    backstop that powers the box off at the cap. NO token is placed on the server -- a server
    can only power itself OFF, never delete itself, so the detached reaper deletes the
    powered-off box (the billing stopper). `ttl_hours` is the configured max_server_hours."""
    minutes = cloud_init_shutdown_minutes(ttl_hours)
    seconds = minutes * 60
    template = (ASSETS_DIR / "cloud-init.yaml").read_text(encoding="utf-8")
    return (template
            .replace("{{MAX_SECONDS_PLUS}}", str(seconds + 120))
            .replace("{{MAX_SECONDS}}", str(seconds))
            .replace("{{MAX_MINUTES}}", str(minutes)))


def _write_temp_cloud_init(rendered: str) -> str:
    """Write the rendered dead-man's-switch to a temp file for `--user-data-from-file` and
    return its path. It carries no secret, but `up` deletes it right after the create anyway."""
    import tempfile

    handle = tempfile.NamedTemporaryFile("w", suffix="-cloud-init.yaml", delete=False, encoding="utf-8")
    try:
        handle.write(rendered)
    finally:
        handle.close()
    return handle.name


def count_managed_servers() -> int:
    """Number of LIVE servers carrying the managed-by label (the runaway-loop guard input)."""
    result = run_hcloud(["server", "list", "--selector", f"managed-by={MANAGED_BY}", "-o", "json"])
    try:
        servers = json.loads(result["stdout"] or "[]")
    except json.JSONDecodeError:
        servers = []
    return len(servers) if isinstance(servers, list) else 0


def reconcile_before_create(config: Any, *, adding: int = 1) -> dict[str, Any]:
    """Runaway-loop guard (plan section 6): before any create, count the LIVE tagged servers
    and abort if creating `adding` more would exceed max_concurrent_servers. It counts what
    Hetzner actually reports, so it stops a looping/crashing agent even when the local
    reservation ledger is stale."""
    max_concurrent = int(getattr(config, "hetzner_max_concurrent_servers", 0) or 0)
    existing = count_managed_servers()
    if max_concurrent and existing + max(1, int(adding)) > max_concurrent:
        raise HetznerDriverError(
            f"reconcile-before-create: {existing} live tagged server(s) + {adding} would exceed "
            f"max_concurrent_servers {max_concurrent} (runaway-loop guard)")
    return {"existing": existing, "adding": int(adding), "max_concurrent": max_concurrent}


def _audit(state_root: Path | None, event: dict[str, Any]) -> None:
    """Append a redacted provision/destroy audit record (plan section 6) to the append-only
    JSONL log. A no-op when no ledger root is available."""
    hetzner_audit.append(state_root, event, token=_token())


# --- planning verbs (free; no server) -----------------------------------------

def doctor(config: Any) -> dict[str, Any]:
    """Offline readiness snapshot. Reuses the backend doctor and adds a driver note."""
    out = dict(hetzner_backend.doctor(config))
    out["driver"] = "hetzner_driver"
    out["confirm_gate"] = "lifecycle verbs require HCLOUD_TOKEN and --confirm"
    return out


def bootstrap(config: Any | None) -> dict[str, Any]:
    import shutil

    result: dict[str, Any] = {
        "hcloud_cli_available": shutil.which("hcloud") is not None,
        "token_present": token_present(),
    }
    result["doctor"] = doctor(config) if config is not None else {"error": "config not found"}
    if not result["hcloud_cli_available"]:
        result["hint"] = "install the hcloud CLI: https://github.com/hetznercloud/cli"
    return result


def preflight(*, job_dir: str | Path, config: Any, state_root: Path | None = None,
              availability: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """The plan the router consumes: server type, region, est wall-h, est EUR, arch, and the
    budget verdict. Availability-checks the live datacenter list (plan section 12) and reports
    the cheapest adequate ORDERABLE (type, region), falling back across the allow-list on a
    stock-out. The only external calls are the read-only availability query; no reservation and
    no provisioning. Cost/worst-case are computed from the resolved (possibly fallen-back) spec
    so the plan reflects what would actually be ordered."""
    manifest = _read_manifest(job_dir)
    estimate = estimate_from_manifest(manifest)
    spec, region, place_reason = resolve_placement(estimate=estimate, config=config, availability=availability)
    probe = hetzner_backend.probe(estimate, config=config, resources=None, state_root=state_root)

    max_hours = float(getattr(config, "hetzner_max_server_hours", 0.0))
    per_job = float(getattr(config, "hetzner_max_eur_per_job", 0.0))
    core_hours = float(estimate.get("core_hours") or 0.0)
    vcpu = int(spec["vcpu"]) if spec else 0
    est_wall_h = hetzner_backend.estimate_wall_hours(core_hours, vcpu) if spec else 0.0
    est_cost = hetzner_backend.estimate_cost_eur(spec, est_wall_h) if spec else 0.0
    worst_case = hetzner_backend.worst_case_eur(spec, max_hours) if spec else 0.0
    within_auto_approve = bool(spec) and worst_case <= per_job

    if spec is None:
        verdict = "no_orderable_server"
    elif not probe["available"]:
        verdict = "blocked"
    elif within_auto_approve:
        verdict = "auto_approve"
    else:
        verdict = "needs_human_confirmation"

    return {
        "backend": "hetzner",
        "job_id": manifest.get("job_id"),
        "server_type": spec["name"] if spec else None,
        "server_arch": spec["arch"] if spec else None,
        "region": region,
        "est_wall_h": round(est_wall_h, 3),
        "est_cost_eur": round(est_cost, 4),
        "worst_case_eur": round(worst_case, 4),
        "adequate": spec is not None,
        "available": bool(probe["available"] and spec is not None),
        "within_auto_approve": within_auto_approve,
        "budget_verdict": verdict,
        "reason": place_reason if spec is None else probe["reason"],
        "provisioned": False,
    }


# --- lifecycle verbs (may hold a paid server) ---------------------------------

def up(*, job_dir: str | Path, config: Any, state_root: Path, confirm: bool = False,
       dry_run: bool = False, image: str | None = None, location: str | None = None,
       user_data: str | None = None) -> dict[str, Any]:
    """Create one labelled server, budget-gated. `--dry-run` prints the planned command with no
    reservation, no availability query, and no create (fully offline). A real create requires
    HCLOUD_TOKEN and --confirm; it availability-checks the live datacenter list (plan section 12)
    and provisions the cheapest adequate ORDERABLE (type, region), falling back across the
    allow-list on a stock-out. An operator --location pins the region."""
    manifest = _read_manifest(job_dir)
    job_id = str(manifest.get("job_id") or _new_job_id())
    estimate = estimate_from_manifest(manifest)
    spec, adequacy_reason = hetzner_backend.select_server_spec(estimate, config)
    if spec is None:
        raise HetznerDriverError(f"no adequate Hetzner server for this job: {adequacy_reason}")

    explicit_location = location
    image = image or getattr(config, "hetzner_image", None) or "ubuntu-24.04"
    ttl_hours = float(getattr(config, "hetzner_max_server_hours", 1.0))
    labels = server_labels(job_id, ttl_hours)
    name = _server_name(job_id)

    def _create_args(chosen_spec: dict[str, Any], chosen_location: str | None) -> list[str]:
        create = ["server", "create", "--name", name, "--type", chosen_spec["name"], "--image", image]
        if chosen_location:
            create += ["--location", chosen_location]
        return create + _label_args(labels)

    # Dead-man's-switch (plan section 6, Arm 1): every server gets a boot-relative shutdown
    # cloud-init UNLESS the operator supplies their own --user-data file. No token on the server.
    arm_dead_mans_switch = user_data is None

    if dry_run:
        # Offline: cheapest adequate type + the pinned/default region (the live availability-check
        # runs only on a real, confirmed create).
        location = explicit_location or getattr(config, "hetzner_location", None)
        _check_allowlists(spec, location, config)
        worst_case = hetzner_backend.worst_case_eur(spec, float(getattr(config, "hetzner_max_server_hours", 0.0)))
        udf_display = user_data if user_data else "<rendered dead-mans-switch cloud-init>"
        return {
            "dry_run": True, "provisioned": False, "job_id": job_id, "server_name": name,
            "server_type": spec["name"], "server_arch": spec["arch"], "location": location,
            "image": image, "labels": labels,
            "command": ["hcloud", *_create_args(spec, location), "--user-data-from-file", udf_display],
            "dead_mans_switch": arm_dead_mans_switch,
            "cloud_init_shutdown_minutes": cloud_init_shutdown_minutes(ttl_hours) if arm_dead_mans_switch else None,
            "worst_case_eur": round(worst_case, 4),
            "would_reserve": worst_case <= float(getattr(config, "hetzner_max_eur_per_job", 0.0)),
        }

    if not token_present():
        raise HetznerDriverError("refusing to provision: HCLOUD_TOKEN is not set")
    if not confirm:
        raise HetznerDriverError("refusing to provision: explicit confirm is required (plan->submit confirm gate)")

    # Runaway-loop guard BEFORE any spend or availability query: live tagged servers vs the cap.
    reconcile = reconcile_before_create(config)

    # Live availability-check (plan section 12): pick an orderable (type, region), falling back
    # across the allow-list on a stock-out, so a stocked-out type/region degrades gracefully
    # instead of failing to provision. An operator --location pins the region.
    spec, location, place_reason = resolve_placement(
        estimate=estimate, config=config, location=explicit_location)
    if spec is None:
        raise HetznerDriverError(f"no orderable Hetzner server for this job: {place_reason}")
    _check_allowlists(spec, location, config)

    # Fail-closed budget gate + worst-case reservation BEFORE any create (with the resolved spec).
    reservation = hetzner_backend.budget_gate(
        job_id=job_id, server_spec=spec, config=config, state_root=Path(state_root)
    )

    user_data_path = user_data
    temp_cloud_init: str | None = None
    if arm_dead_mans_switch:
        temp_cloud_init = _write_temp_cloud_init(render_cloud_init(config, ttl_hours))
        user_data_path = temp_cloud_init
    create_args = _create_args(spec, location)
    if user_data_path:
        create_args += ["--user-data-from-file", user_data_path]
    try:
        result = run_hcloud(create_args)
    finally:
        # Never leave the rendered cloud-init lying around (it carries no secret, but tidy).
        if temp_cloud_init is not None:
            try:
                os.unlink(temp_cloud_init)
            except OSError:
                pass

    _audit(Path(state_root), {
        "event": hetzner_audit.EVENT_PROVISION, "job_id": job_id, "server_name": name,
        "server_type": spec["name"], "server_arch": spec["arch"], "location": location,
        "labels": labels, "est_eur": reservation.get("worst_case"), "real_eur": None,
        "reason": "up", "dead_mans_switch": arm_dead_mans_switch,
    })
    return {
        "provisioned": True, "job_id": job_id, "server_name": name, "server_type": spec["name"],
        "server_arch": spec["arch"], "location": location, "image": image, "labels": labels,
        "dead_mans_switch": arm_dead_mans_switch, "reconcile": reconcile,
        "reservation": reservation, "hcloud_stdout": result["stdout"],
    }


def _server_ip(job_id: str) -> str:
    """Resolve the public IPv4 of the server labelled with this job-id."""
    result = run_hcloud(["server", "list", "--selector", f"job-id={job_id}", "-o", "json"])
    try:
        servers = json.loads(result["stdout"] or "[]")
    except json.JSONDecodeError as exc:
        raise HetznerDriverError(f"could not parse hcloud server list: {exc}") from exc
    for server in servers:
        ip = (((server or {}).get("public_net") or {}).get("ipv4") or {}).get("ip")
        if ip:
            return str(ip)
    raise HetznerDriverError(f"no running server found for job-id={job_id}")


def push(*, job_id: str, job_dir: str | Path, config: Any, confirm: bool = False,
         dry_run: bool = False) -> dict[str, Any]:
    """Copy the job bundle to the server (rsync over SSH)."""
    local = f"{str(Path(job_dir).expanduser()).rstrip('/')}/"
    if dry_run:
        return {"dry_run": True, "job_id": job_id,
                "command": ["rsync", "-az", "-e", "ssh " + " ".join(SSH_OPTS), local,
                            f"root@<server-ip>:{REMOTE_DIR}/"]}
    if not confirm:
        raise HetznerDriverError("refusing to push: explicit confirm is required")
    ip = _server_ip(job_id)
    argv = ["rsync", "-az", "-e", "ssh " + " ".join(SSH_OPTS), local, f"root@{ip}:{REMOTE_DIR}/"]
    _run(argv, timeout=600.0)
    return {"job_id": job_id, "pushed_to": f"{REMOTE_DIR}/", "server_ip_known": True}


def run(*, job_id: str, config: Any, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Start the bundle detached at full cores on the server."""
    remote_cmd = f"cd {REMOTE_DIR} && CORES=$(nproc) nohup bash run.sh > run.log 2>&1 & echo started"
    if dry_run:
        return {"dry_run": True, "job_id": job_id,
                "command": ["ssh", *SSH_OPTS, "root@<server-ip>", remote_cmd]}
    if not confirm:
        raise HetznerDriverError("refusing to run: explicit confirm is required")
    ip = _server_ip(job_id)
    _run(["ssh", *SSH_OPTS, f"root@{ip}", remote_cmd])
    return {"job_id": job_id, "started": True}


def status(*, job_id: str, config: Any) -> dict[str, Any]:
    """Server state for this job (hcloud list by label). Free of remote side effects."""
    result = run_hcloud(["server", "list", "--selector", f"job-id={job_id}", "-o", "json"])
    try:
        servers = json.loads(result["stdout"] or "[]")
    except json.JSONDecodeError:
        servers = []
    return {"job_id": job_id, "servers": [
        {"name": s.get("name"), "status": s.get("status")} for s in servers]}


def wait(*, job_id: str, config: Any, timeout: float | None = None, interval: float = 20.0,
         max_polls: int = 100000) -> dict[str, Any]:
    """Poll for the bundle's result marker over SSH until it appears or the wall cap hits."""
    import time

    marker = f"{REMOTE_DIR}/RESULTS.json"
    ip = _server_ip(job_id)
    start = time.time()
    for poll in range(int(max_polls)):
        try:
            _run(["ssh", *SSH_OPTS, f"root@{ip}", f"test -f {marker}"])
            return {"job_id": job_id, "status": "completed", "polls": poll + 1}
        except HetznerDriverError:
            pass
        if timeout is not None and time.time() - start > timeout:
            return {"job_id": job_id, "status": "timeout", "polls": poll + 1}
        time.sleep(interval)
    return {"job_id": job_id, "status": "timeout", "polls": int(max_polls)}


def fetch(*, job_id: str, config: Any, dest: str | Path | None = None,
          salvage: bool = False) -> dict[str, Any]:
    """Copy results (and the resumable out/ tree) back and verify they are well formed.
    On failure/timeout paths, `salvage=True` fetches checkpoints before teardown."""
    dest_dir = Path(dest).expanduser() if dest else Path.cwd() / "hetzner-results" / job_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    ip = _server_ip(job_id)
    _run(["scp", *SSH_OPTS, "-r", f"root@{ip}:{REMOTE_DIR}/out", str(dest_dir)], timeout=600.0)
    result_ok = False
    try:
        _run(["scp", *SSH_OPTS, f"root@{ip}:{REMOTE_DIR}/RESULTS.json", str(dest_dir)], timeout=120.0)
        results_path = dest_dir / "RESULTS.json"
        if results_path.is_file():
            json.loads(results_path.read_text(encoding="utf-8"))  # verify well formed
            result_ok = True
    except (HetznerDriverError, json.JSONDecodeError):
        if not salvage:
            raise
    return {"job_id": job_id, "fetched_to": str(dest_dir), "results_present": result_ok,
            "salvage": salvage}


def down(*, config: Any, state_root: Path | None = None, job_id: str | None = None,
         server_id: str | None = None, all_tagged: bool = False, orphans: bool = False,
         confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """DESTROY servers -- the only thing that stops Hetzner billing. Selects by job-id, by
    server-id, `--all` (kill switch: every managed server), or `--orphans` (managed servers
    the reaper predicate would collect; the precise TTL/heartbeat filter is a later phase)."""
    if all_tagged or orphans:
        selector = f"managed-by={MANAGED_BY}"
        mode = "all" if all_tagged else "orphans"
    elif job_id:
        selector = f"job-id={job_id}"
        mode = "job"
    elif server_id:
        selector = None
        mode = "server"
    else:
        raise HetznerDriverError("down requires job_id, server_id, all_tagged, or orphans")

    if dry_run:
        listed = ["hcloud", "server", "list", "--selector", selector, "-o", "json"] if selector \
            else ["hcloud", "server", "describe", str(server_id)]
        delete = ["hcloud", "server", "delete", "<server-id>" if selector else str(server_id)]
        return {"dry_run": True, "mode": mode, "selector": selector,
                "list_command": listed, "delete_command": delete, "destroyed": []}

    if not token_present():
        raise HetznerDriverError("refusing to destroy: HCLOUD_TOKEN is not set")
    if not confirm:
        raise HetznerDriverError("refusing to destroy: explicit confirm is required")

    records: list[dict[str, Any]] = []
    if selector is None:
        records = [{"id": server_id}]
    else:
        result = run_hcloud(["server", "list", "--selector", selector, "-o", "json"])
        try:
            servers = json.loads(result["stdout"] or "[]")
        except json.JSONDecodeError:
            servers = []
        # For a precise TTL / powered-off / stale-heartbeat / not-in-ledger filter run the
        # standalone hetzner_reaper; `down --all|--orphans` targets managed servers by label.
        records = [s for s in servers if (s.get("id") or s.get("name"))]

    destroyed: list[str] = []
    for server in records:
        target = str(server.get("id") or server.get("name"))
        run_hcloud(["server", "delete", target])
        destroyed.append(target)
        labels = server.get("labels") or {}
        reconcile_job = labels.get("job-id") or (job_id if mode == "job" else None)
        _audit(state_root, {
            "event": hetzner_audit.EVENT_DESTROY, "server": target, "name": server.get("name"),
            "mode": mode, "reason": f"down {mode}", "job_id": reconcile_job,
            "labels": labels or None, "real_eur": None,
        })
        if state_root is not None and reconcile_job:
            try:
                budget_ledger.reconcile(Path(state_root), "hetzner", str(reconcile_job), None)
            except Exception:  # noqa: BLE001 - reconciliation is best-effort at teardown
                pass
    return {"mode": mode, "selector": selector, "destroyed": destroyed}


def _install_teardown_signals(teardown: Callable[[str], Any]) -> dict[Any, Any]:
    """Install SIGINT/SIGTERM/SIGHUP handlers that run teardown then re-raise, so the
    `finally` in `oneshot` always fires. This is the Python equivalent of a shell
    `trap 'down' EXIT INT TERM HUP`. No-op off the main thread."""
    import signal

    installed: dict[Any, Any] = {}

    def _handler(signum: int, _frame: Any) -> None:
        teardown(f"signal-{signum}")
        raise KeyboardInterrupt(f"terminated by signal {signum}")

    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            installed[sig] = signal.signal(sig, _handler)
        except (ValueError, OSError):  # not the main thread / unsupported
            pass
    return installed


def _restore_signals(installed: dict[Any, Any]) -> None:
    import signal

    for sig, old in installed.items():
        try:
            signal.signal(sig, old)
        except (ValueError, OSError):
            pass


def oneshot(*, job_dir: str | Path, config: Any, state_root: Path, confirm: bool = False,
            dry_run: bool = False, dest: str | Path | None = None,
            timeout: float | None = None) -> dict[str, Any]:
    """up -> push -> run -> wait -> fetch -> down, with teardown GUARANTEED on every exit
    path (finally + signal handlers == `trap 'down' EXIT INT TERM HUP`). Failure and
    timeout paths salvage checkpoints before destroy so the run is resumable."""
    manifest = _read_manifest(job_dir)
    job_id = str(manifest.get("job_id") or _new_job_id())

    if dry_run:
        return {
            "dry_run": True, "job_id": job_id,
            "sequence": ["up", "push", "run", "wait", "fetch", "down"],
            "up": up(job_dir=job_dir, config=config, state_root=state_root, dry_run=True),
            "down": down(config=config, job_id=job_id, dry_run=True),
            "teardown": "guaranteed on every exit (finally + signal handlers == trap 'down' EXIT INT TERM HUP)",
        }

    if not token_present():
        raise HetznerDriverError("refusing to run oneshot: HCLOUD_TOKEN is not set")
    if not confirm:
        raise HetznerDriverError("refusing to run oneshot: explicit confirm is required")

    torn_down = {"done": False, "result": None}

    def _teardown(_reason: str) -> Any:
        if torn_down["done"]:
            return torn_down["result"]
        torn_down["done"] = True
        torn_down["result"] = down(config=config, state_root=state_root, job_id=job_id, confirm=True)
        return torn_down["result"]

    installed = _install_teardown_signals(_teardown)
    steps: dict[str, Any] = {}
    try:
        steps["up"] = up(job_dir=job_dir, config=config, state_root=state_root, confirm=True)
        steps["push"] = push(job_id=job_id, job_dir=job_dir, config=config, confirm=True)
        steps["run"] = run(job_id=job_id, config=config, confirm=True)
        steps["wait"] = wait(job_id=job_id, config=config, timeout=timeout)
        steps["fetch"] = fetch(job_id=job_id, config=config, dest=dest,
                               salvage=steps["wait"].get("status") != "completed")
        outcome = "completed" if steps["wait"].get("status") == "completed" else steps["wait"].get("status")
    except BaseException:
        # Salvage checkpoints before the guaranteed teardown, best-effort.
        try:
            steps["fetch"] = fetch(job_id=job_id, config=config, dest=dest, salvage=True)
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        steps["down"] = _teardown("exit")
        _restore_signals(installed)
    return {"job_id": job_id, "status": outcome, "steps": steps}


# --- CLI ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hetzner-research-compute",
        description="Hetzner Cloud lifecycle driver for the research-compute Hetzner lane.",
    )
    parser.add_argument("--config", default=None, help="Path to research-compute.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bootstrap", help="Check the hcloud CLI + token and report doctor (no provisioning)")
    sub.add_parser("doctor", help="Offline lane / token / hcloud / caps readiness")

    pf = sub.add_parser("preflight", help="Emit the Hetzner plan for a job bundle (no server)")
    pf.add_argument("--job", required=True, help="Path to a portable job-bundle directory")
    pf.add_argument("--json", action="store_true", help="(accepted for parity; output is always JSON)")

    up_p = sub.add_parser("up", help="Create a labelled server (budget-gated)")
    up_p.add_argument("--job", required=True)
    up_p.add_argument("--confirm", action="store_true")
    up_p.add_argument("--dry-run", action="store_true")
    up_p.add_argument("--image", default=None)
    up_p.add_argument("--location", default=None)
    up_p.add_argument("--user-data", default=None, help="cloud-init user-data file (Phase C dead-man's-switch)")

    push_p = sub.add_parser("push", help="Copy the job bundle to the server")
    push_p.add_argument("job_id")
    push_p.add_argument("--job", required=True)
    push_p.add_argument("--confirm", action="store_true")
    push_p.add_argument("--dry-run", action="store_true")

    run_p = sub.add_parser("run", help="Start the bundle detached at full cores")
    run_p.add_argument("job_id")
    run_p.add_argument("--confirm", action="store_true")
    run_p.add_argument("--dry-run", action="store_true")

    status_p = sub.add_parser("status", help="Server state for a job")
    status_p.add_argument("job_id")

    wait_p = sub.add_parser("wait", help="Poll until the run finishes or the wall cap hits")
    wait_p.add_argument("job_id")
    wait_p.add_argument("--timeout", type=float, default=None)

    fetch_p = sub.add_parser("fetch", help="Copy results back and verify they are well formed")
    fetch_p.add_argument("job_id")
    fetch_p.add_argument("--dest", default=None)

    down_p = sub.add_parser("down", help="DESTROY servers (the only thing that stops billing)")
    down_p.add_argument("job_id", nargs="?", default=None)
    down_p.add_argument("--server-id", default=None)
    down_p.add_argument("--all", dest="all_tagged", action="store_true")
    down_p.add_argument("--orphans", action="store_true")
    down_p.add_argument("--confirm", action="store_true")
    down_p.add_argument("--dry-run", action="store_true")

    one_p = sub.add_parser("oneshot", help="up->push->run->wait->fetch->down, teardown guaranteed")
    one_p.add_argument("--job", required=True)
    one_p.add_argument("--confirm", action="store_true")
    one_p.add_argument("--dry-run", action="store_true")
    one_p.add_argument("--dest", default=None)
    one_p.add_argument("--timeout", type=float, default=None)
    return parser


def _load(args: argparse.Namespace) -> tuple[Any | None, Path]:
    root = workspace_root()
    config_path = Path(args.config).expanduser().resolve() if args.config else default_config_path(root)
    state_root = config_path.parent.parent / "memories" / "research-compute"
    config: Any | None = None
    if config_path.exists():
        config = load_config(config_path)
        state_root = config.state_root(root)
    return config, state_root


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config, state_root = _load(args)
        if args.command == "bootstrap":
            result = bootstrap(config)
        else:
            if config is None:
                raise HetznerDriverError("research-compute.toml not found; run the broker bootstrap first")
            Path(state_root).mkdir(parents=True, exist_ok=True)
            if args.command == "doctor":
                result = doctor(config)
            elif args.command == "preflight":
                result = preflight(job_dir=args.job, config=config, state_root=Path(state_root))
            elif args.command == "up":
                result = up(job_dir=args.job, config=config, state_root=Path(state_root),
                            confirm=args.confirm, dry_run=args.dry_run, image=args.image,
                            location=args.location, user_data=args.user_data)
            elif args.command == "push":
                result = push(job_id=args.job_id, job_dir=args.job, config=config,
                              confirm=args.confirm, dry_run=args.dry_run)
            elif args.command == "run":
                result = run(job_id=args.job_id, config=config, confirm=args.confirm, dry_run=args.dry_run)
            elif args.command == "status":
                result = status(job_id=args.job_id, config=config)
            elif args.command == "wait":
                result = wait(job_id=args.job_id, config=config, timeout=args.timeout)
            elif args.command == "fetch":
                result = fetch(job_id=args.job_id, config=config, dest=args.dest)
            elif args.command == "down":
                result = down(config=config, state_root=Path(state_root), job_id=args.job_id,
                              server_id=args.server_id, all_tagged=args.all_tagged,
                              orphans=args.orphans, confirm=args.confirm, dry_run=args.dry_run)
            elif args.command == "oneshot":
                result = oneshot(job_dir=args.job, config=config, state_root=Path(state_root),
                                 confirm=args.confirm, dry_run=args.dry_run, dest=args.dest,
                                 timeout=args.timeout)
            else:  # pragma: no cover - argparse guards this
                raise HetznerDriverError(f"unhandled command: {args.command}")
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": _redact(str(exc))}, indent=2))
        return 1
    print(json.dumps({"ok": True, **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
