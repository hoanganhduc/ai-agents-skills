#!/usr/bin/env python3
"""Offline ledger helper for autonomous research loops."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
DEFAULT_PLATEAU_RULE = "stop after three consecutive iterations with no new evidence or reduced uncertainty"
VALID_DECISIONS = {"continue", "revise", "delegate", "stop", "blocked"}
TERMINAL_DECISIONS = {"stop", "blocked"}
TERMINAL_STATUSES = {"stopped", "blocked"}
SUCCESS_STOP_REASONS = {"success", "success_criteria_met", "proof", "proof_found", "found_proof", "proved"}
PROOF_ARTIFACT_DIRNAME = "proof_artifacts"
PROOF_ARTIFACT_TYPES = {"lean", "coq", "isabelle", "agda", "sagemath", "python-verifier", "external-verifier"}
SAFE_EVIDENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
VALID_MODES = {
    "monitor",
    "bounded-research",
    "implementation-support",
    "panel-loop",
    "recovery",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    # Write atomically (temp file + os.replace) so a crash mid-write cannot
    # truncate the destination and lose loop state.
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def read_iterations(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for index, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"iterations.jsonl line {index} is invalid JSON: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"iterations.jsonl line {index} must contain a JSON object")
            records.append(record)
    return records


def append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(data, sort_keys=True))
        handle.write("\n")


def parse_many(values: list[str] | None) -> list[str]:
    return [value for value in values or [] if value]


def normalized_stop_reason(reason: object) -> str:
    return str(reason or "").strip().lower().replace("-", "_").replace(" ", "_")


def is_success_stop_reason(reason: object) -> bool:
    return normalized_stop_reason(reason) in SUCCESS_STOP_REASONS


def proof_artifacts_dir(run_dir: Path) -> Path:
    return run_dir / PROOF_ARTIFACT_DIRNAME


def is_safe_evidence_id(evidence_id: object) -> bool:
    return isinstance(evidence_id, str) and SAFE_EVIDENCE_ID.fullmatch(evidence_id) is not None


def proof_artifact_path(run_dir: Path, evidence_id: str) -> Path:
    return proof_artifacts_dir(run_dir) / f"{evidence_id}.json"


def validate_relative_proof_path(run_dir: Path, raw_path: object, evidence_id: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(raw_path, str) or not raw_path.strip():
        return [f"proof artifact {evidence_id!r} proof_path must be a non-empty relative path"]
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return [f"proof artifact {evidence_id!r} proof_path must stay inside the loop directory"]
    resolved_run_dir = run_dir.resolve()
    resolved_proof = (resolved_run_dir / candidate).resolve()
    try:
        resolved_proof.relative_to(resolved_run_dir)
    except ValueError:
        errors.append(f"proof artifact {evidence_id!r} proof_path must stay inside the loop directory")
    if not resolved_proof.is_file():
        errors.append(f"proof artifact {evidence_id!r} proof_path does not exist: {raw_path}")
    return errors


def validate_proof_artifact(run_dir: Path, evidence_id: object) -> list[str]:
    if not is_safe_evidence_id(evidence_id):
        return [
            "proof evidence_id must be 1-128 characters of letters, digits, underscore, hyphen, or dot, "
            "and must start with a letter or digit"
        ]
    evidence_id = str(evidence_id)
    path = proof_artifact_path(run_dir, evidence_id)
    if not path.exists():
        return [f"proof artifact for evidence_id {evidence_id!r} is missing: {PROOF_ARTIFACT_DIRNAME}/{evidence_id}.json"]
    try:
        artifact = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"invalid proof artifact for evidence_id {evidence_id!r}: {exc}"]

    errors: list[str] = []
    if artifact.get("id") != evidence_id:
        errors.append(f"proof artifact {evidence_id!r} id must match evidence_id")
    if artifact.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"proof artifact {evidence_id!r} schema_version must be {SCHEMA_VERSION!r}")
    if artifact.get("machine_checkable") is not True:
        errors.append(f"proof artifact {evidence_id!r} machine_checkable must be true")
    if artifact.get("artifact_type") not in PROOF_ARTIFACT_TYPES:
        errors.append(f"proof artifact {evidence_id!r} artifact_type is invalid")

    checker = artifact.get("checker")
    if not isinstance(checker, dict):
        errors.append(f"proof artifact {evidence_id!r} checker must be an object")
    else:
        if not isinstance(checker.get("name"), str) or not checker.get("name", "").strip():
            errors.append(f"proof artifact {evidence_id!r} checker.name must be non-empty")
        if checker.get("status") != "passed":
            errors.append(f"proof artifact {evidence_id!r} checker.status must be 'passed'")

    if not isinstance(artifact.get("target"), str) or not artifact.get("target", "").strip():
        errors.append(f"proof artifact {evidence_id!r} target must be non-empty")
    errors.extend(validate_relative_proof_path(run_dir, artifact.get("proof_path"), evidence_id))
    return errors


def valid_proof_artifact_evidence_ids(run_dir: Path, evidence_ids: list[str]) -> list[str]:
    return [evidence_id for evidence_id in evidence_ids if not validate_proof_artifact(run_dir, evidence_id)]


def record_evidence_ids(record: dict[str, Any]) -> list[str]:
    evidence_checked = record.get("evidence_checked")
    if not isinstance(evidence_checked, dict):
        return []
    evidence_ids = evidence_checked.get("evidence_ids")
    return [item for item in evidence_ids or [] if isinstance(item, str) and item]


def loop_paths(run_dir: Path) -> dict[str, Path]:
    return {
        "state": run_dir / "loop_state.json",
        "budget": run_dir / "budget.json",
        "iterations": run_dir / "iterations.jsonl",
        "recovery": run_dir / "recovery.md",
    }


def init_loop(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    paths = loop_paths(run_dir)
    if run_dir.exists() and any(path.exists() for path in paths.values()) and not args.force:
        raise ValueError(f"{run_dir} already contains loop files; pass --force to overwrite")

    now = utc_now()
    state = {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(uuid.uuid4()),
        "goal": args.goal,
        "success_criteria": args.success_criteria,
        "default_mode": args.mode,
        "status": "initialized",
        "stop_flags": {
            "stop_on_guard_fail": args.stop_on_guard_fail,
            "stop_on_missing_evidence": args.stop_on_missing_evidence,
            "stop_on_scope_change": args.stop_on_scope_change,
        },
        "plateau_rule": args.plateau_rule,
        "stop_conditions": {
            "require_user_stop_only": args.require_user_stop_only,
            "user_overrides": parse_many(args.stop_condition),
        },
        "success_check": args.success_check,
        "created_at": now,
        "updated_at": now,
        "last_iteration": 0,
    }
    budget = {
        "schema_version": SCHEMA_VERSION,
        "budget_owner": args.budget_owner,
        "max_iterations": args.max_iterations,
        "max_wall_time_seconds": args.max_wall_time_seconds,
        "max_tokens": args.max_tokens,
        "max_usd": args.max_usd,
        "max_depth": args.max_depth,
        "max_hops": args.max_hops,
        "max_child_workers": args.max_child_workers,
        "spent_iterations": 0,
        "spent_tokens": 0,
        "spent_usd": 0.0,
        "created_at": now,
        "updated_at": now,
    }

    write_json(paths["state"], state)
    write_json(paths["budget"], budget)
    paths["iterations"].parent.mkdir(parents=True, exist_ok=True)
    paths["iterations"].write_text("", encoding="utf-8", newline="\n")
    proof_artifacts_dir(run_dir).mkdir(parents=True, exist_ok=True)
    paths["recovery"].write_text(
        "\n".join(
            [
                "# Autonomous Research Loop Recovery",
                "",
                f"- Goal: {args.goal}",
                "- Status: initialized",
                "- Last completed iteration: 0",
                "- Next safe action: start the first bounded iteration",
                "- Remaining evidence gaps: not yet assessed",
                "- Active blockers: none recorded",
                f"- Budget remaining: {args.max_iterations} iterations",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    return {
        "status": "ok",
        "action": "init",
        "dir": str(run_dir),
        "files": {name: str(path) for name, path in paths.items()},
        "directories": {"proof_artifacts": str(proof_artifacts_dir(run_dir))},
    }


def append_iteration(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    paths = loop_paths(run_dir)
    errors = validate_loop_dir(run_dir)["errors"]
    if errors:
        raise ValueError("cannot append iteration before validation passes: " + "; ".join(errors))
    if args.decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(VALID_DECISIONS)}")
    if args.mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")

    state = read_json(paths["state"])
    budget = read_json(paths["budget"])
    iterations = read_iterations(paths["iterations"])
    max_iterations = int(budget["max_iterations"])
    spent_iterations = int(budget.get("spent_iterations", 0))
    if state.get("status") in TERMINAL_STATUSES:
        raise ValueError(f"cannot append iteration after loop status is {state.get('status')}")
    if len(iterations) >= max_iterations:
        raise ValueError("cannot append iteration because max_iterations is exhausted")
    if spent_iterations >= max_iterations:
        raise ValueError("cannot append iteration because spent_iterations reached max_iterations")
    number = len(iterations) + 1
    remaining_after_append = max_iterations - number
    if remaining_after_append == 0 and args.decision not in TERMINAL_DECISIONS:
        raise ValueError("final allowed iteration must use decision stop or blocked, not a continuing decision")
    if args.decision == "blocked" and remaining_after_append > 0:
        raise ValueError(
            "early blocked before max_iterations is not a valid stop under the enforcement policy: "
            "record the blocker and continue with decision revise or delegate"
        )
    claim_ids = parse_many(args.claim_id)
    evidence_ids = parse_many(args.evidence_id)
    if args.decision == "stop" and remaining_after_append > 0:
        if not is_success_stop_reason(args.stop_reason):
            raise ValueError("early stop before max_iterations requires a success/proof stop_reason")
        if not evidence_ids:
            raise ValueError("early stop before max_iterations requires at least one proof artifact evidence_id")
        proof_errors: list[str] = []
        for evidence_id in evidence_ids:
            proof_errors.extend(validate_proof_artifact(run_dir, evidence_id))
        if len(proof_errors) == len(evidence_ids) or not valid_proof_artifact_evidence_ids(run_dir, evidence_ids):
            raise ValueError(
                "early stop before max_iterations requires at least one evidence_id with a valid proof artifact: "
                + "; ".join(proof_errors)
            )
    now = utc_now()
    record = {
        "schema_version": SCHEMA_VERSION,
        "iteration": number,
        "timestamp": now,
        "mode": args.mode,
        "objective": args.objective,
        "input_refs": parse_many(args.input_ref),
        "evidence_checked": {
            "source_ids": parse_many(args.source_id),
            "claim_ids": claim_ids,
            "evidence_ids": evidence_ids,
            "guard_refs": parse_many(args.guard_ref),
        },
        "actions_taken": parse_many(args.action_taken),
        "output": args.output,
        "remaining_gaps": parse_many(args.remaining_gap),
        "budget_delta": {
            "iterations": 1,
            "tokens": args.tokens,
            "usd": args.usd,
            "wall_time_seconds": args.wall_time_seconds,
        },
        "decision": args.decision,
        "stop_reason": args.stop_reason,
    }
    append_jsonl(paths["iterations"], record)

    state["last_iteration"] = number
    state["updated_at"] = now
    state["status"] = "blocked" if args.decision == "blocked" else "stopped" if args.decision == "stop" else "running"
    budget["spent_iterations"] = number
    budget["spent_tokens"] = int(budget.get("spent_tokens", 0)) + args.tokens
    budget["spent_usd"] = float(budget.get("spent_usd", 0.0)) + args.usd
    budget["updated_at"] = now
    write_json(paths["state"], state)
    write_json(paths["budget"], budget)

    remaining_iterations = max(0, int(budget["max_iterations"]) - int(budget["spent_iterations"]))
    paths["recovery"].write_text(
        "\n".join(
            [
                "# Autonomous Research Loop Recovery",
                "",
                f"- Goal: {state.get('goal', '')}",
                f"- Status: {state.get('status', '')}",
                f"- Last completed iteration: {number}",
                f"- Next safe action: {'report stop status' if args.decision in {'stop', 'blocked'} else 'continue from the last recorded decision'}",
                f"- Remaining evidence gaps: {', '.join(record['remaining_gaps']) if record['remaining_gaps'] else 'none recorded'}",
                f"- Active blockers: {args.stop_reason if args.decision == 'blocked' and args.stop_reason else 'none recorded'}",
                f"- Budget remaining: {remaining_iterations} iterations",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    return {
        "status": "ok",
        "action": "append-iteration",
        "dir": str(run_dir),
        "iteration": number,
        "decision": args.decision,
    }


def validate_loop_dir(run_dir: Path) -> dict[str, Any]:
    paths = loop_paths(run_dir)
    errors: list[str] = []
    for name, path in paths.items():
        if not path.exists():
            errors.append(f"missing {name} file: {path.name}")

    state: dict[str, Any] = {}
    budget: dict[str, Any] = {}
    iterations: list[dict[str, Any]] = []
    if paths["state"].exists():
        try:
            state = read_json(paths["state"])
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"invalid loop_state.json: {exc}")
    if paths["budget"].exists():
        try:
            budget = read_json(paths["budget"])
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"invalid budget.json: {exc}")
    if paths["iterations"].exists():
        try:
            iterations = read_iterations(paths["iterations"])
        except (OSError, ValueError) as exc:
            errors.append(str(exc))

    for field in ("schema_version", "run_id", "goal", "success_criteria", "default_mode", "status"):
        if state and field not in state:
            errors.append(f"loop_state.json missing {field}")
    if state and state.get("default_mode") not in VALID_MODES:
        errors.append("loop_state.json default_mode is invalid")

    for field in ("schema_version", "max_iterations", "max_depth", "max_hops", "max_child_workers"):
        if budget and field not in budget:
            errors.append(f"budget.json missing {field}")
    if budget:
        for field in ("max_iterations", "max_depth", "max_hops", "max_child_workers"):
            value = budget.get(field)
            if not isinstance(value, int) or value < 0:
                errors.append(f"budget.json {field} must be a non-negative integer")
        spent_iterations = budget.get("spent_iterations", 0)
        if not isinstance(spent_iterations, int) or spent_iterations < 0:
            errors.append("budget.json spent_iterations must be a non-negative integer")
        else:
            max_iterations = budget.get("max_iterations")
            if isinstance(max_iterations, int) and max_iterations >= 0:
                remaining_iterations = max(0, max_iterations - spent_iterations)
                if len(iterations) > max_iterations:
                    errors.append("iterations.jsonl exceeds budget.json max_iterations")
                if spent_iterations != len(iterations):
                    errors.append("budget.json spent_iterations must equal iterations.jsonl record count")
                if state and state.get("status") == "running" and remaining_iterations == 0:
                    errors.append("loop_state.json status cannot be running when iteration budget is exhausted")

    expected = 1
    for record in iterations:
        if record.get("iteration") != expected:
            errors.append(f"iterations.jsonl expected iteration {expected}")
        if record.get("decision") not in VALID_DECISIONS:
            errors.append(f"iteration {expected} has invalid decision")
        if record.get("mode") not in VALID_MODES:
            errors.append(f"iteration {expected} has invalid mode")
        if "objective" not in record:
            errors.append(f"iteration {expected} missing objective")
        expected += 1
    if budget and iterations:
        max_iterations = budget.get("max_iterations")
        spent_iterations = budget.get("spent_iterations")
        if isinstance(max_iterations, int) and isinstance(spent_iterations, int):
            remaining_iterations = max(0, max_iterations - spent_iterations)
            last = iterations[-1]
            if last.get("decision") not in TERMINAL_DECISIONS and remaining_iterations == 0:
                errors.append("latest iteration cannot have a continuing decision when iteration budget is exhausted")
            for record in iterations:
                iteration_number = record.get("iteration")
                if (
                    record.get("decision") == "stop"
                    and isinstance(iteration_number, int)
                    and iteration_number < max_iterations
                ):
                    if not is_success_stop_reason(record.get("stop_reason")):
                        errors.append(
                            f"iteration {iteration_number} early stop before max_iterations must use a success/proof stop_reason"
                        )
                    evidence_ids = record_evidence_ids(record)
                    if not evidence_ids:
                        errors.append(
                            f"iteration {iteration_number} early stop before max_iterations must cite proof artifact evidence_ids"
                        )
                    elif not valid_proof_artifact_evidence_ids(run_dir, evidence_ids):
                        errors.append(
                            f"iteration {iteration_number} early stop before max_iterations must cite a valid proof artifact"
                        )
                        for evidence_id in evidence_ids:
                            errors.extend(
                                f"iteration {iteration_number}: {error}"
                                for error in validate_proof_artifact(run_dir, evidence_id)
                            )

    return {
        "status": "failed" if errors else "ok",
        "errors": errors,
        "checked": {
            "dir": str(run_dir),
            "files": {name: path.exists() for name, path in paths.items()},
            "iterations": len(iterations),
        },
    }


def validate_command(args: argparse.Namespace) -> dict[str, Any]:
    return validate_loop_dir(Path(args.dir).expanduser().resolve())


def status_command(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    validation = validate_loop_dir(run_dir)
    paths = loop_paths(run_dir)
    iterations = read_iterations(paths["iterations"]) if paths["iterations"].exists() else []
    state = read_json(paths["state"]) if paths["state"].exists() else {}
    budget = read_json(paths["budget"]) if paths["budget"].exists() else {}
    last = iterations[-1] if iterations else {}
    return {
        "status": validation["status"],
        "dir": str(run_dir),
        "state_status": state.get("status"),
        "iterations": len(iterations),
        "last_decision": last.get("decision"),
        "remaining_iterations": max(
            0,
            int(budget.get("max_iterations", 0)) - int(budget.get("spent_iterations", 0)),
        )
        if budget
        else None,
        "validation": validation,
    }


def selftest_init_args(run_dir: Path, max_iterations: int) -> argparse.Namespace:
    return argparse.Namespace(
        dir=str(run_dir),
        goal="offline smoke test",
        success_criteria="ledger validates after one iteration",
        mode="bounded-research",
        force=False,
        stop_on_guard_fail=True,
        stop_on_missing_evidence=True,
        stop_on_scope_change=True,
        plateau_rule=DEFAULT_PLATEAU_RULE,
        success_check="",
        require_user_stop_only=False,
        stop_condition=[],
        budget_owner="selftest",
        max_iterations=max_iterations,
        max_wall_time_seconds=300,
        max_tokens=0,
        max_usd=0.0,
        max_depth=1,
        max_hops=1,
        max_child_workers=0,
    )


def selftest_drive_args(run_dir: Path, registry: Path, stub_cmd: str) -> argparse.Namespace:
    return argparse.Namespace(
        dir=str(run_dir),
        root=str(run_dir),
        cmd=stub_cmd,
        provider=None,
        iteration_timeout=60,
        max_failures=1,
        poll=0.0,
        quota_backoff=0,
        max_quota_waits=3,
        log_dir=None,
        registry_dir=str(registry),
    )


STUB_ITERATION_SNIPPET = (
    "import json, os, sys\n"
    "run_dir = os.environ['AUTOLOOP_DIR']\n"
    "marker = os.path.join(run_dir, 'quota_marker')\n"
    "if '--quota-first' in sys.argv and not os.path.exists(marker):\n"
    "    open(marker, 'w').write('seen')\n"
    "    print('provider error: HTTP 429 Too Many Requests')\n"
    "    sys.exit(1)\n"
    "budget_path = os.path.join(run_dir, 'budget.json')\n"
    "budget = json.load(open(budget_path))\n"
    "budget['spent_iterations'] = int(budget.get('spent_iterations', 0)) + 1\n"
    "json.dump(budget, open(budget_path, 'w'))\n"
    "print('stub iteration complete')\n"
)


def selftest_driver_checks() -> dict[str, Any]:
    """Offline checks for the provider adapters and the headless driver. Uses
    only stub commands (this Python interpreter); no provider CLI is invoked."""
    errors: list[str] = []
    providers_checked = 0
    with tempfile.TemporaryDirectory(prefix="autoloop-driver-smoke-") as tmp:
        base = Path(tmp)
        # 1. Provider command construction: every provider builds an argv with
        # the prompt substituted; a stubbed binary override must be honored and
        # reported as not found without consulting PATH defaults.
        for provider in sorted(PROVIDER_SPECS):
            key = provider_env_key(provider)
            environ = {f"AAS_AUTOLOOP_BIN_{key}": str(base / "missing-bin")}
            spec = resolve_provider_command(provider, base / "loop", environ=environ)
            providers_checked += 1
            if spec["mode"] != "argv" or spec["binary_found"]:
                errors.append(f"{provider}: expected argv mode with missing binary")
                continue
            joined = " ".join(spec["argv"])
            if "{prompt}" in joined or "exactly ONE iteration" not in joined:
                errors.append(f"{provider}: prompt placeholder not substituted")
            if str(base / "loop") not in joined:
                errors.append(f"{provider}: loop dir missing from command")
        # Full-command override: {dir} substituted, mode shell.
        override_env = {"AAS_AUTOLOOP_CMD_CLAUDE": "echo {dir}"}
        spec = resolve_provider_command("claude", base / "loop", environ=override_env)
        if spec["mode"] != "shell" or str(base / "loop") not in spec["shell"]:
            errors.append("claude: AAS_AUTOLOOP_CMD override not honored")
        # 2. Quota-signal detection.
        for text in (
            "HTTP 429 Too Many Requests",
            "insufficient credit balance",
            "usage limit reached, resets 5pm",
            "You have run out of credits",
            "rate limit exceeded",
        ):
            if not QUOTA_PATTERN.search(text):
                errors.append(f"quota pattern missed: {text!r}")
        if QUOTA_PATTERN.search("all checks passed cleanly"):
            errors.append("quota pattern false-positive on benign text")
        # 3. Drive to completion on a stub command (budget cap = 2 iterations).
        loop_a = base / "loop-a"
        init_loop(selftest_init_args(loop_a, max_iterations=2))
        stub = base / "stub_iteration.py"
        stub.write_text(STUB_ITERATION_SNIPPET, encoding="utf-8", newline="\n")
        stub_cmd = f'"{sys.executable}" "{stub}"'
        result = drive_command(selftest_drive_args(loop_a, base / "reg", stub_cmd))
        budget_a = read_json(loop_a / "budget.json")
        if (
            result.get("reason") != "done"
            or result.get("exit_code") != 0
            or int(budget_a.get("spent_iterations", 0)) != 2
            or result.get("iterations_run") != 2
        ):
            errors.append(f"drive stub run did not complete cleanly: {result}")
        elif not list((loop_a / "driver_logs").glob("iter_*.log")):
            errors.append("drive stub run left no iteration logs")
        # 4. Quota pause-and-resume: first stub call fails with a 429 signal and
        # must be waited out (not counted as a failure with max_failures=1),
        # the second call succeeds and the budget cap ends the loop.
        loop_b = base / "loop-b"
        init_loop(selftest_init_args(loop_b, max_iterations=1))
        quota_cmd = f'"{sys.executable}" "{stub}" --quota-first'
        result_b = drive_command(selftest_drive_args(loop_b, base / "reg", quota_cmd))
        if (
            result_b.get("reason") != "done"
            or result_b.get("quota_waits_total") != 1
            or int(read_json(loop_b / "budget.json").get("spent_iterations", 0)) != 1
        ):
            errors.append(f"drive quota pause-and-resume misbehaved: {result_b}")
    return {
        "ok": not errors,
        "errors": errors,
        "providers_checked": providers_checked,
        "provider_cli_attempted": False,
    }


def selftest_command(_: argparse.Namespace) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="autonomous-loop-smoke-") as tmp:
        run_dir = Path(tmp) / "loop"
        init_args = argparse.Namespace(
            dir=str(run_dir),
            goal="offline smoke test",
            success_criteria="ledger validates after one iteration",
            mode="bounded-research",
            force=False,
            stop_on_guard_fail=True,
            stop_on_missing_evidence=True,
            stop_on_scope_change=True,
            plateau_rule=DEFAULT_PLATEAU_RULE,
            success_check="",
            require_user_stop_only=False,
            stop_condition=[],
            budget_owner="selftest",
            max_iterations=2,
            max_wall_time_seconds=60,
            max_tokens=0,
            max_usd=0.0,
            max_depth=1,
            max_hops=1,
            max_child_workers=0,
        )
        init_loop(init_args)
        proof_path = run_dir / "proofs" / "offline_smoke.proof"
        proof_path.parent.mkdir(parents=True, exist_ok=True)
        proof_path.write_text("offline smoke proof artifact\n", encoding="utf-8", newline="\n")
        write_json(
            proof_artifact_path(run_dir, "offline-smoke-evidence"),
            {
                "schema_version": SCHEMA_VERSION,
                "id": "offline-smoke-evidence",
                "artifact_type": "python-verifier",
                "machine_checkable": True,
                "target": "offline smoke test",
                "proof_path": "proofs/offline_smoke.proof",
                "checker": {
                    "name": "offline-smoke",
                    "status": "passed",
                },
            },
        )
        append_args = argparse.Namespace(
            dir=str(run_dir),
            mode="bounded-research",
            objective="validate local ledger mechanics",
            decision="stop",
            input_ref=[],
            source_id=[],
            claim_id=[],
            evidence_id=["offline-smoke-evidence"],
            guard_ref=["offline-smoke"],
            action_taken=["initialized ledger"],
            output="selftest complete",
            remaining_gap=[],
            tokens=0,
            usd=0.0,
            wall_time_seconds=0,
            stop_reason="success",
        )
        append_iteration(append_args)
        validation = validate_loop_dir(run_dir)
        driver = selftest_driver_checks()
        return {
            "status": "ok" if validation["status"] == "ok" and driver["ok"] else "failed",
            "driver": driver,
            "smoke_mode": "offline",
            "network_required": False,
            "live_api_attempted": False,
            "package_install_attempted": False,
            "server_started": False,
            "config_written": False,
            "provider_cli_attempted": False,
            "subagents_spawned": False,
            "run_dir_created": run_dir.exists(),
            "validation_status": validation["status"],
            "iterations": validation["checked"]["iterations"],
        }


# --- Autoloop enforcement: arm/disarm/active/done/hook-check ------------------
# Force-management for autonomous loops: a registry of armed loops plus a
# fail-open stop check the Stop hook can call on every turn. The runtime never
# executes the success_check command (the driver/agent runs it and records a
# terminal stop); `done`/`hook-check` are read-only/derived and safe to call
# repeatedly. Stop policy (priority): explicit user stop > terminal status >
# recorded blocker > [user override: stop-only-on-user] > credit/budget caps >
# loops reached.

SENTINEL_STOP = "STOP_REQUESTED"
SENTINEL_BLOCKED = "BLOCKED"
SENTINEL_PAUSE = "PAUSE"
HEARTBEAT_TTL_SECONDS = 1800


def registry_dir(args: argparse.Namespace) -> Path:
    raw = getattr(args, "registry_dir", None) or os.environ.get("AAS_AUTOLOOP_REGISTRY")
    base = Path(raw).expanduser() if raw else Path.home() / ".local" / "share" / "ai-agents-skills" / "autoloop"
    return base / "active.d"


def windows_pid_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    if pid > 0xFFFFFFFF:
        return False

    process_query_limited_information = 0x1000
    error_access_denied = 5
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_exit_code_process = kernel32.GetExitCodeProcess
    get_exit_code_process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_exit_code_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code_process(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        close_handle(handle)


def pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        # On Windows, os.kill(pid, 0) sends CTRL_C_EVENT instead of performing
        # the harmless POSIX existence check.
        return windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def entry_is_live(entry: dict[str, Any]) -> bool:
    pid = entry.get("pid")
    if isinstance(pid, int) and pid > 0 and not pid_alive(pid):
        return False
    stamp = parse_iso(entry.get("heartbeat") or entry.get("created_at"))
    if stamp is None:
        return True
    return (datetime.now(timezone.utc) - stamp).total_seconds() <= HEARTBEAT_TTL_SECONDS


def entry_owned_by_live_driver(entry: dict[str, Any]) -> bool:
    """True when a live headless-driver process owns this registry entry.

    The interactive Stop-hook must stand down while a driver governs the loop;
    otherwise a hooked session would run an iteration concurrently with the
    driver's own iteration session against the same single-path ledger.
    Entries written by `drive` carry driver=true plus the driver pid; entries
    from before that flag are recognized via /proc cmdline proof only, so a
    merely-alive non-driver pid never suppresses the hook.
    """
    pid = entry.get("pid")
    if not isinstance(pid, int) or pid <= 0 or not pid_alive(pid):
        return False
    if entry.get("driver") is True:
        return True
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return b"drive" in argv and any(b"autonomous_research_loop_runtime" in part for part in argv)


def list_registry_entries(reg: Path) -> list[tuple[Path, dict[str, Any]]]:
    out: list[tuple[Path, dict[str, Any]]] = []
    if not reg.exists():
        return out
    for path in sorted(reg.glob("*.json")):
        try:
            out.append((path, read_json(path)))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return out


def gc_registry(reg: Path) -> int:
    removed = 0
    for path, entry in list_registry_entries(reg):
        if not entry_is_live(entry):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def arm_loop(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    state = read_json(loop_paths(run_dir)["state"])
    run_id = state.get("run_id") or str(uuid.uuid4())
    root = Path(args.root).expanduser().resolve() if args.root else run_dir
    reg = registry_dir(args)
    reg.mkdir(parents=True, exist_ok=True)
    gc_registry(reg)
    if not args.force:
        for _, entry in list_registry_entries(reg):
            if (
                entry.get("project_root") == str(root)
                and entry.get("run_id") != run_id
                and entry_is_live(entry)
            ):
                raise ValueError(f"a live autoloop is already armed for {root}; pass --force to override")
    now = utc_now()
    entry = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "loop_dir": str(run_dir),
        "project_root": str(root),
        "pid": int(args.pid) if args.pid else 0,
        "driver": bool(getattr(args, "driver", False)),
        "heartbeat": now,
        "created_at": now,
    }
    write_json(reg / f"{run_id}.json", entry)
    return {"status": "ok", "action": "arm", "run_id": run_id, "registry": str(reg), "project_root": str(root)}


def disarm_loop(args: argparse.Namespace) -> dict[str, Any]:
    reg = registry_dir(args)
    run_id = getattr(args, "run_id", None)
    loop_dir: str | None = None
    if getattr(args, "dir", None):
        loop_dir = str(Path(args.dir).expanduser().resolve())
        if not run_id:
            try:
                run_id = read_json(loop_paths(Path(loop_dir))["state"]).get("run_id")
            except (OSError, ValueError, json.JSONDecodeError):
                run_id = None
    removed: list[str] = []
    for path, entry in list_registry_entries(reg):
        if (run_id and entry.get("run_id") == run_id) or (loop_dir and entry.get("loop_dir") == loop_dir):
            try:
                path.unlink()
                removed.append(str(entry.get("run_id")))
            except OSError:
                pass
    return {"status": "ok", "action": "disarm", "removed": removed, "registry": str(reg)}


def active_command(args: argparse.Namespace) -> dict[str, Any]:
    reg = registry_dir(args)
    gc_registry(reg)
    loops = [entry for _, entry in list_registry_entries(reg) if entry_is_live(entry)]
    return {"status": "ok", "action": "active", "registry": str(reg), "count": len(loops), "loops": loops}


def compute_done(run_dir: Path) -> dict[str, Any]:
    paths = loop_paths(run_dir)
    state = read_json(paths["state"]) if paths["state"].exists() else {}
    budget = read_json(paths["budget"]) if paths["budget"].exists() else {}
    stop_conditions = state.get("stop_conditions") or {}
    paused = (run_dir / SENTINEL_PAUSE).exists()
    require_user = bool(stop_conditions.get("require_user_stop_only"))
    max_usd = budget.get("max_usd") or 0
    max_tokens = budget.get("max_tokens") or 0
    max_wall = budget.get("max_wall_time_seconds") or 0
    max_iter = budget.get("max_iterations")
    started = parse_iso(state.get("created_at"))
    done = False
    reason: str | None = None
    if (run_dir / SENTINEL_STOP).exists():
        # user-owned stop sentinel: always terminal (stop condition 4).
        done, reason = True, "user_stop_requested"
    elif (run_dir / SENTINEL_BLOCKED).exists():
        # operator-owned stop file: always terminal.
        done, reason = True, "operator_blocked"
    elif max_usd and float(budget.get("spent_usd", 0)) >= float(max_usd):
        done, reason = True, "credit_exhausted:usd"
    elif max_tokens and int(budget.get("spent_tokens", 0)) >= int(max_tokens):
        done, reason = True, "credit_exhausted:tokens"
    elif max_wall and started and (datetime.now(timezone.utc) - started).total_seconds() >= float(max_wall):
        done, reason = True, "credit_exhausted:wall_time"
    elif isinstance(max_iter, int) and max_iter > 0 and int(budget.get("spent_iterations", 0)) >= max_iter:
        # the iteration cap is physical (append refuses beyond it): always terminal.
        done, reason = True, "loops_reached"
    elif require_user:
        # Strongest user policy: beyond a user/operator stop or a physical budget
        # cap (handled above), only the user may end the loop. An agent-written
        # terminal status must NOT release the session.
        done, reason = False, "awaiting_user_stop"
    elif state.get("status") in TERMINAL_STATUSES:
        done, reason = True, f"terminal_status:{state.get('status')}"
    return {"done": done, "paused": paused, "reason": reason}


def done_command(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    return {"status": "ok", "action": "done", "dir": str(run_dir), **compute_done(run_dir)}


def read_hook_payload() -> str:
    """Best-effort read of the Stop-hook JSON on stdin that never blocks the
    fail-open hook. On POSIX a zero-timeout select guards against an inherited idle
    pipe; on Windows (or where select is unavailable) the runtime reads directly,
    matching how Claude Code and the tests pipe the payload and then close stdin."""
    stdin = sys.stdin
    try:
        if stdin is None or stdin.isatty():
            return ""
    except (ValueError, OSError):
        return ""
    if os.name == "posix":
        try:
            import select

            ready, _, _ = select.select([stdin], [], [], 0)
        except (OSError, ValueError):
            return ""
        if not ready:
            return ""
    try:
        return stdin.read() or ""
    except (OSError, ValueError):
        return ""


def hook_payload_is_reentrant(payload: str) -> bool:
    """True when Claude reports the Stop hook is already active, so a block can never
    build an infinite loop."""
    if not payload:
        return False
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        # Substring fallback for non-JSON or partial input (matches the old shell wrapper).
        return '"stop_hook_active": true' in payload or '"stop_hook_active":true' in payload
    return bool(isinstance(data, dict) and data.get("stop_hook_active"))


def hook_check_command(args: argparse.Namespace) -> dict[str, Any]:
    try:
        if os.environ.get("AUTOLOOP_DISABLE"):
            return {"status": "ok", "action": "hook-check", "block": False, "reason": "disabled_env"}
        # Headless driver runs enforce the policy themselves; the interactive hook
        # stands down so it never double-governs a driver iteration.
        if os.environ.get("AUTOLOOP_DRIVER"):
            return {"status": "ok", "action": "hook-check", "block": False, "reason": "driver_active"}
        # Re-entrancy: allow turn-end when Claude reports the Stop hook is already active.
        if hook_payload_is_reentrant(read_hook_payload()):
            return {"status": "ok", "action": "hook-check", "block": False, "reason": "stop_hook_active"}
        reg = registry_dir(args)
        gc_registry(reg)
        # Workspace root for hooks: Grok sets GROK_WORKSPACE_ROOT and the Claude
        # alias CLAUDE_PROJECT_DIR; Claude sets CLAUDE_PROJECT_DIR. Diagnostic only
        # on Grok (Stop is non-blocking there).
        raw_root = (
            args.root
            or os.environ.get("GROK_WORKSPACE_ROOT")
            or os.environ.get("CLAUDE_PROJECT_DIR")
        )
        root = Path(raw_root).expanduser().resolve() if raw_root else Path.cwd().resolve()
        match: dict[str, Any] | None = None
        for _, entry in list_registry_entries(reg):
            if not entry_is_live(entry):
                continue
            entry_root = entry.get("project_root")
            if not entry_root:
                continue
            # Re-resolve the stored root at match time so a planted or stale
            # entry cannot capture an unrelated session via a symlink or a
            # non-canonical path.
            entry_root = str(Path(str(entry_root)).expanduser().resolve())
            if str(root) == entry_root or str(root).startswith(entry_root + os.sep):
                match = entry
                break
        if match is None:
            return {"status": "ok", "action": "hook-check", "block": False, "reason": "no_active_loop"}
        if entry_owned_by_live_driver(match):
            return {
                "status": "ok",
                "action": "hook-check",
                "block": False,
                "reason": "headless_driver_active",
                "run_id": match.get("run_id"),
            }
        loop_dir = Path(str(match.get("loop_dir", ""))).expanduser()
        if not loop_dir.exists():
            return {"status": "ok", "action": "hook-check", "block": False, "reason": "loop_dir_missing"}
        verdict = compute_done(loop_dir)
        if verdict.get("done") or verdict.get("paused"):
            return {
                "status": "ok",
                "action": "hook-check",
                "block": False,
                "reason": verdict.get("reason") or ("paused" if verdict.get("paused") else "done"),
            }
        return {
            "status": "ok",
            "action": "hook-check",
            "block": True,
            "reason": "active_not_done",
            "loop_dir": str(loop_dir),
            "message": (
                "Autoloop is active and not finished: do the next iteration now. Do NOT stop, "
                "pause, or ask to continue. Kill switch: set AUTOLOOP_DISABLE=1, remove the "
                f"registry entry for run {match.get('run_id')}, or run `touch {loop_dir}/{SENTINEL_STOP}`."
            ),
        }
    except Exception as exc:  # noqa: BLE001 - the Stop hook must fail open.
        return {"status": "ok", "action": "hook-check", "block": False, "reason": f"error_fail_open:{exc}"}


# --- Provider adapters: truly-autonomous headless driving per install target ---
# Each spec builds the exact one-iteration headless invocation for one agent
# CLI. `agent-cmd` only constructs and PATH-probes commands (offline); `drive
# --provider` executes them. Operator overrides (highest first):
#   AAS_AUTOLOOP_CMD_<PROVIDER>  full shell command template; {prompt} is
#                                substituted shell-quoted, {dir} verbatim, and
#                                the prompt is also exported as $AUTOLOOP_PROMPT
#   AAS_AUTOLOOP_ARGS_<PROVIDER> replacement argument template (shlex-split;
#                                {prompt}/{dir} placeholders substituted)
#   AAS_AUTOLOOP_BIN_<PROVIDER>  replacement binary path
#   AAS_GROK                     (grok only) binary override when AAS_AUTOLOOP_BIN_GROK
#                                is unset; aligned with installer GROK_CLI_TOOL_SPEC
# The default flag sets grant full tool autonomy, which unattended research
# loops require; point the loop at a workspace you trust the agent to write.
#
# Grok binary preference MUST stay aligned with installer GROK_CLI_TOOL_SPEC
# (drive/delegation): prefer region proxy grok-remote when present, else bare
# grok. Diagnostics/smoke stay bare-grok-only (installer GROK_DIAGNOSTIC_*).
# Logical provider id is always "grok" — never a separate "grok-remote" provider.

# Platform-keyed candidates for provider "grok" (first existing/executable wins).
# Keep preference order in sync with installer/ai_agents_skills/grok.py GROK_CLI_TOOL_SPEC.
GROK_BINARY_CANDIDATES: dict[str, list[str]] = {
    "linux": [
        "grok-remote",
        "~/grok-proxy/grok-remote",
        "grok",
        "~/.local/bin/grok",
        "~/.grok/bin/grok",
    ],
    "macos": [
        "grok-remote",
        "~/grok-proxy/grok-remote",
        "grok",
        "~/.local/bin/grok",
        "~/.grok/bin/grok",
        "/opt/homebrew/bin/grok",
        "/usr/local/bin/grok",
    ],
    "wsl": [
        "grok-remote",
        "~/grok-proxy/grok-remote",
        "grok",
        "~/.local/bin/grok",
        "~/.grok/bin/grok",
    ],
    "windows": [
        "grok-remote.cmd",
        "grok-remote",
        "%USERPROFILE%\\.grok\\bin\\grok.exe",
        "grok.exe",
        "grok",
    ],
}

PROVIDER_SPECS: dict[str, dict[str, Any]] = {
    "claude": {
        "binaries": ["claude"],
        "args": ["-p", "{prompt}", "--dangerously-skip-permissions"],
        "consent_note": "--dangerously-skip-permissions grants full tool autonomy",
    },
    "codex": {
        "binaries": ["codex"],
        "args": ["exec", "--full-auto", "{prompt}"],
        "consent_note": "--full-auto runs with the workspace-write sandbox",
    },
    "deepseek": {
        "binaries": ["codewhale", "codewhale-tui", "deepseek"],
        "args": ["exec", "--auto", "{prompt}"],
        "consent_note": "--auto enables tool-backed agent mode with auto-approvals",
    },
    "opencode": {
        "binaries": ["opencode"],
        "args": ["run", "{prompt}"],
        "consent_note": "runs with the opencode agent's configured permissions",
    },
    "copilot": {
        "binaries": ["copilot"],
        "args": ["-p", "{prompt}", "--allow-all-tools"],
        "consent_note": "--allow-all-tools grants full tool autonomy",
    },
    "antigravity": {
        "binaries": ["gemini"],
        "args": ["--yolo", "-p", "{prompt}"],
        "consent_note": "--yolo auto-approves all actions",
    },
    "grok": {
        # Short display list for error messages; full platform lists in GROK_BINARY_CANDIDATES.
        "binaries": ["grok-remote", "grok"],
        "args": ["-p", "{prompt}", "--yolo"],
        "consent_note": (
            "--yolo auto-approves tools for unattended loops; "
            "binary is first of grok-remote then grok (platform-aware candidates); "
            "override AAS_AUTOLOOP_BIN_GROK or AAS_GROK"
        ),
        "platform_candidates": GROK_BINARY_CANDIDATES,
    },
}

# Scanned only over the output of FAILED iteration commands: a match means
# "provider credit/quota outage - pause and retry" instead of a hard failure.
QUOTA_PATTERN = re.compile(
    r"rate.?limit|quota|credit ?balance|insufficient[ _-]?(?:credit|funds|quota)|"
    r"usage ?limit|out of credits?|credits? (?:has |have |is |are )?(?:been )?"
    r"(?:run out|exhausted|depleted)|limit (?:reached|exceeded)|"
    r"too many requests|\b429\b|overloaded|billing",
    re.IGNORECASE,
)


def format_remote_inbox_for_prompt(job_id: str | None = None) -> str:
    """Best-effort remote-bridge inbox block for headless iterations.

    Uses AAS_REMOTE_JOB_ID when job_id is omitted. Never raises; empty on failure.
    Claims and consumes items so each is delivered at-most-once after a successful
    claim+consume cycle (see remote-bridge mailbox SM).
    """
    jid = job_id or os.environ.get("AAS_REMOTE_JOB_ID")
    if not jid:
        return ""
    try:
        # Import sibling skill when installed under the same workspace/skills tree.
        skills_root = Path(__file__).resolve().parent.parent
        rb_path = skills_root / "remote-bridge" / "remote_bridge.py"
        if not rb_path.is_file():
            return ""
        import importlib.util

        spec = importlib.util.spec_from_file_location("aas_remote_bridge", rb_path)
        if spec is None or spec.loader is None:
            return ""
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        mb = mod.Mailbox()
        block, ids = mb.format_inbox_block(jid, claimer=f"arl-drive-{os.getpid()}")
        if ids:
            mb.consume_claimed(jid, ids)
        return block or ""
    except Exception:  # noqa: BLE001 - prompt assembly must not kill drive
        return ""


def iteration_prompt(run_dir: Path) -> str:
    """The standard one-iteration contract handed to a headless agent."""
    base = (
        "You are one iteration of a bounded autonomous research loop governed by "
        "the autonomous-research-loop skill and the autonomous-loop-enforcement "
        f"policy. The loop directory is: {run_dir}. Do exactly ONE iteration now: "
        "(1) read recovery.md, loop_state.json, budget.json, and the tail of "
        "iterations.jsonl in that directory; (2) execute the single next action "
        "they record, following the loop's single-path policy and evidence gates; "
        "(3) verify the result independently as the loop protocol requires; "
        "(4) append exactly one iteration record to iterations.jsonl (prefer the "
        "autonomous-research-loop-runtime append-iteration helper) and update "
        "loop_state.json, budget.json, and recovery.md so the next iteration can "
        "resume from files alone; (5) append a 3-6 sentence human-readable entry "
        "to PROGRESS_REPORT.md in the loop directory (create it with a short "
        "header if absent): what this iteration did, what it concluded, whether "
        "it was independently verified, and what comes next — written for the "
        "project owner, not for the next agent; (6) exit. Do not run more than one iteration. "
        "Do not stop the loop yourself: the headless driver owns the stop "
        "conditions. If you hit a credit or quota error, exit nonzero with the "
        "provider's error text visible in your output."
    )
    inbox = format_remote_inbox_for_prompt()
    if inbox:
        return base + "\n\n" + inbox
    return base


def resolve_remote_notify_argv(channel: str, text: str, job_id: str | None = None) -> list[str] | None:
    """Build argv for remote-bridge send (no shell). Returns None if unavailable."""
    if os.environ.get("AAS_ALLOW_RAW_NOTIFY_CMD") == "1" and os.environ.get("AAS_AUTOLOOP_NOTIFY_CMD"):
        return None  # caller may use shell escape hatch
    skills_root = Path(__file__).resolve().parent.parent
    rb = skills_root / "remote-bridge" / "remote_bridge.py"
    if not rb.is_file():
        return None
    py = os.environ.get("AAS_RUNTIME_PYTHON") or sys.executable
    argv = [py, str(rb), "send", "--text", text]
    if channel in {"zulip", "telegram", "both"}:
        argv.extend(["--channel", channel])
    if job_id:
        argv.extend(["--job", job_id])
    return argv


def provider_env_key(provider: str) -> str:
    return provider.upper().replace("-", "_")


def runtime_platform_name() -> str:
    """Coarse platform key for binary candidate lists (linux/macos/wsl/windows)."""
    if os.name == "nt" or sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    try:
        if "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower():
            return "wsl"
    except OSError:
        pass
    return "linux"


def expand_env_in_path(raw: str, environ: dict[str, str]) -> str:
    """Expand ${VAR}, $VAR, and %VAR% using *environ*, then ~."""
    text = raw
    # Windows %VAR%
    while True:
        start = text.find("%")
        if start < 0:
            break
        end = text.find("%", start + 1)
        if end < 0:
            break
        name = text[start + 1 : end]
        if not name:
            break
        text = text[:start] + environ.get(name, environ.get(name.upper(), "")) + text[end + 1 :]
    # ${VAR} then $VAR (POSIX-ish)
    def _dollar_brace(match: re.Match[str]) -> str:
        return environ.get(match.group(1), "")

    text = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", _dollar_brace, text)
    text = re.sub(
        r"\$([A-Za-z_][A-Za-z0-9_]*)",
        lambda m: environ.get(m.group(1), ""),
        text,
    )
    # Expand ~ using *environ* HOME/USERPROFILE so isolated resolves (tests) work.
    home = environ.get("HOME") or environ.get("USERPROFILE")
    if text == "~":
        text = home or str(Path.home())
    elif text.startswith("~/") or text.startswith("~\\"):
        text = str(Path(home or Path.home()) / text[2:])
    elif text.startswith("~"):
        # Other ~user forms: fall back to Path.expanduser (host home).
        text = str(Path(text).expanduser())
    return text


def candidate_is_usable(raw: str, environ: dict[str, str]) -> tuple[bool, str]:
    """Return (usable, resolved_path_or_name) for a binary candidate."""
    expanded = expand_env_in_path(raw, environ)
    if not expanded:
        return False, raw
    path = Path(expanded)
    # Absolute / explicit path (after expand): must exist as a file.
    if path.is_absolute() or os.sep in expanded or (os.altsep and os.altsep in expanded):
        try:
            if path.is_file():
                return True, str(path)
        except OSError:
            return False, expanded
        return False, expanded
    # Bare command name: PATH lookup (Windows PATHEXT applies via shutil.which).
    # Honor *environ* PATH when provided (tests and isolated resolve).
    path_env = environ.get("PATH")
    located = shutil.which(expanded, path=path_env) if path_env is not None else shutil.which(expanded)
    if located:
        return True, located
    # Relative path that exists as a file (e.g. ./grok)
    try:
        if path.is_file():
            return True, str(path.resolve())
    except OSError:
        pass
    return False, expanded


def provider_binary_candidates(
    provider: str, environ: dict[str, str] | None = None, platform: str | None = None
) -> list[str]:
    """Ordered binary candidates for a provider (for probe + error messages)."""
    env = os.environ if environ is None else environ
    if provider not in PROVIDER_SPECS:
        raise ValueError(f"unknown provider: {provider}")
    spec = PROVIDER_SPECS[provider]
    plat = platform or runtime_platform_name()
    platform_map = spec.get("platform_candidates")
    if isinstance(platform_map, dict):
        return list(platform_map.get(plat) or platform_map.get("linux") or spec["binaries"])
    return list(spec["binaries"])


def resolve_provider_binary(
    provider: str, environ: dict[str, str] | None = None, platform: str | None = None
) -> tuple[str, bool, list[str]]:
    """Resolve binary path for a provider.

    Precedence: AAS_AUTOLOOP_BIN_<P> → (grok only) AAS_GROK → platform candidates.
    Returns (binary, found, tried_list).
    """
    env = os.environ if environ is None else environ
    if provider not in PROVIDER_SPECS:
        raise ValueError(f"unknown provider: {provider}")
    key = provider_env_key(provider)
    tried: list[str] = []
    override = env.get(f"AAS_AUTOLOOP_BIN_{key}")
    if override:
        tried.append(override)
        ok, resolved = candidate_is_usable(override, env)
        return (resolved if ok else override), ok, tried
    if provider == "grok":
        aas_grok = env.get("AAS_GROK")
        if aas_grok:
            tried.append(aas_grok)
            ok, resolved = candidate_is_usable(aas_grok, env)
            if ok:
                return resolved, True, tried
    candidates = provider_binary_candidates(provider, environ=env, platform=platform)
    for cand in candidates:
        tried.append(cand)
        ok, resolved = candidate_is_usable(cand, env)
        if ok:
            return resolved, True, tried
    # Nothing found: return first candidate name for error display.
    fallback = candidates[0] if candidates else provider
    return fallback, False, tried


def resolve_provider_command(
    provider: str, run_dir: Path, environ: dict[str, str] | None = None
) -> dict[str, Any]:
    """Build the headless one-iteration command for a provider (no execution)."""
    env = os.environ if environ is None else environ
    if provider not in PROVIDER_SPECS:
        raise ValueError(f"unknown provider: {provider}")
    spec = PROVIDER_SPECS[provider]
    key = provider_env_key(provider)
    prompt = iteration_prompt(run_dir)
    full = env.get(f"AAS_AUTOLOOP_CMD_{key}")
    if full:
        shell_cmd = full.replace("{prompt}", shlex.quote(prompt)).replace(
            "{dir}", str(run_dir)
        )
        return {
            "provider": provider,
            "mode": "shell",
            "shell": shell_cmd,
            "binary": None,
            "binary_found": True,
            "prompt": prompt,
            "consent_note": spec["consent_note"],
            "tried": [],
        }
    binary, binary_found, tried = resolve_provider_binary(provider, environ=env)
    args_raw = env.get(f"AAS_AUTOLOOP_ARGS_{key}")
    template = shlex.split(args_raw) if args_raw else list(spec["args"])
    argv = [str(binary)] + [
        arg.replace("{prompt}", prompt).replace("{dir}", str(run_dir))
        for arg in template
    ]
    return {
        "provider": provider,
        "mode": "argv",
        "argv": argv,
        "binary": str(binary),
        "binary_found": binary_found,
        "prompt": prompt,
        "consent_note": spec["consent_note"],
        "tried": tried,
    }


def agent_cmd_command(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.dir).expanduser().resolve()
    providers = sorted(PROVIDER_SPECS) if args.provider == "all" else [args.provider]
    entries: dict[str, Any] = {}
    for provider in providers:
        entry = resolve_provider_command(provider, run_dir)
        if not args.print_prompt:
            entry.pop("prompt", None)
        entries[provider] = entry
    result: dict[str, Any] = {
        "status": "ok",
        "action": "agent-cmd",
        "dir": str(run_dir),
        "providers": entries,
    }
    if args.print_prompt:
        result["iteration_prompt"] = iteration_prompt(run_dir)
    return result


def last_ledger_record(run_dir: Path) -> dict[str, Any] | None:
    path = loop_paths(run_dir)["iterations"]
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def loop_driver_entry(reg: Path, run_dir: Path) -> dict[str, Any] | None:
    for _, entry in list_registry_entries(reg):
        if str(entry.get("loop_dir", "")) == str(run_dir):
            return entry
    return None


def progress_paths(run_dir: Path, log_dir: Path | None = None) -> dict[str, Path]:
    """On-disk progress surfaces updated by drive and watch."""
    logs = Path(log_dir).expanduser() if log_dir is not None else run_dir / "driver_logs"
    return {
        "log_dir": logs,
        "progress_jsonl": logs / "progress.jsonl",
        "live_status": run_dir / "LIVE_STATUS.md",
    }


def build_progress_event(
    run_dir: Path,
    event: str,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured progress event from the current ledger tip."""
    paths = loop_paths(run_dir)
    record = last_ledger_record(run_dir) or {}
    state: dict[str, Any] = {}
    budget: dict[str, Any] = {}
    try:
        if paths["state"].exists():
            state = read_json(paths["state"])
    except (OSError, ValueError, json.JSONDecodeError):
        state = {}
    try:
        if paths["budget"].exists():
            budget = read_json(paths["budget"])
    except (OSError, ValueError, json.JSONDecodeError):
        budget = {}
    iteration = int(record.get("iteration") or state.get("last_iteration") or 0)
    spent = int(budget.get("spent_iterations") or iteration or 0)
    max_iter = int(budget.get("max_iterations") or 0)
    decision = str(record.get("decision") or state.get("status") or "?")
    objective = str(record.get("objective") or "")
    output = str(record.get("output") or "")
    remaining = max(0, max_iter - spent) if max_iter else 0
    text = (
        f"autoloop {run_dir.name}: [{event}] iter {iteration}/{max_iter or '?'} "
        f"({decision}) — {objective[:160]}"
        + (f" | {output[:240]}" if output else "")
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": utc_now(),
        "event": event,
        "dir": str(run_dir),
        "iteration": iteration,
        "spent_iterations": spent,
        "max_iterations": max_iter,
        "remaining_iterations": remaining,
        "decision": decision,
        "status": str(state.get("status") or ""),
        "objective": objective[:400],
        "output_preview": output[:500],
        "text": text,
    }
    if extra:
        for key, value in extra.items():
            if value is None or key == "text_override":
                continue
            payload[key] = value
        if extra.get("text_override"):
            payload["text"] = str(extra["text_override"])
    return payload


def write_live_status(run_dir: Path, payload: dict[str, Any], log_dir: Path | None = None) -> None:
    """Write LIVE_STATUS.md and append progress.jsonl (best-effort, never raises)."""
    try:
        paths = progress_paths(run_dir, log_dir)
        paths["log_dir"].mkdir(parents=True, exist_ok=True)
        append_jsonl(paths["progress_jsonl"], payload)
        recovery_hint = ""
        recovery_path = loop_paths(run_dir)["recovery"]
        if recovery_path.exists():
            try:
                for line in recovery_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("- HEARTBEAT") or line.startswith("- Next safe action"):
                        recovery_hint = line.lstrip("- ").strip()
                        break
            except OSError:
                recovery_hint = ""
        body = "\n".join(
            [
                "# Autonomous loop live status",
                "",
                f"- Updated: {payload.get('timestamp', utc_now())}",
                f"- Event: `{payload.get('event', '')}`",
                f"- Loop: `{run_dir}`",
                (
                    f"- Progress: **{payload.get('spent_iterations', '?')}"
                    f"/{payload.get('max_iterations') or '?'}**"
                    f" ({payload.get('remaining_iterations', '?')} remaining)"
                ),
                f"- Loop status: `{payload.get('status') or '?'}`",
                f"- Last decision: `{payload.get('decision') or '?'}`",
                f"- Last iteration: **{payload.get('iteration', '?')}**",
                f"- Objective: {payload.get('objective') or '(none yet)'}",
                f"- Output preview: {payload.get('output_preview') or '(none yet)'}",
                f"- Summary: {payload.get('text') or ''}",
            ]
            + (
                [f"- Driver rc: `{payload.get('rc')}`", f"- Drive cycle: {payload.get('drive_cycle')}"]
                if payload.get("drive_cycle") is not None or payload.get("rc") is not None
                else []
            )
            + (
                [f"- Log: `{payload.get('log_path')}`"]
                if payload.get("log_path")
                else []
            )
            + (
                [f"- Recovery hint: {recovery_hint}"]
                if recovery_hint
                else []
            )
            + [
                "",
                "This file is rewritten after every drive cycle and every `watch` event.",
                "Full history: `driver_logs/progress.jsonl`. Narrative log: `PROGRESS_REPORT.md`.",
                "Kill switches: `STOP_REQUESTED`, `PAUSE`, or disarm the driver registry entry.",
                "",
            ]
        )
        paths["live_status"].write_text(body, encoding="utf-8")
    except Exception:  # noqa: BLE001 - progress surfaces must never kill the driver.
        pass


_DEFAULT_REMOTE_NOTIFY_EVENTS = frozenset(
    {
        "iteration_ok",
        "iteration_failed",
        "iteration",
        "quota_wait",
        "drive_stop",
        "terminal",
        "driver_dead",
    }
)


def emit_loop_progress(
    run_dir: Path,
    event: str,
    *,
    log_dir: Path | None = None,
    notify_cmd: str | None = None,
    notify_channel: str | None = None,
    to_stderr: bool = True,
    to_stdout_json: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a progress event to disk, optional notify hook, and console."""
    payload = build_progress_event(run_dir, event, extra=extra)
    write_live_status(run_dir, payload, log_dir=log_dir)
    env_payload = {
        "AUTOLOOP_EVENT": str(event),
        "AUTOLOOP_DIR": str(run_dir),
        "AUTOLOOP_ITERATION": str(payload.get("iteration", "")),
        "AUTOLOOP_DECISION": str(payload.get("decision", "")),
        "AUTOLOOP_TEXT": str(payload.get("text", "")),
        "AUTOLOOP_SPENT": str(payload.get("spent_iterations", "")),
        "AUTOLOOP_MAX": str(payload.get("max_iterations", "")),
        "AUTOLOOP_STATUS": str(payload.get("status", "")),
    }
    if to_stderr:
        sys.stderr.write(str(payload.get("text", "")) + "\n")
        sys.stderr.flush()
    if to_stdout_json and not notify_cmd and not notify_channel:
        print(json.dumps(env_payload), flush=True)
    # Structured remote-bridge notify (preferred).
    channel = notify_channel or os.environ.get("AAS_AUTOLOOP_NOTIFY") or os.environ.get(
        "AAS_REMOTE_NOTIFY"
    )
    if channel and event in _DEFAULT_REMOTE_NOTIFY_EVENTS:
        argv = resolve_remote_notify_argv(
            channel if channel != "both" else "both",
            str(payload.get("text") or ""),
            job_id=os.environ.get("AAS_REMOTE_JOB_ID"),
        )
        if argv:
            try:
                subprocess.run(argv, check=False, timeout=60, capture_output=True)
            except Exception:  # noqa: BLE001 - notify is best-effort
                pass
    if notify_cmd:
        if os.environ.get("AAS_ALLOW_RAW_NOTIFY_CMD") == "1" or notify_cmd:
            # Raw shell notify remains available; prefer AAS_ALLOW_RAW_NOTIFY_CMD=1.
            watch_notify(notify_cmd, env_payload)
    return payload


def watch_notify(cmd: str | None, payload: dict[str, str]) -> None:
    if not cmd:
        print(json.dumps(payload), flush=True)
        return
    env = dict(os.environ)
    env.update(payload)
    try:
        subprocess.run(cmd, shell=True, env=env, timeout=60, check=False)
    except Exception:  # noqa: BLE001 - notification is best-effort.
        pass


def watch_command(args: argparse.Namespace) -> dict[str, Any]:
    """Progress reporter for a driven loop.

    Emits one event per newly appended iteration, one on terminal state, and
    one when the registry says a driver owns the loop but its pid is dead.
    Always refreshes LIVE_STATUS.md and appends driver_logs/progress.jsonl.
    Read-only alongside `drive`; safe to start or stop at any time. Without
    --notify-cmd, events also print as JSON lines; with it, the command runs via
    the shell with AUTOLOOP_EVENT/_DIR/_ITERATION/_DECISION/_TEXT in env.
    """
    run_dir = Path(args.dir).expanduser().resolve()
    reg = registry_dir(args)
    log_dir = (
        Path(args.log_dir).expanduser()
        if getattr(args, "log_dir", None)
        else run_dir / "driver_logs"
    )
    start_record = last_ledger_record(run_dir)
    start_iter = int(start_record.get("iteration", 0)) if start_record else 0
    seen = args.from_iteration if args.from_iteration >= 0 else start_iter
    driver_dead_alerted = False
    events = 0
    notify_channel = getattr(args, "notify", None) or os.environ.get("AAS_AUTOLOOP_NOTIFY")
    # Seed LIVE_STATUS immediately so operators see current tip without waiting.
    emit_loop_progress(
        run_dir,
        "watch_start",
        log_dir=log_dir,
        notify_cmd=None,
        notify_channel=None,
        to_stderr=False,
        to_stdout_json=False,
        extra={"source": "watch"},
    )
    while True:
        verdict = compute_done(run_dir)
        record = last_ledger_record(run_dir)
        current = int(record.get("iteration", 0)) if record else 0
        if record and current > seen:
            decision = str(record.get("decision", "?"))
            text = (
                f"autoloop {run_dir.name}: iteration {current} ({decision}) — "
                f"{str(record.get('objective', ''))[:160]} | {str(record.get('output', ''))[:240]}"
            )
            emit_loop_progress(
                run_dir,
                "iteration",
                log_dir=log_dir,
                notify_cmd=args.notify_cmd,
                notify_channel=notify_channel,
                to_stderr=bool(args.notify_cmd or notify_channel),
                to_stdout_json=not bool(args.notify_cmd or notify_channel),
                extra={"source": "watch", "text_override": text},
            )
            events += 1
            seen = current
        if verdict.get("done"):
            reason = str(verdict.get("reason") or "done")
            emit_loop_progress(
                run_dir,
                "terminal",
                log_dir=log_dir,
                notify_cmd=args.notify_cmd,
                notify_channel=notify_channel,
                to_stderr=bool(args.notify_cmd or notify_channel),
                to_stdout_json=not bool(args.notify_cmd or notify_channel),
                extra={"source": "watch", "terminal_reason": reason},
            )
            events += 1
            return {"status": "ok", "action": "watch", "events": events, "reason": reason}
        if not verdict.get("paused"):
            entry = loop_driver_entry(reg, run_dir)
            pid = entry.get("pid") if entry else None
            alive = isinstance(pid, int) and pid > 0 and pid_alive(pid)
            if entry is not None and not alive and not driver_dead_alerted:
                driver_dead_alerted = True
                emit_loop_progress(
                    run_dir,
                    "driver_dead",
                    log_dir=log_dir,
                    notify_cmd=args.notify_cmd,
                    notify_channel=notify_channel,
                    to_stderr=bool(args.notify_cmd or notify_channel),
                    to_stdout_json=not bool(args.notify_cmd or notify_channel),
                    extra={"source": "watch", "driver_pid": pid},
                )
                events += 1
            elif alive:
                driver_dead_alerted = False
        if args.once:
            return {"status": "ok", "action": "watch", "events": events, "reason": "once"}
        time.sleep(max(5, int(args.poll)))


def refresh_heartbeat(reg: Path, run_id: object) -> None:
    if not isinstance(run_id, str) or not run_id:
        return
    path = reg / f"{run_id}.json"
    try:
        entry = read_json(path)
        entry["heartbeat"] = utc_now()
        write_json(path, entry)
    except Exception:  # noqa: BLE001 - heartbeat refresh is best-effort.
        pass


def read_log_tail(path: Path, limit: int = 8192) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


DRIVE_EXIT_CODES = {
    "max_failures": 3,
    "runtime_error": 4,
    "quota_wait_exhausted": 5,
    "provider_unavailable": 6,
    "bad_arguments": 2,
}


def drive_command(args: argparse.Namespace) -> dict[str, Any]:
    """Cross-platform headless driver: run one iteration command per loop until the
    runtime reports the loop is done (loops reached, credit/budget exhausted, goal
    resolved, or user stop), or until the iteration command fails too many times in
    a row, or the runtime state cannot be read. The driver is the sole enforcer in
    headless mode: it exports AUTOLOOP_DRIVER=1 so the interactive Stop hook stands
    down, and it derives "done" only from the runtime, never from the agent's own
    say-so. On any inability to determine state it fails safe (stops). This is the
    platform-neutral replacement for the bash driver; the .sh shim delegates here."""
    run_dir = Path(args.dir).expanduser().resolve()
    root = Path(args.root).expanduser().resolve() if args.root else run_dir
    iter_timeout = args.iteration_timeout if args.iteration_timeout and args.iteration_timeout > 0 else None
    max_failures = max(1, int(args.max_failures))
    poll = max(0.0, float(args.poll))
    provider = getattr(args, "provider", None)
    cmd = getattr(args, "cmd", None)
    if bool(provider) == bool(cmd):
        return {
            "status": "failed",
            "action": "drive",
            "dir": str(run_dir),
            "reason": "bad_arguments",
            "error": "exactly one of --cmd or --provider is required",
            "exit_code": DRIVE_EXIT_CODES["bad_arguments"],
        }
    quota_backoff = max(0, int(getattr(args, "quota_backoff", 900)))
    max_quota_waits = max(0, int(getattr(args, "max_quota_waits", 0)))
    log_dir = (
        Path(args.log_dir).expanduser()
        if getattr(args, "log_dir", None)
        else run_dir / "driver_logs"
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    notify_cmd = getattr(args, "notify_cmd", None)
    notify_channel = getattr(args, "notify", None) or os.environ.get("AAS_AUTOLOOP_NOTIFY")
    progress_enabled = not bool(getattr(args, "no_progress", False))

    def _progress(event: str, **extra: Any) -> None:
        if not progress_enabled:
            return
        emit_loop_progress(
            run_dir,
            event,
            log_dir=log_dir,
            notify_cmd=notify_cmd,
            notify_channel=notify_channel,
            to_stderr=True,
            to_stdout_json=False,
            extra=extra or None,
        )

    # Arm (best-effort) so a concurrent interactive Stop hook for this root stands
    # down; the driver itself enforces "done" regardless of the registry.
    reg = registry_dir(args)
    run_id: object = None
    arm_ns = argparse.Namespace(
        dir=str(run_dir),
        root=str(root),
        force=False,
        pid=os.getpid(),
        driver=True,
        registry_dir=getattr(args, "registry_dir", None),
    )
    try:
        run_id = arm_loop(arm_ns).get("run_id")
    except Exception:  # noqa: BLE001 - arming is best-effort.
        pass

    failures = 0
    quota_waits = 0
    quota_waits_total = 0
    iterations_run = 0
    reason = "unknown"
    was_paused = False
    _progress("drive_start", source="drive", provider=provider or "", drive_pid=os.getpid())
    try:
        while True:
            try:
                verdict = compute_done(run_dir)
            except Exception:  # noqa: BLE001 - unreadable state -> fail safe (stop).
                reason = "runtime_error"
                break
            if verdict.get("done"):
                reason = "done"
                break
            if verdict.get("paused"):
                if not was_paused:
                    _progress("paused", source="drive")
                    was_paused = True
                time.sleep(poll)
                continue
            was_paused = False
            refresh_heartbeat(reg, run_id)
            if provider:
                try:
                    spec = resolve_provider_command(provider, run_dir)
                except ValueError:
                    reason = "bad_arguments"
                    break
                if spec["mode"] == "argv" and not spec["binary_found"]:
                    tried = spec.get("tried") or PROVIDER_SPECS[provider]["binaries"]
                    sys.stderr.write(
                        f"autoloop-driver: no {provider} binary found "
                        f"(tried: {', '.join(tried)}); "
                        f"set AAS_AUTOLOOP_BIN_{provider_env_key(provider)} or "
                        f"AAS_AUTOLOOP_CMD_{provider_env_key(provider)}"
                        + (
                            " or AAS_GROK"
                            if provider == "grok"
                            else ""
                        )
                        + "\n"
                    )
                    reason = "provider_unavailable"
                    break
                run_args: object = spec["argv"] if spec["mode"] == "argv" else spec["shell"]
                use_shell = spec["mode"] == "shell"
                prompt = spec["prompt"]
            else:
                run_args = cmd
                use_shell = True
                prompt = iteration_prompt(run_dir)
            child_env = dict(
                os.environ,
                AUTOLOOP_DRIVER="1",
                AUTOLOOP_DIR=str(run_dir),
                AUTOLOOP_ROOT=str(root),
                AUTOLOOP_PROMPT=prompt,
            )
            iterations_run += 1
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            log_path = log_dir / f"iter_{stamp}_{iterations_run:04d}.log"
            _progress(
                "iteration_start",
                source="drive",
                drive_cycle=iterations_run,
                log_path=str(log_path),
                provider=provider or "",
            )
            try:
                with log_path.open("w", encoding="utf-8", errors="replace") as log_fh:
                    # Always run the iteration agent with project root as cwd so
                    # headless CLIs (including grok -p) see the correct workspace
                    # even when the driver was started from another directory.
                    completed = subprocess.run(
                        run_args,
                        shell=use_shell,
                        env=child_env,
                        cwd=str(root),
                        timeout=iter_timeout,
                        stdout=log_fh,
                        stderr=subprocess.STDOUT,
                    )
                rc = completed.returncode
            except subprocess.TimeoutExpired:
                rc = 124
            except OSError as exc:
                with log_path.open("a", encoding="utf-8", errors="replace") as log_fh:
                    log_fh.write(f"autoloop-driver: spawn failed: {exc}\n")
                rc = 127
            if rc != 0:
                tail = read_log_tail(log_path)
                if QUOTA_PATTERN.search(tail):
                    # Credit/quota outage: honor the pause-and-wait policy instead
                    # of counting a failure. `done` re-checks budget caps and the
                    # STOP_REQUESTED sentinel on every cycle, so a paused run
                    # still stops the moment a real stop condition fires.
                    quota_waits += 1
                    quota_waits_total += 1
                    _progress(
                        "quota_wait",
                        source="drive",
                        drive_cycle=iterations_run,
                        rc=rc,
                        log_path=str(log_path),
                        quota_waits=quota_waits,
                    )
                    if max_quota_waits and quota_waits > max_quota_waits:
                        reason = "quota_wait_exhausted"
                        break
                    sys.stderr.write(
                        f"autoloop-driver: provider credit/quota signal (rc={rc}); "
                        f"waiting {quota_backoff}s before retry "
                        f"(consecutive waits: {quota_waits}"
                        + (f"/{max_quota_waits}" if max_quota_waits else "")
                        + f", log: {log_path})\n"
                    )
                    time.sleep(quota_backoff)
                    continue
                failures += 1
                _progress(
                    "iteration_failed",
                    source="drive",
                    drive_cycle=iterations_run,
                    rc=rc,
                    log_path=str(log_path),
                    failures=failures,
                    max_failures=max_failures,
                )
                sys.stderr.write(
                    f"autoloop-driver: iteration command failed "
                    f"(rc={rc}, {failures}/{max_failures}, log: {log_path})\n"
                )
                if failures >= max_failures:
                    reason = "max_failures"
                    break
            else:
                failures = 0
                quota_waits = 0
                _progress(
                    "iteration_ok",
                    source="drive",
                    drive_cycle=iterations_run,
                    rc=0,
                    log_path=str(log_path),
                )
    finally:
        disarm_ns = argparse.Namespace(
            dir=str(run_dir), run_id=None, registry_dir=getattr(args, "registry_dir", None)
        )
        try:
            disarm_loop(disarm_ns)
        except Exception:  # noqa: BLE001 - disarm is best-effort cleanup.
            pass
        _progress(
            "drive_stop",
            source="drive",
            drive_cycle=iterations_run,
            terminal_reason=reason,
            provider=provider or "",
        )

    exit_code = DRIVE_EXIT_CODES.get(reason, 0)
    return {
        "status": "failed" if exit_code else "ok",
        "action": "drive",
        "dir": str(run_dir),
        "provider": provider,
        "reason": reason,
        "iterations_run": iterations_run,
        "quota_waits_total": quota_waits_total,
        "log_dir": str(log_dir),
        "exit_code": exit_code,
        "live_status": str(run_dir / "LIVE_STATUS.md"),
        "progress_jsonl": str(log_dir / "progress.jsonl"),
    }


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline autonomous research loop ledger helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="initialize loop ledger files")
    init.add_argument("--dir", required=True, help="loop directory")
    init.add_argument("--goal", required=True, help="research loop goal")
    init.add_argument("--success-criteria", required=True, help="observable success criteria")
    init.add_argument("--mode", choices=sorted(VALID_MODES), default="bounded-research")
    init.add_argument("--max-iterations", type=positive_int, default=5)
    init.add_argument("--max-wall-time-seconds", type=positive_int, default=3600)
    init.add_argument("--max-tokens", type=positive_int, default=0)
    init.add_argument("--max-usd", type=nonnegative_float, default=0.0)
    init.add_argument("--max-depth", type=positive_int, default=3)
    init.add_argument("--max-hops", type=positive_int, default=20)
    init.add_argument("--max-child-workers", type=positive_int, default=2)
    init.add_argument("--plateau-rule", default=DEFAULT_PLATEAU_RULE)
    init.add_argument("--budget-owner", default="user")
    init.add_argument("--force", action="store_true")
    init.add_argument("--stop-on-guard-fail", action=argparse.BooleanOptionalAction, default=True)
    init.add_argument("--stop-on-missing-evidence", action=argparse.BooleanOptionalAction, default=True)
    init.add_argument("--stop-on-scope-change", action=argparse.BooleanOptionalAction, default=True)
    init.add_argument(
        "--success-check",
        default="",
        help="machine-checkable shell command that exits 0 when the loop goal is resolved "
        "(run by the driver/agent, never by the Stop hook)",
    )
    init.add_argument(
        "--require-user-stop-only",
        action="store_true",
        help="user override (priority 0): stop ONLY on explicit user stop; ignore the "
        "loop-count/credit/goal defaults",
    )
    init.add_argument("--stop-condition", action="append", help="free-text user stop requirement (priority 0)")
    init.set_defaults(func=init_loop)

    append = subparsers.add_parser("append-iteration", help="append one iteration record")
    append.add_argument("--dir", required=True)
    append.add_argument("--mode", choices=sorted(VALID_MODES), required=True)
    append.add_argument("--objective", required=True)
    append.add_argument("--decision", choices=sorted(VALID_DECISIONS), required=True)
    append.add_argument("--input-ref", action="append")
    append.add_argument("--source-id", action="append")
    append.add_argument("--claim-id", action="append")
    append.add_argument("--evidence-id", action="append")
    append.add_argument("--guard-ref", action="append")
    append.add_argument("--action-taken", action="append")
    append.add_argument("--output", default="")
    append.add_argument("--remaining-gap", action="append")
    append.add_argument("--tokens", type=positive_int, default=0)
    append.add_argument("--usd", type=nonnegative_float, default=0.0)
    append.add_argument("--wall-time-seconds", type=positive_int, default=0)
    append.add_argument("--stop-reason", default="")
    append.set_defaults(func=append_iteration)

    validate = subparsers.add_parser("validate", help="validate loop ledger files")
    validate.add_argument("--dir", required=True)
    validate.set_defaults(func=validate_command)

    status = subparsers.add_parser("status", help="summarize loop status")
    status.add_argument("--dir", required=True)
    status.set_defaults(func=status_command)

    selftest = subparsers.add_parser("selftest", help="run offline smoke test")
    selftest.set_defaults(func=selftest_command)

    def add_registry_args(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--registry-dir",
            default=None,
            help="autoloop registry root (default: $AAS_AUTOLOOP_REGISTRY or "
            "~/.local/share/ai-agents-skills/autoloop)",
        )

    watch = subparsers.add_parser(
        "watch",
        help="report loop progress: per-iteration, terminal, and driver-death events "
        "(also refreshes LIVE_STATUS.md and driver_logs/progress.jsonl)",
    )
    watch.add_argument("--dir", required=True)
    watch.add_argument(
        "--notify",
        default=None,
        choices=["zulip", "telegram", "both"],
        help="structured remote-bridge notify channel",
    )
    watch.add_argument(
        "--notify-cmd", default=None,
        help="shell command run per event with AUTOLOOP_EVENT/_DIR/_ITERATION/_DECISION/_TEXT in env (default: print JSON lines)",
    )
    watch.add_argument("--poll", type=int, default=60)
    watch.add_argument("--from-iteration", type=int, default=-1,
                       help="baseline iteration; report anything newer (default: current ledger tip)")
    watch.add_argument("--once", action="store_true", help="single poll cycle, then exit")
    watch.add_argument(
        "--log-dir",
        default=None,
        help="directory for progress.jsonl (default: <dir>/driver_logs)",
    )
    add_registry_args(watch)
    watch.set_defaults(func=watch_command)

    arm = subparsers.add_parser("arm", help="register a loop as active (force-management)")
    arm.add_argument("--dir", required=True)
    arm.add_argument("--root", default=None, help="project root this loop governs (default: loop dir)")
    arm.add_argument("--pid", type=int, default=0, help="long-lived loop/driver pid for liveness (0 = heartbeat-only)")
    arm.add_argument("--driver", action="store_true",
                     help="mark the entry as owned by a headless driver; the interactive Stop-hook stands down while that pid is alive")
    arm.add_argument("--force", action="store_true")
    add_registry_args(arm)
    arm.set_defaults(func=arm_loop)

    disarm = subparsers.add_parser("disarm", help="deregister an active loop (kill switch)")
    disarm.add_argument("--dir", default=None)
    disarm.add_argument("--run-id", default=None)
    add_registry_args(disarm)
    disarm.set_defaults(func=disarm_loop)

    active = subparsers.add_parser("active", help="list live active loops")
    add_registry_args(active)
    active.set_defaults(func=active_command)

    done = subparsers.add_parser("done", help="report whether a loop dir has met its stop condition")
    done.add_argument("--dir", required=True)
    done.set_defaults(func=done_command)

    hook = subparsers.add_parser(
        "hook-check",
        help="fail-open Stop-hook check; exit 2 only when an active loop for --root is not done",
    )
    hook.add_argument(
        "--root",
        default=None,
        help=(
            "current session project root "
            "(default: $GROK_WORKSPACE_ROOT, $CLAUDE_PROJECT_DIR, or cwd)"
        ),
    )
    add_registry_args(hook)
    hook.set_defaults(func=hook_check_command)

    agent_cmd = subparsers.add_parser(
        "agent-cmd",
        help="print the per-provider headless one-iteration command (offline; PATH probe only)",
    )
    agent_cmd.add_argument(
        "--provider",
        required=True,
        choices=sorted(PROVIDER_SPECS) + ["all"],
        help="install target whose iteration command to build, or `all` for the matrix",
    )
    agent_cmd.add_argument("--dir", required=True, help="loop directory the prompt references")
    agent_cmd.add_argument(
        "--print-prompt",
        action="store_true",
        help="include the standard one-iteration prompt in the output",
    )
    agent_cmd.set_defaults(func=agent_cmd_command)

    drive = subparsers.add_parser(
        "drive",
        help="cross-platform headless driver: run the iteration command per loop until done",
    )
    drive.add_argument("--dir", required=True)
    drive.add_argument("--root", default=None, help="project root this loop governs (default: loop dir)")
    drive.add_argument(
        "--cmd",
        default=None,
        help="iteration shell command run once per loop (mutually exclusive with --provider)",
    )
    drive.add_argument(
        "--provider",
        default=None,
        choices=sorted(PROVIDER_SPECS),
        help="build and run the standard headless iteration command for this install target",
    )
    drive.add_argument("--iteration-timeout", type=positive_int, default=1800)
    drive.add_argument("--max-failures", type=positive_int, default=3)
    drive.add_argument("--poll", type=nonnegative_float, default=5.0)
    drive.add_argument(
        "--quota-backoff",
        type=positive_int,
        default=900,
        help="seconds to wait after a detected credit/quota outage before retrying",
    )
    drive.add_argument(
        "--max-quota-waits",
        type=positive_int,
        default=0,
        help="max consecutive quota waits before giving up (0 = wait indefinitely, honoring pause-and-resume on credit exhaustion)",
    )
    drive.add_argument(
        "--log-dir",
        default=None,
        help="directory for per-iteration output logs (default: <dir>/driver_logs)",
    )
    drive.add_argument(
        "--notify",
        default=None,
        choices=["zulip", "telegram", "both"],
        help="structured remote-bridge notify channel (preferred over --notify-cmd)",
    )
    drive.add_argument(
        "--notify-cmd",
        default=None,
        help="optional shell command per progress event (AUTOLOOP_EVENT/_DIR/_ITERATION/_DECISION/_TEXT env); "
        "prefer --notify; set AAS_ALLOW_RAW_NOTIFY_CMD=1 when using untrusted templates",
    )
    drive.add_argument(
        "--no-progress",
        action="store_true",
        help="disable LIVE_STATUS.md / progress.jsonl / stderr progress lines",
    )
    add_registry_args(drive)
    drive.set_defaults(func=drive_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "command", None)
    try:
        result = args.func(args)
    except Exception as exc:  # noqa: BLE001 - CLI should return structured failure.
        result = {"status": "failed", "error": str(exc)}
        print(json.dumps(result, indent=2, sort_keys=True), file=sys.stdout)
        # The Stop hook must fail open: never block turn-end on an internal error.
        return 0 if command == "hook-check" else 1
    if command == "hook-check":
        if result.get("block"):
            sys.stderr.write(
                (result.get("message") or "Autoloop active and not finished: continue the next iteration now.")
                + "\n"
            )
            return 2
        return 0
    if command == "drive":
        print(json.dumps(result, indent=2, sort_keys=True), file=sys.stdout)
        return int(result.get("exit_code", 0))
    print(json.dumps(result, indent=2, sort_keys=True), file=sys.stdout)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
