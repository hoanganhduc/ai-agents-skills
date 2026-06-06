from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .opencode import run_opencode_native_smoke
from .capabilities import smoke_artifact
from .runtime_smoke import run_installed_runtime_smoke
from .state import load_state, preflight_state_path, save_state, state_dir, validate_run_id, write_text_atomic
from .verify import verify as verify_state


POST_INSTALL_SMOKE_MODES = ("auto", "verify", "strict", "off")
NEUTRAL_STATUSES = {"ok", "skipped", "no-managed-artifacts"}
DEGRADED_STATUSES = {"degraded", "unsupported"}
FAILED_STATUSES = {"failed", "error", "missing"}


def run_post_install_smoke(
    root: Path,
    manifests: dict[str, Any],
    apply_result: dict[str, Any],
    *,
    skills: set[str] | None = None,
    agents: set[str] | None = None,
    platform: str | None = None,
    mode: str = "auto",
    timeout: int = 60,
) -> dict[str, Any]:
    if mode not in POST_INSTALL_SMOKE_MODES:
        raise ValueError(f"unknown post-install smoke mode: {mode}")
    if timeout <= 0:
        raise ValueError("post-install smoke timeout must be positive")
    run_id = validate_run_id(apply_result["run_id"])
    result: dict[str, Any] = {
        "schema_version": "post-install-smoke.v1",
        "mode": mode,
        "run_id": run_id,
        "apply_status": "ok",
        "selected_skills": sorted(skills) if skills else None,
        "selected_agents": sorted(agents) if agents else None,
    }
    if apply_result.get("dry_run"):
        result.update({"status": "skipped", "reason": "post-install smoke is not run for dry-run installs"})
        return result
    if not apply_result.get("actions"):
        result.update({"status": "skipped", "reason": "no install actions were applied"})
        return result
    if mode == "off":
        result.update({"status": "skipped", "reason": "post-install smoke disabled"})
        return result

    result["verify"] = guarded_check("verify", lambda: verify_state(root, skills, agents))
    if mode in {"auto", "strict"}:
        result["skill_smoke"] = guarded_check("skill-smoke", lambda: smoke_state(root, skills, agents))
        result["runtime_smoke"] = guarded_check(
            "runtime-smoke",
            lambda: run_installed_runtime_smoke(
                root,
                manifests,
                skills=skills,
                agents=agents,
                platform=platform,
                timeout=timeout,
            ),
        )
        result["opencode_smoke"] = guarded_check(
            "opencode-smoke",
            lambda: run_opencode_native_smoke(
                root,
                agents=agents,
                platform=platform,
                timeout=timeout,
            ),
        )
    else:
        result["skill_smoke"] = {"status": "skipped", "reason": "mode verify runs only installer integrity checks"}
        result["runtime_smoke"] = {"status": "skipped", "reason": "mode verify runs only installer integrity checks"}
        result["opencode_smoke"] = {"status": "skipped", "reason": "mode verify runs only installer integrity checks"}

    result["status"] = aggregate_status(
        result["verify"],
        result["skill_smoke"],
        result["runtime_smoke"],
        result["opencode_smoke"],
    )
    write_report_and_state_summary(root, run_id, result)
    return result


def smoke_state(root: Path, skills: set[str] | None = None, agents: set[str] | None = None) -> dict[str, Any]:
    state = load_state(root)
    results = [
        smoke_artifact(item)
        for item in state.get("artifacts", [])
        if item.get("artifact_type") == "skill-file"
        and (not skills or item.get("skill") in skills)
        and (not agents or item.get("agent") in agents)
    ]
    if not results:
        return {
            "status": "no-managed-artifacts",
            "checked": 0,
            "results": [],
            "reason": "no managed skill-file artifacts matched this scope",
        }
    status = "ok" if all(item["status"] == "ok" for item in results) else "degraded"
    return {"status": status, "checked": len(results), "results": results}


def guarded_check(name: str, check: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return check()
    except Exception as exc:
        return {"status": "failed", "check": name, "error": str(exc)}


def aggregate_status(*checks: dict[str, Any]) -> str:
    statuses = [str(check.get("status", "failed")) for check in checks]
    if any(status in FAILED_STATUSES for status in statuses):
        return "failed"
    if any(status in DEGRADED_STATUSES for status in statuses):
        return "degraded"
    if all(status in NEUTRAL_STATUSES for status in statuses):
        return "ok"
    return "failed"


def write_report_and_state_summary(root: Path, run_id: str, result: dict[str, Any]) -> None:
    report_path = state_dir(root) / "runs" / f"{run_id}.post-install-smoke.json"
    result["report_path"] = str(report_path)
    try:
        preflight_state_path(root, report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(report_path, json.dumps(result, indent=2, sort_keys=True) + "\n")
    except Exception as exc:
        result["report_status"] = "failed"
        result["report_error"] = str(exc)
        return
    result["report_status"] = "ok"
    try:
        state = load_state(root)
        summary = compact_summary(result)
        for run in state.get("runs", []):
            if run.get("run_id") == run_id:
                run["post_install"] = summary
                save_state(root, state)
                break
    except Exception as exc:
        result["state_summary_status"] = "failed"
        result["state_summary_error"] = str(exc)


def compact_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": result.get("mode"),
        "status": result.get("status"),
        "verify_status": result.get("verify", {}).get("status"),
        "skill_smoke_status": result.get("skill_smoke", {}).get("status"),
        "runtime_smoke_status": result.get("runtime_smoke", {}).get("status"),
        "opencode_smoke_status": result.get("opencode_smoke", {}).get("status"),
        "report_path": result.get("report_path"),
    }
