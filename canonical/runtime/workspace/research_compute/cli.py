from __future__ import annotations

import argparse
import importlib.util
import json
import re
import secrets
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DEFAULT_ROUTING_ORDER, caller_cwd, default_config_path, example_config_path, load_config, modal_config_path, routing_order_error, workspace_root
from .modal_backend import cancel_function_call, deploy_modal_app, modal_ready_summary, run_remote_job, submit_remote_job, wait_for_result
from . import github_actions_backend as gha
from .fanout import plan_fanout
from .planner import normalize_job, plan_job
from .state import append_event, attempt_dir, ensure_root, job_dir, manifest_path, next_attempt_id, plan_path, read_json, status_path, write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-compute", description="Codex broker for Modal-backed research compute.")
    parser.add_argument("--config", default=None, help="Path to research-compute.toml")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Check broker, Modal, and config readiness")

    plan_parser = subparsers.add_parser("plan", help="Plan a broker manifest")
    plan_parser.add_argument("manifest", help="Path to a job manifest JSON file or '-' for stdin")

    fanout_parser = subparsers.add_parser(
        "fanout-plan",
        help="Plan a multi-backend parallel fan-out for a large divisible job (M chunks)",
    )
    fanout_parser.add_argument("manifest", help="Path to a job manifest JSON file or '-' for stdin")

    submit_parser = subparsers.add_parser("submit", help="Submit a broker manifest")
    submit_parser.add_argument("manifest", help="Path to a job manifest JSON file or '-' for stdin")
    submit_parser.add_argument("--wait", action="store_true", help="Wait for completion after submission")
    submit_parser.add_argument("--timeout", type=float, default=None, help="Maximum seconds to wait when --wait is used")

    wait_parser = subparsers.add_parser("wait", help="Wait on a submitted remote job")
    wait_parser.add_argument("job_id")
    wait_parser.add_argument("--timeout", type=float, default=None)

    fetch_parser = subparsers.add_parser("fetch", help="Materialize result artifacts locally")
    fetch_parser.add_argument("job_id")
    fetch_parser.add_argument("--dest", default=None)

    cancel_parser = subparsers.add_parser("cancel", help="Cancel a submitted remote job")
    cancel_parser.add_argument("job_id")

    resume_parser = subparsers.add_parser("resume", help="Resume by re-submitting a stored manifest")
    resume_parser.add_argument("job_id")
    resume_parser.add_argument("--wait", action="store_true")
    resume_parser.add_argument("--timeout", type=float, default=None)

    subparsers.add_parser("deploy", help="Deploy the shared Modal app using the current config")

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help="One-time setup: generate config if absent, authenticate gh, check deps, run doctor",
    )
    bootstrap_parser.add_argument("--install-deps", action="store_true", help="pip install --user any missing required deps")
    bootstrap_parser.add_argument("--no-auth", action="store_true", help="skip the gh authentication step")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root = workspace_root()
    config_path = Path(args.config).expanduser().resolve() if args.config else default_config_path(root)

    if args.command == "bootstrap":
        try:
            result = command_bootstrap(
                config_path=config_path,
                root=root,
                install_deps=args.install_deps,
                auth=not args.no_auth,
            )
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            return 1
        print(json.dumps({"ok": True, **result}, indent=2))
        return 0

    config = load_config(config_path)
    state_root = ensure_root(config.state_root(root))

    try:
        if args.command == "doctor":
            result = command_doctor(config=config, config_path=config_path, state_root=state_root)
        elif args.command == "plan":
            job = load_manifest(args.manifest)
            result = command_plan(job=job, config=config, state_root=state_root, persist=True)
        elif args.command == "fanout-plan":
            job = load_manifest(args.manifest)
            result = command_fanout_plan(job=job, config=config, state_root=state_root)
        elif args.command == "submit":
            job = load_manifest(args.manifest)
            result = command_submit(
                job=job,
                config=config,
                config_path=config_path,
                state_root=state_root,
                wait=args.wait,
                timeout=args.timeout,
            )
        elif args.command == "wait":
            result = command_wait(job_id=args.job_id, config=config, state_root=state_root, timeout=args.timeout)
        elif args.command == "fetch":
            result = command_fetch(job_id=args.job_id, config=config, state_root=state_root, dest=args.dest)
        elif args.command == "cancel":
            result = command_cancel(job_id=args.job_id, state_root=state_root)
        elif args.command == "resume":
            result = command_resume(
                job_id=args.job_id,
                config=config,
                config_path=config_path,
                state_root=state_root,
                wait=args.wait,
                timeout=args.timeout,
            )
        elif args.command == "deploy":
            result = command_deploy(config=config, root=root)
        else:  # pragma: no cover - argparse guards this
            raise RuntimeError(f"Unhandled command: {args.command}")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1

    print(json.dumps({"ok": True, **result}, indent=2))
    return 0


def command_doctor(*, config: Any, config_path: Path, state_root: Path) -> dict[str, Any]:
    modal_summary = modal_ready_summary(config, modal_config_path())
    configured_order = getattr(config, "routing_order", [])
    routing_order = (
        list(configured_order)
        if isinstance(configured_order, (list, tuple))
        else configured_order
    )
    summary = {
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "example_config_path": str(example_config_path(workspace_root())),
        "workspace_root": str(workspace_root()),
        "caller_cwd": str(caller_cwd()),
        "state_root": str(state_root),
        "routing_order": routing_order,
        **modal_summary,
    }
    recommended_order = list(DEFAULT_ROUTING_ORDER)
    summary["warnings"] = []
    validation_error = routing_order_error(routing_order)
    if validation_error:
        summary["warnings"].append(
            {
                "code": "routing_order_invalid",
                "message": (
                    "Configured routing_order is invalid; automatic planning will reject "
                    "until it is corrected."
                ),
                "configured": routing_order,
                "error": validation_error,
            }
        )
    if routing_order != recommended_order and not validation_error:
        summary["warnings"].append(
            {
                "code": "routing_order_deviation",
                "message": (
                    "Configured routing_order differs from the recommended priority; "
                    "the planner will honor the configured order."
                ),
                "configured": routing_order,
                "recommended": recommended_order,
            }
        )
    if getattr(config, "gha_enabled", False):
        try:
            summary["gha"] = gha.doctor(config)
        except Exception as exc:  # noqa: BLE001
            summary["gha"] = {"ready": False, "error": str(exc)}
    return summary


def command_plan(*, job: dict[str, Any], config: Any, state_root: Path, persist: bool) -> dict[str, Any]:
    normalized = normalize_job(job, config=config)
    plan = plan_job(
        normalized,
        config=config,
        resources=load_local_resources(),
        modal_ready=modal_ready_summary(config, modal_config_path())["modal_ready"],
        state_root=state_root,
    )
    if persist:
        persist_plan(state_root=state_root, job=normalized, plan=plan)
    return {
        "job_id": normalized["job_id"],
        "job": normalized,
        "plan": plan,
    }


def command_fanout_plan(*, job: dict[str, Any], config: Any, state_root: Path) -> dict[str, Any]:
    """Plan a multi-backend parallel fan-out for a large divisible job. Distinct from the
    single-lane `plan`: it splits the job's M chunks across several lanes at once, each sized
    to its spare capacity, under every lane's hard rail. Returns the allocation and per-lane
    chunk-id ranges; it does not dispatch (execution reuses the per-lane drivers)."""
    normalized = normalize_job(job, config=config)
    modal_ready = modal_ready_summary(config, modal_config_path())["modal_ready"]
    fanout = plan_fanout(
        normalized,
        config=config,
        resources=load_local_resources(),
        modal_ready=modal_ready,
        state_root=state_root,
    )
    return {"job_id": normalized["job_id"], "job": normalized, "fanout": fanout}


def command_submit(
    *,
    job: dict[str, Any],
    config: Any,
    config_path: Path,
    state_root: Path,
    wait: bool,
    timeout: float | None,
) -> dict[str, Any]:
    planning = command_plan(job=job, config=config, state_root=state_root, persist=True)
    normalized = planning["job"]
    plan = planning["plan"]
    job_id = normalized["job_id"]

    if not plan["accepted"]:
        update_status(
            state_root=state_root,
            job_id=job_id,
            status="rejected",
            plan=plan,
        )
        return {
            "job_id": job_id,
            "status": "rejected",
            "plan": plan,
        }

    if plan["decision"] == "gha":
        return command_submit_gha(
            job=normalized, plan=plan, config=config, config_path=config_path,
            state_root=state_root, wait=wait, timeout=timeout,
        )

    selected_backend = str(plan.get("backend", "") or "")
    if selected_backend in {"kaggle", "hetzner"}:
        lane_skill = f"{selected_backend}-research-compute"
        update_status(
            state_root=state_root,
            job_id=job_id,
            status="external_driver_required",
            plan=plan,
        )
        return {
            "job_id": job_id,
            "status": "external_driver_required",
            "plan": plan,
            "message": (
                f"The broker selected {selected_backend}; execute this plan through the "
                f"{lane_skill} lane driver."
            ),
        }

    if not str(plan["decision"]).startswith("modal_"):
        update_status(
            state_root=state_root,
            job_id=job_id,
            status="local_only",
            plan=plan,
        )
        return {
            "job_id": job_id,
            "status": "local_only",
            "plan": plan,
            "message": "The broker kept this workload local; execute it outside the Modal submit path.",
        }

    attempt_id = next_attempt_id(state_root, job_id)
    attempt_root = ensure_root(attempt_dir(state_root, job_id, attempt_id))

    if wait:
        execution = run_remote_job(job=normalized, plan=plan, config=config)
        submission_record = {
            "job_id": job_id,
            "attempt_id": attempt_id,
            "submitted_at": timestamp(),
            "decision": plan["decision"],
            "execution_primitive": plan["execution_primitive"],
            "function_name": execution["function_name"],
            "function_call_id": None,
            "mode": "synchronous_remote",
        }
        write_json(attempt_root / "submission.json", submission_record)
        write_json(attempt_root / "result.json", execution["result_manifest"])
        append_event(job_dir(state_root, job_id) / "events.jsonl", {"event": "submitted", **submission_record})
        append_event(job_dir(state_root, job_id) / "events.jsonl", {"event": "completed", "job_id": job_id, "attempt_id": attempt_id})
        update_status(
            state_root=state_root,
            job_id=job_id,
            status="completed",
            plan=plan,
            attempt_id=attempt_id,
            function_call_id=None,
        )
        return {
            "job_id": job_id,
            "status": "completed",
            "attempt_id": attempt_id,
            "function_name": execution["function_name"],
            "plan": plan,
            "config_path": str(config_path),
            "result_manifest_path": str(attempt_root / "result.json"),
        }

    submission = submit_remote_job(job=normalized, plan=plan, config=config)
    submission_record = {
        "job_id": job_id,
        "attempt_id": attempt_id,
        "submitted_at": timestamp(),
        "decision": plan["decision"],
        "execution_primitive": plan["execution_primitive"],
        "function_name": submission["function_name"],
        "function_call_id": submission["function_call_id"],
    }
    write_json(attempt_root / "submission.json", submission_record)
    append_event(job_dir(state_root, job_id) / "events.jsonl", {"event": "submitted", **submission_record})
    update_status(
        state_root=state_root,
        job_id=job_id,
        status="submitted",
        plan=plan,
        attempt_id=attempt_id,
        function_call_id=submission["function_call_id"],
    )

    result: dict[str, Any] = {
        "job_id": job_id,
        "status": "submitted",
        "attempt_id": attempt_id,
        "function_call_id": submission["function_call_id"],
        "function_name": submission["function_name"],
        "plan": plan,
        "config_path": str(config_path),
    }
    return result


def command_submit_gha(
    *,
    job: dict[str, Any],
    plan: dict[str, Any],
    config: Any,
    config_path: Path,
    state_root: Path,
    wait: bool,
    timeout: float | None,
) -> dict[str, Any]:
    """Submit to GitHub Actions: fail-closed minutes budget gate (reserve worst case) ->
    dispatch the private repo's experiment.yml -> correlate by run-name -> optional wait/fetch."""
    job_id = job["job_id"]
    template = str(job.get("template", "") or "")
    gha_target = str(job.get("gha_target", "") or template)
    repo_cfg = dict((config.gha_repos or {}).get(gha_target, {}))
    if not repo_cfg:
        update_status(state_root=state_root, job_id=job_id, status="rejected", plan=plan)
        return {"job_id": job_id, "status": "rejected", "plan": plan,
                "message": f"gha target '{gha_target}' is not registered"}

    parameters = dict(job.get("payload", {}).get("parameters", {}) or {})
    constraints = dict(job.get("constraints", {}) or {})
    cells = int(constraints.get("matrix_cells", 1) or 1)

    gha_constraints = dict(constraints)
    core_hours = gha_constraints.get("core_hours")
    if core_hours in (None, 0, 0.0):
        core_hours = parameters.get("core_hours")
    if core_hours in (None, 0, 0.0):
        core_hours = float(plan.get("estimated_runtime_sec", 0) or 0) / 3600.0
    gha_constraints["core_hours"] = core_hours
    adequate, adequacy_reason = gha.job_adequacy(repo_cfg, gha_constraints)
    if not adequate:
        update_status(state_root=state_root, job_id=job_id, status="rejected", plan=plan)
        return {"job_id": job_id, "status": "rejected", "plan": plan,
                "reason": f"GHA runner inadequate: {adequacy_reason}"}
    ready, ready_reason = gha.repo_ready(repo_cfg)
    if not ready:
        update_status(state_root=state_root, job_id=job_id, status="rejected", plan=plan)
        return {"job_id": job_id, "status": "rejected", "plan": plan,
                "reason": f"GHA readiness failed: {ready_reason}"}

    nonce = secrets.token_hex(16)
    attempt_id = f"{next_attempt_id(state_root, job_id)}-{nonce[:8]}"
    dispatch_id = f"gha-{nonce}"
    attempt_root = ensure_root(attempt_dir(state_root, job_id, attempt_id))
    workflow = repo_cfg.get("workflow", "experiment.yml")
    ref = str(repo_cfg.get("ref", "main"))
    runner_os = str(repo_cfg.get("runner_os", "linux"))
    submission_record = {
        "job_id": job_id,
        "attempt_id": attempt_id,
        "gha_dispatch_id": dispatch_id,
        "decision": "gha",
        "gha_repo": repo_cfg["repo"],
        "gha_workflow": workflow,
        "gha_ref": ref,
        "gha_runner_os": runner_os,
        "gha_matrix_cells": cells,
        "gha_run_id": None,
        "budget": None,
    }
    write_json(attempt_root / "submission.json", submission_record)
    update_gha_status(
        state_root=state_root,
        job_id=job_id,
        status="budgeting",
        plan=plan,
        attempt_id=attempt_id,
        repo=repo_cfg["repo"],
        workflow=workflow,
        run_id=None,
        dispatch_id=dispatch_id,
        dispatched_at=None,
        ref=ref,
        runner_os=runner_os,
        cells=cells,
    )

    try:  # fail-closed budget gate + worst-case reservation
        budget = gha.budget_gate(job_id=dispatch_id, repo_cfg=repo_cfg, config=config,
                                 state_root=state_root, cells=cells)
    except gha.GhaBudgetError as exc:
        update_status(state_root=state_root, job_id=job_id, status="rejected", plan=plan)
        return {"job_id": job_id, "status": "rejected", "plan": plan, "reason": str(exc)}

    dispatched_at = timestamp()
    submission_record["submitted_at"] = dispatched_at
    submission_record["budget"] = budget
    write_json(attempt_root / "submission.json", submission_record)
    update_gha_status(
        state_root=state_root,
        job_id=job_id,
        status="dispatching",
        plan=plan,
        attempt_id=attempt_id,
        repo=repo_cfg["repo"],
        workflow=workflow,
        run_id=None,
        dispatch_id=dispatch_id,
        dispatched_at=dispatched_at,
        ref=ref,
        runner_os=runner_os,
        cells=cells,
    )
    try:
        gha.dispatch(job_id=dispatch_id, repo_cfg=repo_cfg, parameters=parameters)
    except gha.GhaError as exc:
        # Dispatch failure can be transport-ambiguous. Keep the reservation fail-closed and
        # persist enough metadata for an operator to correlate/reconcile safely.
        update_gha_status(
            state_root=state_root,
            job_id=job_id,
            status="dispatch_failed",
            plan=plan,
            attempt_id=attempt_id,
            repo=repo_cfg["repo"],
            workflow=workflow,
            run_id=None,
            dispatch_id=dispatch_id,
            dispatched_at=dispatched_at,
            ref=ref,
            runner_os=runner_os,
            cells=cells,
        )
        return {
            "job_id": job_id,
            "status": "dispatch_failed",
            "attempt_id": attempt_id,
            "gha_dispatch_id": dispatch_id,
            "budget": budget,
            "detail": f"{exc}; reservation remains active until reconciled",
        }

    try:
        run_id = gha.correlate(
            repo=repo_cfg["repo"], workflow=workflow, job_id=dispatch_id,
            ref=ref, not_before=dispatched_at,
        )
        correlation_detail = None
    except gha.GhaError as exc:
        run_id = None
        correlation_detail = str(exc)

    submission_record["gha_run_id"] = run_id
    write_json(attempt_root / "submission.json", submission_record)
    append_event(
        job_dir(state_root, job_id) / "events.jsonl",
        {"event": "submitted", **submission_record},
    )
    submission_status = "submitted" if run_id is not None else "correlation_pending"
    update_gha_status(
        state_root=state_root,
        job_id=job_id,
        status=submission_status,
        plan=plan,
        attempt_id=attempt_id,
        repo=repo_cfg["repo"],
        workflow=workflow,
        run_id=run_id,
        dispatch_id=dispatch_id,
        dispatched_at=dispatched_at,
        ref=ref,
        runner_os=runner_os,
        cells=cells,
    )

    if wait and run_id:
        conclusion = gha.wait(repo=repo_cfg["repo"], run_id=run_id, timeout=timeout)
        if conclusion == "timeout":
            update_gha_status(
                state_root=state_root,
                job_id=job_id,
                status="running",
                plan=plan,
                attempt_id=attempt_id,
                repo=repo_cfg["repo"],
                workflow=workflow,
                run_id=run_id,
                dispatch_id=dispatch_id,
                dispatched_at=dispatched_at,
                ref=ref,
                runner_os=runner_os,
                cells=cells,
            )
            return {
                "job_id": job_id,
                "status": "running",
                "attempt_id": attempt_id,
                "gha_run_id": run_id,
                "gha_dispatch_id": dispatch_id,
                "budget": budget,
                "detail": "wait timed out; reservation remains active",
            }
        fetched = gha.fetch(repo=repo_cfg["repo"], job_id=dispatch_id,
                            dest=ensure_root(attempt_root / "download"),
                            state_root=state_root, run_id=run_id,
                            runner_os=runner_os, cells=cells)
        write_json(attempt_root / "result.json", fetched["result"])
        final_status = (
            "artifact_missing"
            if conclusion == "success" and fetched["result"].get("status") == "no-artifact"
            else ("completed" if conclusion == "success" else conclusion)
        )
        update_gha_status(
            state_root=state_root,
            job_id=job_id,
            status=final_status,
            plan=plan,
            attempt_id=attempt_id,
            repo=repo_cfg["repo"],
            workflow=workflow,
            run_id=run_id,
            dispatch_id=dispatch_id,
            dispatched_at=dispatched_at,
            ref=ref,
            runner_os=runner_os,
            cells=cells,
        )
        return {"job_id": job_id, "status": final_status, "attempt_id": attempt_id,
                "gha_run_id": run_id, "gha_dispatch_id": dispatch_id,
                "budget": budget, "actual_minutes": fetched["actual_minutes"],
                "result_manifest_path": str(attempt_root / "result.json")}

    result = {"job_id": job_id, "status": submission_status, "attempt_id": attempt_id,
              "gha_run_id": run_id, "gha_dispatch_id": dispatch_id,
              "budget": budget, "plan": plan,
              "config_path": str(config_path)}
    if correlation_detail:
        result["detail"] = (
            f"{correlation_detail}; retry wait to correlate by job id; reservation remains active"
        )
    return result


def command_wait(*, job_id: str, config: Any, state_root: Path, timeout: float | None) -> dict[str, Any]:
    status = read_json(status_path(state_root, job_id))
    gha_repo = status.get("gha_repo")
    if gha_repo:
        attempt_id = status.get("attempt_id") or next_attempt_id(state_root, job_id)
        workflow = str(status.get("gha_workflow") or "experiment.yml")
        dispatch_id = str(status.get("gha_dispatch_id") or job_id)
        dispatched_at = status.get("gha_dispatched_at")
        ref = str(status.get("gha_ref") or "main")
        runner_os = str(status.get("gha_runner_os") or "linux")
        cells = int(status.get("gha_matrix_cells") or 1)
        run_id = status.get("gha_run_id")
        if run_id is None:
            try:
                run_id = gha.correlate(
                    repo=str(gha_repo), workflow=workflow, job_id=dispatch_id,
                    ref=ref, not_before=dispatched_at,
                )
            except gha.GhaError as exc:
                return {
                    "job_id": job_id,
                    "status": "correlation_pending",
                    "detail": (
                        f"{exc}; retry wait; the reservation remains active"
                    ),
                }
            if run_id is None:
                return {
                    "job_id": job_id,
                    "status": "correlation_pending",
                    "detail": "GitHub Actions run id is not visible yet; retry wait.",
                }
        conclusion = gha.wait(repo=str(gha_repo), run_id=int(run_id), timeout=timeout)
        if conclusion == "timeout":
            update_gha_status(
                state_root=state_root,
                job_id=job_id,
                status="running",
                plan=status.get("plan", {}),
                attempt_id=attempt_id,
                repo=str(gha_repo),
                workflow=workflow,
                run_id=int(run_id),
                dispatch_id=dispatch_id,
                dispatched_at=dispatched_at,
                ref=ref,
                runner_os=runner_os,
                cells=cells,
            )
            return {
                "job_id": job_id,
                "status": "running",
                "gha_run_id": int(run_id),
                "detail": "wait timed out; reservation remains active",
            }
        attempt_root = ensure_root(attempt_dir(state_root, job_id, attempt_id))
        fetched = gha.fetch(
            repo=str(gha_repo),
            job_id=dispatch_id,
            dest=ensure_root(attempt_root / "download"),
            state_root=state_root,
            run_id=int(run_id),
            runner_os=runner_os,
            cells=cells,
        )
        write_json(attempt_root / "result.json", fetched["result"])
        final_status = (
            "artifact_missing"
            if conclusion == "success" and fetched["result"].get("status") == "no-artifact"
            else ("completed" if conclusion == "success" else conclusion)
        )
        update_gha_status(
            state_root=state_root,
            job_id=job_id,
            status=final_status,
            plan=status.get("plan", {}),
            attempt_id=attempt_id,
            repo=str(gha_repo),
            workflow=workflow,
            run_id=int(run_id),
            dispatch_id=dispatch_id,
            dispatched_at=dispatched_at,
            ref=ref,
            runner_os=runner_os,
            cells=cells,
        )
        return {
            "job_id": job_id,
            "status": final_status,
            "attempt_id": attempt_id,
            "gha_run_id": int(run_id),
            "gha_dispatch_id": dispatch_id,
            "actual_minutes": fetched["actual_minutes"],
            "result_manifest_path": str(attempt_root / "result.json"),
        }

    function_call_id = status.get("function_call_id")
    if not function_call_id:
        raise RuntimeError(f"No remote function call is recorded for job '{job_id}'.")

    try:
        result_manifest = wait_for_result(function_call_id=function_call_id, timeout=timeout)
    except Exception as exc:
        message = str(exc).lower()
        is_timeout = "timeout" in message or "timed out" in message
        next_status = "running" if is_timeout else "failed"
        update_status(
            state_root=state_root,
            job_id=job_id,
            status=next_status,
            plan=status.get("plan", {}),
            attempt_id=status.get("attempt_id"),
            function_call_id=function_call_id,
        )
        return {
            "job_id": job_id,
            "status": next_status,
            "function_call_id": function_call_id,
            "detail": str(exc),
        }

    attempt_id = status.get("attempt_id") or next_attempt_id(state_root, job_id)
    attempt_root = ensure_root(attempt_dir(state_root, job_id, attempt_id))
    write_json(attempt_root / "result.json", result_manifest)
    append_event(job_dir(state_root, job_id) / "events.jsonl", {"event": "completed", "job_id": job_id, "attempt_id": attempt_id})
    update_status(
        state_root=state_root,
        job_id=job_id,
        status="completed",
        plan=status.get("plan", {}),
        attempt_id=attempt_id,
        function_call_id=function_call_id,
    )
    return {
        "job_id": job_id,
        "status": "completed",
        "attempt_id": attempt_id,
        "result_manifest_path": str(attempt_root / "result.json"),
    }


def command_fetch(*, job_id: str, config: Any, state_root: Path, dest: str | None) -> dict[str, Any]:
    status = read_json(status_path(state_root, job_id))
    attempt_id = status.get("attempt_id")
    if not attempt_id:
        raise RuntimeError(f"No attempt metadata exists for job '{job_id}'.")

    attempt_root = attempt_dir(state_root, job_id, attempt_id)
    result_path = attempt_root / "result.json"
    if not result_path.exists():
        waited = command_wait(job_id=job_id, config=config, state_root=state_root, timeout=0)
        if waited.get("status") != "completed":
            raise RuntimeError(f"Job '{job_id}' is not complete yet.")
        status = read_json(status_path(state_root, job_id))

    result_manifest = read_json(result_path)
    materialize_root = resolve_materialize_root(dest, config.default_materialize_root)
    target_root = ensure_root(materialize_root / "results" / job_id)
    write_json(target_root / "manifest.json", result_manifest)
    write_json(target_root / "status.json", status)
    (target_root / "stdout.txt").write_text(str(result_manifest.get("stdout", "")), encoding="utf-8")
    (target_root / "stderr.txt").write_text(str(result_manifest.get("stderr", "")), encoding="utf-8")
    write_json(target_root / "result.json", {"result": result_manifest.get("result")})

    return {
        "job_id": job_id,
        "status": status.get("status"),
        "materialized_to": str(target_root),
        "manifest_path": str(target_root / "manifest.json"),
    }


def command_cancel(*, job_id: str, state_root: Path) -> dict[str, Any]:
    status = read_json(status_path(state_root, job_id))
    function_call_id = status.get("function_call_id")
    if not function_call_id:
        raise RuntimeError(f"No remote function call is recorded for job '{job_id}'.")
    cancelled = cancel_function_call(function_call_id=function_call_id)
    append_event(job_dir(state_root, job_id) / "events.jsonl", {"event": "cancelled", "job_id": job_id, "function_call_id": function_call_id})
    update_status(
        state_root=state_root,
        job_id=job_id,
        status="cancelled",
        plan=status.get("plan", {}),
        attempt_id=status.get("attempt_id"),
        function_call_id=function_call_id,
    )
    return {
        "job_id": job_id,
        "status": "cancelled",
        **cancelled,
    }


def command_resume(
    *,
    job_id: str,
    config: Any,
    config_path: Path,
    state_root: Path,
    wait: bool,
    timeout: float | None,
) -> dict[str, Any]:
    manifest = read_json(manifest_path(state_root, job_id))
    manifest.setdefault("provenance", {})
    manifest["provenance"]["resume_of"] = job_id
    return command_submit(job=manifest, config=config, config_path=config_path, state_root=state_root, wait=wait, timeout=timeout)


def command_deploy(*, config: Any, root: Path) -> dict[str, Any]:
    return deploy_modal_app(config=config, workspace_root=root)


def command_bootstrap(*, config_path: Path, root: Path, install_deps: bool, auth: bool) -> dict[str, Any]:
    """One-time, opt-in machine setup for the broker.

    Generates research-compute.toml from the example if absent (never clobbers an
    existing one, so it does not fight a config that another tool manages),
    authenticates the GitHub CLI for the GitHub Actions lane, checks Python deps,
    and reports `doctor`. Interactive `gh` steps run only in a TTY.
    """
    summary: dict[str, Any] = {
        "config": _bootstrap_config(config_path=config_path, root=root),
        "gh": _bootstrap_gh(auth=auth),
        "deps": _bootstrap_deps(install=install_deps),
    }
    summary["doctor"] = _bootstrap_doctor(config_path=config_path, root=root)
    return summary


def _detect_platform() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return sys.platform


def _replace_toml_scalar(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf'(?m)^({re.escape(key)}\s*=\s*)"[^"]*"')
    return pattern.sub(rf'\g<1>"{value}"', text, count=1)


def _bootstrap_config(*, config_path: Path, root: Path) -> dict[str, Any]:
    if config_path.exists():
        return {"path": str(config_path), "generated": False, "reason": "already exists; left unchanged"}
    example = example_config_path(root)
    if not example.exists():
        return {"path": str(config_path), "generated": False, "error": f"example config not found: {example}"}
    install_id = socket.gethostname() or "research-compute"
    platform_name = _detect_platform()
    text = example.read_text(encoding="utf-8")
    text = _replace_toml_scalar(text, "install_id", install_id)
    text = _replace_toml_scalar(text, "platform", platform_name)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8")
    return {
        "path": str(config_path),
        "generated": True,
        "install_id": install_id,
        "platform": platform_name,
        "note": "generated from the example; set [gha].enabled and fill [gha].repos to use GitHub Actions compute",
    }


def _parse_gh_scopes(text: str) -> set[str]:
    scopes: set[str] = set()
    for line in text.splitlines():
        if "scope" in line.lower():
            scopes.update(token.strip() for token in re.findall(r"'([^']+)'", line))
    return scopes


def _bootstrap_gh(*, auth: bool) -> dict[str, Any]:
    if shutil.which("gh") is None:
        return {"installed": False, "hint": "install the GitHub CLI: https://cli.github.com"}
    try:
        status = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=20)
    except (subprocess.SubprocessError, OSError) as exc:
        # A `gh` hiccup (e.g. a slow/hung probe on a loaded Windows runner that
        # trips the timeout) must never crash bootstrap: report it as data so
        # config/deps/doctor still succeed.
        return {"installed": True, "probe_error": str(exc), "action": "skipped (gh probe failed)"}
    authenticated = status.returncode == 0
    combined = (status.stdout or "") + (status.stderr or "")
    has_user_scope = "user" in _parse_gh_scopes(combined)
    info: dict[str, Any] = {
        "installed": True,
        "authenticated": authenticated,
        "has_user_scope": has_user_scope,
    }
    if not auth:
        info["action"] = "skipped (--no-auth)"
        return info
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not authenticated:
        if interactive:
            info["action"] = "ran `gh auth login -s user`"
            info["exit_code"] = subprocess.run(["gh", "auth", "login", "-s", "user"]).returncode
        else:
            info["action"] = "authentication required (non-interactive shell)"
            info["run"] = "gh auth login -s user"
    elif not has_user_scope:
        if interactive:
            info["action"] = "ran `gh auth refresh -s user`"
            info["exit_code"] = subprocess.run(["gh", "auth", "refresh", "-h", "github.com", "-s", "user"]).returncode
        else:
            info["action"] = "missing 'user' scope (needed for Actions billing reads)"
            info["run"] = "gh auth refresh -h github.com -s user"
    else:
        info["action"] = "ok"
    return info


def _bootstrap_deps(*, install: bool) -> dict[str, Any]:
    required = ["tomli"] if sys.version_info < (3, 11) else []
    optional = ["modal", "networkx", "psutil"]
    missing_required = [name for name in required if importlib.util.find_spec(name) is None]
    missing_optional = [name for name in optional if importlib.util.find_spec(name) is None]
    info: dict[str, Any] = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "missing_required": missing_required,
        "missing_optional": missing_optional,
    }
    if install and missing_required:
        info["exit_code"] = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--user", *missing_required]
        ).returncode
        info["installed"] = missing_required
    elif missing_required:
        info["hint"] = f"{sys.executable} -m pip install --user " + " ".join(missing_required)
    if missing_optional:
        info["optional_hint"] = f"{sys.executable} -m pip install --user " + " ".join(missing_optional)
    return info


def _bootstrap_doctor(*, config_path: Path, root: Path) -> dict[str, Any]:
    try:
        config = load_config(config_path)
        state_root = ensure_root(config.state_root(root))
        return command_doctor(config=config, config_path=config_path, state_root=state_root)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def persist_plan(*, state_root: Path, job: dict[str, Any], plan: dict[str, Any]) -> None:
    job_root = ensure_root(job_dir(state_root, job["job_id"]))
    write_json(manifest_path(state_root, job["job_id"]), job)
    write_json(plan_path(state_root, job["job_id"]), plan)
    append_event(job_root / "events.jsonl", {"event": "planned", "job_id": job["job_id"], "decision": plan["decision"]})
    update_status(state_root=state_root, job_id=job["job_id"], status="planned", plan=plan)


def update_status(
    *,
    state_root: Path,
    job_id: str,
    status: str,
    plan: dict[str, Any],
    attempt_id: str | None = None,
    function_call_id: str | None = None,
) -> None:
    payload = {
        "job_id": job_id,
        "status": status,
        "updated_at": timestamp(),
        "plan": plan,
        "attempt_id": attempt_id,
        "function_call_id": function_call_id,
    }
    write_json(status_path(state_root, job_id), payload)


def update_gha_status(
    *,
    state_root: Path,
    job_id: str,
    status: str,
    plan: dict[str, Any],
    attempt_id: str,
    repo: str,
    workflow: str,
    run_id: int | None,
    dispatch_id: str | None = None,
    dispatched_at: str | None = None,
    ref: str | None = None,
    runner_os: str = "linux",
    cells: int = 1,
) -> None:
    """Persist generic status plus the recovery handle required by GHA wait/fetch."""
    update_status(
        state_root=state_root,
        job_id=job_id,
        status=status,
        plan=plan,
        attempt_id=attempt_id,
    )
    payload = read_json(status_path(state_root, job_id))
    payload.update({
        "gha_repo": repo,
        "gha_workflow": workflow,
        "gha_run_id": run_id,
        "gha_dispatch_id": dispatch_id or job_id,
        "gha_dispatched_at": dispatched_at,
        "gha_ref": ref,
        "gha_runner_os": runner_os,
        "gha_matrix_cells": cells,
    })
    write_json(status_path(state_root, job_id), payload)


def load_manifest(path_arg: str) -> dict[str, Any]:
    if path_arg == "-":
        return json.load(__import__("sys").stdin)
    return json.loads(Path(path_arg).read_text(encoding="utf-8"))


def load_local_resources() -> dict[str, Any] | None:
    path = workspace_root() / ".codex_resources.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_materialize_root(dest: str | None, default_relative: str) -> Path:
    if dest:
        path = Path(dest).expanduser()
        return path.resolve() if path.is_absolute() else (caller_cwd() / path).resolve()
    return (caller_cwd() / default_relative).resolve()


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
