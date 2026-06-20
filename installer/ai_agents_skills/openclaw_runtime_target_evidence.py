"""OpenClaw runtime/support target evidence (v3) — issues 8 & 10.

Parallel to ``openclaw_target_evidence`` (the v2 skill-file model, left untouched).
Adds the evidence types the runtime/support surfaces need, a per-surface
authorization predicate, helper-invocation detection from an action list, and an
optional host-key signature layer (E5 hardening).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from .openclaw_target_evidence import (
    AUTHORING_EVIDENCE_SOURCE,
    PATH_STYLES,
    PLATFORMS,
    now_utc,
    stable_digest,
)

SCHEMA_VERSION_V3 = "openclaw.target-evidence.v3"
GENERATOR_VERSION_V3 = "openclaw-target-evidence.phase3.v1"
PHASE_V3 = "phase3-runtime-support-authorizing"

# Reused v2 types + the new runtime/support types.
EVIDENCE_TYPES_V3 = (
    "native-loader",
    "quiescence-lock",
    "neutral-runtime-root",
    "runtime-pre-state",
    "support-file-pre-state",
    "helper-invocation",
    "compatibility-tuple-match",
)

# Required evidence per surface action class (helper-invocation added conditionally).
SURFACE_REQUIRED_EVIDENCE = {
    "managed-support-file": {
        "native-loader",
        "quiescence-lock",
        "compatibility-tuple-match",
        "support-file-pre-state",
    },
    "shared-runtime-file": {
        "native-loader",
        "quiescence-lock",
        "neutral-runtime-root",
        "runtime-pre-state",
        "compatibility-tuple-match",
    },
}

# A runtime/support file is treated as an executable helper (=> helper-invocation
# evidence is required) when its target is an interpreter/script file or 0755.
EXECUTABLE_SUFFIXES = (".sh", ".bash", ".bat", ".ps1", ".py", ".cmd", ".js")


def canonical_runtime_evidence_payload(evidence: dict[str, Any]) -> str:
    """Canonical content for the content-address + signature (excludes the fields
    that are derived from the payload: evidence_id, key_id, signature)."""
    payload = {k: v for k, v in evidence.items() if k not in ("evidence_id", "key_id", "signature")}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def build_runtime_target_evidence(
    *,
    evidence_type: str,
    platform: str,
    path_style: str,
    observed_behavior: str,
    target_realpath: str,
    managed_skills_realpath: str,
    runtime_realpath: str,
    checks: dict[str, Any],
    captured_at: str | None = None,
    openclaw_version: str | None = None,
) -> dict[str, Any]:
    evidence = {
        "schema_version": SCHEMA_VERSION_V3,
        "evidence_id": "target_evidence_pending",
        "generator_version": GENERATOR_VERSION_V3,
        "target": "openclaw",
        "phase": PHASE_V3,
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
        "runtime_realpath": runtime_realpath,
        "checks": checks,
    }
    if openclaw_version:
        evidence["openclaw_version"] = openclaw_version
    evidence["evidence_id"] = f"target_evidence_{stable_digest(canonical_runtime_evidence_payload(evidence))}"
    validate_runtime_target_evidence(evidence)
    return evidence


def validate_runtime_target_evidence(evidence: dict[str, Any]) -> None:
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
        "runtime_realpath",
        "checks",
    }
    if evidence.get("schema_version") != SCHEMA_VERSION_V3:
        raise ValueError("OpenClaw runtime target evidence schema version is not supported")
    missing = sorted(required - set(evidence))
    if missing:
        raise ValueError(f"OpenClaw runtime target evidence is missing required fields: {', '.join(missing)}")
    if evidence["generator_version"] != GENERATOR_VERSION_V3:
        raise ValueError("OpenClaw runtime target evidence generator version is not supported")
    if evidence["target"] != "openclaw":
        raise ValueError("OpenClaw runtime target evidence must target openclaw")
    if evidence["phase"] != PHASE_V3:
        raise ValueError("OpenClaw runtime target evidence phase is not supported")
    if evidence["evidence_type"] not in EVIDENCE_TYPES_V3:
        raise ValueError(f"OpenClaw runtime target evidence type is not supported: {evidence['evidence_type']}")
    if evidence["platform"] not in PLATFORMS:
        raise ValueError(f"OpenClaw runtime target evidence platform is not supported: {evidence['platform']}")
    if evidence["path_style"] not in PATH_STYLES:
        raise ValueError(f"OpenClaw runtime target evidence path_style is not supported: {evidence['path_style']}")
    if evidence["evidence_source"] != AUTHORING_EVIDENCE_SOURCE:
        raise ValueError("OpenClaw authorizing runtime evidence must come from native-probe")
    if evidence["authorizes_real_writes"] is not True or evidence["approval_eligible"] is not True:
        raise ValueError("Phase 3 OpenClaw runtime evidence must authorize real writes and be approval eligible")
    if not isinstance(evidence["limitations"], list) or evidence["limitations"]:
        raise ValueError("OpenClaw authorizing runtime evidence must have an empty limitations list")
    for field in ("observed_behavior", "target_realpath", "managed_skills_realpath", "runtime_realpath"):
        if not str(evidence[field]).strip():
            raise ValueError(f"OpenClaw runtime target evidence {field} is required")
    if not isinstance(evidence["checks"], dict) or not evidence["checks"]:
        raise ValueError("OpenClaw runtime target evidence checks must be a non-empty object")
    expected = f"target_evidence_{stable_digest(canonical_runtime_evidence_payload(evidence))}"
    if evidence["evidence_id"] != expected:
        raise ValueError("OpenClaw runtime target evidence content address does not match evidence_id")


def runtime_action_is_executable(action: dict[str, Any]) -> bool:
    target = str(action.get("target_relpath") or action.get("path") or "")
    if str(action.get("mode")) == "0755":
        return True
    return any(target.endswith(suffix) for suffix in EXECUTABLE_SUFFIXES)


def runtime_actions_require_helper_invocation(actions: list[dict[str, Any]]) -> bool:
    """E7: requires_helper_invocation is computed from the action LIST (per-file
    executable detection), never from a single scalar action_class."""
    return any(runtime_action_is_executable(a) for a in actions)


def runtime_target_evidence_authorizes_real_writes(
    evidence_items: list[dict[str, Any]],
    *,
    action_class: str,
    requires_helper_invocation: bool,
) -> bool:
    if not evidence_items:
        return False
    for item in evidence_items:
        validate_runtime_target_evidence(item)
    required = SURFACE_REQUIRED_EVIDENCE.get(action_class)
    if required is None:
        return False
    required = set(required)
    if requires_helper_invocation:
        required.add("helper-invocation")
    observed = {str(item["evidence_type"]) for item in evidence_items}
    if not required <= observed:
        return False
    # A-cannot-authorize-B: a single bound runtime/target realpath across all items.
    for field in ("target_realpath", "managed_skills_realpath", "runtime_realpath"):
        if len({str(item[field]) for item in evidence_items}) != 1:
            return False
    return all(
        item.get("authorizes_real_writes") is True and item.get("approval_eligible") is True
        for item in evidence_items
    )


# --- Host-key signature layer (E5 hardening) ---------------------------------
def host_key_id(key: bytes) -> str:
    return "hostkey_" + hashlib.sha256(b"aas-openclaw-runtime-evidence\x00" + key).hexdigest()[:16]


def sign_runtime_evidence(evidence: dict[str, Any], *, key: bytes) -> dict[str, Any]:
    """Attach a host-key HMAC signature so a hand-crafted evidence record cannot
    pass a signature-checking apply. The key lives outside the synced tree."""
    validate_runtime_target_evidence(evidence)
    signed = dict(evidence)
    signed.pop("signature", None)
    signed["key_id"] = host_key_id(key)
    mac = hmac.new(key, canonical_runtime_evidence_payload(signed).encode("utf-8"), hashlib.sha256)
    signed["signature"] = mac.hexdigest()
    return signed


def verify_runtime_evidence_signature(evidence: dict[str, Any], *, key: bytes) -> bool:
    signature = evidence.get("signature")
    if not signature or evidence.get("key_id") != host_key_id(key):
        return False
    expected = hmac.new(key, canonical_runtime_evidence_payload(evidence).encode("utf-8"), hashlib.sha256)
    return hmac.compare_digest(str(signature), expected.hexdigest())
