#!/usr/bin/env python3
"""Offline ledger helper for autonomous research loops."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


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


def has_success_evidence(record: dict[str, Any]) -> bool:
    evidence_checked = record.get("evidence_checked")
    if not isinstance(evidence_checked, dict):
        return False
    claim_ids = evidence_checked.get("claim_ids")
    evidence_ids = evidence_checked.get("evidence_ids")
    return any(isinstance(item, str) and item for item in claim_ids or []) or any(
        isinstance(item, str) and item for item in evidence_ids or []
    )


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
    claim_ids = parse_many(args.claim_id)
    evidence_ids = parse_many(args.evidence_id)
    if args.decision == "stop" and remaining_after_append > 0:
        if not is_success_stop_reason(args.stop_reason):
            raise ValueError("early stop before max_iterations requires a success/proof stop_reason")
        if not claim_ids and not evidence_ids:
            raise ValueError("early stop before max_iterations requires at least one claim_id or evidence_id")
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
                    if not has_success_evidence(record):
                        errors.append(
                            f"iteration {iteration_number} early stop before max_iterations must cite claim_ids or evidence_ids"
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
        return {
            "status": "ok" if validation["status"] == "ok" else "failed",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except Exception as exc:  # noqa: BLE001 - CLI should return structured failure.
        result = {"status": "failed", "error": str(exc)}
        print(json.dumps(result, indent=2, sort_keys=True), file=sys.stdout)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True), file=sys.stdout)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
