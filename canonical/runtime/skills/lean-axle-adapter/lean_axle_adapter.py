#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "lean-axle-adapter-dry-run.v1"
GATE_SCHEMA_VERSION = "lean-strict-verification-gate.v1"
INTAKE_SCHEMA_VERSION = "lean-formalization-intake.v1"

CHECKS_RUN = [
    "strict_gate_contract_static_check",
    "repo_path_static_check",
    "noop_request_contract_static_check",
    "optional_intake_hash_static_check",
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
    "ci_status",
    "notebook_execution",
    "training_workflow",
]

BANNED_PROOF_PHRASES = [
    "proof valid",
    "problem solved",
    "formally verified",
    "final verifier passed",
    "trusted proof",
    "axle accepted",
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


def source_id_commit(source_id: str | None) -> str | None:
    if not source_id or "@" not in source_id:
        return None
    return source_id.rsplit("@", 1)[1] or None


def status_of(payload: dict[str, Any], field: str) -> str:
    value = payload.get("fields", {}).get(field, {})
    if isinstance(value, dict):
        return str(value.get("status", "missing"))
    return "missing"


def files_of(payload: dict[str, Any], field: str) -> list[str]:
    value = payload.get("fields", {}).get(field, {})
    files = value.get("files", []) if isinstance(value, dict) else []
    paths: list[str] = []
    if isinstance(files, list):
        for item in files:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                paths.append(item["path"])
    return sorted(set(paths))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def safe_relative_file(root: Path, relative: str) -> Path:
    rel_path = Path(relative)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise ValueError("path_not_relative")
    candidate = root / rel_path
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved_root not in (resolved, *resolved.parents):
        raise ValueError("path_escape")
    if candidate.is_symlink():
        raise ValueError("symlink")
    if not candidate.is_file():
        raise ValueError("not_file")
    return candidate


def hash_bundle(repo: Path, intake_payload: dict[str, Any]) -> tuple[dict[str, str], str | None, list[dict[str, Any]]]:
    hashes: dict[str, str] = {}
    findings: list[dict[str, Any]] = []
    for field in ("problem_files", "solution_files"):
        for relative in files_of(intake_payload, field):
            try:
                path = safe_relative_file(repo, relative)
                hashes[relative] = sha256_file(path)
            except (OSError, ValueError) as exc:
                findings.append(
                    finding(
                        f"bundle.{field}",
                        "fail",
                        "could not hash formal input file",
                        severity="blocker",
                        evidence={"path": relative, "reason": exc.__class__.__name__},
                    )
                )
    if not hashes:
        findings.append(finding("bundle.formal_inputs", "fail", "no formal input files were hashed", severity="blocker"))
        return hashes, None, findings

    aggregate = hashlib.sha256()
    for relative in sorted(hashes):
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(hashes[relative].encode("utf-8"))
        aggregate.update(b"\n")
    return hashes, "sha256:" + aggregate.hexdigest(), findings


def dry_run_claim(ok: bool) -> dict[str, Any]:
    return {
        "tier": "T1_AXLE_NOOP_DRY_RUN" if ok else "T0_AXLE_NOOP_BLOCKED",
        "runtime_behavior": "incomplete analysis",
        "incomplete_analysis": True,
        "checks_run": list(CHECKS_RUN),
        "checks_not_run": list(CHECKS_NOT_RUN),
        "claim": "offline AXLE adapter request contract only; no AXLE call and no proof-validity claim",
        "t2_axle_accepted_not_claimed": True,
        "theorem_intent_match_not_claimed": True,
    }


def request_contract(
    source_id: str | None,
    intake_payload: dict[str, Any] | None,
    file_hashes: dict[str, str],
    bundle_hash: str | None,
) -> dict[str, Any]:
    fields = intake_payload.get("fields", {}) if isinstance(intake_payload, dict) else {}
    mathlib = fields.get("mathlib_revision", {}) if isinstance(fields.get("mathlib_revision"), dict) else {}
    return {
        "mode": "noop_dry_run",
        "would_call_axle": False,
        "endpoint_allowlist": [],
        "credential_env_allowlist": [],
        "mcp_config_mutation": False,
        "background_server": False,
        "network_access": False,
        "source_id": source_id,
        "lean_toolchain_path": fields.get("lean_toolchain", {}).get("path") if isinstance(fields.get("lean_toolchain"), dict) else None,
        "mathlib_revision": mathlib.get("revision"),
        "formal_input_file_hashes": file_hashes,
        "formal_input_bundle_sha256": bundle_hash,
        "theorem_intent_review": {"status": "not_reviewed"},
    }


def gate_contract_findings(gate_payload: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if gate_payload.get("schema_version") != GATE_SCHEMA_VERSION:
        findings.append(finding("gate.schema_version", "fail", "gate schema_version is not lean-strict-verification-gate.v1", severity="blocker"))
    if gate_payload.get("runtime_behavior") != "incomplete analysis":
        findings.append(finding("gate.runtime_behavior", "fail", "gate runtime_behavior must remain incomplete analysis", severity="blocker"))
    if gate_payload.get("incomplete_analysis") is not True:
        findings.append(finding("gate.incomplete_analysis", "fail", "gate incomplete_analysis must be true", severity="blocker"))
    if gate_payload.get("gate_status") != "pass":
        findings.append(finding("gate.status", "fail", "strict gate must pass before AXLE no-op contract generation", severity="blocker"))
    if gate_payload.get("blockers"):
        findings.append(finding("gate.blockers", "fail", "strict gate has blockers", severity="blocker", evidence=len(gate_payload.get("blockers", []))))
    if gate_payload.get("warnings"):
        findings.append(finding("gate.warnings", "fail", "strict gate warnings must be resolved before AXLE no-op contract generation", severity="blocker", evidence=len(gate_payload.get("warnings", []))))

    claim = gate_payload.get("gate_claim", {})
    if not isinstance(claim, dict) or claim.get("tier") != "T1_STATIC_PREFLIGHT_READY":
        findings.append(finding("gate.claim.tier", "fail", "gate claim tier must be T1_STATIC_PREFLIGHT_READY", severity="blocker"))
    serialized = json.dumps(gate_payload, sort_keys=True).lower()
    for phrase in BANNED_PROOF_PHRASES:
        if phrase in serialized and phrase != "axle accepted":
            findings.append(finding("gate.proof_phrase", "fail", f"banned proof phrase present: {phrase}", severity="blocker"))
    if not findings:
        findings.append(finding("gate.contract", "pass", "strict gate contract admits offline no-op adapter dry-run"))
    return findings


def intake_contract_findings(gate_source_id: str | None, intake_payload: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if intake_payload.get("schema_version") != INTAKE_SCHEMA_VERSION:
        findings.append(finding("intake.schema_version", "fail", "intake schema_version is not lean-formalization-intake.v1", severity="blocker"))
    source_id = intake_payload.get("repo", {}).get("source_id")
    if gate_source_id and source_id != gate_source_id:
        findings.append(
            finding(
                "intake.source_id",
                "fail",
                "intake source_id must match strict gate source_id",
                severity="blocker",
                evidence={"gate": gate_source_id, "intake": source_id},
            )
        )
    for field in ("problem_files", "solution_files", "lean_toolchain", "mathlib_revision"):
        status = status_of(intake_payload, field)
        expected = "reported" if field == "mathlib_revision" else "detected"
        if status != expected:
            findings.append(
                finding(f"intake.field.{field}", "fail", f"intake {field} must be {expected}", severity="blocker", evidence=status)
            )
    for surface in ("redactions", "do_not_run_commands", "unchecked_gaps", "errors"):
        items = intake_payload.get(surface, [])
        if items:
            findings.append(finding(f"intake.{surface}", "fail", f"intake has {surface}", severity="blocker", evidence=len(items)))
    if not findings:
        findings.append(finding("intake.contract", "pass", "intake contract is compatible with offline no-op adapter dry-run"))
    return findings


def evaluate_adapter(
    repo: Path,
    gate_payload: dict[str, Any],
    *,
    intake_payload: dict[str, Any] | None = None,
    gate_json: Path | None = None,
    intake_json: Path | None = None,
    expected_source_id: str | None = None,
    expected_commit: str | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    repo_path = repo.resolve()
    if repo.is_symlink():
        findings.append(finding("repo.symlink", "fail", "repo path must not be a symlink", severity="blocker"))
    if not repo_path.exists() or not repo_path.is_dir():
        findings.append(finding("repo.exists", "fail", "repo path must be an existing local directory", severity="blocker"))
    else:
        findings.append(finding("repo.exists", "pass", "repo path is an existing local directory"))

    for label, path in (("gate_json", gate_json), ("intake_json", intake_json)):
        if path is None:
            continue
        if path.is_symlink():
            findings.append(finding(f"input.{label}", "fail", f"{label} path must not be a symlink", severity="blocker"))
        elif path.exists() and path.is_file():
            findings.append(finding(f"input.{label}", "pass", f"{label} path is a regular file"))
        else:
            findings.append(finding(f"input.{label}", "fail", f"{label} path must be a regular file", severity="blocker"))

    findings.extend(gate_contract_findings(gate_payload))
    source_id = gate_payload.get("inputs", {}).get("source_id")
    if expected_source_id and source_id != expected_source_id:
        findings.append(
            finding(
                "input.expected_source_id",
                "fail",
                "gate source_id does not match expected_source_id",
                severity="blocker",
                evidence={"expected": expected_source_id, "actual": source_id},
            )
        )
    elif expected_source_id:
        findings.append(finding("input.expected_source_id", "pass", "gate source_id matches expected_source_id"))

    actual_commit = source_id_commit(source_id)
    if expected_commit and actual_commit != expected_commit:
        findings.append(
            finding(
                "input.expected_commit",
                "fail",
                "gate source_id commit does not match expected_commit",
                severity="blocker",
                evidence={"expected": expected_commit, "actual": actual_commit},
            )
        )
    elif expected_commit:
        findings.append(finding("input.expected_commit", "pass", "gate source_id commit matches expected_commit"))

    file_hashes: dict[str, str] = {}
    bundle_hash: str | None = None
    if intake_payload is not None:
        findings.extend(intake_contract_findings(source_id, intake_payload))
        hashes, aggregate, bundle_findings = hash_bundle(repo_path, intake_payload)
        file_hashes = hashes
        bundle_hash = aggregate
        findings.extend(bundle_findings)
    else:
        findings.append(finding("input.intake_json", "warn", "no intake JSON supplied; request contract will omit formal input hashes", severity="warning"))

    blockers = [item for item in findings if item.get("status") == "fail" and item.get("severity") == "blocker"]
    warnings = [item for item in findings if item.get("status") == "warn"]
    ok = not blockers
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "adapter_status": "noop_contract_ready" if ok else "blocked",
        "runtime_behavior": "incomplete analysis",
        "incomplete_analysis": True,
        "inputs": {
            "repo_path": ".",
            "gate_json": gate_json.name if gate_json else None,
            "intake_json": intake_json.name if intake_json else None,
            "source_id": source_id,
            "expected_source_id": expected_source_id,
            "expected_commit": expected_commit,
        },
        "dry_run_claim": dry_run_claim(ok),
        "request_contract": request_contract(source_id, intake_payload, file_hashes, bundle_hash) if ok else None,
        "blockers": blockers,
        "warnings": warnings,
        "findings": findings,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Lean AXLE Adapter Dry-Run",
        "",
        "Runtime behavior: incomplete analysis",
        "",
        f"Adapter status: `{payload.get('adapter_status')}`",
        f"Tier: `{payload.get('dry_run_claim', {}).get('tier')}`",
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
    lines.extend(["", "## No-Op Contract", ""])
    contract = payload.get("request_contract")
    if contract:
        lines.append(f"- source_id: `{contract.get('source_id')}`")
        lines.append(f"- would_call_axle: `{contract.get('would_call_axle')}`")
        lines.append(f"- formal_input_bundle_sha256: `{contract.get('formal_input_bundle_sha256')}`")
    else:
        lines.append("- not emitted")
    lines.extend(["", "## Claim", "", "- offline AXLE adapter request contract only; no AXLE call and no proof-validity claim", ""])
    return "\n".join(lines)


def write_text(path: Path, text: str) -> None:
    if path.exists() and path.is_symlink():
        raise ValueError(f"refusing to replace symlink output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Lean AXLE adapter dry-run/no-op contract")
    parser.add_argument("repo", help="explicit local repository path")
    parser.add_argument("--gate-json", required=True, help="lean-strict-verification-gate JSON")
    parser.add_argument("--intake-json", default=None, help="optional lean-formalization-intake JSON")
    parser.add_argument("--expected-source-id", default=None)
    parser.add_argument("--expected-commit", default=None)
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--output-md", default=None)
    args = parser.parse_args(argv)

    gate_path = Path(args.gate_json)
    intake_path = Path(args.intake_json) if args.intake_json else None
    try:
        gate_payload = load_json(gate_path)
        intake_payload = load_json(intake_path) if intake_path else None
        payload = evaluate_adapter(
            Path(args.repo),
            gate_payload,
            intake_payload=intake_payload,
            gate_json=gate_path,
            intake_json=intake_path,
            expected_source_id=args.expected_source_id,
            expected_commit=args.expected_commit,
        )
    except (OSError, json.JSONDecodeError) as exc:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "adapter_status": "blocked",
            "runtime_behavior": "incomplete analysis",
            "incomplete_analysis": True,
            "inputs": {"repo_path": ".", "gate_json": gate_path.name, "intake_json": intake_path.name if intake_path else None},
            "dry_run_claim": dry_run_claim(False),
            "request_contract": None,
            "blockers": [finding("input.json", "fail", f"could not load JSON input: {exc.__class__.__name__}", severity="blocker")],
            "warnings": [],
            "findings": [],
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
