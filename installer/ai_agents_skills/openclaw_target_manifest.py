from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .openclaw_target_gate import GATE_POLICY_VERSION
from .openclaw_target_evidence import (
    SCHEMA_VERSION_V2 as TARGET_EVIDENCE_SCHEMA_VERSION_V2,
    target_evidence_authorizes_real_writes,
    validate_target_evidence,
)
from .openclaw_target_paths import (
    OPENCLAW_REAL_WRITE_ACTION_CLASSES,
    checked_openclaw_target_relative_path,
    checked_skill_slug,
    openclaw_home,
    openclaw_managed_skills_dir,
    openclaw_target_path,
    path_leak_block_reason,
    skill_file_relative_path,
    validate_openclaw_target_home,
)
from .state import artifact_signature, sha256_text


MANIFEST_SCHEMA_VERSION_V1 = "openclaw.target-manifest.v1"
MANIFEST_SCHEMA_VERSION_V2 = "openclaw.target-manifest.v2"
MANIFEST_SCHEMA_VERSION = MANIFEST_SCHEMA_VERSION_V1
GENERATOR_VERSION = "openclaw-target-manifest.phase1.v1"
GENERATOR_VERSION_V2 = "openclaw-target-manifest.phase2.v1"
GATE_POLICY_VERSION_V2 = "openclaw-target-gate.phase2.v1"
PHASE = "phase1-non-authorizing"
PHASE_V2 = "phase2-authorizing"
TARGET_AGENTS = ("openclaw",)
ACTION_CLASSES = ("diagnostic-only", "blocked-real-write")


def load_target_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("OpenClaw target manifest file is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("OpenClaw target manifest file must contain a JSON object")
    validate_target_manifest(manifest)
    return manifest


def build_diagnostic_target_manifest(
    *,
    target_root: Path,
    created_at: str | None = None,
    actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    manifest = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "manifest_id": "target_manifest_pending",
        "generator_version": GENERATOR_VERSION,
        "created_at": created_at or now_utc(),
        "phase": PHASE,
        "target_agent_refs": ["openclaw"],
        "target_realpath": str(target_root.expanduser().resolve(strict=False)),
        "gate_policy_version": GATE_POLICY_VERSION,
        "real_write_status": "blocked",
        "authorizes_real_writes": False,
        "approval_eligible": False,
        "actions": actions or [],
        "approval": {
            "review_status": "diagnostic-only",
        },
    }
    manifest["manifest_id"] = f"target_manifest_{stable_digest(canonical_manifest_payload(manifest))}"
    validate_target_manifest(manifest)
    return manifest


def build_skill_file_target_manifest(
    *,
    root: Path,
    skill: str,
    content: str,
    evidence_items: list[dict[str, Any]],
    action_class: str = "managed-skill-file",
    created_at: str | None = None,
    openclaw_version: str | None = None,
) -> dict[str, Any]:
    checked_skill = checked_skill_slug(skill)
    if action_class not in OPENCLAW_REAL_WRITE_ACTION_CLASSES:
        raise ValueError("OpenClaw target action class is not allowed for real writes")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("OpenClaw target skill content is required")
    encoded = content.encode("utf-8")
    if b"\x00" in encoded:
        raise ValueError("OpenClaw target skill content must be text")
    paths = validate_openclaw_target_home(root)
    relative_path = skill_file_relative_path(checked_skill)
    target_path = openclaw_target_path(root, relative_path, action_class=action_class)
    expected_hash = sha256_text(content)
    pre_state = artifact_signature(target_path)
    if pre_state.get("exists") is True:
        if pre_state.get("kind") != "file" or pre_state.get("hash") != expected_hash:
            raise ValueError("OpenClaw target skill file already exists and is not this managed content")
        operation = "no-op"
    else:
        operation = "create"
    action_seed = json.dumps(
        {
            "action_class": action_class,
            "expected_hash": expected_hash,
            "relative_path": relative_path,
            "skill": checked_skill,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    action_id = f"target_action_{stable_digest(action_seed)}"
    action = {
        "action_id": action_id,
        "action_class": action_class,
        "operation": operation,
        "target": {
            "target_agent": "openclaw",
            "relative_path": relative_path,
            "containment_policy": "must-stay-under-openclaw-home",
        },
        "diagnostic_only": False,
        "approval_eligible": True,
        "writes_real_path": True,
        "artifact_type": "skill-file",
        "install_mode": "copy",
        "skill": checked_skill,
        "content": content,
        "expected_hash": expected_hash,
        "pre_state": pre_state,
        "rollback_policy": "delete-only-if-unchanged",
        "post_apply_native_check": True,
    }
    manifest = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION_V2,
        "manifest_id": "target_manifest_pending",
        "generator_version": GENERATOR_VERSION_V2,
        "created_at": created_at or now_utc(),
        "phase": PHASE_V2,
        "target_agent_refs": ["openclaw"],
        "home_root_realpath": str(root.expanduser().resolve(strict=False)),
        "target_realpath": paths["home_realpath"],
        "managed_skills_realpath": paths["managed_skills_realpath"],
        "gate_policy_version": GATE_POLICY_VERSION_V2,
        "real_write_status": "approval-required",
        "authorizes_real_writes": True,
        "approval_eligible": True,
        "target_evidence_schema_version": TARGET_EVIDENCE_SCHEMA_VERSION_V2,
        "target_evidence": evidence_items,
        "actions": [action],
        "approval": {
            "review_status": "unreviewed",
        },
    }
    if openclaw_version:
        manifest["openclaw_version"] = openclaw_version
    manifest["manifest_id"] = f"target_manifest_{stable_digest(canonical_manifest_payload(manifest))}"
    validate_target_manifest(manifest)
    return manifest


def approve_target_manifest(
    manifest: dict[str, Any],
    *,
    reviewer: str,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    validate_target_manifest(manifest)
    if manifest.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION_V2:
        raise ValueError("Only OpenClaw target manifest v2 can be approved for real writes")
    if not reviewer.strip():
        raise ValueError("reviewer is required")
    approved = json.loads(json.dumps(manifest))
    approved["approval"] = {
        "review_status": "approved",
        "reviewer": reviewer,
        "reviewed_at": reviewed_at or now_utc(),
        "approval_hash": approved["manifest_id"],
    }
    validate_target_manifest(approved, require_approved=True)
    return approved


def validate_target_manifest(manifest: dict[str, Any], *, require_approved: bool = False) -> None:
    schema_version = manifest.get("manifest_schema_version")
    if schema_version == MANIFEST_SCHEMA_VERSION_V1:
        validate_target_manifest_v1(manifest, require_approved=require_approved)
        return
    if schema_version == MANIFEST_SCHEMA_VERSION_V2:
        validate_target_manifest_v2(manifest, require_approved=require_approved)
        return
    if schema_version != MANIFEST_SCHEMA_VERSION:
        if schema_version == "openclaw.apply-manifest.v1":
            raise ValueError("OpenClaw source/import apply manifest cannot authorize target writes")
        raise ValueError("OpenClaw target manifest schema version is not supported")


def validate_target_manifest_v1(manifest: dict[str, Any], *, require_approved: bool = False) -> None:
    required = {
        "manifest_schema_version",
        "manifest_id",
        "generator_version",
        "created_at",
        "phase",
        "target_agent_refs",
        "target_realpath",
        "gate_policy_version",
        "real_write_status",
        "authorizes_real_writes",
        "approval_eligible",
        "actions",
        "approval",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ValueError(f"OpenClaw target manifest is missing required fields: {', '.join(missing)}")
    if require_approved:
        raise ValueError("Phase 1 OpenClaw target manifests cannot be approved for real writes")
    if manifest["phase"] != PHASE:
        raise ValueError("OpenClaw target manifest phase is not supported")
    if manifest["gate_policy_version"] != GATE_POLICY_VERSION:
        raise ValueError("OpenClaw target manifest gate policy is not supported")
    if manifest["target_agent_refs"] != ["openclaw"]:
        raise ValueError("OpenClaw target manifest target_agent_refs must be ['openclaw']")
    if manifest["real_write_status"] != "blocked":
        raise ValueError("Phase 1 OpenClaw target manifest real_write_status must be blocked")
    if manifest["authorizes_real_writes"] is not False:
        raise ValueError("Phase 1 OpenClaw target manifest cannot authorize real writes")
    if manifest["approval_eligible"] is not False:
        raise ValueError("Phase 1 OpenClaw target manifest cannot be approval eligible")
    if not isinstance(manifest["actions"], list):
        raise ValueError("OpenClaw target manifest actions must be a list")
    for action in manifest["actions"]:
        validate_target_manifest_action(action)
    approval = manifest.get("approval")
    if not isinstance(approval, dict):
        raise ValueError("OpenClaw target manifest approval must be an object")
    if approval.get("review_status") not in {"diagnostic-only", "unreviewed"}:
        raise ValueError("Phase 1 OpenClaw target manifest approval cannot approve real writes")
    if "approval_hash" in approval:
        raise ValueError("Phase 1 OpenClaw target manifest cannot carry approval_hash")
    expected_manifest_id = f"target_manifest_{stable_digest(canonical_manifest_payload(manifest))}"
    if manifest["manifest_id"] != expected_manifest_id:
        raise ValueError("OpenClaw target manifest content address does not match manifest_id")


def validate_target_manifest_action(action: dict[str, Any]) -> None:
    required = {
        "action_id",
        "action_class",
        "target",
        "diagnostic_only",
        "approval_eligible",
        "writes_real_path",
    }
    missing = sorted(required - set(action))
    if missing:
        raise ValueError(f"OpenClaw target manifest action is missing required fields: {', '.join(missing)}")
    if action["action_class"] not in ACTION_CLASSES:
        raise ValueError("OpenClaw target manifest action class is not supported in Phase 1")
    if action["diagnostic_only"] is not True:
        raise ValueError("Phase 1 OpenClaw target manifest actions must be diagnostic only")
    if action["approval_eligible"] is not False:
        raise ValueError("Phase 1 OpenClaw target manifest actions cannot be approval eligible")
    if action["writes_real_path"] is not False:
        raise ValueError("Phase 1 OpenClaw target manifest actions cannot write real paths")
    target = action["target"]
    if not isinstance(target, dict):
        raise ValueError("OpenClaw target manifest action target must be an object")
    if target.get("target_agent") != "openclaw":
        raise ValueError("OpenClaw target manifest actions must target openclaw")
    relative_path = target.get("relative_path")
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("OpenClaw target manifest action relative_path is required")
    if relative_path.startswith(("/", "\\")) or ".." in Path(relative_path).parts:
        raise ValueError("OpenClaw target manifest action relative_path must be contained")


def validate_target_manifest_v2(manifest: dict[str, Any], *, require_approved: bool = False) -> None:
    required = {
        "manifest_schema_version",
        "manifest_id",
        "generator_version",
        "created_at",
        "phase",
        "target_agent_refs",
        "home_root_realpath",
        "target_realpath",
        "managed_skills_realpath",
        "gate_policy_version",
        "real_write_status",
        "authorizes_real_writes",
        "approval_eligible",
        "target_evidence_schema_version",
        "target_evidence",
        "actions",
        "approval",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ValueError(f"OpenClaw target manifest is missing required fields: {', '.join(missing)}")
    if manifest["generator_version"] != GENERATOR_VERSION_V2:
        raise ValueError("OpenClaw target manifest generator version is not supported")
    if manifest["phase"] != PHASE_V2:
        raise ValueError("OpenClaw target manifest phase is not supported")
    if manifest["gate_policy_version"] != GATE_POLICY_VERSION_V2:
        raise ValueError("OpenClaw target manifest gate policy is not supported")
    if manifest["target_agent_refs"] != ["openclaw"]:
        raise ValueError("OpenClaw target manifest target_agent_refs must be ['openclaw']")
    if manifest["real_write_status"] != "approval-required":
        raise ValueError("OpenClaw target manifest real_write_status must be approval-required")
    if manifest["authorizes_real_writes"] is not True:
        raise ValueError("OpenClaw target manifest v2 must authorize real writes")
    if manifest["approval_eligible"] is not True:
        raise ValueError("OpenClaw target manifest v2 must be approval eligible")
    if manifest["target_evidence_schema_version"] != TARGET_EVIDENCE_SCHEMA_VERSION_V2:
        raise ValueError("OpenClaw target manifest evidence schema version is not supported")
    if not isinstance(manifest["target_evidence"], list):
        raise ValueError("OpenClaw target manifest target_evidence must be a list")
    for evidence in manifest["target_evidence"]:
        validate_target_evidence(evidence)
    if not isinstance(manifest["actions"], list) or not manifest["actions"]:
        raise ValueError("OpenClaw target manifest actions must be a non-empty list")
    action_ids = [action.get("action_id") for action in manifest["actions"] if isinstance(action, dict)]
    if len(action_ids) != len(manifest["actions"]):
        raise ValueError("OpenClaw target manifest action entries must be objects with action_id")
    if len(action_ids) != len(set(action_ids)):
        raise ValueError("OpenClaw target manifest action IDs must be unique")
    for action in manifest["actions"]:
        validate_target_manifest_action_v2(action, manifest)
    action_classes = {action["action_class"] for action in manifest["actions"]}
    if len(action_classes) != 1:
        raise ValueError("OpenClaw target manifest cannot mix action classes")
    action_class = next(iter(action_classes))
    if not target_evidence_authorizes_real_writes(manifest["target_evidence"], action_class=action_class):
        raise ValueError("OpenClaw target evidence does not authorize manifest action class")
    evidence_target_realpaths = {str(item["target_realpath"]) for item in manifest["target_evidence"]}
    evidence_skills_realpaths = {str(item["managed_skills_realpath"]) for item in manifest["target_evidence"]}
    if evidence_target_realpaths != {str(manifest["target_realpath"])}:
        raise ValueError("OpenClaw target evidence target realpath does not match manifest")
    if evidence_skills_realpaths != {str(manifest["managed_skills_realpath"])}:
        raise ValueError("OpenClaw target evidence managed skills realpath does not match manifest")
    approval = manifest.get("approval")
    if not isinstance(approval, dict):
        raise ValueError("OpenClaw target manifest approval must be an object")
    review_status = approval.get("review_status")
    if review_status not in {"unreviewed", "approved", "rejected"}:
        raise ValueError("OpenClaw target manifest approval status is invalid")
    if require_approved:
        if review_status != "approved":
            raise ValueError("OpenClaw target manifest must be approved before apply")
        if approval.get("approval_hash") != manifest["manifest_id"]:
            raise ValueError("OpenClaw target manifest approval hash does not match manifest_id")
    expected_manifest_id = f"target_manifest_{stable_digest(canonical_manifest_payload(manifest))}"
    if manifest["manifest_id"] != expected_manifest_id:
        raise ValueError("OpenClaw target manifest content address does not match manifest_id")


def validate_target_manifest_action_v2(action: dict[str, Any], manifest: dict[str, Any]) -> None:
    required = {
        "action_id",
        "action_class",
        "operation",
        "target",
        "diagnostic_only",
        "approval_eligible",
        "writes_real_path",
        "artifact_type",
        "install_mode",
        "skill",
        "content",
        "expected_hash",
        "pre_state",
        "rollback_policy",
        "post_apply_native_check",
    }
    missing = sorted(required - set(action))
    if missing:
        raise ValueError(f"OpenClaw target manifest action is missing required fields: {', '.join(missing)}")
    if action["action_class"] not in OPENCLAW_REAL_WRITE_ACTION_CLASSES:
        raise ValueError("OpenClaw target manifest action class is not allowed for real writes")
    if action["operation"] not in {"create", "no-op"}:
        raise ValueError("OpenClaw target manifest action operation is not supported")
    if action["diagnostic_only"] is not False:
        raise ValueError("OpenClaw target manifest v2 actions must not be diagnostic only")
    if action["approval_eligible"] is not True:
        raise ValueError("OpenClaw target manifest v2 actions must be approval eligible")
    if action["writes_real_path"] is not True:
        raise ValueError("OpenClaw target manifest v2 actions must write real paths")
    if action["artifact_type"] != "skill-file":
        raise ValueError("OpenClaw target manifest v2 only supports skill-file actions")
    if action["install_mode"] != "copy":
        raise ValueError("OpenClaw target manifest v2 only supports copy mode")
    if action["rollback_policy"] != "delete-only-if-unchanged":
        raise ValueError("OpenClaw target manifest rollback policy is not supported")
    if action["post_apply_native_check"] is not True:
        raise ValueError("OpenClaw target manifest actions require post-apply native checks")
    checked_skill = checked_skill_slug(str(action["skill"]))
    target = action["target"]
    if not isinstance(target, dict):
        raise ValueError("OpenClaw target manifest action target must be an object")
    if target.get("target_agent") != "openclaw":
        raise ValueError("OpenClaw target manifest actions must target openclaw")
    if target.get("containment_policy") != "must-stay-under-openclaw-home":
        raise ValueError("OpenClaw target manifest action must require OpenClaw containment")
    relative_path = checked_openclaw_target_relative_path(
        target.get("relative_path"),
        action_class=action["action_class"],
    )
    if relative_path != skill_file_relative_path(checked_skill):
        raise ValueError("OpenClaw target manifest skill does not match target path")
    content = action["content"]
    if "Managed by ai-agents-skills" not in content or "Generated target: openclaw" not in content:
        raise ValueError("OpenClaw target manifest action content must be managed OpenClaw skill content")
    leak = path_leak_block_reason(content)
    if leak is not None:
        raise ValueError(f"OpenClaw target manifest action content leaks machine-specific paths: {leak}")
    if sha256_text(action["content"]) != action["expected_hash"]:
        raise ValueError("OpenClaw target manifest action content hash does not match expected_hash")
    if not isinstance(action["pre_state"], dict):
        raise ValueError("OpenClaw target manifest action pre_state must be an object")
    if str(manifest.get("target_realpath", "")) != str(openclaw_home(Path(manifest["home_root_realpath"])).resolve(strict=False)):
        raise ValueError("OpenClaw target manifest target_realpath does not match home root")
    if str(manifest.get("managed_skills_realpath", "")) != str(openclaw_managed_skills_dir(Path(manifest["home_root_realpath"])).resolve(strict=False)):
        raise ValueError("OpenClaw target manifest managed_skills_realpath does not match home root")


def target_manifest_authorizes_real_writes(manifest: dict[str, Any]) -> bool:
    validate_target_manifest(manifest)
    if manifest.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION_V2:
        return False
    if manifest.get("approval", {}).get("review_status") != "approved":
        return False
    validate_target_manifest(manifest, require_approved=True)
    return True


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_manifest_payload(manifest: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in manifest.items()
        if key not in {"manifest_id", "approval"}
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def stable_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
