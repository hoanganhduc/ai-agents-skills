"""GitHub Actions compute backend for the research broker.

GitHub Actions ToS compliance: this dispatches ONLY to a PRIVATE research repo's own
committed `experiment.yml` (params are DATA inputs, never code), as that project's own
validation — never a general/serverless compute pool (see docs/github-actions-offload-
routing.md). Every dispatch is gated, fail-closed, on the account's available Actions
minutes (the billing usage API + a local reservation ledger), and reserves the worst case
(timeout x runner-multiplier x matrix-cells) so concurrent dispatches cannot over-spend.

It reuses the proven flow: `gh workflow run` -> correlate by `run-name == exp-<job_id>`
-> `gh run watch` -> `gh run download`.
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


def _gh(args: list[str], *, timeout: float = 60.0) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise GhaError(f"gh {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def _gh_api(path: str, *, api_version: str | None = None, timeout: float = 60.0) -> Any:
    args = ["api"]
    if api_version:
        args += ["-H", f"X-GitHub-Api-Version: {api_version}"]
    args.append(path)
    return json.loads(_gh(args, timeout=timeout) or "null")


# ---- budget (fail-closed) ----------------------------------------------------

def included_minutes(owner: str, config: Any) -> int:
    override = getattr(config, "gha_included_minutes", 0)
    if override:
        return int(override)
    try:
        plan = str((_gh_api("/user") or {}).get("plan", {}).get("name", "")).lower()
    except GhaError:
        plan = ""
    return PLAN_INCLUDED_MINUTES.get(plan, 2000)


def minutes_used_this_cycle(owner: str) -> float:
    """Linux-equivalent Actions minutes used this billing month. Raises on any failure
    (fail-closed: an unverifiable budget must block, not proceed)."""
    now = datetime.now(timezone.utc)
    data = _gh_api(
        f"/users/{owner}/settings/billing/usage?year={now.year}&month={now.month}",
        api_version=GH_API_VERSION,
    )
    items = (data or {}).get("usageItems")
    if items is None:
        raise GhaBudgetError("billing usage API returned no usageItems (cannot verify budget)")
    used = 0.0
    for item in items:
        product = str(item.get("product", "")).lower()
        sku = str(item.get("sku", "")).lower()
        if "action" not in product or "storage" in sku:
            continue
        mult = 2.0 if "windows" in sku else (10.0 if "macos" in sku else 1.0)
        used += float(item.get("quantity", 0)) * mult
    return used


def worst_case_minutes(repo_cfg: dict[str, Any], cells: int = 1) -> float:
    timeout = min(int(repo_cfg.get("timeout_minutes", 30)), 360)
    mult = OS_MULTIPLIER.get(str(repo_cfg.get("runner_os", "linux")).lower(), 1.0)
    return math.ceil(timeout) * mult * max(1, cells)


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


def usage_cap_ok(*, repo_cfg: dict[str, Any], config: Any, cells: int = 1,
                 resources: Any = None) -> tuple[bool, dict[str, Any]]:
    """GitHub Actions lane availability under the CUMULATIVE minutes cap (plan §6.1). The
    lane is available only while the account-wide minutes already used this cycle plus this
    job's worst case stay within ``max_usage_fraction`` of the included minutes:

        used_this_cycle + job_worst_case <= max_usage_fraction * included_minutes

    ``used_this_cycle`` is the TOTAL cumulative usage across ALL workflows (not a per-job
    number), so the remaining ``1 - fraction`` of the monthly minutes is always reserved
    for the user's other workflows. Injection-first (resources['liveness']['gha']) offline;
    otherwise the real usage probe. Any usage-API failure => unavailable (fail-closed).
    Returns (ok, detail)."""
    fraction = float(getattr(config, "gha_max_usage_fraction", DEFAULT_MAX_USAGE_FRACTION))
    owner = str(repo_cfg.get("repo", "")).split("/")[0]
    injected = _injected_usage(resources)
    try:
        if injected is not None:
            used = float(injected.get("used_this_cycle", 0.0))
            inc = float(injected.get("included_minutes", 0.0)
                        or getattr(config, "gha_included_minutes", 0) or 0.0)
        else:
            probe = USAGE_PROBE(owner, config)
            used = float(probe["used_this_cycle"])
            inc = float(probe["included_minutes"])
    except GhaError as exc:
        return False, {"ok": False, "reason": f"usage API unavailable (fail-closed): {exc}"}
    worst = float(worst_case_minutes(repo_cfg, cells))
    cap = fraction * inc
    ok = inc > 0 and (used + worst) <= cap
    detail = {
        "ok": ok, "used_this_cycle": round(used, 1), "included_minutes": inc,
        "worst_case": worst, "cap_minutes": round(cap, 1), "max_usage_fraction": fraction,
        "reason": ("within usage cap" if ok else
                   (f"used {used:.0f} + worst {worst:.0f} > {fraction:.0%} cap "
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
    available = inc - used
    monthly_cap = float(repo_cfg.get("monthly_minute_budget", available))
    available = min(available, monthly_cap)
    wc = worst_case_minutes(repo_cfg, cells)
    res = budget_ledger.check_and_reserve(
        state_root=state_root, backend="gha", job_id=job_id,
        worst_case=wc, available=available, unit="minutes",
    )
    res.update({"included": inc, "used_equiv": round(used, 1), "worst_case": wc, "cells": cells})
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


def correlate(*, repo: str, workflow: str, job_id: str, attempts: int = 12,
              interval: float = 5.0) -> int | None:
    """Find the run id by run-name == exp-<job_id> (workflow_dispatch returns no id)."""
    title = f"exp-{job_id}"
    for _ in range(attempts):
        rows = _gh_api(f"/repos/{repo}/actions/workflows/{workflow}/runs?per_page=20")
        for run in (rows or {}).get("workflow_runs", []):
            if run.get("display_title") == title or run.get("name") == title:
                return int(run["id"])
        time.sleep(interval)
    return None


def wait(*, repo: str, run_id: int, timeout: float | None = None, interval: float = 20.0) -> str:
    start = time.time()
    while True:
        run = _gh_api(f"/repos/{repo}/actions/runs/{run_id}")
        if run.get("status") == "completed":
            return str(run.get("conclusion"))
        if timeout is not None and time.time() - start > timeout:
            return "timeout"
        time.sleep(interval)


def fetch(*, repo: str, job_id: str, dest: Path, state_root: Path,
          run_id: int | None = None) -> dict[str, Any]:
    """Download result-<job_id> back here; reconcile the reservation to actual billed
    minutes when the run id is known."""
    dest.mkdir(parents=True, exist_ok=True)
    try:
        _gh(["run", "download", "-R", repo, "-n", f"result-{job_id}", "-D", str(dest)])
        files = list(dest.glob("*.json"))
        result = json.loads(files[0].read_text()) if files else {"status": "no-artifact"}
    except GhaError:
        result = {"status": "no-artifact"}
    actual = None
    if run_id is not None:
        try:
            usage = _gh_api(f"/repos/{repo}/actions/runs/{run_id}/timing")
            actual = float(usage.get("run_duration_ms", 0)) / 60000.0
        except GhaError:
            actual = None
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
            vis = _gh_api(f"/repos/{cfg['repo']}")
            private[cfg["repo"]] = bool(vis.get("private"))
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
