from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION_V1 = "openclaw.target-evidence.v1"
SCHEMA_VERSION_V2 = "openclaw.target-evidence.v2"
SCHEMA_VERSION = SCHEMA_VERSION_V1
GENERATOR_VERSION = "openclaw-target-evidence.phase1.v1"
GENERATOR_VERSION_V2 = "openclaw-target-evidence.phase2.v1"
PHASE_V1 = "phase1-non-authorizing"
PHASE_V2 = "phase2-authorizing"
EVIDENCE_TYPES_V1 = (
    "native-loader",
    "native-inertness",
    "helper-invocation",
    "runtime-root",
    "quiescence-lock",
    "target-pre-state",
)
EVIDENCE_TYPES_V2 = (
    "native-loader",
    "native-managed-skill-root",
    "native-managed-skill-canary",
    "target-pre-state",
    "quiescence-lock",
)
EVIDENCE_TYPES = EVIDENCE_TYPES_V1
EVIDENCE_SOURCES = ("native-probe", "manual-review", "fixture", "upstream-doc")
AUTHORING_EVIDENCE_SOURCE = "native-probe"
PLATFORMS = ("linux", "macos", "windows", "wsl-native", "wsl-mounted-windows", "ci-container")
PATH_STYLES = ("posix", "windows-drive", "windows-unc", "wsl-posix", "mounted-windows")


def load_target_evidence(path: Path) -> dict[str, Any]:
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("OpenClaw target evidence file is not valid JSON") from exc
    if not isinstance(evidence, dict):
        raise ValueError("OpenClaw target evidence file must contain a JSON object")
    validate_target_evidence(evidence)
    return evidence


def build_target_evidence(
    *,
    evidence_type: str,
    evidence_source: str,
    platform: str,
    path_style: str,
    observed_behavior: str,
    limitations: list[str],
    captured_at: str | None = None,
    openclaw_version: str | None = None,
    artifact_hashes: list[str] | None = None,
) -> dict[str, Any]:
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": "target_evidence_pending",
        "generator_version": GENERATOR_VERSION,
        "target": "openclaw",
        "phase": "phase1-non-authorizing",
        "evidence_type": evidence_type,
        "evidence_source": evidence_source,
        "platform": platform,
        "path_style": path_style,
        "captured_at": captured_at or now_utc(),
        "observed_behavior": observed_behavior,
        "limitations": limitations,
        "authorizes_real_writes": False,
        "approval_eligible": False,
    }
    if openclaw_version:
        evidence["openclaw_version"] = openclaw_version
    if artifact_hashes:
        evidence["artifact_hashes"] = artifact_hashes
    evidence["evidence_id"] = f"target_evidence_{stable_digest(canonical_evidence_payload(evidence))}"
    validate_target_evidence(evidence)
    return evidence


def build_authorizing_target_evidence(
    *,
    evidence_type: str,
    platform: str,
    path_style: str,
    observed_behavior: str,
    target_realpath: str,
    managed_skills_realpath: str,
    checks: dict[str, Any],
    captured_at: str | None = None,
    openclaw_version: str | None = None,
    artifact_hashes: list[str] | None = None,
) -> dict[str, Any]:
    evidence = {
        "schema_version": SCHEMA_VERSION_V2,
        "evidence_id": "target_evidence_pending",
        "generator_version": GENERATOR_VERSION_V2,
        "target": "openclaw",
        "phase": PHASE_V2,
        "evidence_type": evidence_type,
        "evidence_source": AUTHORING_EVIDENCE_SOURCE,
        "platform": platform,
        "path_style": path_style,
        "captured_at": captured_at or now_utc(),
        "observed_behavior": observed_behavior,
        "limitations": [],
        "authorizes_real_writes": True,
        "approval_eligible": True,
        "target_realpath": target_realpath,
        "managed_skills_realpath": managed_skills_realpath,
        "checks": checks,
    }
    if openclaw_version:
        evidence["openclaw_version"] = openclaw_version
    if artifact_hashes:
        evidence["artifact_hashes"] = artifact_hashes
    evidence["evidence_id"] = f"target_evidence_{stable_digest(canonical_evidence_payload(evidence))}"
    validate_target_evidence(evidence)
    return evidence


def validate_target_evidence(evidence: dict[str, Any]) -> None:
    schema_version = evidence.get("schema_version")
    if schema_version == SCHEMA_VERSION_V1:
        validate_target_evidence_v1(evidence)
        return
    if schema_version == SCHEMA_VERSION_V2:
        validate_target_evidence_v2(evidence)
        return
    if schema_version != SCHEMA_VERSION:
        if schema_version == "openclaw.evidence.v1":
            raise ValueError("OpenClaw source/import evidence cannot authorize target writes")
        raise ValueError("OpenClaw target evidence schema version is not supported")


def validate_target_evidence_v1(evidence: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "evidence_id",
        "generator_version",
        "target",
        "phase",
        "evidence_type",
        "evidence_source",
        "platform",
        "path_style",
        "captured_at",
        "observed_behavior",
        "limitations",
        "authorizes_real_writes",
        "approval_eligible",
    }
    missing = sorted(required - set(evidence))
    if missing:
        raise ValueError(f"OpenClaw target evidence is missing required fields: {', '.join(missing)}")
    if evidence["target"] != "openclaw":
        raise ValueError("OpenClaw target evidence must target openclaw")
    if evidence["phase"] != PHASE_V1:
        raise ValueError("OpenClaw target evidence phase is not supported")
    enum_checks = (
        ("evidence_type", EVIDENCE_TYPES_V1),
        ("evidence_source", EVIDENCE_SOURCES),
        ("platform", PLATFORMS),
        ("path_style", PATH_STYLES),
    )
    for field, allowed in enum_checks:
        if evidence[field] not in allowed:
            raise ValueError(f"OpenClaw target evidence field {field} is not supported: {evidence[field]}")
    if evidence["authorizes_real_writes"] is not False:
        raise ValueError("Phase 1 OpenClaw target evidence cannot authorize real writes")
    if evidence["approval_eligible"] is not False:
        raise ValueError("Phase 1 OpenClaw target evidence cannot be approval eligible")
    if not isinstance(evidence["limitations"], list):
        raise ValueError("OpenClaw target evidence limitations must be a list")
    if not str(evidence["observed_behavior"]).strip():
        raise ValueError("OpenClaw target evidence observed_behavior is required")
    expected = f"target_evidence_{stable_digest(canonical_evidence_payload(evidence))}"
    if evidence["evidence_id"] != expected:
        raise ValueError("OpenClaw target evidence content address does not match evidence_id")


def validate_target_evidence_v2(evidence: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "evidence_id",
        "generator_version",
        "target",
        "phase",
        "evidence_type",
        "evidence_source",
        "platform",
        "path_style",
        "captured_at",
        "observed_behavior",
        "limitations",
        "authorizes_real_writes",
        "approval_eligible",
        "target_realpath",
        "managed_skills_realpath",
        "checks",
    }
    missing = sorted(required - set(evidence))
    if missing:
        raise ValueError(f"OpenClaw target evidence is missing required fields: {', '.join(missing)}")
    if evidence["generator_version"] != GENERATOR_VERSION_V2:
        raise ValueError("OpenClaw target evidence generator version is not supported")
    if evidence["target"] != "openclaw":
        raise ValueError("OpenClaw target evidence must target openclaw")
    if evidence["phase"] != PHASE_V2:
        raise ValueError("OpenClaw target evidence phase is not supported")
    enum_checks = (
        ("evidence_type", EVIDENCE_TYPES_V2),
        ("platform", PLATFORMS),
        ("path_style", PATH_STYLES),
    )
    for field, allowed in enum_checks:
        if evidence[field] not in allowed:
            raise ValueError(f"OpenClaw target evidence field {field} is not supported: {evidence[field]}")
    if evidence["evidence_source"] != AUTHORING_EVIDENCE_SOURCE:
        raise ValueError("OpenClaw authorizing target evidence must come from native-probe")
    if evidence["authorizes_real_writes"] is not True:
        raise ValueError("Phase 2 OpenClaw target evidence must authorize real writes")
    if evidence["approval_eligible"] is not True:
        raise ValueError("Phase 2 OpenClaw target evidence must be approval eligible")
    if not isinstance(evidence["limitations"], list):
        raise ValueError("OpenClaw target evidence limitations must be a list")
    if evidence["limitations"]:
        raise ValueError("OpenClaw authorizing target evidence must have no limitations")
    if not str(evidence["observed_behavior"]).strip():
        raise ValueError("OpenClaw target evidence observed_behavior is required")
    if not str(evidence["target_realpath"]).strip():
        raise ValueError("OpenClaw target evidence target_realpath is required")
    if not str(evidence["managed_skills_realpath"]).strip():
        raise ValueError("OpenClaw target evidence managed_skills_realpath is required")
    if not isinstance(evidence["checks"], dict) or not evidence["checks"]:
        raise ValueError("OpenClaw target evidence checks must be a non-empty object")
    if "artifact_hashes" in evidence and not isinstance(evidence["artifact_hashes"], list):
        raise ValueError("OpenClaw target evidence artifact_hashes must be a list")
    expected = f"target_evidence_{stable_digest(canonical_evidence_payload(evidence))}"
    if evidence["evidence_id"] != expected:
        raise ValueError("OpenClaw target evidence content address does not match evidence_id")


def target_evidence_authorizes_real_writes(
    evidence_items: list[dict[str, Any]],
    *,
    action_class: str | None = None,
) -> bool:
    for item in evidence_items:
        validate_target_evidence(item)
    if not evidence_items:
        return False
    if any(item.get("schema_version") != SCHEMA_VERSION_V2 for item in evidence_items):
        return False
    required_types = {"native-loader", "native-managed-skill-root", "target-pre-state", "quiescence-lock"}
    if action_class == "managed-skill-file":
        required_types.add("native-managed-skill-canary")
    observed_types = {str(item["evidence_type"]) for item in evidence_items}
    if not required_types <= observed_types:
        return False
    target_realpaths = {str(item["target_realpath"]) for item in evidence_items}
    managed_realpaths = {str(item["managed_skills_realpath"]) for item in evidence_items}
    if len(target_realpaths) != 1 or len(managed_realpaths) != 1:
        return False
    return all(
        item.get("authorizes_real_writes") is True and item.get("approval_eligible") is True
        for item in evidence_items
    )


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_evidence_payload(evidence: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in evidence.items()
        if key != "evidence_id"
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def stable_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
