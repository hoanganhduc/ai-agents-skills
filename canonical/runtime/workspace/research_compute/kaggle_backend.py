"""Kaggle Kernels compute backend for the research broker (routing/quota lane).

This is the router-facing Kaggle lane, peer to the Modal, Hetzner, and GitHub Actions
backends. It ships ROUTING and QUOTA logic only: the `probe` the planner consults, a
self-imposed weekly GPU-hour cap (a local usage ledger + self-cap, because Kaggle exposes
no clean quota API), and a fail-closed `gpu_budget_gate` for GPU submits. No network call,
no `kaggle` CLI invocation, and no kernel is ever run here; the kernel-lifecycle driver
ships alongside the skill.

Routing order is local > kaggle > modal > hetzner > gha. Kaggle sits right behind local
because its CPU compute is FREE and does NOT consume the GPU quota, so a CPU batch that
fits Kaggle's constraints is preferred over the paid/quota'd lanes (Modal, Hetzner, GHA).

What makes Kaggle different (the quota model this file encodes):
  * CPU sessions are FREE and quota-free -- availability for a CPU job is just the API token +
    account-usable, never a cost or quota gate. There is no EUR/USD budget here.
  * The weekly ~30h (floating) quota is GPU-ONLY. Kaggle has no clean quota API, so GPU
    availability is a SELF-IMPOSED weekly GPU-hour cap read from a local usage ledger
    (the same "un-queryable balance -> local ledger + self-cap" pattern as Hetzner's
    balance and GHA's minutes), not an API query.
  * Every kernel run is capped at a 12h session on ~4 vCPU / ~32 GB, so a job is ADEQUATE
    only when it fits ~32 GB and is chunkable/resumable to <=12h per run. Bigger-than-RAM or
    non-resumable jobs are inadequate and fall through to the next lane.
  * Free + auto-stops at 12h => no cost gate, no reaper, no teardown. Availability is a
    lighter check than the paid lanes by construction.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import shutil
import time
from pathlib import Path
from typing import Any

# Per-kernel free-tier resources (verified 2026, plan §2): ~4 vCPU (2x Xeon) / ~32 GB RAM /
# 12h max session. Used as conservative fallbacks; override via [kaggle] if the tier changes.
KAGGLE_KERNEL_CORES = 4
KAGGLE_KERNEL_RAM_GB = 32.0
KAGGLE_SESSION_HOURS = 12.0

# The weekly GPU-hour usage ledger is windowed over the trailing 7 days (the floating
# ~30h/week Kaggle GPU quota is a rolling window, not a calendar reset).
WEEK_SECONDS = 7 * 24 * 3600


class KaggleError(RuntimeError):
    pass


class KaggleBudgetError(KaggleError):
    pass


# --- API token (the new Kaggle API token; env-first, never argv, never logged) ----
#
# Kaggle's current "API Tokens (Recommended)" auth is a SINGLE token (not the legacy
# KAGGLE_USERNAME + KAGGLE_KEY pair and not a kaggle.json). It is read from the KAGGLE_API_TOKEN
# environment variable, or from ~/.kaggle/access_token (the raw token) -- the same file the
# kaggle CLI (>=1.8.0) and kagglehub (>=0.4.1) read natively. The config directory honors
# KAGGLE_CONFIG_DIR, matching the kaggle CLI's own convention. We only ever pass the token via
# the environment; it never travels on argv and is never logged.

TOKEN_ENV = "KAGGLE_API_TOKEN"


def access_token_path() -> Path:
    """Path to the raw-token file the kaggle CLI / kagglehub read (~/.kaggle/access_token),
    honoring KAGGLE_CONFIG_DIR like the kaggle CLI does."""
    base = os.environ.get("KAGGLE_CONFIG_DIR") or "~/.kaggle"
    return Path(base).expanduser() / "access_token"


def read_token() -> str | None:
    """The Kaggle API token, resolved env-first (KAGGLE_API_TOKEN) then ~/.kaggle/access_token.
    Returns the raw value only for in-process redaction; it is never logged or placed on argv."""
    env_token = os.environ.get(TOKEN_ENV)
    if env_token and env_token.strip():
        return env_token.strip()
    path = access_token_path()
    try:
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            return text or None
    except OSError:  # pragma: no cover - an unreadable token file is treated as absent
        return None
    return None


def token_present() -> bool:
    """Whether the new Kaggle API token is available (KAGGLE_API_TOKEN or ~/.kaggle/access_token).
    Presence only -- the value is never read for logging, argv, or persistence. The legacy
    KAGGLE_USERNAME + KAGGLE_KEY / kaggle.json auth is not consulted."""
    return read_token() is not None


# --- account-usable liveness via kagglehub (mockable + gated) -----------------
#
# AVAILABLE requires more than the token string being present: the token must be VALID. Kaggle
# exposes NO clean quota API, so availability is a light authenticated check only -- kagglehub
# reliably validates the token: kagglehub.whoami() returns the authenticated username, after
# which the kaggle CLI's kernel ops (push/status/output) authenticate with the same token.
# 401/403/network-fail => unusable => the cascade falls through. Every real validation goes
# through the module-level KAGGLEHUB_VALIDATE hook so it is mockable; offline tests inject a
# result via resources['liveness']['kaggle'] (deterministic across the subprocess boundary) and
# never touch the network. The real call fires only at plan/bootstrap time.

def _default_kagglehub_validate(config: Any) -> dict[str, Any]:  # pragma: no cover - real kagglehub/network path, never exercised in tests
    """Validate the API token via kagglehub.whoami(), which returns the authenticated username.
    kagglehub reads the token itself from KAGGLE_API_TOKEN or ~/.kaggle/access_token, so the
    token never travels on argv and is never logged. Any failure => unusable (fall through).
    ToS: this is a read-only auth check, made only at plan/bootstrap time."""
    if not token_present():
        return {"usable": False, "username": None, "reason": "no_kaggle_api_token"}
    try:
        import kagglehub

        info = kagglehub.whoami()
        username = info.get("username") if isinstance(info, dict) else info
        if username:
            return {"usable": True, "username": str(username), "reason": "kagglehub_validated"}
        return {"usable": False, "username": None, "reason": "kagglehub_no_username"}
    except Exception as exc:  # noqa: BLE001 - any kagglehub/import/network failure means unusable
        return {"usable": False, "username": None, "reason": f"kagglehub_validate_failed: {exc.__class__.__name__}"}


# Tests replace this hook (in-process) so no external call ever runs offline.
KAGGLEHUB_VALIDATE = _default_kagglehub_validate


def _injected_kaggle(resources: dict[str, Any] | None) -> dict[str, Any] | None:
    node = resources.get("liveness") if isinstance(resources, dict) else None
    node = node.get("kaggle") if isinstance(node, dict) else None
    return node if isinstance(node, dict) else None


def account_usable(config: Any, resources: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Account-usable liveness for the Kaggle lane. Injection-first
    (resources['liveness']['kaggle'] with a 'usable' key) so offline tests are deterministic and
    never touch the network; otherwise kagglehub validates the token (plan/bootstrap time only).
    No token => unusable. Returns (usable, reason)."""
    injected = _injected_kaggle(resources)
    if injected is not None and "usable" in injected:
        return bool(injected.get("usable", False)), str(injected.get("reason", "injected"))
    if not token_present():
        return False, "no_kaggle_api_token"
    result = KAGGLEHUB_VALIDATE(config)
    return bool(result.get("usable", False)), str(result.get("reason", "unknown"))


# --- weekly GPU-hour usage ledger (local self-cap; no quota API) --------------
#
# Kaggle exposes no programmatic GPU-quota number, so the lane self-caps GPU usage with a
# local append-only JSONL ledger windowed over the trailing 7 days -- the same
# "un-queryable balance -> local ledger + self-cap" discipline as Hetzner's balance and
# GHA's minutes. CPU work never touches this ledger (CPU is free and quota-free).

def _gpu_ledger_path(state_root: Path) -> Path:
    return Path(state_root) / "kaggle-gpu-usage.jsonl"


def _read_gpu_ledger(state_root: Path) -> list[dict[str, Any]]:
    path = _gpu_ledger_path(state_root)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def gpu_hours_used_this_week(state_root: Path, *, now: float | None = None) -> float:
    """Sum of GPU-hours reserved in the trailing 7 days from the local usage ledger."""
    now = time.time() if now is None else now
    return sum(float(r.get("gpu_hours", 0.0)) for r in _read_gpu_ledger(state_root)
               if now - float(r.get("reserved_at", 0.0)) <= WEEK_SECONDS)


def reserve_gpu_hours(state_root: Path, *, job_id: str, gpu_hours: float,
                      now: float | None = None) -> None:
    """Append a GPU-hour reservation to the local weekly ledger (the self-cap's write side)."""
    path = _gpu_ledger_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"job_id": job_id, "gpu_hours": float(gpu_hours),
           "reserved_at": (time.time() if now is None else now)}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def _default_gpu_usage_probe(config: Any) -> dict[str, Any]:  # pragma: no cover - real ledger read at plan time
    """Read the trailing-7-day GPU-hours from the local ledger under the resolved broker
    state root. Injection-first callers never reach this; it is the production read the
    planner uses when no snapshot is injected."""
    from .config import workspace_root

    try:
        root = config.state_root(workspace_root())
        return {"gpu_hours_used_this_week": gpu_hours_used_this_week(Path(root))}
    except Exception:  # noqa: BLE001 - a missing ledger just means zero used
        return {"gpu_hours_used_this_week": 0.0}


# Tests replace this hook (in-process) so the real ledger read never runs offline.
GPU_USAGE_PROBE = _default_gpu_usage_probe


def gpu_hours_used(config: Any, resources: dict[str, Any] | None = None,
                   state_root: Path | None = None) -> float:
    """Trailing-7-day GPU-hours used, injection-first for deterministic offline routing:
    resources['liveness']['kaggle']['gpu_hours_used_this_week'] wins; otherwise the explicit
    state-root ledger (driver preflight); otherwise the GPU_USAGE_PROBE hook (planner in
    production). Defaults to 0.0 so a fresh install is not spuriously quota-blocked."""
    injected = _injected_kaggle(resources)
    if injected is not None and "gpu_hours_used_this_week" in injected:
        return float(injected.get("gpu_hours_used_this_week", 0.0))
    if state_root is not None:
        return gpu_hours_used_this_week(Path(state_root))
    return float(GPU_USAGE_PROBE(config).get("gpu_hours_used_this_week", 0.0))


# --- resource / run estimation ------------------------------------------------

def kernel_cores(config: Any) -> int:
    return int(getattr(config, "kaggle_kernel_cores", 0) or KAGGLE_KERNEL_CORES)


def kernel_ram_gb(config: Any) -> float:
    return float(getattr(config, "kaggle_kernel_ram_gb", 0.0) or KAGGLE_KERNEL_RAM_GB)


def session_hours(config: Any) -> float:
    return float(getattr(config, "kaggle_session_hours", 0.0) or KAGGLE_SESSION_HOURS)


def concurrency(config: Any) -> int:
    return max(1, int(getattr(config, "kaggle_concurrency", 0) or 5))


def max_runs(config: Any) -> int:
    return max(1, int(getattr(config, "kaggle_max_runs", 0) or 5))


def estimate_gpu_hours(estimate: dict[str, Any], config: Any) -> float:
    """GPU wall-hours estimate for the weekly-cap arithmetic. Uses an explicit gpu_hours
    field when the manifest provides one, else the core-hour estimate as a proxy for GPU
    wall time, else the runtime estimate."""
    gpu_hours = estimate.get("gpu_hours")
    if gpu_hours in (None, 0, 0.0):
        gpu_hours = estimate.get("core_hours")
    if gpu_hours in (None, 0, 0.0):
        gpu_hours = float(estimate.get("runtime_sec", 0) or 0) / 3600.0
    return float(gpu_hours or 0.0)


def estimate_runs(core_hours: float, config: Any) -> dict[str, Any]:
    """Estimate how the job fans out over Kaggle's 12h/~4-core kernels. per_kernel core-hours
    per run = cores x session_hours; a round fans out up to `concurrency` kernels, so the
    round throughput is per_kernel x concurrency. `est_rounds` is the multi-run resume-loop
    depth; `est_kernels` is the total kernel runs across all rounds."""
    cores = kernel_cores(config)
    hours = session_hours(config)
    fanout = concurrency(config)
    per_kernel_core_h = max(1.0, float(cores) * float(hours))
    per_round_core_h = per_kernel_core_h * float(fanout)
    core_hours = max(0.0, float(core_hours))
    est_kernels = max(1, math.ceil(core_hours / per_kernel_core_h)) if core_hours else 1
    est_rounds = max(1, math.ceil(core_hours / per_round_core_h)) if core_hours else 1
    return {"est_kernels": est_kernels, "est_rounds": est_rounds,
            "per_kernel_core_hours": per_kernel_core_h, "per_round_core_hours": per_round_core_h,
            "concurrency": fanout, "session_hours": hours, "kernel_cores": cores}


def adequacy(estimate: dict[str, Any], config: Any) -> tuple[bool, str]:
    """A job is adequate for Kaggle when it fits one kernel's RAM (~32 GB) and is
    chunkable/resumable to <=12h per run. CPU batch is almost always adequate; a
    bigger-than-RAM job is inadequate and falls through. GPU jobs are adequate because the
    job is GPU-requested and Kaggle offers GPU kernels (availability is then gated by the
    weekly GPU-hour cap, not adequacy)."""
    peak_ram = float(estimate.get("peak_ram_gb") or 0.0)
    ram_cap = kernel_ram_gb(config)
    if peak_ram > ram_cap:
        return False, f"peak_ram {peak_ram:.0f}GB exceeds kernel RAM {ram_cap:.0f}GB"
    if estimate.get("gpu"):
        return True, "gpu_adequate"
    return True, "cpu_chunkable_resumable"


def probe(
    estimate: dict[str, Any],
    *,
    config: Any,
    resources: dict[str, Any] | None = None,
    state_root: Path | None = None,
) -> dict[str, Any]:
    """Router-facing feasibility check consumed by the planner cascade.

    Returns {backend, available, adequate, account_usable, kind, est_runs, est_kernels,
    concurrency, session_hours, gpu_hours_est, gpu_hours_used_week, gpu_hours_cap,
    within_gpu_cap, est_cost, reason}.

    AVAILABLE = the lane is enabled, the API token is present, the account is usable, AND --
    for GPU jobs only -- the trailing-7-day GPU-hours plus this job's estimate stay within
    the self-imposed weekly GPU-hour cap. CPU jobs are NOT quota-gated (Kaggle CPU is free),
    so a CPU job is available on the API token + account-usable alone. ADEQUATE = the job fits
    one kernel's RAM and is chunkable/resumable to <=12h per run (GPU jobs are adequate when
    GPU-requested). There is NO cost gate -- Kaggle is free. The only network call is the
    account-usable kagglehub validation (mockable; injection-first offline), gated on
    enabled + token, so a disabled lane never triggers a validation."""
    enabled = bool(getattr(config, "kaggle_enabled", False))
    has_token = token_present()
    if enabled and has_token:
        usable, usable_reason = account_usable(config, resources)
    else:
        usable, usable_reason = False, ("kaggle_disabled" if not enabled else "no_kaggle_api_token")

    adequate, adequacy_reason = adequacy(estimate, config)
    gpu = bool(estimate.get("gpu"))
    kind = "gpu" if gpu else "cpu"

    runs = estimate_runs(float(estimate.get("core_hours") or 0.0), config)

    # GPU-only weekly cap (a local self-cap; CPU is free and ungated).
    cap = float(getattr(config, "kaggle_weekly_gpu_hours_cap", 0.0) or 0.0)
    gpu_hours_est = estimate_gpu_hours(estimate, config) if gpu else 0.0
    used_week = gpu_hours_used(config, resources, state_root) if gpu else 0.0
    within_gpu_cap = (not gpu) or (cap > 0.0 and (used_week + gpu_hours_est) <= cap)

    reasons: list[str] = []
    if not enabled:
        reasons.append("kaggle_disabled")
    if not has_token:
        reasons.append("no_kaggle_api_token")
    if enabled and has_token and not usable:
        reasons.append(usable_reason)
    if not adequate:
        reasons.append(adequacy_reason)
    if gpu and enabled and has_token and usable and not within_gpu_cap:
        reasons.append(
            f"gpu_hours used {used_week:.1f} + est {gpu_hours_est:.1f} over weekly cap {cap:.1f}h")

    available = bool(enabled and has_token and usable and within_gpu_cap)
    reason = "available" if (available and adequate) else ("; ".join(reasons) or "unavailable")

    return {
        "backend": "kaggle",
        "available": available,
        "adequate": adequate,
        "account_usable": usable,
        "kind": kind,
        "est_runs": runs["est_rounds"],
        "est_kernels": runs["est_kernels"],
        "concurrency": runs["concurrency"],
        "session_hours": runs["session_hours"],
        "gpu_hours_est": round(gpu_hours_est, 3),
        "gpu_hours_used_week": round(used_week, 3),
        "gpu_hours_cap": cap,
        "within_gpu_cap": within_gpu_cap,
        "est_cost": 0.0,
        "est_cost_unit": "free",
        "reason": reason,
    }


def gpu_budget_gate(*, job_id: str, estimate: dict[str, Any], config: Any,
                    state_root: Path) -> dict[str, Any]:
    """Fail-closed pre-submit gate for GPU kernels (mirrors the GHA minutes gate / Hetzner
    EUR gate). Refuses if this job's GPU-hour estimate plus the trailing-7-day usage would
    exceed the self-imposed weekly GPU-hour cap, then reserves the estimate in the local
    usage ledger so concurrent GPU submits cannot collectively blow the cap. CPU jobs never
    call this -- CPU is free and quota-free. Returns the reservation record or raises."""
    cap = float(getattr(config, "kaggle_weekly_gpu_hours_cap", 0.0) or 0.0)
    est = estimate_gpu_hours(estimate, config)
    used = gpu_hours_used_this_week(Path(state_root))
    if cap <= 0.0:
        raise KaggleBudgetError("fail-closed: Kaggle GPU weekly cap is 0 (GPU lane disabled)")
    if used + est > cap:
        raise KaggleBudgetError(
            f"GPU weekly cap refused: used {used:.1f} + est {est:.1f} > cap {cap:.1f}h")
    reserve_gpu_hours(Path(state_root), job_id=job_id, gpu_hours=est)
    return {"ok": True, "reserved_gpu_hours": round(est, 3), "used_this_week": round(used, 3),
            "cap": cap, "job_id": job_id}


def doctor(config: Any) -> dict[str, Any]:
    """Offline readiness snapshot -- no network call (kagglehub validation fires only at
    plan/bootstrap time). Reports whether the lane is enabled, the API token is present, the
    `kaggle` CLI and kagglehub are installed, and the configured caps."""
    out: dict[str, Any] = {
        "kaggle_enabled": bool(getattr(config, "kaggle_enabled", False)),
        "api_token_present": token_present(),
        "kaggle_cli_available": shutil.which("kaggle") is not None,
        "kagglehub_available": importlib.util.find_spec("kagglehub") is not None,
        "kernel_cores": kernel_cores(config),
        "kernel_ram_gb": kernel_ram_gb(config),
        "session_hours": session_hours(config),
        "concurrency": concurrency(config),
        "max_runs": max_runs(config),
        "weekly_gpu_hours_cap": float(getattr(config, "kaggle_weekly_gpu_hours_cap", 0.0) or 0.0),
        "network_probe": "skipped (doctor performs no network calls)",
    }
    out["ready_offline"] = bool(out["kaggle_enabled"] and out["api_token_present"])
    return out
