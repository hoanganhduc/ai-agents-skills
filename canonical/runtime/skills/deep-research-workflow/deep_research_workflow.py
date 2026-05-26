#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any


SOURCE_ID_RE = re.compile(r"^S[1-9][0-9]*$")
CLAIM_ID_RE = re.compile(r"^C[1-9][0-9]*$")
GUARD_ID_RE = re.compile(r"^G[1-9][0-9]*$")

SOURCE_TYPES = {
    "paper",
    "preprint",
    "manuscript",
    "book",
    "web",
    "dataset",
    "documentation",
    "database",
    "software",
    "other",
}
PAPER_LIKE_SOURCE_TYPES = {"paper", "preprint", "manuscript", "book"}
LIBRARY_STATUSES = {"[IN_LIBRARY]", "[NOT_IN_LIBRARY]", "[NOT_A_PAPER]", "[UNVERIFIED]"}
CLAIM_STATUSES = {"supported", "provisional", "unsupported", "rejected"}
CONFIDENCE_VALUES = {"high", "medium", "low", "unknown"}
GUARDS = {"ScopeGuard", "EvidenceGuard", "VerifyGuard", "BudgetGuard", "RegressionGuard"}
GUARD_STATUSES = {"pass", "warn", "fail", "not-applicable"}
DELIVERY_DECISIONS = {"ready", "ready-with-caveats", "not-ready"}

SOURCE_REQUIRED = {"source_id", "source", "source_type", "library_status"}
SOURCE_FIELDS = SOURCE_REQUIRED | {
    "title",
    "authors",
    "year",
    "identifier",
    "access_date",
    "retrieval_method",
    "reliability_notes",
    "artifact_refs",
    "excluded",
    "exclusion_reason",
}
CLAIM_REQUIRED = {"claim_id", "claim", "source_ids", "evidence_ids", "status"}
CLAIM_FIELDS = CLAIM_REQUIRED | {"confidence", "gaps", "notes"}
GUARD_FIELDS = {
    "guard_output_id",
    "guard",
    "status",
    "claim_or_scope_ref",
    "source_ids",
    "evidence_ids",
    "inspected_artifacts",
    "gap",
    "blocking",
    "recommended_action",
}
DELIVERY_REQUIRED = {"decision", "report_ref", "checked_at", "guard_output_ids", "blockers", "gaps", "caveats"}
DELIVERY_FIELDS = DELIVERY_REQUIRED

TASK_SCHEMA_VERSION = "cross-agent-delegation.task.v1"
RESULT_SCHEMA_VERSION = "cross-agent-delegation.result.v1"
TASK_REQUIRED = {
    "schema_version",
    "packet_id",
    "recipient_profile",
    "intent",
    "requested_actions",
    "side_effects",
    "context_policy",
    "confirmation_requirement",
    "failure_policy",
}
RESULT_REQUIRED = {
    "schema_version",
    "result_id",
    "task_packet_id",
    "task_schema_version",
    "recipient_profile",
    "status",
    "findings",
    "evidence",
    "next_step",
}
FORBIDDEN_KEY_RE = re.compile(
    r"(^|[_-])(api[_-]?key|secret|token|password|credential|private[_-]?key|ssh[_-]?key)([_-]|$)",
    re.I,
)
FORBIDDEN_VALUE_RE = re.compile(
    r"\b(sk-[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|Bearer\s+[A-Za-z0-9._~+/=-]{16,})\b",
    re.I,
)
FORBIDDEN_PACKET_KEYS = {
    "command",
    "commands",
    "args",
    "cwd",
    "env",
    "provider_config",
    "model_config",
    "session_id",
    "resume_token",
    "resolved_model",
    "resolved_thinking",
    "budget_owner",
    "spent_tokens",
    "spent_usd",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_deep_research_workflow")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor")

    init_parser = sub.add_parser("init")
    init_parser.add_argument("--dir", default=".")
    init_parser.add_argument("--subdir", default="research")
    init_parser.add_argument("--force", action="store_true")
    init_parser.add_argument("--structured", action="store_true")

    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--dir", default=".")

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor()
    if args.command == "init":
        return init(args)
    if args.command == "validate":
        result = validate_directory(Path(args.dir))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "ok" else 1
    raise AssertionError(args.command)


def runtime_workspace() -> Path:
    env_workspace = os.environ.get("AAS_RUNTIME_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE")
    if env_workspace:
        return Path(env_workspace)
    script_dir = Path(__file__).resolve().parent
    return script_dir.parents[1]


def template_dir() -> Path:
    return runtime_workspace() / "templates"


def template_paths() -> dict[str, Path]:
    templates = template_dir()
    return {
        "sources": templates / "deep-research-sources.md",
        "analysis": templates / "deep-research-analysis.md",
        "report": templates / "deep-research-report.md",
    }


def doctor() -> int:
    missing = 0
    for path in template_paths().values():
        if path.is_file():
            print(f"OK\t{path}")
        else:
            print(f"MISSING\t{path}", file=sys.stderr)
            missing = 1
    return missing


def init(args: argparse.Namespace) -> int:
    target_dir = Path(args.dir) / args.subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    written = [
        copy_file(template_paths()["sources"], target_dir / "sources.md", force=args.force),
        copy_file(template_paths()["analysis"], target_dir / "analysis.md", force=args.force),
        copy_file(template_paths()["report"], target_dir / "report.md", force=args.force),
    ]
    if args.structured:
        written.extend(write_structured_files(target_dir, force=args.force))
    for path in written:
        print(f"WROTE\t{path}")
    return 0


def copy_file(source: Path, target: Path, *, force: bool) -> Path:
    if not source.is_file():
        raise SystemExit(f"missing template: {source}")
    if target.exists() and not force:
        raise SystemExit(f"refusing to overwrite existing file without --force: {target}")
    shutil.copyfile(source, target)
    return target


def write_structured_files(target_dir: Path, *, force: bool) -> list[Path]:
    delivery = {
        "decision": "not-ready",
        "report_ref": "",
        "checked_at": "",
        "guard_output_ids": [],
        "blockers": [
            {
                "blocker_id": "B1",
                "description": "research artifacts are scaffolded but not completed",
                "required_action": "complete sources, claims, guards, and delivery checks",
            }
        ],
        "gaps": ["research artifacts are scaffolded but not completed"],
        "caveats": [],
    }
    written = [
        write_text(target_dir / "sources.jsonl", "", force=force),
        write_text(target_dir / "claims.jsonl", "", force=force),
        write_text(target_dir / "guards.jsonl", "", force=force),
        write_text(target_dir / "delivery.json", json.dumps(delivery, indent=2, sort_keys=True) + "\n", force=force),
    ]
    delegation_dir = target_dir / "delegation"
    delegation_dir.mkdir(exist_ok=True)
    written.append(delegation_dir)
    return written


def write_text(path: Path, text: str, *, force: bool) -> Path:
    if path.exists() and not force:
        raise SystemExit(f"refusing to overwrite existing file without --force: {path}")
    path.write_text(text, encoding="utf-8")
    return path


def validate_directory(root: Path) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    sources = read_jsonl(root / "sources.jsonl", "sources", errors)
    claims = read_jsonl(root / "claims.jsonl", "claims", errors)
    guards = read_jsonl(root / "guards.jsonl", "guards", errors)
    delivery = read_json(root / "delivery.json", "delivery", errors)

    source_ids = validate_sources(sources, root / "sources.jsonl", errors)
    validate_claims(claims, root / "claims.jsonl", source_ids, errors)
    guard_ids, blocking_guard_ids = validate_guards(guards, root / "guards.jsonl", source_ids, errors)
    if isinstance(delivery, dict):
        validate_delivery(delivery, root / "delivery.json", guard_ids, blocking_guard_ids, errors, warnings)

    delegation_results = validate_delegation_dir(root / "delegation", errors)
    checked = {
        "sources": len(sources),
        "claims": len(claims),
        "guards": len(guards),
        "delivery": 1 if isinstance(delivery, dict) else 0,
        "delegation_packets": len(delegation_results),
    }
    return {
        "status": "ok" if not errors else "failed",
        "errors": errors,
        "warnings": warnings,
        "checked": checked,
    }


def read_jsonl(path: Path, artifact: str, errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not path.is_file():
        add_error(errors, "MISSING_FILE", path, f"missing {artifact} file")
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            add_error(errors, "JSONL_INVALID", path, f"line {line_no}: {exc}")
            continue
        if not isinstance(value, dict):
            add_error(errors, "JSONL_RECORD_NOT_OBJECT", path, f"line {line_no}: record must be an object")
            continue
        value["_line"] = line_no
        rows.append(value)
    return rows


def read_json(path: Path, artifact: str, errors: list[dict[str, Any]]) -> Any:
    if not path.is_file():
        add_error(errors, "MISSING_FILE", path, f"missing {artifact} file")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        add_error(errors, "JSON_INVALID", path, str(exc))
        return None
    if not isinstance(value, dict):
        add_error(errors, "JSON_NOT_OBJECT", path, f"{artifact} must be an object")
        return None
    return value


def validate_sources(rows: list[dict[str, Any]], path: Path, errors: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for row in rows:
        line = row.get("_line")
        unknown = set(row) - SOURCE_FIELDS - {"_line"}
        if unknown:
            add_error(errors, "UNKNOWN_FIELD", path, f"line {line}: unknown source fields: {sorted(unknown)}")
        missing = SOURCE_REQUIRED - set(row)
        if missing:
            add_error(errors, "MISSING_REQUIRED_FIELD", path, f"line {line}: missing source fields: {sorted(missing)}")
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not SOURCE_ID_RE.match(source_id):
            add_error(errors, "SOURCE_ID_INVALID", path, f"line {line}: source_id must look like S1")
            continue
        if source_id in ids:
            add_error(errors, "DUPLICATE_ID", path, f"line {line}: duplicate source_id {source_id}")
        ids.add(source_id)
        source_type = row.get("source_type")
        if source_type not in SOURCE_TYPES:
            add_error(errors, "SOURCE_TYPE_INVALID", path, f"line {line}: unsupported source_type {source_type!r}")
        library_status = row.get("library_status")
        if library_status not in LIBRARY_STATUSES:
            add_error(errors, "LIBRARY_STATUS_INVALID", path, f"line {line}: unsupported library_status {library_status!r}")
        if source_type in PAPER_LIKE_SOURCE_TYPES and library_status == "[NOT_A_PAPER]":
            add_error(errors, "PAPER_SOURCE_NOT_A_PAPER", path, f"line {line}: paper-like source must not use [NOT_A_PAPER]")
        if not is_nonempty_string(row.get("source")):
            add_error(errors, "SOURCE_VALUE_EMPTY", path, f"line {line}: source must be non-empty")
    return ids


def validate_claims(
    rows: list[dict[str, Any]],
    path: Path,
    source_ids: set[str],
    errors: list[dict[str, Any]],
) -> set[str]:
    ids: set[str] = set()
    for row in rows:
        line = row.get("_line")
        unknown = set(row) - CLAIM_FIELDS - {"_line"}
        if unknown:
            add_error(errors, "UNKNOWN_FIELD", path, f"line {line}: unknown claim fields: {sorted(unknown)}")
        missing = CLAIM_REQUIRED - set(row)
        if missing:
            add_error(errors, "MISSING_REQUIRED_FIELD", path, f"line {line}: missing claim fields: {sorted(missing)}")
        claim_id = row.get("claim_id")
        if not isinstance(claim_id, str) or not CLAIM_ID_RE.match(claim_id):
            add_error(errors, "CLAIM_ID_INVALID", path, f"line {line}: claim_id must look like C1")
            continue
        if claim_id in ids:
            add_error(errors, "DUPLICATE_ID", path, f"line {line}: duplicate claim_id {claim_id}")
        ids.add(claim_id)
        if row.get("status") not in CLAIM_STATUSES:
            add_error(errors, "CLAIM_STATUS_INVALID", path, f"line {line}: unsupported claim status {row.get('status')!r}")
        if "confidence" in row and row.get("confidence") not in CONFIDENCE_VALUES:
            add_error(errors, "CLAIM_CONFIDENCE_INVALID", path, f"line {line}: unsupported confidence {row.get('confidence')!r}")
        row_source_ids = require_string_list(row.get("source_ids"), errors, path, line, "source_ids")
        evidence_ids = require_string_list(row.get("evidence_ids"), errors, path, line, "evidence_ids")
        if not row_source_ids and not evidence_ids:
            add_error(errors, "EMPTY_EVIDENCE_LINK", path, f"line {line}: claim must cite source_ids or evidence_ids")
        for source_id in row_source_ids:
            if source_id not in source_ids:
                add_error(errors, "UNKNOWN_SOURCE_ID", path, f"line {line}: unknown source_id {source_id}")
        if not is_nonempty_string(row.get("claim")):
            add_error(errors, "CLAIM_VALUE_EMPTY", path, f"line {line}: claim must be non-empty")
    return ids


def validate_guards(
    rows: list[dict[str, Any]],
    path: Path,
    source_ids: set[str],
    errors: list[dict[str, Any]],
) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    blocking: set[str] = set()
    for row in rows:
        line = row.get("_line")
        unknown = set(row) - GUARD_FIELDS - {"_line"}
        if unknown:
            add_error(errors, "UNKNOWN_FIELD", path, f"line {line}: unknown guard fields: {sorted(unknown)}")
        missing = GUARD_FIELDS - set(row)
        if missing:
            add_error(errors, "MISSING_REQUIRED_FIELD", path, f"line {line}: missing guard fields: {sorted(missing)}")
        guard_id = row.get("guard_output_id")
        if not isinstance(guard_id, str) or not GUARD_ID_RE.match(guard_id):
            add_error(errors, "GUARD_ID_INVALID", path, f"line {line}: guard_output_id must look like G1")
            continue
        if guard_id in ids:
            add_error(errors, "DUPLICATE_ID", path, f"line {line}: duplicate guard_output_id {guard_id}")
        ids.add(guard_id)
        if row.get("guard") not in GUARDS:
            add_error(errors, "GUARD_TYPE_INVALID", path, f"line {line}: unsupported guard {row.get('guard')!r}")
        status = row.get("status")
        if status not in GUARD_STATUSES:
            add_error(errors, "GUARD_STATUS_INVALID", path, f"line {line}: unsupported guard status {status!r}")
        guard_source_ids = require_string_list(row.get("source_ids"), errors, path, line, "source_ids")
        evidence_ids = require_string_list(row.get("evidence_ids"), errors, path, line, "evidence_ids")
        require_string_list(row.get("inspected_artifacts"), errors, path, line, "inspected_artifacts")
        if status in {"pass", "warn"} and not guard_source_ids and not evidence_ids:
            add_error(errors, "GUARD_WITHOUT_EVIDENCE", path, f"line {line}: pass/warn guard must cite source or evidence IDs")
        if status in {"fail", "not-applicable"} and not is_nonempty_string(row.get("gap")):
            add_error(errors, "GUARD_GAP_REQUIRED", path, f"line {line}: fail/not-applicable guard requires gap")
        for source_id in guard_source_ids:
            if source_id not in source_ids:
                add_error(errors, "UNKNOWN_SOURCE_ID", path, f"line {line}: unknown source_id {source_id}")
        if row.get("blocking") is True:
            blocking.add(guard_id)
        elif row.get("blocking") is not False:
            add_error(errors, "GUARD_BLOCKING_INVALID", path, f"line {line}: blocking must be boolean")
    return ids, blocking


def validate_delivery(
    delivery: dict[str, Any],
    path: Path,
    guard_ids: set[str],
    blocking_guard_ids: set[str],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    unknown = set(delivery) - DELIVERY_FIELDS
    if unknown:
        add_error(errors, "UNKNOWN_FIELD", path, f"unknown delivery fields: {sorted(unknown)}")
    missing = DELIVERY_REQUIRED - set(delivery)
    if missing:
        add_error(errors, "MISSING_REQUIRED_FIELD", path, f"missing delivery fields: {sorted(missing)}")
    decision = delivery.get("decision")
    if decision not in DELIVERY_DECISIONS:
        add_error(errors, "DELIVERY_DECISION_INVALID", path, f"unsupported decision {decision!r}")
    delivery_guard_ids = require_string_list(delivery.get("guard_output_ids"), errors, path, None, "guard_output_ids")
    blockers = require_list(delivery.get("blockers"), errors, path, None, "blockers")
    gaps = require_string_list(delivery.get("gaps"), errors, path, None, "gaps")
    caveats = require_string_list(delivery.get("caveats"), errors, path, None, "caveats")
    for guard_id in delivery_guard_ids:
        if guard_id not in guard_ids:
            add_error(errors, "UNKNOWN_GUARD_ID", path, f"delivery references unknown guard_output_id {guard_id}")
    if blocking_guard_ids and decision == "ready":
        add_error(errors, "BLOCKING_READY_DECISION", path, "ready delivery is invalid while blocking guard gaps remain")
    if blocking_guard_ids and decision == "ready-with-caveats" and not caveats and not gaps:
        add_error(errors, "BLOCKING_CAVEAT_REQUIRED", path, "blocking guard gaps require caveats or gaps")
    if decision == "ready" and (blockers or gaps):
        add_error(errors, "READY_WITH_GAPS", path, "ready delivery must not carry blockers or gaps")
    if decision == "not-ready" and not blockers and not gaps:
        warnings.append({"code": "NOT_READY_WITHOUT_GAPS", "path": str(path), "message": "not-ready delivery should list blockers or gaps"})


def validate_delegation_dir(path: Path, errors: list[dict[str, Any]]) -> list[Path]:
    if not path.exists():
        return []
    if not path.is_dir():
        add_error(errors, "DELEGATION_PATH_NOT_DIR", path, "delegation path must be a directory")
        return []
    packet_paths = sorted(item for item in path.glob("*.json") if item.is_file())
    for packet_path in packet_paths:
        packet = read_json(packet_path, "delegation packet", errors)
        if isinstance(packet, dict):
            validate_delegation_packet(packet, packet_path, errors)
    return packet_paths


def validate_delegation_packet(packet: dict[str, Any], path: Path, errors: list[dict[str, Any]]) -> None:
    schema_version = packet.get("schema_version")
    if schema_version == TASK_SCHEMA_VERSION:
        required = TASK_REQUIRED
    elif schema_version == RESULT_SCHEMA_VERSION:
        required = RESULT_REQUIRED
    else:
        add_error(errors, "DELEGATION_PACKET_SCHEMA_INVALID", path, "unsupported delegation packet schema_version")
        return
    missing = required - set(packet)
    if missing:
        add_error(errors, "MISSING_REQUIRED_FIELD", path, f"delegation packet missing fields: {sorted(missing)}")
    forbidden = recursive_forbidden_packet_material(packet)
    for code in sorted(set(forbidden)):
        add_error(errors, code, path, "delegation packet contains forbidden authority, runtime, model, or secret material")


def recursive_forbidden_packet_material(value: Any) -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_PACKET_KEYS:
                errors.append("FORBIDDEN_DELEGATION_FIELD")
            if FORBIDDEN_KEY_RE.search(key):
                errors.append("SECRET_MATERIAL")
            errors.extend(recursive_forbidden_packet_material(item))
    elif isinstance(value, list):
        for item in value:
            errors.extend(recursive_forbidden_packet_material(item))
    elif isinstance(value, str) and FORBIDDEN_VALUE_RE.search(value):
        errors.append("SECRET_MATERIAL")
    return errors


def require_string_list(
    value: Any,
    errors: list[dict[str, Any]],
    path: Path,
    line: int | None,
    field: str,
) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        location = f"line {line}: " if line is not None else ""
        add_error(errors, "FIELD_NOT_STRING_LIST", path, f"{location}{field} must be a list of strings")
        return []
    return value


def require_list(value: Any, errors: list[dict[str, Any]], path: Path, line: int | None, field: str) -> list[Any]:
    if not isinstance(value, list):
        location = f"line {line}: " if line is not None else ""
        add_error(errors, "FIELD_NOT_LIST", path, f"{location}{field} must be a list")
        return []
    return value


def is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def add_error(errors: list[dict[str, Any]], code: str, path: Path, message: str) -> None:
    errors.append({"code": code, "path": str(path), "message": message})


if __name__ == "__main__":
    raise SystemExit(main())
