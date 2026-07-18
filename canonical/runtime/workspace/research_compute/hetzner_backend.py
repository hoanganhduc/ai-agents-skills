"""Hetzner Cloud compute backend for the research broker (routing/budget lane).

This is the router-facing Hetzner lane, peer to the Modal and GitHub Actions
backends. Phase A ships ROUTING, BUDGET, and TEARDOWN-SAFETY LOGIC ONLY: the
`probe` the planner consults, a fail-closed `budget_gate` mirroring the GitHub
Actions minutes gate, and guarded `provision`/`teardown` stubs that refuse to
touch real infrastructure without credentials and an explicit confirmation. No
network call, no `hcloud` invocation, and no server is ever created here; the
provisioning driver ships in a later phase.

Routing order is local > kaggle > modal > hetzner > gha. Hetzner is the offload tier
after Modal for CPU / high-RAM work; GPU work is out of scope for v1 and falls through to
Modal. Server sizing picks the cheapest configured type whose vCPU meets the
requested parallelism and whose RAM meets the estimate -- CPX22 (2 shared AMD cores)
for small jobs, CPX62 (16 shared AMD cores) for up to 16-way fan-out, otherwise CCX63
(48 dedicated cores); all are current orderable x86 types (Hetzner ARM cax* is
supply-constrained). `select_orderable_placement` is the pure picker the driver's
preflight/up feed the live `hcloud datacenter list` into, so a stocked-out (type,
location) falls back across the allow-list instead of failing to provision. Wall time is
core-hours / vCPU because a rented box runs dedicated at full cores. Budget is
reserved pessimistically (rate x ceil(max server-hours) x count + IPv4) in EUR
through the shared append-only ledger, so concurrent submits cannot collectively
overspend. The only thing that stops Hetzner billing is DELETE, so the real
teardown (a detached reaper, later phase) must run on every terminal path.
"""
from __future__ import annotations

import math
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import budget_ledger

HCLOUD_API = "https://api.hetzner.cloud/v1"

# Approximate Hetzner Cloud list prices (EUR/hour) for the default rate card, refreshed to
# the current orderable x86 generation (ARM cax* is supply-constrained/unorderable and the
# older cx*1 / cpx*1 types are deprecated). These are conservative fallbacks used only when
# [hetzner.server_types] is not configured; override them per account. The cheapest adequate
# type wins, so with these defaults a small job lands on CPX22, a <=16-way job on CPX62, and
# anything larger on CCX63.
DEFAULT_SERVER_TYPES: dict[str, dict[str, Any]] = {
    "cpx22": {"vcpu": 2, "ram_gb": 4, "arch": "x86", "eur_per_hour": 0.0180},
    "cpx62": {"vcpu": 16, "ram_gb": 32, "arch": "x86", "eur_per_hour": 0.1550},
    "ccx63": {"vcpu": 48, "ram_gb": 192, "arch": "x86", "eur_per_hour": 1.3678},
}

# A primary IPv4 carries a small surcharge; modelled as a flat per-server addend in
# the pessimistic worst-case reservation.
IPV4_EUR = 0.01


class HetznerError(RuntimeError):
    pass


class HetznerBudgetError(HetznerError):
    pass


def token_present() -> bool:
    """HCLOUD_TOKEN is read from the environment at runtime (env-injection, never
    argv). Phase A only checks presence; it never reads, logs, or persists the value."""
    return bool(os.environ.get("HCLOUD_TOKEN"))


# --- account-usable liveness probe (plan §6.1; mockable + gated) --------------
#
# AVAILABLE requires more than a token string: the token must be valid, the API
# reachable, and the account not payment-blocked. There is NO reliable
# "credits remaining" number for a postpaid vendor, so availability is a light
# authenticated call (GET /server_types) -- 401/403/network-fail => unusable =>
# the cascade falls through. Every real call goes through the module-level
# ACCOUNT_LIVENESS_PROBE hook so it is mockable; offline tests inject a result via
# resources['liveness']['hetzner'] (deterministic across the subprocess boundary)
# and never touch the network. The real call fires only at plan/doctor time.

def _default_account_liveness_probe(config: Any, *, timeout: float = 8.0) -> dict[str, Any]:  # pragma: no cover - real network path, never exercised in tests
    """Light authenticated hcloud API call proving token-valid + API-reachable +
    account-not-payment-blocked (no balance query). The token is read from the env and
    sent only in the Authorization header -- never on argv, never logged. Any 401/403 or
    network failure => unusable (fall through)."""
    token = os.environ.get("HCLOUD_TOKEN")
    if not token:
        return {"usable": False, "reason": "no_hcloud_token"}
    request = urllib.request.Request(
        f"{HCLOUD_API}/server_types?per_page=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed https host
            usable = 200 <= int(response.status) < 300
            return {"usable": usable, "reason": "account_usable" if usable else f"http_{response.status}"}
    except urllib.error.HTTPError as exc:  # 401/403 = invalid token / payment-blocked
        return {"usable": False, "reason": f"http_{exc.code}"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"usable": False, "reason": f"api_unreachable: {exc.__class__.__name__}"}


# Tests replace this hook (in-process) so no external call ever runs offline.
ACCOUNT_LIVENESS_PROBE = _default_account_liveness_probe


def _injected_liveness(resources: dict[str, Any] | None) -> dict[str, Any] | None:
    node = resources.get("liveness") if isinstance(resources, dict) else None
    node = node.get("hetzner") if isinstance(node, dict) else None
    return node if isinstance(node, dict) else None


def account_usable(config: Any, resources: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Account-usable liveness for the Hetzner lane (plan §6.1). Injection-first
    (resources['liveness']['hetzner']) so offline tests are deterministic and never touch
    the network; otherwise the real authenticated probe runs (plan/doctor time only). No
    token => unusable. Returns (usable, reason)."""
    injected = _injected_liveness(resources)
    if injected is not None:
        return bool(injected.get("usable", False)), str(injected.get("reason", "injected"))
    if not token_present():
        return False, "no_hcloud_token"
    result = ACCOUNT_LIVENESS_PROBE(config)
    return bool(result.get("usable", False)), str(result.get("reason", "unknown"))


def server_catalog(config: Any) -> dict[str, dict[str, Any]]:
    configured = dict(getattr(config, "hetzner_server_types", {}) or {})
    return configured or dict(DEFAULT_SERVER_TYPES)


def adequate_specs(estimate: dict[str, Any], config: Any) -> list[dict[str, Any]]:
    """All configured server types that fit the estimate, cheapest first (each returned as a
    name-tagged spec). GPU jobs get an empty list (they fall through to Modal in v1). A type
    fits when its vCPU meets the requested parallelism and its RAM meets the peak estimate."""
    if estimate.get("gpu"):
        return []
    parallelism = max(1, int(estimate.get("parallelism") or 1))
    peak_ram = float(estimate.get("peak_ram_gb") or 0.0)
    specs: list[dict[str, Any]] = []
    for name, spec in server_catalog(config).items():
        vcpu = int(spec.get("vcpu", 0) or 0)
        ram = float(spec.get("ram_gb", 0) or 0)
        if vcpu >= parallelism and ram >= peak_ram:
            specs.append({"name": name, **spec})
    specs.sort(key=lambda item: float(item.get("eur_per_hour", 0.0) or 0.0))
    return specs


def select_server_spec(estimate: dict[str, Any], config: Any) -> tuple[dict[str, Any] | None, str]:
    """Pick the cheapest configured server whose vCPU meets the requested parallelism
    and whose RAM meets the estimate. GPU jobs are inadequate in v1 (they fall through
    to Modal). Returns (spec-with-name | None, reason)."""
    if estimate.get("gpu"):
        return None, "gpu_out_of_scope_v1"
    specs = adequate_specs(estimate, config)
    if not specs:
        parallelism = max(1, int(estimate.get("parallelism") or 1))
        peak_ram = float(estimate.get("peak_ram_gb") or 0.0)
        return None, f"no configured server meets {parallelism}-way / {peak_ram:.0f}GB"
    return specs[0], "ok"


def allowed_locations(config: Any) -> list[str]:
    """The location allow-list in preference order: the configured allowed_locations, or the
    single default location when the allow-list is empty."""
    locations = [loc for loc in (getattr(config, "hetzner_allowed_locations", []) or []) if loc]
    if locations:
        return list(locations)
    default = getattr(config, "hetzner_location", None)
    return [default] if default else []


def select_orderable_placement(
    estimate: dict[str, Any], *, config: Any, availability: dict[str, Any] | None,
    locations: list[str] | None = None,
) -> tuple[dict[str, Any] | None, str | None, str]:
    """Pick a currently-orderable (server_spec, location) for the estimate. Walk the cheapest
    adequate types first and, within each type, the allowed locations in preference order, and
    return the first (type, location) that is orderable -- falling back to the next combo on a
    stock-out, so an out-of-stock type or region degrades gracefully instead of failing to
    provision. `availability` maps a location name to the server-type names orderable there, as
    produced from `hcloud datacenter list`. Returns (spec | None, location | None, reason)."""
    specs = adequate_specs(estimate, config)
    if not specs:
        return None, None, "no adequate server type for this job"
    locs = list(locations) if locations is not None else allowed_locations(config)
    if not locs:
        return None, None, "no allowed_locations configured"
    avail = availability or {}
    for spec in specs:                       # cheapest adequate type first
        for loc in locs:                     # allowed locations in preference order
            if spec["name"] in (avail.get(loc) or []):
                return spec, loc, "orderable"
    return None, None, "all adequate types out of stock across the allowed locations"


def estimate_wall_hours(core_hours: float, vcpu: int) -> float:
    if vcpu <= 0:
        return float(core_hours)
    return max(float(core_hours) / float(vcpu), 0.0)


def estimate_cost_eur(server_spec: dict[str, Any], wall_hours: float) -> float:
    return float(server_spec.get("eur_per_hour", 0.0) or 0.0) * max(float(wall_hours), 0.0)


def worst_case_eur(server_spec: dict[str, Any], max_server_hours: float, count: int = 1) -> float:
    """Pessimistic reservation: a server bills until the dead-man's-switch deletes it,
    so reserve rate x ceil(max server-hours) x count + IPv4 per server."""
    rate = float(server_spec.get("eur_per_hour", 0.0) or 0.0)
    hours = math.ceil(max(float(max_server_hours), 0.0))
    n = max(1, int(count))
    return rate * hours * n + IPV4_EUR * n


def probe(
    estimate: dict[str, Any],
    *,
    config: Any,
    resources: dict[str, Any] | None = None,
    state_root: Path | None = None,
) -> dict[str, Any]:
    """Router-facing feasibility check consumed by the planner cascade.

    Returns {backend, available, adequate, account_usable, server_spec, est_cost,
    est_wall_h, reason}. AVAILABLE = the lane is enabled, HCLOUD_TOKEN is present, the
    account is usable (token valid + API reachable + not payment-blocked, per §6.1), and
    the expected spend is within the daily EUR envelope. ADEQUATE = a configured server
    type fits the estimate (GPU is inadequate in v1). The only network call is the
    account-usable liveness probe (mockable; injection-first offline), gated on
    enabled + token; it reserves nothing -- the hard worst-case reservation happens in
    budget_gate at submit. `state_root`, when given, is the reservation-ledger root used to
    discount already-outstanding EUR.
    """
    enabled = bool(getattr(config, "hetzner_enabled", False))
    has_token = token_present()
    # Liveness is consulted only when the lane could actually be used (enabled + token),
    # so a disabled lane never triggers a network call.
    if enabled and has_token:
        usable, usable_reason = account_usable(config, resources)
    else:
        usable, usable_reason = False, ("hetzner_disabled" if not enabled else "no_hcloud_token")
    spec, adequacy_reason = select_server_spec(estimate, config)
    adequate = spec is not None

    core_hours = float(estimate.get("core_hours") or 0.0)
    vcpu = int(spec["vcpu"]) if spec else 0
    est_wall_h = estimate_wall_hours(core_hours, vcpu) if spec else 0.0
    est_cost = estimate_cost_eur(spec, est_wall_h) if spec else 0.0

    per_day = float(getattr(config, "hetzner_max_eur_per_day", 0.0))
    per_job = float(getattr(config, "hetzner_max_eur_per_job", 0.0))
    max_hours = float(getattr(config, "hetzner_max_server_hours", 0.0))
    worst_case = worst_case_eur(spec, max_hours) if spec else 0.0
    within_auto_approve = bool(spec) and worst_case <= per_job

    outstanding = 0.0
    if state_root is not None:
        outstanding = budget_ledger.outstanding(Path(state_root), "hetzner")
    day_headroom = max(per_day - outstanding, 0.0)
    within_budget = bool(spec) and est_cost <= day_headroom

    reasons: list[str] = []
    if not enabled:
        reasons.append("hetzner_disabled")
    if not has_token:
        reasons.append("no_hcloud_token")
    if enabled and has_token and not usable:
        reasons.append(usable_reason)
    if not adequate:
        reasons.append(adequacy_reason)
    if spec and not within_budget:
        reasons.append(f"est_cost EUR{est_cost:.2f} over daily headroom EUR{day_headroom:.2f}")

    available = bool(enabled and has_token and usable and within_budget)
    reason = "available" if (available and adequate) else ("; ".join(reasons) or "unavailable")

    return {
        "backend": "hetzner",
        "available": available,
        "adequate": adequate,
        "account_usable": usable,
        "server_spec": spec,
        "est_cost": round(est_cost, 4),
        "est_cost_unit": "eur",
        "est_wall_h": round(est_wall_h, 3),
        "within_auto_approve": within_auto_approve,
        "reason": reason,
    }


def budget_gate(*, job_id: str, server_spec: dict[str, Any] | None, config: Any,
                state_root: Path, count: int = 1) -> dict[str, Any]:
    """Fail-closed pre-provision gate run on EVERY submit (mirrors the GitHub Actions
    minutes gate). Refuses if the pessimistic worst-case reservation exceeds the per-job
    cap (the auto-approve envelope -- a larger spend needs out-of-band human
    confirmation), exceeds the concurrent-server cap, or would push outstanding EUR past
    the daily cap. Reserves the worst case in the shared ledger (unit='eur') so
    concurrent submits cannot collectively overspend. Returns the reservation result or
    raises HetznerBudgetError."""
    if server_spec is None:
        raise HetznerBudgetError("fail-closed: no adequate Hetzner server type for this job")
    per_job = float(getattr(config, "hetzner_max_eur_per_job", 0.0))
    per_day = float(getattr(config, "hetzner_max_eur_per_day", 0.0))
    max_hours = float(getattr(config, "hetzner_max_server_hours", 0.0))
    max_concurrent = int(getattr(config, "hetzner_max_concurrent_servers", 0) or 0)
    n = max(1, int(count))
    if max_concurrent and n > max_concurrent:
        raise HetznerBudgetError(
            f"fail-closed: {n} servers over max_concurrent_servers {max_concurrent}")
    wc = worst_case_eur(server_spec, max_hours, n)
    if wc > per_job:
        raise HetznerBudgetError(
            f"worst-case EUR{wc:.2f} exceeds per-job cap EUR{per_job:.2f} "
            f"(above the auto-approve envelope; needs out-of-band human confirmation)")
    res = budget_ledger.check_and_reserve(
        state_root=Path(state_root), backend="hetzner", job_id=job_id,
        worst_case=wc, available=per_day, unit="eur",
    )
    res.update({"worst_case": round(wc, 4), "per_job_cap": per_job, "per_day_cap": per_day,
                "server_type": server_spec.get("name"), "count": n})
    if not res["ok"]:
        raise HetznerBudgetError(f"Hetzner budget refused: {res['reason']}")
    return res


def provision(*, server_spec: dict[str, Any], config: Any, job_id: str,
              confirm: bool = False) -> dict[str, Any]:
    """GUARDED STUB. Phase A performs no provisioning. Refuses without a token AND an
    explicit confirm; even with both, raises because the provisioning driver ships in a
    later phase. No `hcloud` call is ever made here."""
    if not token_present():
        raise HetznerError("refusing to provision: HCLOUD_TOKEN is not set")
    if not confirm:
        raise HetznerError(
            "refusing to provision: explicit confirm=True is required (plan->submit confirm gate)")
    raise NotImplementedError(
        "Hetzner provisioning driver is out of scope for Phase A (routing/budget/veto only)")


def teardown(*, config: Any, server_id: str | None = None, all_tagged: bool = False,
             confirm: bool = False) -> dict[str, Any]:
    """GUARDED STUB. Phase A performs no teardown. Returns a skip record unless a token
    AND an explicit confirm are both present; the reaper / `down` driver ships in a later
    phase. DELETE is the only thing that stops Hetzner billing, so the real teardown must
    run on every terminal path -- enforced in that later phase."""
    if not token_present() or not confirm:
        return {"ok": False, "skipped": True,
                "reason": "no credentials + confirm; Phase A performs no teardown"}
    raise NotImplementedError("Hetzner teardown / reaper driver is out of scope for Phase A")


def doctor(config: Any) -> dict[str, Any]:
    """Offline readiness snapshot -- no network call in Phase A. Reports whether the lane
    is enabled, the token is present, the `hcloud` CLI is installed, and the configured
    caps / server types. Live project and quota checks arrive with the driver later."""
    catalog = server_catalog(config)
    out: dict[str, Any] = {
        "hetzner_enabled": bool(getattr(config, "hetzner_enabled", False)),
        "token_present": token_present(),
        "hcloud_cli_available": shutil.which("hcloud") is not None,
        "server_types": sorted(catalog),
        "gpu_server_types": list(getattr(config, "hetzner_gpu_server_types", []) or []),
        "max_eur_per_job": float(getattr(config, "hetzner_max_eur_per_job", 0.0)),
        "max_eur_per_day": float(getattr(config, "hetzner_max_eur_per_day", 0.0)),
        "max_server_hours": float(getattr(config, "hetzner_max_server_hours", 0.0)),
        "max_concurrent_servers": int(getattr(config, "hetzner_max_concurrent_servers", 0) or 0),
        "location": getattr(config, "hetzner_location", None),
        "allowed_locations": list(getattr(config, "hetzner_allowed_locations", []) or []),
        "network_probe": "skipped (Phase A performs no network calls)",
    }
    out["ready_offline"] = bool(out["hetzner_enabled"] and out["token_present"])
    return out
