"""GitHub Actions compute backend for the research broker.

GitHub Actions ToS compliance: this dispatches ONLY to a PRIVATE research repo's own
committed `experiment.yml` (params are DATA inputs, never code), as that project's own
validation — never a general/serverless compute pool (see docs/github-actions-offload-
routing.md). Every dispatch is gated, fail-closed, on the account's available Actions
minutes (the billing usage API + a local reservation ledger), and reserves the worst case
(timeout x runner-multiplier x matrix-cells) so concurrent dispatches cannot over-spend.

It reuses the proven flow: `gh workflow run` -> correlate by an attempt-unique
`run-name == exp-<dispatch_id>` -> wait -> download artifacts from that exact run id.
"""
from __future__ import annotations

import json
import math
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import budget_ledger

GH_API_VERSION = "2026-03-10"
OS_MULTIPLIER = {"linux": 1.0, "ubuntu": 1.0, "windows": 2.0, "macos": 10.0}
PLAN_INCLUDED_MINUTES = {"free": 2000, "pro": 3000, "team": 3000, "enterprise": 50000}
DEFAULT_MAX_USAGE_FRACTION = 0.60


class GhaError(RuntimeError):
    pass


class GhaBudgetError(GhaError):
    pass


def runner_multiplier(runner_os: Any) -> float:
    """Return the documented billing multiplier; unknown labels fail closed."""
    name = str(runner_os or "linux").strip().lower()
    if name not in OS_MULTIPLIER:
        raise GhaError(
            f"unsupported runner_os '{name}'; expected one of {sorted(OS_MULTIPLIER)}"
        )
    return OS_MULTIPLIER[name]


def _gh(args: list[str], *, timeout: float = 60.0) -> str:
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GhaError(
            f"gh {args[0] if args else 'command'} unavailable: {exc.__class__.__name__}"
        ) from exc
    if proc.returncode != 0:
        # Do not echo argv or provider output: workflow inputs are carried on argv and may
        # contain job data. Callers need a fail-closed category, not a data-bearing transcript.
        raise GhaError(
            f"gh {args[0] if args else 'command'} failed with exit {proc.returncode}"
        )
    return proc.stdout


def _gh_api(path: str, *, api_version: str | None = None, timeout: float = 60.0) -> Any:
    args = ["api"]
    if api_version:
        args += ["-H", f"X-GitHub-Api-Version: {api_version}"]
    args.append(path)
    try:
        return json.loads(_gh(args, timeout=timeout) or "null")
    except json.JSONDecodeError as exc:
        raise GhaError("gh api returned malformed JSON") from exc


def _require_object(value: Any, context: str) -> dict[str, Any]:
    """Normalize an unexpected provider response shape into a fail-closed error."""
    if not isinstance(value, dict):
        raise GhaError(f"{context} response was not an object")
    return value


# ---- budget (fail-closed) ----------------------------------------------------

def included_minutes(owner: str, config: Any) -> int:
    override = getattr(config, "gha_included_minutes", 0)
    if override:
        return int(override)
    try:
        user = _require_object(_gh_api("/user"), "user API")
        plan_detail = user.get("plan")
        if not isinstance(plan_detail, dict):
            raise GhaError("user API response omitted plan object")
        plan_name = plan_detail.get("name")
        if not isinstance(plan_name, str):
            raise GhaError("user API response omitted plan name")
        plan = plan_name.lower()
    except GhaError:
        plan = ""
    return PLAN_INCLUDED_MINUTES.get(plan, 2000)


def minutes_used_this_cycle(owner: str) -> float:
    """Linux-equivalent Actions minutes used this billing month. Raises on any failure
    (fail-closed: an unverifiable budget must block, not proceed)."""
    now = datetime.now(timezone.utc)
    try:
        data = _require_object(
            _gh_api(
                f"/users/{owner}/settings/billing/usage?year={now.year}&month={now.month}",
                api_version=GH_API_VERSION,
            ),
            "billing usage API",
        )
    except GhaError as exc:
        raise GhaBudgetError(f"cannot verify billing usage: {exc}") from exc
    items = data.get("usageItems")
    if not isinstance(items, list):
        raise GhaBudgetError(
            "billing usage API returned invalid usageItems (cannot verify budget)"
        )
    used = 0.0
    for item in items:
        if not isinstance(item, dict):
            raise GhaBudgetError(
                "billing usage API returned a non-object usage item"
            )
        product = str(item.get("product", "")).lower()
        sku = str(item.get("sku", "")).lower()
        if "action" not in product or "storage" in sku:
            continue
        mult = 2.0 if "windows" in sku else (10.0 if "macos" in sku else 1.0)
        try:
            quantity = float(item.get("quantity", 0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise GhaBudgetError(
                "billing usage API returned an invalid quantity"
            ) from exc
        if not math.isfinite(quantity) or quantity < 0:
            raise GhaBudgetError("billing usage API returned an invalid quantity")
        used += quantity * mult
    return used


def worst_case_minutes(repo_cfg: dict[str, Any], cells: int = 1) -> float:
    timeout = min(int(repo_cfg.get("timeout_minutes", 30)), 360)
    mult = runner_multiplier(repo_cfg.get("runner_os", "linux"))
    return math.ceil(timeout) * mult * max(1, cells)


def job_adequacy(
    repo_cfg: dict[str, Any], constraints: dict[str, Any]
) -> tuple[bool, str]:
    """Check declared job requirements against the registered runner envelope.

    The standard private Linux runner defaults are deliberately conservative. A target
    using a larger runner must register its actual limits. ``core_hours`` is converted to
    a best-case wall time at the requested width, so omitting ``timeout_sec`` cannot let a
    job whose declared work exceeds the workflow timeout pass admission.
    """
    try:
        runner_multiplier(repo_cfg.get("runner_os", "linux"))
        timeout_minutes = min(float(repo_cfg.get("timeout_minutes", 30)), 360.0)
        requested_timeout = float(constraints.get("timeout_sec", 0) or 0)
        requested_memory = float(constraints.get("memory_mb", 0) or 0)
        max_memory = float(
            repo_cfg.get("max_memory_mb", repo_cfg.get("memory_mb", 7168)) or 7168
        )
        requested_cpu = float(constraints.get("cpu", 0) or 0)
        requested_parallelism = float(constraints.get("parallelism", 0) or 0)
        max_cpu = float(repo_cfg.get("max_cpu", repo_cfg.get("cpu", 2)) or 2)
        core_hours = float(constraints.get("core_hours", 0) or 0)
    except (GhaError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, GhaError):
            return False, str(exc)
        return False, "GHA runner requirements and limits must be finite numbers"

    numeric_values = (
        timeout_minutes,
        requested_timeout,
        requested_memory,
        max_memory,
        requested_cpu,
        requested_parallelism,
        max_cpu,
        core_hours,
    )
    if not all(math.isfinite(value) for value in numeric_values):
        return False, "GHA runner requirements and limits must be finite numbers"
    if timeout_minutes <= 0 or max_memory <= 0 or max_cpu <= 0:
        return False, "registered GHA timeout, CPU, and memory limits must be positive"
    if any(value < 0 for value in (
        requested_timeout,
        requested_memory,
        requested_cpu,
        requested_parallelism,
        core_hours,
    )):
        return False, "GHA job requirements must not be negative"

    if requested_timeout and requested_timeout > timeout_minutes * 60:
        return False, (
            f"requested timeout {requested_timeout:g}s exceeds registered GHA timeout "
            f"{timeout_minutes * 60:g}s"
        )

    resource_class = str(constraints.get("resource_class", "") or "").lower()
    if resource_class == "highmem_cpu" and "max_memory_mb" not in repo_cfg:
        return False, "high-memory runner capacity is not registered for this GHA target"
    if max_memory and requested_memory > max_memory:
        return False, (
            f"requested memory {requested_memory:g}MB exceeds registered GHA capacity "
            f"{max_memory:g}MB"
        )

    requested_width = max(requested_cpu, requested_parallelism)
    if requested_width > max_cpu:
        return False, (
            f"requested CPU/parallelism {requested_width:g} exceeds registered GHA "
            f"capacity {max_cpu:g}"
        )

    if core_hours:
        effective_width = max(1.0, requested_width)
        estimated_wall_seconds = core_hours / effective_width * 3600.0
        if estimated_wall_seconds > timeout_minutes * 60:
            return False, (
                f"declared {core_hours:g} core-hours need at least "
                f"{estimated_wall_seconds / 60:g} minutes at requested width "
                f"{effective_width:g}, exceeding the registered GHA timeout "
                f"{timeout_minutes:g} minutes"
            )
    return True, "gha_runner_adequate"


def repo_ready(
    repo_cfg: dict[str, Any], resources: Any = None
) -> tuple[bool, str]:
    """Verify authenticated access and the private-repository boundary.

    Offline callers may inject both booleans under ``liveness.gha``. Without that trusted
    snapshot, use a read-only repository API query and fail closed.
    """
    injected = _injected_usage(resources)
    if isinstance(injected, dict) and (
        "authenticated" in injected or "repo_private" in injected
    ):
        if "authenticated" not in injected or "repo_private" not in injected:
            return False, "gha_readiness_snapshot_incomplete"
        authenticated = injected["authenticated"]
        private = injected["repo_private"]
        if not isinstance(authenticated, bool) or not isinstance(private, bool):
            return False, "gha_readiness_snapshot_must_use_booleans"
        if not authenticated:
            return False, "gha_not_authenticated"
        if not private:
            return False, "gha_repo_not_private"
        return True, "gha_authenticated_private_repo"
    repo = str(repo_cfg.get("repo", "") or "")
    if not repo:
        return False, "gha_repo_not_registered"
    try:
        detail = _require_object(_gh_api(f"/repos/{repo}"), "repository API")
        private = detail.get("private")
        if not isinstance(private, bool):
            raise GhaError("repository API response omitted boolean private status")
    except GhaError as exc:
        return False, f"gha_repo_unreachable:{exc.__class__.__name__}"
    if not private:
        return False, "gha_repo_not_private"
    return True, "gha_authenticated_private_repo"


# ---- lane availability: cumulative 60% minutes cap (plan §6.1; mockable + gated) ----

def usage_probe(owner: str, config: Any) -> dict[str, float]:  # pragma: no cover - real API path, never exercised in tests
    """Actual CUMULATIVE account-wide Actions minutes used this cycle (all workflows) plus
    the plan's included minutes, from the GitHub billing/usage API. The single mockable
    hook for the lane's usage read; tests replace USAGE_PROBE (in-process) or inject a
    result via resources['liveness']['gha']. The real call fires only at plan/doctor time."""
    return {"used_this_cycle": float(minutes_used_this_cycle(owner)),
            "included_minutes": float(included_minutes(owner, config))}


USAGE_PROBE = usage_probe


def _injected_usage(resources: Any) -> dict[str, Any] | None:
    node = resources.get("liveness") if isinstance(resources, dict) else None
    node = node.get("gha") if isinstance(node, dict) else None
    return node if isinstance(node, dict) else None


def _outstanding_minutes(resources: Any, state_root: Path | None) -> float:
    node = resources.get("outstanding") if isinstance(resources, dict) else None
    injected = 0.0
    if isinstance(node, dict) and "gha" in node:
        try:
            value = float(node["gha"])
            injected = value if math.isfinite(value) and value >= 0 else math.inf
        except (TypeError, ValueError, OverflowError):
            injected = math.inf
    if state_root is not None:
        ledger = max(0.0, budget_ledger.outstanding(Path(state_root), "gha"))
        return max(injected, ledger)
    return injected


def usage_cap_ok(
    *,
    repo_cfg: dict[str, Any],
    config: Any,
    cells: int = 1,
    resources: Any = None,
    state_root: Path | None = None,
) -> tuple[bool, dict[str, Any]]:
    """GitHub Actions lane availability under the CUMULATIVE minutes cap (plan §6.1). The
    lane is available only while the account-wide minutes already used this cycle plus this
    job's worst case stay within ``max_usage_fraction`` of the included minutes:

        used_this_cycle + outstanding_reservations + job_worst_case
            <= max_usage_fraction * included_minutes

    ``used_this_cycle`` is the TOTAL cumulative usage across ALL workflows (not a per-job
    number), so the remaining ``1 - fraction`` of the monthly minutes is always reserved
    for the user's other workflows. Injection-first (resources['liveness']['gha']) offline;
    otherwise the real usage probe. Any usage-API failure => unavailable (fail-closed).
    Returns (ok, detail)."""
    owner = str(repo_cfg.get("repo", "")).split("/")[0]
    injected = _injected_usage(resources)
    try:
        if injected is not None:
            if "used_this_cycle" not in injected:
                return False, {
                    "ok": False,
                    "reason": "injected GHA usage snapshot is incomplete (fail-closed)",
                }
            used = float(injected["used_this_cycle"])
            inc = float(injected.get("included_minutes", 0.0)
                        or getattr(config, "gha_included_minutes", 0) or 0.0)
        else:
            probe = USAGE_PROBE(owner, config)
            used = float(probe["used_this_cycle"])
            inc = float(probe["included_minutes"])
        fraction = float(
            getattr(config, "gha_max_usage_fraction", DEFAULT_MAX_USAGE_FRACTION)
        )
        worst = float(worst_case_minutes(repo_cfg, cells))
        outstanding = _outstanding_minutes(resources, state_root)
    except (GhaError, KeyError, TypeError, ValueError, OverflowError) as exc:
        return False, {"ok": False, "reason": f"usage API unavailable (fail-closed): {exc}"}
    if (
        not all(math.isfinite(value) for value in (used, inc, fraction, worst, outstanding))
        or used < 0
        or inc <= 0
        or not 0 < fraction <= 1
        or worst <= 0
        or outstanding < 0
    ):
        return False, {
            "ok": False,
            "reason": "GHA usage snapshot or budget limits are invalid (fail-closed)",
        }
    cap = fraction * inc
    ok = inc > 0 and (used + outstanding + worst) <= cap
    detail = {
        "ok": ok, "used_this_cycle": round(used, 1), "included_minutes": inc,
        "outstanding_minutes": round(outstanding, 1),
        "worst_case": worst, "cap_minutes": round(cap, 1), "max_usage_fraction": fraction,
        "reason": ("within usage cap" if ok else
                   (f"used {used:.0f} + outstanding {outstanding:.0f} + worst "
                    f"{worst:.0f} > {fraction:.0%} cap "
                    f"({cap:.0f} of {inc:.0f} included)" if inc > 0 else "included_minutes unknown")),
    }
    return ok, detail


def budget_gate(*, job_id: str, repo_cfg: dict[str, Any], config: Any, state_root: Path,
                cells: int = 1) -> dict[str, Any]:
    """Fail-closed pre-flight gate run on EVERY submit. Returns the reservation result or
    raises GhaBudgetError."""
    owner = str(repo_cfg["repo"]).split("/")[0]
    try:
        used = minutes_used_this_cycle(owner)
        inc = included_minutes(owner, config)
    except GhaError as exc:
        raise GhaBudgetError(f"fail-closed: could not verify GHA budget ({exc})") from exc
    fraction = float(getattr(config, "gha_max_usage_fraction", DEFAULT_MAX_USAGE_FRACTION))
    fraction_headroom = max(0.0, fraction * inc - used)
    monthly_cap = float(repo_cfg.get("monthly_minute_budget", fraction_headroom))
    available = min(fraction_headroom, monthly_cap)
    wc = worst_case_minutes(repo_cfg, cells)
    res = budget_ledger.check_and_reserve(
        state_root=state_root, backend="gha", job_id=job_id,
        worst_case=wc, available=available, unit="minutes",
    )
    res.update({
        "included": inc,
        "used_equiv": round(used, 1),
        "worst_case": wc,
        "cells": cells,
        "max_usage_fraction": fraction,
        "fraction_cap_minutes": round(fraction * inc, 1),
    })
    if not res["ok"]:
        raise GhaBudgetError(f"GHA budget refused: {res['reason']}")
    return res


# ---- dispatch / correlate / wait / fetch (the proven flow) -------------------

def dispatch(*, job_id: str, repo_cfg: dict[str, Any], parameters: dict[str, Any]) -> None:
    repo = repo_cfg["repo"]
    _gh([
        "workflow", "run", repo_cfg.get("workflow", "experiment.yml"), "-R", repo,
        "--ref", repo_cfg.get("ref", "main"),
        "-f", f"runtime={repo_cfg['runtime']}",
        "-f", f"experiment={repo_cfg['experiment']}",
        "-f", f"params_json={json.dumps(parameters)}",
        "-f", f"job_id={job_id}",
    ])


def correlate(
    *,
    repo: str,
    workflow: str,
    job_id: str,
    attempts: int = 12,
    interval: float = 5.0,
    ref: str | None = None,
    not_before: str | None = None,
) -> int | None:
    """Find the exact attempt run when ``workflow_dispatch`` returns no run id.

    ``job_id`` is the attempt-unique dispatch id. Optional ref/time checks add a second
    boundary against stale runs; the unique run-name remains the primary correlation key.
    """
    title = f"exp-{job_id}"
    threshold: datetime | None = None
    if not_before:
        try:
            threshold = datetime.fromisoformat(not_before.replace("Z", "+00:00"))
            if threshold.tzinfo is None:
                threshold = threshold.replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise GhaError("invalid dispatch timestamp for GHA correlation") from exc
    for _ in range(attempts):
        rows = _require_object(
            _gh_api(f"/repos/{repo}/actions/workflows/{workflow}/runs?per_page=20"),
            "workflow runs API",
        )
        runs = rows.get("workflow_runs")
        if not isinstance(runs, list):
            raise GhaError("workflow runs API response omitted workflow_runs list")
        for run in runs:
            if not isinstance(run, dict):
                raise GhaError("workflow runs API returned a non-object run")
            if run.get("display_title") != title:
                continue
            if run.get("event") not in (None, "workflow_dispatch"):
                continue
            if ref and run.get("head_branch") not in (None, ref):
                continue
            if threshold is not None:
                created_raw = run.get("created_at")
                if not isinstance(created_raw, str):
                    continue
                try:
                    created = datetime.fromisoformat(
                        created_raw.replace("Z", "+00:00")
                    )
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if created < threshold:
                    continue
            run_id = run.get("id")
            if isinstance(run_id, bool):
                raise GhaError("workflow runs API returned an invalid run id")
            try:
                return int(run_id)
            except (TypeError, ValueError, OverflowError) as exc:
                raise GhaError("workflow runs API returned an invalid run id") from exc
        time.sleep(interval)
    return None


def wait(*, repo: str, run_id: int, timeout: float | None = None, interval: float = 20.0) -> str:
    start = time.time()
    while True:
        run = _require_object(
            _gh_api(f"/repos/{repo}/actions/runs/{run_id}"),
            "workflow run API",
        )
        if run.get("status") == "completed":
            return str(run.get("conclusion"))
        if timeout is not None and time.time() - start > timeout:
            return "timeout"
        time.sleep(interval)


def fetch(
    *,
    repo: str,
    job_id: str,
    dest: Path,
    state_root: Path,
    run_id: int,
    runner_os: str = "linux",
    cells: int = 1,
) -> dict[str, Any]:
    """Download the attempt artifact from exactly ``run_id``.

    ``job_id`` is the attempt-unique dispatch/reservation id. The reservation is reconciled
    only when timing is positively verified; its actual remains locally accrued for the UTC
    billing cycle so provider-reporting lag cannot reopen budget headroom.
    """
    dest.mkdir(parents=True, exist_ok=True)
    try:
        _gh([
            "run", "download", str(run_id), "-R", repo,
            "-n", f"result-{job_id}", "-D", str(dest),
        ])
        files = list(dest.glob("*.json"))
        result = json.loads(files[0].read_text()) if files else {"status": "no-artifact"}
    except GhaError:
        result = {"status": "no-artifact"}
    actual = None
    try:
        usage = _gh_api(f"/repos/{repo}/actions/runs/{run_id}/timing")
        if not isinstance(usage, dict):
            raise GhaError("GHA timing response was not an object")
        billable = usage.get("billable")
        if isinstance(billable, dict) and billable:
            billed_equivalent = 0.0
            observed = False
            for os_name, detail in billable.items():
                if not isinstance(detail, dict) or "total_ms" not in detail:
                    raise GhaError("GHA billable timing entry omitted total_ms")
                duration_ms = float(detail["total_ms"])
                if not math.isfinite(duration_ms) or duration_ms < 0:
                    raise GhaError("GHA billable timing contained an invalid duration")
                name = str(os_name).lower()
                multiplier = (
                    10.0 if "mac" in name else (2.0 if "windows" in name else 1.0)
                )
                billed_equivalent += math.ceil(duration_ms / 60000.0) * multiplier
                observed = True
            if observed:
                actual = billed_equivalent
        elif "run_duration_ms" in usage:
            duration_ms = float(usage["run_duration_ms"])
            if not math.isfinite(duration_ms) or duration_ms < 0:
                raise GhaError("GHA timing response contained an invalid duration")
            multiplier = runner_multiplier(runner_os)
            # The aggregate field may be wall time rather than summed matrix-job time.
            # Multiplying by cells is conservative and preserves the gate's accounting unit.
            actual = (
                math.ceil(duration_ms / 60000.0)
                * multiplier
                * max(1, int(cells))
            )
        else:
            raise GhaError("GHA timing response omitted verifiable billable duration")
    except (GhaError, TypeError, ValueError, OverflowError):
        actual = None
    if actual is not None:
        budget_ledger.reconcile(state_root, "gha", job_id, actual)
    return {"result": result, "actual_minutes": actual}


def doctor(config: Any) -> dict[str, Any]:
    """Validate gh auth/scopes (classic PAT with billing-read), billing reachability, and
    that the registered repos are private. Drives `gha_enabled`."""
    out: dict[str, Any] = {"gha_enabled": bool(getattr(config, "gha_enabled", False))}
    try:
        status = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=20)
        out["gh_authenticated"] = status.returncode == 0
        out["billing_scope"] = "user" in (status.stdout + status.stderr)
    except Exception as exc:  # pragma: no cover
        out["gh_authenticated"] = False
        out["error"] = str(exc)
    repos = getattr(config, "gha_repos", {}) or {}
    out["registered_targets"] = sorted(repos)
    private = {}
    for key, cfg in repos.items():
        try:
            vis = _require_object(
                _gh_api(f"/repos/{cfg['repo']}"), "repository API"
            )
            private_value = vis.get("private")
            if not isinstance(private_value, bool):
                raise GhaError(
                    "repository API response omitted boolean private status"
                )
            private[cfg["repo"]] = private_value
        except GhaError:
            private[cfg["repo"]] = None
    out["repos_private"] = private
    try:
        owner = next(iter(repos.values()))["repo"].split("/")[0] if repos else None
        out["minutes_used_this_cycle"] = round(minutes_used_this_cycle(owner), 1) if owner else None
        out["billing_readable"] = True
    except GhaError as exc:
        out["billing_readable"] = False
        out["billing_error"] = str(exc)
    out["ready"] = bool(
        out.get("gh_authenticated") and out.get("billing_scope")
        and out.get("billing_readable") and all(v is True for v in private.values())
    )
    return out
