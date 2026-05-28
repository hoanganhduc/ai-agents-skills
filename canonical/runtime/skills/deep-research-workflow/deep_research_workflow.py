#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any


SOURCE_ID_RE = re.compile(r"^S[1-9][0-9]*$")
CLAIM_ID_RE = re.compile(r"^C[1-9][0-9]*$")
GUARD_ID_RE = re.compile(r"^G[1-9][0-9]*$")
EVIDENCE_ID_RE = re.compile(r"^E[1-9][0-9]*$|^E-[A-Za-z0-9][A-Za-z0-9._-]*$")
FORMAL_TARGET_ID_RE = re.compile(r"^FT[1-9][0-9]*$")
STATEMENT_REVIEW_ID_RE = re.compile(r"^SER[1-9][0-9]*$")
URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")

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
SCHEMA_VERSION = "deep-research.run.v2"
EVIDENCE_SCHEMA_VERSION = "deep-research.evidence.v2"
FORMAL_TARGET_SCHEMA_VERSION = "deep-research.formal-target.v1"
STATEMENT_REVIEW_SCHEMA_VERSION = "deep-research.statement-equivalence-review.v1"
EVIDENCE_TYPES = {
    "source_note",
    "formal_statement",
    "formal_check",
    "axle_remote_check",
    "computation",
    "guard",
    "consent",
    "agd_result",
    "report",
    "other",
}
INSPECTION_STATUSES = {"unchecked", "checked", "failed", "not_applicable"}
REDACTION_STATUSES = {"safe", "redacted", "private", "not_reviewed"}
SENSITIVITY_CLASSES = {"public", "private", "unpublished", "unknown"}
COMPUTATION_RESULT_STATUSES = {"passed", "failed", "partial", "timeout", "unavailable"}
AXLE_REMOTE_RESULT_STATUSES = {"passed", "failed", "partial", "timeout", "unavailable", "expired"}
COMPUTATION_COVERAGE_STATUSES = {
    "exhaustive",
    "bounded_complete",
    "bounded_only",
    "partial",
    "sampled",
    "heuristic",
    "timed_out",
    "failed",
    "unavailable",
}
AGD_VALIDATION_STATUSES = {"parent_validated", "invalid", "pending"}
FORMAL_ARTIFACT_STAGES = {"intake", "stub", "candidate_solution", "final_candidate", "archived"}
LEAN_CHECK_STATUSES = {"not_run", "typecheck_failed", "typechecked", "command_failed", "tool_unavailable"}
PLACEHOLDER_STATUSES = {
    "not_scanned",
    "active_placeholders_found",
    "no_active_placeholders",
    "placeholders_allowed_for_stub",
}
TRUST_BASE_STATUSES = {"not_scanned", "accepted_trust_base", "unsanctioned_axiom_or_unsafe", "unknown"}
STATEMENT_RELATION_STATUSES = {
    "not_reviewed",
    "equivalent_reviewed",
    "weaker_than_claim",
    "stronger_than_claim",
    "semantic_gap",
    "not_applicable",
}
FORMAL_REVIEW_STATUSES = {"not_reviewed", "reviewed_by_lead", "reviewed_by_human", "review_rejected"}
CLAIM_SUPPORT_STATUSES = {
    "no_support",
    "supports_formal_statement_only",
    "supports_claim_after_equivalence_review",
    "blocked",
}
FORMAL_CHECK_REQUIREMENTS = {"not_requested", "optional", "explicitly_requested", "required_for_delivery"}
PROMOTION_REVIEW_STATUSES = {"reviewed_by_lead", "reviewed_by_human"}
PROMOTED_SUPPORT = "supports_claim_after_equivalence_review"
BLOCKING_LEAN_CHECK_STATUSES = {"not_run", "tool_unavailable", "command_failed", "typecheck_failed"}
WEAK_COMPUTATION_COVERAGE = {"bounded_only", "partial", "sampled", "heuristic", "timed_out", "failed", "unavailable"}

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
EVIDENCE_REQUIRED = {
    "schema_version",
    "evidence_id",
    "evidence_type",
    "source_ids",
    "claim_ids",
    "artifact_ref",
    "summary",
    "inspection_status",
    "redaction_status",
    "sensitivity_class",
    "created_at",
    "limitations",
}
EVIDENCE_FIELDS = EVIDENCE_REQUIRED | {
    "tool_name",
    "tool_version",
    "input_encoding_ref",
    "checked_domain",
    "graph_model_assumptions",
    "random_seed",
    "resource_bounds",
    "result_status",
    "coverage_status",
    "enumeration_method",
    "exhaustiveness_argument",
    "timeout_status",
    "verification_source",
    "agd_participant_id",
    "agd_round",
    "agd_packet_ref",
    "validation_status",
    "parent_validation_owner",
    "consent_source",
    "consent_run_id",
    "endpoint",
    "operation",
    "payload_hash",
    "expiry",
}
FORMAL_TARGET_REQUIRED = {
    "schema_version",
    "formal_target_id",
    "claim_ids",
    "source_ids",
    "informal_statement_ref",
    "lean_statement_ref",
    "artifact_stage",
    "lean_check_status",
    "placeholder_status",
    "trust_base_status",
    "statement_relation_status",
    "review_status",
    "claim_support_status",
    "formal_check_requirement",
    "toolchain",
    "mathlib",
    "verification_evidence_ids",
    "statement_equivalence_review_ids",
}
FORMAL_TARGET_FIELDS = FORMAL_TARGET_REQUIRED
STATEMENT_REVIEW_REQUIRED = {
    "schema_version",
    "statement_equivalence_review_id",
    "formal_target_id",
    "reviewer",
    "review_status",
    "relation_status",
    "informal_statement_ref",
    "lean_statement_ref",
    "compared_definitions",
    "hypothesis_deltas",
    "quantifier_deltas",
    "conclusion_deltas",
    "boundary_cases",
    "limitations",
    "encoding_assumptions",
}
STATEMENT_REVIEW_FIELDS = STATEMENT_REVIEW_REQUIRED

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
    init_parser.add_argument("--schema-version", choices=["1", "2"], default="1")
    init_parser.add_argument("--formal", action="store_true")

    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--dir", default=".")
    validate_parser.add_argument("--schema-version", choices=["1", "2"], default=None)

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor()
    if args.command == "init":
        return init(args)
    if args.command == "validate":
        result = validate_directory(Path(args.dir), schema_version=args.schema_version)
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
    if args.formal and not args.structured:
        raise SystemExit("--formal requires --structured")
    if args.formal and args.schema_version != "2":
        raise SystemExit("--formal requires --schema-version 2")
    if args.structured:
        written.extend(write_structured_files(
            target_dir,
            force=args.force,
            schema_version=args.schema_version,
            formal=args.formal,
        ))
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


def write_structured_files(target_dir: Path, *, force: bool, schema_version: str = "1", formal: bool = False) -> list[Path]:
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
    if schema_version == "2":
        schema = {"schema_version": SCHEMA_VERSION}
        written.append(write_text(target_dir / "research_schema.json", json.dumps(schema, indent=2, sort_keys=True) + "\n", force=force))
        written.append(write_text(target_dir / "evidence.jsonl", "", force=force))
    if formal:
        formal_dir = target_dir / "formal"
        for name in ("input", "output", "final", "artifacts/remote/axle"):
            directory = formal_dir / name
            directory.mkdir(parents=True, exist_ok=True)
            written.append(directory)
        written.extend([
            write_text(formal_dir / "formal_targets.jsonl", "", force=force),
            write_text(formal_dir / "statement_equivalence_reviews.jsonl", "", force=force),
            write_text(formal_dir / "placeholder_scan.json", "{}\n", force=force),
            write_text(formal_dir / "trust_base_scan.json", "{}\n", force=force),
            write_text(formal_dir / "verification.json", "{}\n", force=force),
            write_text(
                formal_dir / "README.md",
                "# Formal Lane Summary\n\n"
                "- requested: true\n"
                "- status: not-started\n"
                "- claim_support: none\n"
                "- delivery_blocked_by_formal_lane: false\n"
                "- axle_remote_check: supplemental only; cannot promote formal support without local formal_check evidence\n"
                "- recommended_next_action: add formal targets or mark the lane deferred/not applicable\n",
                force=force,
            ),
        ])
    return written


def write_text(path: Path, text: str, *, force: bool) -> Path:
    if path.exists() and not force:
        raise SystemExit(f"refusing to overwrite existing file without --force: {path}")
    path.write_text(text, encoding="utf-8")
    return path


def validate_directory(root: Path, schema_version: str | None = None) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    sources = read_jsonl(root / "sources.jsonl", "sources", errors)
    claims = read_jsonl(root / "claims.jsonl", "claims", errors)
    guards = read_jsonl(root / "guards.jsonl", "guards", errors)
    delivery = read_json(root / "delivery.json", "delivery", errors)

    v2_mode = schema_version == "2" or detects_v2_mode(root)
    formal_mode = detects_formal_mode(root)

    source_map = validate_sources(sources, root / "sources.jsonl", errors)
    claim_map = validate_claims(claims, root / "claims.jsonl", set(source_map), errors)
    evidence_map: dict[str, dict[str, Any]] = {}
    formal_targets: dict[str, dict[str, Any]] = {}
    if v2_mode:
        evidence = read_jsonl(root / "evidence.jsonl", "evidence", errors)
        evidence_map = validate_evidence(evidence, root / "evidence.jsonl", source_map, set(claim_map), root, errors)
        validate_claim_evidence_links(claims, root / "claims.jsonl", evidence_map, errors)
    guard_ids, blocking_guard_ids = validate_guards(
        guards,
        root / "guards.jsonl",
        set(source_map),
        errors,
        evidence_map=evidence_map if v2_mode else None,
    )
    if formal_mode:
        reviews = read_jsonl(root / "formal" / "statement_equivalence_reviews.jsonl", "statement equivalence reviews", errors)
        review_map = validate_statement_reviews(
            reviews,
            root / "formal" / "statement_equivalence_reviews.jsonl",
            source_map,
            set(claim_map),
            root,
            errors,
        )
        targets = read_jsonl(root / "formal" / "formal_targets.jsonl", "formal targets", errors)
        formal_targets = validate_formal_targets(
            targets,
            root / "formal" / "formal_targets.jsonl",
            source_map,
            set(claim_map),
            evidence_map,
            review_map,
            root,
            errors,
        )
        if not v2_mode:
            add_error(errors, "FORMAL_REQUIRES_V2", root / "formal", "formal lane artifacts require v2 structured validation")
    if isinstance(delivery, dict):
        validate_delivery(
            delivery,
            root / "delivery.json",
            guard_ids,
            blocking_guard_ids,
            errors,
            warnings,
            root=root,
            source_map=source_map,
            claims=claim_map,
            evidence_map=evidence_map,
            formal_targets=formal_targets,
            v2_mode=v2_mode,
        )

    delegation_results = validate_delegation_dir(root / "delegation", errors)
    checked = {
        "sources": len(sources),
        "claims": len(claims),
        "guards": len(guards),
        "delivery": 1 if isinstance(delivery, dict) else 0,
        "delegation_packets": len(delegation_results),
        "schema_version": 2 if v2_mode else 1,
        "evidence": len(evidence_map),
        "formal_targets": len(formal_targets),
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


def detects_v2_mode(root: Path) -> bool:
    if (root / "research_schema.json").is_file():
        return True
    evidence = root / "evidence.jsonl"
    if evidence.is_file() and evidence.read_text(encoding="utf-8").strip():
        return True
    formal_targets = root / "formal" / "formal_targets.jsonl"
    if formal_targets.is_file() and formal_targets.read_text(encoding="utf-8").strip():
        return True
    return detects_formal_mode(root)


def detects_formal_mode(root: Path) -> bool:
    formal = root / "formal"
    if not formal.exists():
        return False
    if not formal.is_dir():
        return True
    return any(item.is_file() or item.is_dir() for item in formal.iterdir())


def validate_sources(rows: list[dict[str, Any]], path: Path, errors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ids: dict[str, dict[str, Any]] = {}
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
        ids[source_id] = row
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
) -> dict[str, dict[str, Any]]:
    ids: dict[str, dict[str, Any]] = {}
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
        ids[claim_id] = row
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


def validate_claim_evidence_links(
    rows: list[dict[str, Any]],
    path: Path,
    evidence_map: dict[str, dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    for row in rows:
        line = row.get("_line")
        for evidence_id in require_string_list(row.get("evidence_ids"), errors, path, line, "evidence_ids"):
            if evidence_id not in evidence_map:
                add_error(errors, "UNKNOWN_EVIDENCE_ID", path, f"line {line}: unknown evidence_id {evidence_id}")


def validate_evidence(
    rows: list[dict[str, Any]],
    path: Path,
    source_map: dict[str, dict[str, Any]],
    claim_ids: set[str],
    root: Path,
    errors: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    ids: dict[str, dict[str, Any]] = {}
    for row in rows:
        line = row.get("_line")
        unknown = set(row) - EVIDENCE_FIELDS - {"_line"}
        if unknown:
            add_error(errors, "UNKNOWN_FIELD", path, f"line {line}: unknown evidence fields: {sorted(unknown)}")
        missing = EVIDENCE_REQUIRED - set(row)
        if missing:
            add_error(errors, "MISSING_REQUIRED_FIELD", path, f"line {line}: missing evidence fields: {sorted(missing)}")
        if row.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
            add_error(errors, "EVIDENCE_SCHEMA_VERSION_INVALID", path, f"line {line}: unsupported evidence schema_version {row.get('schema_version')!r}")
        evidence_id = row.get("evidence_id")
        if not isinstance(evidence_id, str) or not EVIDENCE_ID_RE.match(evidence_id):
            add_error(errors, "EVIDENCE_ID_INVALID", path, f"line {line}: invalid evidence_id")
            continue
        if evidence_id in ids:
            add_error(errors, "DUPLICATE_ID", path, f"line {line}: duplicate evidence_id {evidence_id}")
        ids[evidence_id] = row
        evidence_type = row.get("evidence_type")
        if evidence_type not in EVIDENCE_TYPES:
            add_error(errors, "EVIDENCE_TYPE_INVALID", path, f"line {line}: unsupported evidence_type {evidence_type!r}")
        source_ids = require_string_list(row.get("source_ids"), errors, path, line, "source_ids")
        for source_id in source_ids:
            if source_id not in source_map:
                add_error(errors, "UNKNOWN_SOURCE_ID", path, f"line {line}: unknown source_id {source_id}")
        row_claim_ids = require_string_list(row.get("claim_ids"), errors, path, line, "claim_ids")
        for claim_id in row_claim_ids:
            if claim_id not in claim_ids:
                add_error(errors, "UNKNOWN_CLAIM_ID", path, f"line {line}: unknown claim_id {claim_id}")
        validate_artifact_ref(row.get("artifact_ref"), root, path, errors, line, "artifact_ref")
        if not is_nonempty_string(row.get("summary")):
            add_error(errors, "EVIDENCE_SUMMARY_EMPTY", path, f"line {line}: summary must be non-empty")
        if row.get("inspection_status") not in INSPECTION_STATUSES:
            add_error(errors, "INSPECTION_STATUS_INVALID", path, f"line {line}: unsupported inspection_status {row.get('inspection_status')!r}")
        if row.get("redaction_status") not in REDACTION_STATUSES:
            add_error(errors, "REDACTION_STATUS_INVALID", path, f"line {line}: unsupported redaction_status {row.get('redaction_status')!r}")
        if row.get("sensitivity_class") not in SENSITIVITY_CLASSES:
            add_error(errors, "SENSITIVITY_CLASS_INVALID", path, f"line {line}: unsupported sensitivity_class {row.get('sensitivity_class')!r}")
        validate_timestamp(row.get("created_at"), path, errors, line, "created_at")
        require_string_list(row.get("limitations"), errors, path, line, "limitations")
        if evidence_type == "computation":
            validate_computation_evidence(row, path, errors, line)
        if evidence_type == "axle_remote_check":
            validate_axle_remote_evidence(row, path, errors, line)
        if evidence_type == "consent":
            validate_consent_evidence(row, path, errors, line)
        if isinstance(evidence_id, str) and evidence_id.startswith("E-AGD-"):
            validate_agd_evidence(row, path, errors, line)
    return ids


def validate_computation_evidence(
    row: dict[str, Any],
    path: Path,
    errors: list[dict[str, Any]],
    line: int | None,
) -> None:
    for field in (
        "tool_name",
        "tool_version",
        "input_encoding_ref",
        "checked_domain",
        "graph_model_assumptions",
        "resource_bounds",
        "result_status",
        "coverage_status",
        "enumeration_method",
        "timeout_status",
    ):
        if not is_nonempty_string(row.get(field)):
            add_error(errors, "COMPUTATION_EVIDENCE_FIELD_REQUIRED", path, f"line {line}: computation evidence requires {field}")
    if row.get("result_status") not in COMPUTATION_RESULT_STATUSES:
        add_error(errors, "COMPUTATION_RESULT_STATUS_INVALID", path, f"line {line}: invalid computation result_status")
    if row.get("coverage_status") not in COMPUTATION_COVERAGE_STATUSES:
        add_error(errors, "COMPUTATION_COVERAGE_STATUS_INVALID", path, f"line {line}: invalid computation coverage_status")
    if row.get("coverage_status") in {"exhaustive", "bounded_complete"} and not is_nonempty_string(row.get("exhaustiveness_argument")):
        add_error(errors, "COMPUTATION_EXHAUSTIVENESS_REQUIRED", path, f"line {line}: exhaustive computation evidence requires exhaustiveness_argument")


def validate_axle_remote_evidence(
    row: dict[str, Any],
    path: Path,
    errors: list[dict[str, Any]],
    line: int | None,
) -> None:
    for field in (
        "tool_name",
        "tool_version",
        "endpoint",
        "operation",
        "payload_hash",
        "input_encoding_ref",
        "result_status",
        "expiry",
    ):
        if not is_nonempty_string(row.get(field)):
            add_error(errors, "AXLE_REMOTE_EVIDENCE_FIELD_REQUIRED", path, f"line {line}: AXLE evidence requires {field}")
    if row.get("tool_name") != "axiom-axle-mcp":
        add_error(errors, "AXLE_REMOTE_TOOL_INVALID", path, f"line {line}: AXLE evidence tool_name must be 'axiom-axle-mcp'")
    if row.get("result_status") not in AXLE_REMOTE_RESULT_STATUSES:
        add_error(errors, "AXLE_REMOTE_RESULT_STATUS_INVALID", path, f"line {line}: invalid AXLE result_status")
    validate_timestamp(row.get("expiry"), path, errors, line, "expiry")


def validate_consent_evidence(
    row: dict[str, Any],
    path: Path,
    errors: list[dict[str, Any]],
    line: int | None,
) -> None:
    required = {
        "consent_source": "parent_user_confirmation",
        "endpoint": None,
        "operation": None,
        "payload_hash": None,
        "expiry": None,
        "parent_validation_owner": None,
    }
    for field, expected in required.items():
        if expected is not None:
            if row.get(field) != expected:
                add_error(errors, "CONSENT_SOURCE_INVALID", path, f"line {line}: consent {field} must be {expected!r}")
        elif not is_nonempty_string(row.get(field)):
            add_error(errors, "CONSENT_FIELD_REQUIRED", path, f"line {line}: consent evidence requires {field}")
    validate_timestamp(row.get("expiry"), path, errors, line, "expiry")


def validate_agd_evidence(
    row: dict[str, Any],
    path: Path,
    errors: list[dict[str, Any]],
    line: int | None,
) -> None:
    for field in ("agd_participant_id", "agd_round", "agd_packet_ref", "validation_status", "parent_validation_owner"):
        if not is_nonempty_string(row.get(field)):
            add_error(errors, "AGD_EVIDENCE_FIELD_REQUIRED", path, f"line {line}: AGD evidence requires {field}")
    if row.get("validation_status") not in AGD_VALIDATION_STATUSES:
        add_error(errors, "AGD_VALIDATION_STATUS_INVALID", path, f"line {line}: invalid AGD validation_status")
    if row.get("validation_status") != "parent_validated":
        add_error(errors, "AGD_EVIDENCE_NOT_PARENT_VALIDATED", path, f"line {line}: AGD evidence must be parent_validated before use")


def validate_guards(
    rows: list[dict[str, Any]],
    path: Path,
    source_ids: set[str],
    errors: list[dict[str, Any]],
    *,
    evidence_map: dict[str, dict[str, Any]] | None = None,
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
        if evidence_map is not None:
            for evidence_id in evidence_ids:
                if evidence_id not in evidence_map:
                    add_error(errors, "UNKNOWN_EVIDENCE_ID", path, f"line {line}: unknown evidence_id {evidence_id}")
        if row.get("blocking") is True:
            blocking.add(guard_id)
        elif row.get("blocking") is not False:
            add_error(errors, "GUARD_BLOCKING_INVALID", path, f"line {line}: blocking must be boolean")
    return ids, blocking


def validate_statement_reviews(
    rows: list[dict[str, Any]],
    path: Path,
    source_map: dict[str, dict[str, Any]],
    claim_ids: set[str],
    root: Path,
    errors: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    del source_map, claim_ids
    ids: dict[str, dict[str, Any]] = {}
    for row in rows:
        line = row.get("_line")
        unknown = set(row) - STATEMENT_REVIEW_FIELDS - {"_line"}
        if unknown:
            add_error(errors, "UNKNOWN_FIELD", path, f"line {line}: unknown statement-equivalence review fields: {sorted(unknown)}")
        missing = STATEMENT_REVIEW_REQUIRED - set(row)
        if missing:
            add_error(errors, "MISSING_REQUIRED_FIELD", path, f"line {line}: missing statement-equivalence review fields: {sorted(missing)}")
        if row.get("schema_version") != STATEMENT_REVIEW_SCHEMA_VERSION:
            add_error(errors, "STATEMENT_REVIEW_SCHEMA_VERSION_INVALID", path, f"line {line}: unsupported statement review schema_version")
        review_id = row.get("statement_equivalence_review_id")
        if not isinstance(review_id, str) or not STATEMENT_REVIEW_ID_RE.match(review_id):
            add_error(errors, "STATEMENT_REVIEW_ID_INVALID", path, f"line {line}: review id must look like SER1")
            continue
        if review_id in ids:
            add_error(errors, "DUPLICATE_ID", path, f"line {line}: duplicate statement_equivalence_review_id {review_id}")
        ids[review_id] = row
        formal_target_id = row.get("formal_target_id")
        if not isinstance(formal_target_id, str) or not FORMAL_TARGET_ID_RE.match(formal_target_id):
            add_error(errors, "FORMAL_TARGET_ID_INVALID", path, f"line {line}: formal_target_id must look like FT1")
        if row.get("review_status") not in PROMOTION_REVIEW_STATUSES | {"review_rejected"}:
            add_error(errors, "FORMAL_REVIEW_STATUS_INVALID", path, f"line {line}: invalid review_status")
        if row.get("relation_status") not in STATEMENT_RELATION_STATUSES:
            add_error(errors, "STATEMENT_RELATION_STATUS_INVALID", path, f"line {line}: invalid relation_status")
        validate_artifact_ref(row.get("informal_statement_ref"), root, path, errors, line, "informal_statement_ref")
        validate_artifact_ref(row.get("lean_statement_ref"), root, path, errors, line, "lean_statement_ref")
        for field in (
            "reviewer",
            "compared_definitions",
            "hypothesis_deltas",
            "quantifier_deltas",
            "conclusion_deltas",
            "boundary_cases",
            "limitations",
            "encoding_assumptions",
        ):
            if not is_nonempty_string(row.get(field)) and not isinstance(row.get(field), list):
                add_error(errors, "STATEMENT_REVIEW_FIELD_REQUIRED", path, f"line {line}: statement review requires {field}")
    return ids


def validate_formal_targets(
    rows: list[dict[str, Any]],
    path: Path,
    source_map: dict[str, dict[str, Any]],
    claim_ids: set[str],
    evidence_map: dict[str, dict[str, Any]],
    review_map: dict[str, dict[str, Any]],
    root: Path,
    errors: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    ids: dict[str, dict[str, Any]] = {}
    for row in rows:
        line = row.get("_line")
        unknown = set(row) - FORMAL_TARGET_FIELDS - {"_line"}
        if unknown:
            add_error(errors, "UNKNOWN_FIELD", path, f"line {line}: unknown formal target fields: {sorted(unknown)}")
        missing = FORMAL_TARGET_REQUIRED - set(row)
        if missing:
            add_error(errors, "MISSING_REQUIRED_FIELD", path, f"line {line}: missing formal target fields: {sorted(missing)}")
        if row.get("schema_version") != FORMAL_TARGET_SCHEMA_VERSION:
            add_error(errors, "FORMAL_TARGET_SCHEMA_VERSION_INVALID", path, f"line {line}: unsupported formal target schema_version")
        target_id = row.get("formal_target_id")
        if not isinstance(target_id, str) or not FORMAL_TARGET_ID_RE.match(target_id):
            add_error(errors, "FORMAL_TARGET_ID_INVALID", path, f"line {line}: formal_target_id must look like FT1")
            continue
        if target_id in ids:
            add_error(errors, "DUPLICATE_ID", path, f"line {line}: duplicate formal_target_id {target_id}")
        ids[target_id] = row
        for claim_id in require_string_list(row.get("claim_ids"), errors, path, line, "claim_ids"):
            if claim_id not in claim_ids:
                add_error(errors, "UNKNOWN_CLAIM_ID", path, f"line {line}: unknown claim_id {claim_id}")
        for source_id in require_string_list(row.get("source_ids"), errors, path, line, "source_ids"):
            if source_id not in source_map:
                add_error(errors, "UNKNOWN_SOURCE_ID", path, f"line {line}: unknown source_id {source_id}")
        validate_artifact_ref(row.get("informal_statement_ref"), root, path, errors, line, "informal_statement_ref")
        validate_artifact_ref(row.get("lean_statement_ref"), root, path, errors, line, "lean_statement_ref")
        validate_formal_target_enums(row, path, errors, line)
        for evidence_id in require_string_list(row.get("verification_evidence_ids"), errors, path, line, "verification_evidence_ids"):
            if evidence_id not in evidence_map:
                add_error(errors, "UNKNOWN_EVIDENCE_ID", path, f"line {line}: unknown evidence_id {evidence_id}")
        review_ids = require_string_list(row.get("statement_equivalence_review_ids"), errors, path, line, "statement_equivalence_review_ids")
        for review_id in review_ids:
            if review_id not in review_map:
                add_error(errors, "UNKNOWN_STATEMENT_REVIEW_ID", path, f"line {line}: unknown statement_equivalence_review_id {review_id}")
        validate_formal_target_state(row, review_ids, review_map, evidence_map, path, errors, line)
    return ids


def validate_formal_target_enums(
    row: dict[str, Any],
    path: Path,
    errors: list[dict[str, Any]],
    line: int | None,
) -> None:
    checks = (
        ("artifact_stage", FORMAL_ARTIFACT_STAGES, "FORMAL_ARTIFACT_STAGE_INVALID"),
        ("lean_check_status", LEAN_CHECK_STATUSES, "LEAN_CHECK_STATUS_INVALID"),
        ("placeholder_status", PLACEHOLDER_STATUSES, "PLACEHOLDER_STATUS_INVALID"),
        ("trust_base_status", TRUST_BASE_STATUSES, "TRUST_BASE_STATUS_INVALID"),
        ("statement_relation_status", STATEMENT_RELATION_STATUSES, "STATEMENT_RELATION_STATUS_INVALID"),
        ("review_status", FORMAL_REVIEW_STATUSES, "FORMAL_REVIEW_STATUS_INVALID"),
        ("claim_support_status", CLAIM_SUPPORT_STATUSES, "CLAIM_SUPPORT_STATUS_INVALID"),
        ("formal_check_requirement", FORMAL_CHECK_REQUIREMENTS, "FORMAL_CHECK_REQUIREMENT_INVALID"),
    )
    for field, values, code in checks:
        if row.get(field) not in values:
            add_error(errors, code, path, f"line {line}: invalid {field} {row.get(field)!r}")


def validate_formal_target_state(
    row: dict[str, Any],
    review_ids: list[str],
    review_map: dict[str, dict[str, Any]],
    evidence_map: dict[str, dict[str, Any]],
    path: Path,
    errors: list[dict[str, Any]],
    line: int | None,
) -> None:
    stage = row.get("artifact_stage")
    support = row.get("claim_support_status")
    if stage in {"intake", "stub"} and support not in {"no_support", "supports_formal_statement_only", "blocked"}:
        add_error(errors, "FORMAL_TARGET_STATE_INVALID", path, f"line {line}: intake/stub cannot claim final support")
    if stage == "archived" and support not in {"no_support", "blocked"}:
        add_error(errors, "FORMAL_TARGET_ARCHIVED_SUPPORT_INVALID", path, f"line {line}: archived formal target cannot support a claim")
    if row.get("placeholder_status") == "placeholders_allowed_for_stub" and stage != "stub":
        add_error(errors, "PLACEHOLDERS_ALLOWED_ONLY_FOR_STUB", path, f"line {line}: placeholders_allowed_for_stub requires artifact_stage=stub")
    if row.get("lean_check_status") in {"not_run", "tool_unavailable"} and support == PROMOTED_SUPPORT:
        add_error(errors, "FORMAL_SUPPORT_WITHOUT_TYPECHECK", path, f"line {line}: not_run/tool_unavailable cannot support a claim")
    if row.get("formal_check_requirement") in {"explicitly_requested", "required_for_delivery"} and row.get("lean_check_status") in BLOCKING_LEAN_CHECK_STATUSES:
        add_error(errors, "FORMAL_REQUIRED_CHECK_FAILED", path, f"line {line}: requested/required formal check did not pass")
    if support != PROMOTED_SUPPORT:
        return
    required = {
        "artifact_stage": "final_candidate",
        "lean_check_status": "typechecked",
        "placeholder_status": "no_active_placeholders",
        "trust_base_status": "accepted_trust_base",
        "statement_relation_status": "equivalent_reviewed",
    }
    for field, expected in required.items():
        if row.get(field) != expected:
            add_error(errors, "FORMAL_PROMOTION_PREREQUISITE_FAILED", path, f"line {line}: {field} must be {expected} for promoted formal support")
    if row.get("review_status") not in PROMOTION_REVIEW_STATUSES:
        add_error(errors, "FORMAL_PROMOTION_REVIEW_REQUIRED", path, f"line {line}: promoted formal support requires lead or human review")
    if not has_matching_statement_review(row, review_ids, review_map):
        add_error(errors, "FORMAL_PROMOTION_REVIEW_ROW_MISSING", path, f"line {line}: promoted formal support requires matching statement-equivalence review row")
    for evidence_id in row.get("verification_evidence_ids", []):
        evidence = evidence_map.get(evidence_id, {})
        if evidence.get("verification_source") == "fake_transport":
            add_error(errors, "FAKE_TRANSPORT_CANNOT_PROMOTE_FORMAL_SUPPORT", path, f"line {line}: fake transport cannot promote formal support")
    if not has_local_formal_check_evidence(row, evidence_map):
        add_error(errors, "LOCAL_FORMAL_CHECK_REQUIRED_FOR_PROMOTION", path, f"line {line}: promoted formal support requires local formal_check evidence")


def has_matching_statement_review(
    target: dict[str, Any],
    review_ids: list[str],
    review_map: dict[str, dict[str, Any]],
) -> bool:
    for review_id in review_ids:
        review = review_map.get(review_id)
        if not review:
            continue
        if review.get("formal_target_id") != target.get("formal_target_id"):
            continue
        if review.get("informal_statement_ref") != target.get("informal_statement_ref"):
            continue
        if review.get("lean_statement_ref") != target.get("lean_statement_ref"):
            continue
        if review.get("relation_status") != "equivalent_reviewed":
            continue
        if review.get("review_status") not in PROMOTION_REVIEW_STATUSES:
            continue
        return True
    return False


def has_local_formal_check_evidence(target: dict[str, Any], evidence_map: dict[str, dict[str, Any]]) -> bool:
    for evidence_id in target.get("verification_evidence_ids", []):
        evidence = evidence_map.get(evidence_id)
        if not evidence:
            continue
        if evidence.get("evidence_type") != "formal_check":
            continue
        if evidence.get("verification_source") in {"local_lean", "local_project_command"}:
            return True
    return False


def validate_delivery(
    delivery: dict[str, Any],
    path: Path,
    guard_ids: set[str],
    blocking_guard_ids: set[str],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    *,
    root: Path,
    source_map: dict[str, dict[str, Any]],
    claims: dict[str, dict[str, Any]],
    evidence_map: dict[str, dict[str, Any]],
    formal_targets: dict[str, dict[str, Any]],
    v2_mode: bool,
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
    report_ref = delivery.get("report_ref")
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
    if decision == "ready":
        validate_ready_delivery_claims(delivery, path, root, source_map, claims, evidence_map, formal_targets, v2_mode, errors)


def validate_ready_delivery_claims(
    delivery: dict[str, Any],
    path: Path,
    root: Path,
    source_map: dict[str, dict[str, Any]],
    claims: dict[str, dict[str, Any]],
    evidence_map: dict[str, dict[str, Any]],
    formal_targets: dict[str, dict[str, Any]],
    v2_mode: bool,
    errors: list[dict[str, Any]],
) -> None:
    caveats = delivery.get("caveats", [])
    report_ref = delivery.get("report_ref")
    report_path = validate_artifact_ref(report_ref, root, path, errors, None, "report_ref")
    if report_path is None:
        add_error(errors, "READY_REPORT_REF_INVALID", path, "ready delivery requires a valid report_ref")
    elif not report_path.is_file():
        add_error(errors, "READY_REPORT_MISSING", path, f"ready delivery report_ref does not exist: {report_ref}")
    else:
        report_text = report_path.read_text(encoding="utf-8")
        if "TODO" in report_text:
            add_error(errors, "READY_REPORT_TODO", path, "ready delivery report contains unresolved TODO")
        if "[UNVERIFIED]" in report_text:
            add_error(errors, "READY_REPORT_UNVERIFIED", path, "ready delivery report contains unresolved [UNVERIFIED]")

    for claim_id, claim in claims.items():
        status = claim.get("status")
        if status == "unsupported":
            add_error(errors, "READY_UNSUPPORTED_CLAIM", path, f"ready delivery contains unsupported claim {claim_id}")
        if status == "provisional" and not any(claim_id in str(caveat) for caveat in caveats):
            add_error(errors, "READY_PROVISIONAL_WITHOUT_CAVEAT", path, f"provisional claim {claim_id} requires a delivery caveat")
        source_ids = require_string_list(claim.get("source_ids"), errors, path, claim.get("_line"), "source_ids")
        evidence_ids = require_string_list(claim.get("evidence_ids"), errors, path, claim.get("_line"), "evidence_ids")
        if source_ids and not evidence_ids:
            statuses = [source_map.get(source_id, {}).get("library_status") for source_id in source_ids]
            if statuses and all(status == "[UNVERIFIED]" for status in statuses):
                add_error(errors, "READY_CLAIM_RELIES_ONLY_ON_UNVERIFIED_SOURCES", path, f"claim {claim_id} relies only on [UNVERIFIED] sources")
        if v2_mode:
            linked_evidence = [evidence_map.get(evidence_id) for evidence_id in evidence_ids if evidence_id in evidence_map]
            if not source_ids and linked_evidence and all(is_weak_computation_evidence(item) for item in linked_evidence):
                add_error(errors, "READY_CLAIM_RELIES_ONLY_ON_WEAK_COMPUTATION", path, f"claim {claim_id} relies only on bounded/partial computation evidence")
    for target_id, target in formal_targets.items():
        if target.get("claim_support_status") == PROMOTED_SUPPORT:
            continue
        if target.get("claim_support_status") == "supports_formal_statement_only":
            continue
        if target.get("formal_check_requirement") == "required_for_delivery":
            add_error(errors, "READY_REQUIRED_FORMAL_TARGET_NOT_PROMOTED", path, f"required formal target {target_id} is not promoted formal support")


def is_weak_computation_evidence(row: dict[str, Any] | None) -> bool:
    if not row or row.get("evidence_type") != "computation":
        return False
    return row.get("coverage_status") in WEAK_COMPUTATION_COVERAGE


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


def validate_timestamp(
    value: Any,
    path: Path,
    errors: list[dict[str, Any]],
    line: int | None,
    field: str,
) -> None:
    if not isinstance(value, str) or not value:
        add_error(errors, "TIMESTAMP_INVALID", path, f"line {line}: {field} must be an RFC3339 timestamp")
        return
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(text)
    except ValueError:
        add_error(errors, "TIMESTAMP_INVALID", path, f"line {line}: {field} must be an RFC3339 timestamp")


def validate_artifact_ref(
    value: Any,
    root: Path,
    path: Path,
    errors: list[dict[str, Any]],
    line: int | None,
    field: str,
    *,
    allow_empty: bool = False,
) -> Path | None:
    location = f"line {line}: " if line is not None else ""
    if value == "" and allow_empty:
        return None
    if not isinstance(value, str) or not value.strip():
        add_error(errors, "ARTIFACT_REF_INVALID", path, f"{location}{field} must be a non-empty string")
        return None
    ref = value.strip()
    if "\x00" in ref or "\\" in ref:
        add_error(errors, "ARTIFACT_REF_INVALID", path, f"{location}{field} must be slash-normalized and contain no null bytes")
        return None
    ref_path, fragment = split_artifact_fragment(ref)
    if not ref_path:
        add_error(errors, "ARTIFACT_REF_INVALID", path, f"{location}{field} path cannot be empty")
        return None
    if URI_RE.match(ref_path) or WINDOWS_DRIVE_RE.match(ref_path) or ref_path.startswith(("/", "//")):
        add_error(errors, "ARTIFACT_REF_UNSAFE", path, f"{location}{field} must be relative and non-URI")
        return None
    logical = PurePosixPath(ref_path)
    if any(part in {"", ".", ".."} for part in logical.parts):
        add_error(errors, "ARTIFACT_REF_UNSAFE", path, f"{location}{field} must not contain traversal or empty path segments")
        return None
    if fragment is not None and (not fragment or "/" in fragment or "\\" in fragment or "\x00" in fragment or URI_RE.match(fragment) or not fragment.isascii()):
        add_error(errors, "ARTIFACT_REF_FRAGMENT_INVALID", path, f"{location}{field} fragment is invalid")
        return None
    candidate = root.joinpath(*logical.parts)
    try:
        candidate.relative_to(root)
    except ValueError:
        add_error(errors, "ARTIFACT_REF_UNSAFE", path, f"{location}{field} escapes run directory")
        return None
    if candidate.exists() or candidate.is_symlink():
        try:
            candidate.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            add_error(errors, "ARTIFACT_REF_SYMLINK_ESCAPE", path, f"{location}{field} resolves outside run directory")
            return None
    return candidate


def split_artifact_fragment(ref: str) -> tuple[str, str | None]:
    if "#" not in ref:
        return ref, None
    path_part, fragment = ref.split("#", 1)
    return path_part, fragment


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
