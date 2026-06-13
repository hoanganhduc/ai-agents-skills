from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import caller_cwd, default_config_path, example_config_path, load_config, modal_config_path, workspace_root
from .modal_backend import cancel_function_call, deploy_modal_app, modal_ready_summary, run_remote_job, submit_remote_job, wait_for_result
from . import github_actions_backend as gha
from .planner import normalize_job, plan_job
from .state import append_event, attempt_dir, ensure_root, job_dir, manifest_path, next_attempt_id, plan_path, read_json, status_path, write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-compute", description="Codex broker for Modal-backed research compute.")
    parser.add_argument("--config", default=None, help="Path to research-compute.toml")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Check broker, Modal, and config readiness")

    plan_parser = subparsers.add_parser("plan", help="Plan a broker manifest")
    plan_parser.add_argument("manifest", help="Path to a job manifest JSON file or '-' for stdin")

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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root = workspace_root()
    config_path = Path(args.config).expanduser().resolve() if args.config else default_config_path(root)
    config = load_config(config_path)
    state_root = ensure_root(config.state_root(root))

    try:
        if args.command == "doctor":
            result = command_doctor(config=config, config_path=config_path, state_root=state_root)
        elif args.command == "plan":
            job = load_manifest(args.manifest)
            result = command_plan(job=job, config=config, state_root=state_root, persist=True)
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
    summary = {
        "config_path": str(config_path),
        "config_exists": config_path.exists(),
        "example_config_path": str(example_config_path(workspace_root())),
        "workspace_root": str(workspace_root()),
        "caller_cwd": str(caller_cwd()),
        "state_root": str(state_root),
        "routing_order": list(getattr(config, "routing_order", [])),
        **modal_summary,
    }
    if getattr(config, "gha_enabled", False):
        try:
            summary["gha"] = gha.doctor(config)
        except Exception as exc:  # noqa: BLE001
            summary["gha"] = {"ready": False, "error": str(exc)}
    return summary


def command_plan(*, job: dict[str, Any], config: Any, state_root: Path, persist: bool) -> dict[str, Any]:
    normalized = normalize_job(job, config=config)
    plan = plan_job(normalized, config=config, resources=load_local_resources(), modal_ready=modal_ready_summary(config, modal_config_path())["modal_sdk_available"])
    if persist:
        persist_plan(state_root=state_root, job=normalized, plan=plan)
    return {
        "job_id": normalized["job_id"],
        "job": normalized,
        "plan": plan,
    }


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
    cells = int(job.get("constraints", {}).get("matrix_cells", 1) or 1)

    try:  # fail-closed budget gate + worst-case reservation
        budget = gha.budget_gate(job_id=job_id, repo_cfg=repo_cfg, config=config,
                                 state_root=state_root, cells=cells)
    except gha.GhaBudgetError as exc:
        update_status(state_root=state_root, job_id=job_id, status="rejected", plan=plan)
        return {"job_id": job_id, "status": "rejected", "plan": plan, "reason": str(exc)}

    gha.dispatch(job_id=job_id, repo_cfg=repo_cfg, parameters=parameters)
    run_id = gha.correlate(repo=repo_cfg["repo"],
                           workflow=repo_cfg.get("workflow", "experiment.yml"), job_id=job_id)

    attempt_id = next_attempt_id(state_root, job_id)
    attempt_root = ensure_root(attempt_dir(state_root, job_id, attempt_id))
    submission_record = {"job_id": job_id, "attempt_id": attempt_id, "submitted_at": timestamp(),
                         "decision": "gha", "gha_repo": repo_cfg["repo"], "gha_run_id": run_id,
                         "budget": budget}
    write_json(attempt_root / "submission.json", submission_record)
    append_event(job_dir(state_root, job_id) / "events.jsonl", {"event": "submitted", **submission_record})
    update_status(state_root=state_root, job_id=job_id, status="submitted", plan=plan, attempt_id=attempt_id)
    status = read_json(status_path(state_root, job_id))
    status.update({"gha_run_id": run_id, "gha_repo": repo_cfg["repo"]})
    write_json(status_path(state_root, job_id), status)

    if wait and run_id:
        conclusion = gha.wait(repo=repo_cfg["repo"], run_id=run_id, timeout=timeout)
        fetched = gha.fetch(repo=repo_cfg["repo"], job_id=job_id,
                            dest=ensure_root(attempt_root / "download"),
                            state_root=state_root, run_id=run_id)
        write_json(attempt_root / "result.json", fetched["result"])
        update_status(state_root=state_root, job_id=job_id,
                      status="completed" if conclusion == "success" else conclusion,
                      plan=plan, attempt_id=attempt_id)
        return {"job_id": job_id, "status": conclusion, "attempt_id": attempt_id,
                "gha_run_id": run_id, "budget": budget, "actual_minutes": fetched["actual_minutes"],
                "result_manifest_path": str(attempt_root / "result.json")}

    return {"job_id": job_id, "status": "submitted", "attempt_id": attempt_id,
            "gha_run_id": run_id, "budget": budget, "plan": plan, "config_path": str(config_path)}


def command_wait(*, job_id: str, config: Any, state_root: Path, timeout: float | None) -> dict[str, Any]:
    status = read_json(status_path(state_root, job_id))
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
