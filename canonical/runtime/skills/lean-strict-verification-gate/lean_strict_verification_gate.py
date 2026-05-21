#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "lean-strict-verification-gate.v1"
INTAKE_SCHEMA_VERSION = "lean-formalization-intake.v1"

CHECKS_RUN = [
    "intake_contract_static_check",
    "repo_path_static_check",
    "required_field_presence_check",
    "trust_no_overclaim_check",
]

CHECKS_NOT_RUN = [
    "lean_build",
    "lake_build",
    "axle_api_call",
    "axle_check",
    "mcp_session",
    "safeverify_run",
    "lean4checker_run",
    "comparator_run",
    "strict_verifier",
    "python_package_install",
    "ci_status",
    "notebook_execution",
    "training_workflow",
]

REQUIRED_FIELDS = [
    "lean_toolchain",
    "lake_manifest",
    "mathlib_revision",
    "problem_files",
    "solution_files",
    "source_materials",
]

BANNED_PROOF_PHRASES = [
    "proof valid",
    "problem solved",
    "formally verified",
    "final verifier passed",
    "trusted proof",
]


def finding(
    check_id: str,
    status: str,
    message: str,
    *,
    severity: str = "info",
    evidence: Any | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": check_id,
        "status": status,
        "severity": severity,
        "message": message,
    }
    if evidence is not None:
        data["evidence"] = evidence
    return data


def status_of(payload: dict[str, Any], field: str) -> str:
    value = payload.get("fields", {}).get(field, {})
    if isinstance(value, dict):
        return str(value.get("status", "missing"))
    return "missing"


def mathlib_revision_reported(payload: dict[str, Any]) -> bool:
    value = payload.get("fields", {}).get("mathlib_revision", {})
    if not isinstance(value, dict):
        return False
    return value.get("status") == "reported" and bool(value.get("revision"))


def source_id_commit(source_id: str | None) -> str | None:
    if not source_id or "@" not in source_id:
        return None
    return source_id.rsplit("@", 1)[1] or None


def source_id_repo_name(source_id: str | None) -> str | None:
    if not source_id:
        return None
    left = source_id.split("@", 1)[0]
    if "/" in left:
        return left.rsplit("/", 1)[1]
    return left or None


def no_overclaim_contract(payload: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if payload.get("schema_version") != INTAKE_SCHEMA_VERSION:
        findings.append(
            finding("intake.schema_version", "fail", "intake schema_version is not lean-formalization-intake.v1", severity="blocker")
        )
    if payload.get("runtime_behavior") != "incomplete analysis":
        findings.append(
            finding("intake.runtime_behavior", "fail", "intake runtime_behavior must remain incomplete analysis", severity="blocker")
        )
    if payload.get("incomplete_analysis") is not True:
        findings.append(
            finding("intake.incomplete_analysis", "fail", "intake incomplete_analysis must be true", severity="blocker")
        )

    claim = payload.get("trust_claim", {})
    if not isinstance(claim, dict):
        findings.append(finding("intake.trust_claim", "fail", "intake trust_claim must be an object", severity="blocker"))
        return findings
    expected = {
        "tier": "T0_STATIC_INTAKE",
        "runtime_behavior": "incomplete analysis",
        "incomplete_analysis": True,
        "checks_run": [],
        "allowed_axioms": "unchecked",
        "sorries_policy": "unchecked",
    }
    for key, value in expected.items():
        if claim.get(key) != value:
            findings.append(
                finding(f"intake.trust_claim.{key}", "fail", f"intake trust_claim.{key} overclaims or is missing", severity="blocker")
            )
    if claim.get("statement_intent_review", {}).get("status") != "not_reviewed":
        findings.append(
            finding(
                "intake.trust_claim.statement_intent_review",
                "fail",
                "intake theorem-intent review must remain not_reviewed",
                severity="blocker",
            )
        )

    serialized = json.dumps(payload, sort_keys=True).lower()
    for phrase in BANNED_PROOF_PHRASES:
        if phrase in serialized:
            findings.append(finding("intake.proof_phrase", "fail", f"banned proof phrase present: {phrase}", severity="blocker"))
    if not findings:
        findings.append(finding("intake.contract", "pass", "intake contract is static and proof-neutral"))
    return findings


def gate_claim(ok: bool) -> dict[str, Any]:
    return {
        "tier": "T1_STATIC_PREFLIGHT_READY" if ok else "T0_STATIC_PREFLIGHT_REJECTED",
        "runtime_behavior": "incomplete analysis",
        "incomplete_analysis": True,
        "checks_run": list(CHECKS_RUN),
        "checks_not_run": list(CHECKS_NOT_RUN),
        "allowed_axioms": "unchecked",
        "sorries_policy": "unchecked",
        "statement_intent_review": {"status": "not_reviewed"},
        "claim": "static verifier preflight only; no proof-validity claim",
    }


def evaluate_gate(
    repo: Path,
    intake_payload: dict[str, Any],
    *,
    intake_json: Path | None = None,
    expected_source_id: str | None = None,
    expected_commit: str | None = None,
    require_ci: bool = True,
    require_license: bool = True,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    repo_path = repo.resolve()

    if repo.is_symlink():
        findings.append(finding("repo.symlink", "fail", "repo path must not be a symlink", severity="blocker"))
    if not repo_path.exists() or not repo_path.is_dir():
        findings.append(finding("repo.exists", "fail", "repo path must be an existing local directory", severity="blocker"))
    else:
        findings.append(finding("repo.exists", "pass", "repo path is an existing local directory"))

    if intake_json is not None:
        if intake_json.is_symlink():
            findings.append(finding("input.intake_json", "fail", "intake JSON path must not be a symlink", severity="blocker"))
        elif intake_json.exists() and intake_json.is_file():
            findings.append(finding("input.intake_json", "pass", "intake JSON path is a regular file"))
        else:
            findings.append(finding("input.intake_json", "fail", "intake JSON path must be a regular file", severity="blocker"))

    findings.extend(no_overclaim_contract(intake_payload))

    source_id = intake_payload.get("repo", {}).get("source_id")
    if expected_source_id and source_id != expected_source_id:
        findings.append(
            finding(
                "input.expected_source_id",
                "fail",
                "intake source_id does not match expected_source_id",
                severity="blocker",
                evidence={"expected": expected_source_id, "actual": source_id},
            )
        )
    elif expected_source_id:
        findings.append(finding("input.expected_source_id", "pass", "intake source_id matches expected_source_id"))

    actual_commit = source_id_commit(source_id)
    if expected_commit and actual_commit != expected_commit:
        findings.append(
            finding(
                "input.expected_commit",
                "fail",
                "intake source_id commit does not match expected_commit",
                severity="blocker",
                evidence={"expected": expected_commit, "actual": actual_commit},
            )
        )
    elif expected_commit:
        findings.append(finding("input.expected_commit", "pass", "intake source_id commit matches expected_commit"))

    repo_name = source_id_repo_name(source_id)
    if repo_name and repo_path.exists() and repo_path.name != repo_name:
        findings.append(
            finding(
                "repo.name",
                "warn",
                "repo directory name differs from intake source_id repo name",
                severity="warning",
                evidence={"repo_dir": repo_path.name, "source_id_repo": repo_name},
            )
        )

    for field in REQUIRED_FIELDS:
        if field == "mathlib_revision":
            passed = mathlib_revision_reported(intake_payload)
        else:
            passed = status_of(intake_payload, field) == "detected"
        findings.append(
            finding(
                f"field.{field}",
                "pass" if passed else "fail",
                f"{field} {'is present' if passed else 'is missing'}",
                severity="info" if passed else "blocker",
            )
        )

    lakefile_present = status_of(intake_payload, "lakefile_toml") == "detected" or status_of(intake_payload, "lakefile_lean") == "detected"
    findings.append(
        finding(
            "field.lakefile",
            "pass" if lakefile_present else "fail",
            "lakefile.toml or lakefile.lean is present" if lakefile_present else "lakefile.toml or lakefile.lean is missing",
            severity="info" if lakefile_present else "blocker",
        )
    )

    for field, required, allow_flag in (("ci", require_ci, "--allow-missing-ci"), ("license", require_license, "--allow-missing-license")):
        present = status_of(intake_payload, field) == "detected"
        if present:
            findings.append(finding(f"field.{field}", "pass", f"{field} is present"))
        elif required:
            findings.append(finding(f"field.{field}", "fail", f"{field} is required for strict preflight", severity="blocker"))
        else:
            findings.append(finding(f"field.{field}", "warn", f"{field} is missing but {allow_flag} was used", severity="warning"))

    do_not_run = intake_payload.get("do_not_run_commands", [])
    if do_not_run:
        findings.append(
            finding("surface.do_not_run_commands", "fail", "intake reported commands that must not be run", severity="blocker", evidence=len(do_not_run))
        )
    else:
        findings.append(finding("surface.do_not_run_commands", "pass", "no do-not-run commands reported"))

    redactions = intake_payload.get("redactions", [])
    if redactions:
        findings.append(
            finding("surface.redactions", "fail", "intake redacted sensitive material; manual cleanup required", severity="blocker", evidence=len(redactions))
        )
    else:
        findings.append(finding("surface.redactions", "pass", "no sensitive-material redactions reported"))

    gaps = intake_payload.get("unchecked_gaps", [])
    if gaps:
        findings.append(finding("surface.unchecked_gaps", "fail", "intake has unchecked gaps", severity="blocker", evidence=len(gaps)))
    else:
        findings.append(finding("surface.unchecked_gaps", "pass", "no unchecked intake gaps reported"))

    blockers = [item for item in findings if item.get("status") == "fail" and item.get("severity") == "blocker"]
    warnings = [item for item in findings if item.get("status") == "warn"]
    ok = not blockers
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "gate_status": "pass" if ok else "fail",
        "runtime_behavior": "incomplete analysis",
        "incomplete_analysis": True,
        "inputs": {
            "repo_path": ".",
            "intake_json": intake_json.name if intake_json else None,
            "source_id": source_id,
            "expected_source_id": expected_source_id,
            "expected_commit": expected_commit,
        },
        "gate_claim": gate_claim(ok),
        "blockers": blockers,
        "warnings": warnings,
        "findings": findings,
        "advisory_soundness_signal_count": len(intake_payload.get("advisory_soundness_signals", [])),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Lean Strict Verification Gate",
        "",
        "Runtime behavior: incomplete analysis",
        "",
        f"Gate status: `{payload.get('gate_status')}`",
        f"Tier: `{payload.get('gate_claim', {}).get('tier')}`",
        "",
        "## Blockers",
        "",
    ]
    blockers = payload.get("blockers", [])
    if blockers:
        for item in blockers:
            lines.append(f"- `{item['id']}`: {item['message']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Findings", ""])
    for item in payload.get("findings", []):
        lines.append(f"- `{item['status']}` `{item['id']}`: {item['message']}")
    lines.extend(["", "## Claim", "", "- static verifier preflight only; no proof-validity claim", ""])
    return "\n".join(lines)


def write_text(path: Path, text: str) -> None:
    if path.exists() and path.is_symlink():
        raise ValueError(f"refusing to replace symlink output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_intake(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Static Lean strict verification preflight gate")
    parser.add_argument("repo", help="explicit local repository path")
    parser.add_argument("--intake-json", required=True, help="lean-formalization-intake JSON")
    parser.add_argument("--expected-source-id", default=None)
    parser.add_argument("--expected-commit", default=None)
    parser.add_argument("--allow-missing-ci", action="store_true")
    parser.add_argument("--allow-missing-license", action="store_true")
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--output-md", default=None)
    args = parser.parse_args(argv)

    intake_path = Path(args.intake_json)
    try:
        intake_payload = load_intake(intake_path)
        payload = evaluate_gate(
            Path(args.repo),
            intake_payload,
            intake_json=intake_path,
            expected_source_id=args.expected_source_id,
            expected_commit=args.expected_commit,
            require_ci=not args.allow_missing_ci,
            require_license=not args.allow_missing_license,
        )
    except (OSError, json.JSONDecodeError) as exc:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "gate_status": "fail",
            "runtime_behavior": "incomplete analysis",
            "incomplete_analysis": True,
            "inputs": {"repo_path": ".", "intake_json": intake_path.name},
            "gate_claim": gate_claim(False),
            "blockers": [finding("input.intake_json", "fail", f"could not load intake JSON: {exc.__class__.__name__}", severity="blocker")],
            "warnings": [],
            "findings": [],
            "advisory_soundness_signal_count": 0,
        }

    json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output_json:
        write_text(Path(args.output_json), json_text)
    else:
        sys.stdout.write(json_text)
    if args.output_md:
        write_text(Path(args.output_md), render_markdown(payload))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
