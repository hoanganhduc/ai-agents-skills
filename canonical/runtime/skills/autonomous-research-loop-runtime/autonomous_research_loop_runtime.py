#!/usr/bin/env python3
"""Offline ledger helper for autonomous research loops."""

from __future__ import annotations

import argparse
import json
import os
import re
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


def pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
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


def hook_check_command(args: argparse.Namespace) -> dict[str, Any]:
    try:
        if os.environ.get("AUTOLOOP_DISABLE"):
            return {"status": "ok", "action": "hook-check", "block": False, "reason": "disabled_env"}
        reg = registry_dir(args)
        gc_registry(reg)
        root = Path(args.root).expanduser().resolve() if args.root else Path.cwd().resolve()
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

    arm = subparsers.add_parser("arm", help="register a loop as active (force-management)")
    arm.add_argument("--dir", required=True)
    arm.add_argument("--root", default=None, help="project root this loop governs (default: loop dir)")
    arm.add_argument("--pid", type=int, default=0, help="long-lived loop/driver pid for liveness (0 = heartbeat-only)")
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
    hook.add_argument("--root", default=None, help="current session project root (default: cwd)")
    add_registry_args(hook)
    hook.set_defaults(func=hook_check_command)
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
    print(json.dumps(result, indent=2, sort_keys=True), file=sys.stdout)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
