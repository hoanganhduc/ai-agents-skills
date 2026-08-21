from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


TASK_SCHEMA_VERSION = "cross-agent-delegation.task.v1"
RESULT_SCHEMA_VERSION = "cross-agent-delegation.result.v1"
PROFILE_VERSION = "v1"

TASK_FIELDS = {
    "schema_version",
    "packet_id",
    "created_at",
    "created_by",
    "intended_recipient",
    "adapter_spec_id",
    "recipient_profile",
    "recipient_capability_snapshot",
    "intent",
    "requested_actions",
    "side_effects",
    "success_criteria",
    "constraints",
    "provenance",
    "input_refs",
    "artifact_refs",
    "scope_constraints",
    "out_of_scope",
    "context_policy",
    "confirmation_requirement",
    "expected_output",
    "evidence_requirements",
    "failure_policy",
    "audit_notes",
}
RESULT_FIELDS = {
    "schema_version",
    "result_id",
    "task_packet_id",
    "task_schema_version",
    "intended_recipient",
    "adapter_spec_id",
    "recipient_profile",
    "produced_at",
    "produced_by",
    "provenance",
    "status",
    "summary",
    "coverage_scope",
    "findings",
    "evidence",
    "artifacts",
    "limitations",
    "warnings",
    "errors",
    "parent_action_request",
    "next_step",
}
REF_FIELDS = {"ref_id", "kind", "source", "sensitivity", "access_note"}
SIDE_EFFECT_FIELDS = {"writes_files", "external_service_posts", "network_calls", "subprocesses"}
PROFILE_FIELDS = {"profile_id", "profile_version", "execution_status"}
CONTEXT_FIELDS = {
    "forward_raw_chat",
    "forward_system_instructions",
    "summary_context_refs",
    "context_refs_to_include",
    "context_refs_to_exclude",
}
FINDING_FIELDS = {
    "finding_id",
    "severity",
    "claim_or_object_ref",
    "evidence_refs",
    "confidence",
    "validation_status",
    "rationale",
    "recommended_parent_action",
}
EVIDENCE_REQUIRED_FIELDS = {"evidence_id", "ref_id", "kind", "quote_or_summary", "status"}
EVIDENCE_FIELDS = EVIDENCE_REQUIRED_FIELDS | {"evidence_disposition", "disposition_rationale"}
ARTIFACT_FIELDS = {"artifact_id", "kind", "ref_id", "description"}
DIAGNOSTIC_FIELDS = {"code", "message", "ref_id"}
PARENT_ACTION_FIELDS = {"requested_action", "target_refs", "side_effects", "reversible", "reason"}

CONFIRMATION_REQUIREMENTS = {"parent_decides_outside_packet", "parent_confirmation_required"}
FAILURE_POLICIES = {"block", "partial_allowed", "ask_parent"}
RESULT_STATUSES = {"completed", "partial", "blocked", "failed"}
RESULT_NEXT_STEPS = {"parent_decides", "revise_packet", "discard"}
EVIDENCE_DISPOSITIONS = {"supports_finding", "contradicts_finding", "context_only", "limited", "unchecked"}

FORBIDDEN_KEYS = {
    "confirmed_by_parent",
    "execute",
    "execution_target",
    "execution_targets",
    "skip_confirmation",
    "approval_receipt",
    "approval_receipts",
    "command",
    "commands",
    "args",
    "cwd",
    "env",
    "environment_variables",
    "provider_config",
    "provider_configs",
    "model_config",
    "model_configs",
    "queue",
    "queues",
    "ledger",
    "session_id",
    "session_ids",
    "resume_token",
    "resume_tokens",
    "participant_probe_status",
    "probe_ref",
    "probe_source_ref",
    "parent_acceptance",
    "accepted_by_parent",
}
FORBIDDEN_RUNTIME_KEYS = {
    "budget_envelope",
    "runtime_budget",
    "budget_owner",
    "max_depth",
    "max_hops",
    "max_tokens",
    "max_usd",
    "budget_spent",
    "spent_tokens",
    "spent_usd",
    "depth_used",
    "hops_used",
}
FORBIDDEN_RUNTIME_KEY_PATTERNS = [
    re.compile(pattern)
    for pattern in (r"^max_.*", r"^spent_.*", r"^budget_.*", r"^depth_.*", r"^hops_.*")
]
FORBIDDEN_MODEL_KEYS = {
    "resolved_model",
    "resolved_thinking",
    "model_policy_source",
    "resolved_at",
    "policy_ref",
    "model_policy",
    "model",
    "provider",
    "reasoning",
    "thinking",
    "api_base",
    "session_id",
}
FORBIDDEN_MODEL_KEY_PATTERNS = [
    re.compile(pattern)
    for pattern in (r"^resolved_.*", r"^model_.*", r"^provider_.*", r".*session.*")
]
FORBIDDEN_SECRET_KEYS = {
    "secret",
    "secrets",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "password",
    "credential",
    "credentials",
    "private_key",
    "ssh_key",
}
FORBIDDEN_SECRET_KEY_PATTERN = re.compile(
    r"(^|[_-])(api[_-]?key|secret|token|password|credential|private[_-]?key|ssh[_-]?key)([_-]|$)",
    re.I,
)
FORBIDDEN_SECRET_VALUE_PATTERNS = [
    re.compile(pattern, re.I | re.S)
    for pattern in (
        r"\bsk-[A-Za-z0-9]{8,}\b",
        r"\bghp_[A-Za-z0-9]{8,}\b",
        r"\bgithub_pat_[A-Za-z0-9_]{8,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b",
    )
]
BUDGET_CONSTRAINT_PATTERNS = {
    "model_policy": re.compile(r"^model_policy=same_resolved_model; reasoning=parent_required_highest_available$"),
    "max_depth": re.compile(r"^max_depth=([0-9]+)$"),
    "max_hops": re.compile(r"^max_hops=([1-9][0-9]*)$"),
    "max_tokens": re.compile(r"^max_tokens=([1-9][0-9]*)$"),
    "max_usd": re.compile(r"^max_usd=([0-9]+)(\.[0-9]{1,2})?$"),
    "budget_policy_ref": re.compile(
        r"^budget_policy_ref=[A-Za-z][A-Za-z0-9_.-]{0,63}(#[A-Za-z][A-Za-z0-9_.-]{0,63})?$"
    ),
}
BUDGET_CONSTRAINT_PREFIXES = tuple(f"{kind}=" for kind in BUDGET_CONSTRAINT_PATTERNS)
PARENT_POLICY_LIMITS = {
    "max_depth": 1,
    "max_hops": 4,
    "max_tokens": 100000,
    "max_usd": Decimal("100.00"),
}


RAW_TARGET_PATTERN = re.compile(
    r"""
    ^\s*(?:
        [/~]                      # POSIX absolute path, or a home-relative one
      | \\\\ | //              # UNC share
      | [A-Za-z]:[\\/]           # drive-letter path, either separator
      | [A-Za-z][A-Za-z0-9+.-]*://  # any URL scheme, not just http(s)
    )
    """,
    re.VERBOSE,
)


def is_raw_target(source: Any) -> bool:
    """Whether ``source`` names a location directly instead of referring to one.

    Both packet contracts forbid "raw absolute paths, URLs, service identifiers,
    and command strings" in a reference object, because the parent is the only
    party allowed to resolve a reference into a location.  The check used to be
    spelled as ``http://``/``https://`` plus a regex requiring a literal
    backslash, which between them recognise a Windows drive-letter path and two
    URL schemes -- so on the platform the gates run on, the most ordinary raw
    target of all, ``/etc/shadow``, validated clean, as did ``~/.aws/credentials``,
    ``file:///etc/passwd``, ``ssh://box/etc/shadow`` and a UNC share.
    """

    return bool(RAW_TARGET_PATTERN.match(str(source)))


def load_packet(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("packet JSON must be an object")
    return data


def infer_packet_kind(packet: dict[str, Any]) -> str | None:
    version = packet.get("schema_version")
    if version == TASK_SCHEMA_VERSION:
        return "task"
    if version == RESULT_SCHEMA_VERSION:
        return "result"
    return None


def validate_packet_file(path: Path, *, kind: str | None = None) -> dict[str, Any]:
    packet = load_packet(path)
    actual_kind = kind or infer_packet_kind(packet)
    if actual_kind not in {"task", "result"}:
        errors = ["PACKET_KIND_UNKNOWN"]
    elif actual_kind == "task":
        errors = validate_task(packet)
    else:
        errors = validate_result(packet)
    return {
        "status": "ok" if not errors else "failed",
        "kind": actual_kind or "unknown",
        "path": str(path),
        "errors": errors,
    }


def recursive_forbidden_key_errors(value: Any) -> list[str]:
    errors = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_KEYS:
                errors.append("FORBIDDEN_AUTHORITY_FIELD")
            if key in FORBIDDEN_RUNTIME_KEYS or any(pattern.match(key) for pattern in FORBIDDEN_RUNTIME_KEY_PATTERNS):
                errors.append("FORBIDDEN_RUNTIME_STATE_FIELD")
            if key in FORBIDDEN_MODEL_KEYS or any(pattern.match(key) for pattern in FORBIDDEN_MODEL_KEY_PATTERNS):
                errors.append("FORBIDDEN_MODEL_POLICY_FIELD")
            if key in FORBIDDEN_SECRET_KEYS or FORBIDDEN_SECRET_KEY_PATTERN.search(key):
                errors.append("SECRET_MATERIAL")
            errors.extend(recursive_forbidden_key_errors(item))
    elif isinstance(value, list):
        for item in value:
            errors.extend(recursive_forbidden_key_errors(item))
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in FORBIDDEN_SECRET_VALUE_PATTERNS):
            errors.append("SECRET_MATERIAL")
    return errors


def budget_constraint_kind(value: str) -> str | None:
    if value == "model_policy=same_resolved_model; reasoning=parent_required_highest_available":
        return "model_policy"
    for kind in ("max_depth", "max_hops", "max_tokens", "max_usd", "budget_policy_ref"):
        if value.startswith(f"{kind}="):
            return kind
    if value.startswith(BUDGET_CONSTRAINT_PREFIXES) or value.startswith("parent_budget_owner="):
        return value.split("=", 1)[0]
    return None


def validate_budget_constraint_bounds(kind: str, match: re.Match[str]) -> list[str]:
    if kind in {"model_policy", "budget_policy_ref"}:
        return []
    try:
        if kind == "max_usd":
            value = Decimal(match.group(1) + (match.group(2) or ""))
            if value > PARENT_POLICY_LIMITS[kind]:
                return ["BUDGET_CONSTRAINT_EXCEEDS_PARENT_POLICY"]
            return []
        value = int(match.group(1))
    except (InvalidOperation, ValueError):
        return ["BUDGET_CONSTRAINT_INVALID"]
    if value > PARENT_POLICY_LIMITS[kind]:
        return ["BUDGET_CONSTRAINT_EXCEEDS_PARENT_POLICY"]
    return []


def validate_delegation_constraints(packet: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for field in ("constraints", "scope_constraints"):
        values = packet.get(field, [])
        if not isinstance(values, list):
            # Skipping the field left the parent's caps advisory: the identical
            # budget string in the wrong container reached no bound check and
            # the wrong container was not itself reported, so the packet
            # validated with an empty error list.
            errors.append("FIELD_NOT_ARRAY")
            continue
        for value in values:
            if not isinstance(value, str):
                errors.append("CONSTRAINT_NOT_STRING")
                continue
            kind = budget_constraint_kind(value)
            if kind is None:
                continue
            if kind == "parent_budget_owner":
                errors.append("BUDGET_CONSTRAINT_INVALID")
                continue
            pattern = BUDGET_CONSTRAINT_PATTERNS.get(kind)
            match = pattern.match(value) if pattern is not None else None
            if match is None:
                errors.append("BUDGET_CONSTRAINT_INVALID")
                continue
            if kind in seen:
                errors.append("DUPLICATE_BUDGET_CONSTRAINT")
            seen.add(kind)
            errors.extend(validate_budget_constraint_bounds(kind, match))
    return errors


def validate_closed_object(value: Any, allowed: set[str]) -> list[str]:
    if not isinstance(value, dict):
        return ["FIELD_NOT_OBJECT"]
    return ["UNKNOWN_FIELD"] if set(value) - allowed else []


def validate_required_fields(value: Any, required: set[str]) -> list[str]:
    if not isinstance(value, dict):
        return ["FIELD_NOT_OBJECT"]
    return ["MISSING_REQUIRED_FIELD"] if not required.issubset(value) else []


def validate_enum(value: Any, allowed: set[str], code: str) -> list[str]:
    # ``value in allowed`` hashes ``value``, so a dict or list where an enum
    # belongs raised ``TypeError: unhashable type`` out of validation entirely.
    if not isinstance(value, str):
        return [code]
    return [] if value in allowed else [code]


def packet_array(packet: dict[str, Any], field: str) -> tuple[list[Any], list[str]]:
    """Return ``(items, errors)`` for one array-typed packet field.

    Every caller used to iterate ``packet.get(field, [])`` directly, so a field
    holding ``null`` or a scalar raised ``TypeError`` from inside validation.
    ``cli.py``'s blanket handler turned that into ``{"status": "error", "error":
    "'NoneType' object is not iterable"}``, losing the ``{status, kind, path,
    errors}`` shape every other invalid packet reports and naming no field --
    and since :func:`validate_result` runs on each participant's output, one
    malformed packet aborted the whole dispatch instead of being recorded as an
    invalid result.
    """

    value = packet.get(field, [])
    if not isinstance(value, list):
        return [], ["FIELD_NOT_ARRAY"]
    return value, []


def validate_ref(ref: Any) -> list[str]:
    errors = validate_closed_object(ref, REF_FIELDS)
    errors.extend(validate_required_fields(ref, REF_FIELDS))
    if not isinstance(ref, dict):
        return errors
    if ref.get("kind") == "workspace" or ref.get("source") in {"entire_workspace", "all_files", "raw_chat"}:
        errors.append("OVERBROAD_REF")
    if is_raw_target(ref.get("source", "")):
        errors.append("RAW_TARGET_REF")
    return errors


def validate_profile(packet: dict[str, Any]) -> list[str]:
    errors = []
    profile = packet.get("recipient_profile", {})
    if not isinstance(profile, dict):
        return ["RECIPIENT_PROFILE_INVALID"]
    errors.extend(validate_closed_object(profile, PROFILE_FIELDS))
    errors.extend(validate_required_fields(profile, PROFILE_FIELDS))
    if profile.get("profile_id") != packet.get("adapter_spec_id"):
        errors.append("PROFILE_ID_MISMATCH")
    if profile.get("profile_version") != PROFILE_VERSION:
        errors.append("PROFILE_VERSION_INVALID")
    if profile.get("execution_status") != "reference_only":
        errors.append("PROFILE_NOT_REFERENCE_ONLY")
    return errors


def validate_task(packet: Any) -> list[str]:
    if not isinstance(packet, dict):
        return ["PACKET_NOT_OBJECT"]
    errors = []
    errors.extend(validate_closed_object(packet, TASK_FIELDS))
    errors.extend(validate_required_fields(packet, TASK_FIELDS))
    if packet.get("schema_version") != TASK_SCHEMA_VERSION:
        errors.append("TASK_SCHEMA_VERSION_INVALID")
    errors.extend(validate_profile(packet))
    side_effects = packet.get("side_effects", {})
    errors.extend(validate_closed_object(side_effects, SIDE_EFFECT_FIELDS))
    errors.extend(validate_required_fields(side_effects, SIDE_EFFECT_FIELDS))
    if isinstance(side_effects, dict) and any(side_effects.values()) and packet.get("confirmation_requirement") != "parent_confirmation_required":
        errors.append("SIDE_EFFECT_REQUIRES_CONFIRMATION")
    context_policy = packet.get("context_policy", {})
    errors.extend(validate_closed_object(context_policy, CONTEXT_FIELDS))
    errors.extend(validate_required_fields(context_policy, CONTEXT_FIELDS))
    if isinstance(context_policy, dict) and (
        context_policy.get("forward_raw_chat") or context_policy.get("forward_system_instructions")
    ):
        errors.append("RAW_FORWARDING")
    errors.extend(validate_enum(
        packet.get("confirmation_requirement"),
        CONFIRMATION_REQUIREMENTS,
        "CONFIRMATION_REQUIREMENT_INVALID",
    ))
    errors.extend(validate_enum(packet.get("failure_policy"), FAILURE_POLICIES, "FAILURE_POLICY_INVALID"))
    for field in ("input_refs", "artifact_refs"):
        refs, shape_errors = packet_array(packet, field)
        errors.extend(shape_errors)
        for ref in refs:
            errors.extend(validate_ref(ref))
    if isinstance(context_policy, dict):
        for field in ("summary_context_refs", "context_refs_to_include", "context_refs_to_exclude"):
            refs, shape_errors = packet_array(context_policy, field)
            errors.extend(shape_errors)
            for ref in refs:
                errors.extend(validate_ref(ref))
    expected = packet.get("expected_output", {})
    if isinstance(expected, dict) and "searched and verified" in str(expected.get("forbidden_claim", "")):
        errors.append("UNVERIFIED_SOURCE_CLAIM")
    errors.extend(validate_delegation_constraints(packet))
    errors.extend(recursive_forbidden_key_errors(packet))
    return sorted(set(errors))


def validate_result(packet: Any) -> list[str]:
    if not isinstance(packet, dict):
        return ["PACKET_NOT_OBJECT"]
    errors = []
    errors.extend(validate_closed_object(packet, RESULT_FIELDS))
    errors.extend(validate_required_fields(packet, RESULT_FIELDS))
    if packet.get("schema_version") != RESULT_SCHEMA_VERSION:
        errors.append("RESULT_SCHEMA_VERSION_INVALID")
    if packet.get("task_schema_version") != TASK_SCHEMA_VERSION:
        errors.append("TASK_SCHEMA_VERSION_INVALID")
    errors.extend(validate_profile(packet))
    errors.extend(validate_enum(packet.get("status"), RESULT_STATUSES, "RESULT_STATUS_INVALID"))
    errors.extend(validate_enum(packet.get("next_step"), RESULT_NEXT_STEPS, "RESULT_NEXT_STEP_INVALID"))
    provenance, shape_errors = packet_array(packet, "provenance")
    errors.extend(shape_errors)
    for ref in provenance:
        errors.extend(validate_ref(ref))
    findings, shape_errors = packet_array(packet, "findings")
    errors.extend(shape_errors)
    for finding in findings:
        errors.extend(validate_closed_object(finding, FINDING_FIELDS))
        errors.extend(validate_required_fields(finding, FINDING_FIELDS))
    evidence_items, shape_errors = packet_array(packet, "evidence")
    errors.extend(shape_errors)
    for evidence in evidence_items:
        errors.extend(validate_closed_object(evidence, EVIDENCE_FIELDS))
        errors.extend(validate_required_fields(evidence, EVIDENCE_REQUIRED_FIELDS))
        if isinstance(evidence, dict) and "evidence_disposition" in evidence:
            errors.extend(validate_enum(
                evidence.get("evidence_disposition"),
                EVIDENCE_DISPOSITIONS,
                "EVIDENCE_DISPOSITION_INVALID",
            ))
    artifacts, shape_errors = packet_array(packet, "artifacts")
    errors.extend(shape_errors)
    for artifact in artifacts:
        errors.extend(validate_closed_object(artifact, ARTIFACT_FIELDS))
        errors.extend(validate_required_fields(artifact, ARTIFACT_FIELDS))
    for field in ("warnings", "errors"):
        diagnostics, shape_errors = packet_array(packet, field)
        errors.extend(shape_errors)
        for diagnostic in diagnostics:
            errors.extend(validate_closed_object(diagnostic, DIAGNOSTIC_FIELDS))
            errors.extend(validate_required_fields(diagnostic, DIAGNOSTIC_FIELDS))
    request = packet.get("parent_action_request")
    if request is not None:
        errors.extend(validate_closed_object(request, PARENT_ACTION_FIELDS))
        errors.extend(validate_required_fields(request, PARENT_ACTION_FIELDS))
        if isinstance(request, dict):
            errors.extend(validate_closed_object(request.get("side_effects", {}), SIDE_EFFECT_FIELDS))
            errors.extend(validate_required_fields(request.get("side_effects", {}), SIDE_EFFECT_FIELDS))
            target_refs, shape_errors = packet_array(request, "target_refs")
            errors.extend(shape_errors)
            for ref in target_refs:
                errors.extend(validate_ref(ref))
    errors.extend(recursive_forbidden_key_errors(packet))
    return sorted(set(errors))
