"""OpenClaw runtime/support target manifest + authorization (P6 integration).

Ties together the phase modules into the decision that replaces the by-name reject:
  - P1 path_leak_scan + neutral_runtime_root_block_reason
  - P2 evidence predicate + helper-invocation-from-action-list
  - P4 classifier + S3/S4 routing
  - P0 per-file integrity hashes pinned into the content-addressed manifest

A runtime install yields a content-addressed manifest only when the evidence
authorizes the surface AND the neutral SKILL.md is leak-free; otherwise it
fail-closes with a structured reason (never a bare write).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .openclaw_runtime_target_classify import build_support_file_metadata, support_file_routing
from .openclaw_runtime_target_evidence import (
    runtime_actions_require_helper_invocation,
    runtime_target_evidence_authorizes_real_writes,
    validate_runtime_target_evidence,
)
from .openclaw_target_evidence import now_utc, stable_digest
from .openclaw_target_paths import path_leak_scan

MANIFEST_SCHEMA_VERSION_V3 = "openclaw.target-manifest.v3"
GENERATOR_VERSION_V3 = "openclaw-target-manifest.phase3.v1"
PHASE_V3 = "phase3-runtime-support-authorizing"
RUNTIME_ACTION_CLASSES = ("managed-support-file", "shared-runtime-file")


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def classify_runtime_files(runtime_files: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Classify each runtime file (P4) into schema records + S3/S4/skip routing,
    binding the approved per-file source hash (P0 integrity). Fail-closed: an
    unclassified file raises (via the classifier)."""
    doc = build_support_file_metadata(
        "runtime",
        [
            {
                "relative_path": f["relative_path"],
                "mode": str(f.get("mode", "0644")),
                "file_type": f.get("file_type", "text"),
                "has_shebang": f.get("has_shebang", False),
            }
            for f in runtime_files
        ],
    )
    by_path = {f["relative_path"]: f for f in runtime_files}
    records = []
    routing: dict[str, str] = {}
    for rec in doc["files"]:
        rec = dict(rec)
        src = by_path.get(rec["relative_path"], {})
        rec["source_sha256"] = src.get("source_sha256")  # P0 pinned integrity hash
        records.append(rec)
        routing[rec["relative_path"]] = support_file_routing(rec)
    return records, routing


def openclaw_runtime_content_id(
    *, source_commit: str, skill: str, neutral_skill_md: str, runtime_files: list[dict[str, Any]]
) -> str:
    """Portable, machine-INDEPENDENT content id (P5/multi-machine): same source +
    selection => same content_id across machines (no path/HOME inputs)."""
    payload = {
        "source_commit": source_commit,
        "skill": skill,
        "skill_md_sha256": _sha256_text(neutral_skill_md),
        "files": sorted((f["relative_path"], f.get("source_sha256") or "") for f in runtime_files),
    }
    return "content_" + stable_digest(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def openclaw_runtime_authorization_reason(
    *,
    action_class: str,
    neutral_skill_md: str,
    runtime_files: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
) -> str | None:
    """Return None if the surface is authorized, else a structured fail-closed reason.

    This is the decision that the by-name reject becomes: with satisfying evidence
    and leak-free neutral content, the runtime/support surface is allowed; otherwise
    it stays blocked."""
    if action_class not in RUNTIME_ACTION_CLASSES:
        return f"unknown OpenClaw runtime action class: {action_class}"
    leaks = path_leak_scan(neutral_skill_md)
    if leaks:
        return f"neutral SKILL.md leaks machine-specific paths: {leaks}"
    # NOTE: the neutral runtime root is validated at PROBE time (producing the
    # neutral-runtime-root evidence the predicate below requires); the manifest
    # builder binds the realpath but does not re-stat it (it may be a remote/peer
    # path, and re-statting would break machine-B applying machine-A content).
    requires_helper = runtime_actions_require_helper_invocation(
        [{"target_relpath": f["relative_path"], "mode": str(f.get("mode", "0644"))} for f in runtime_files]
    )
    if not runtime_target_evidence_authorizes_real_writes(
        evidence_items, action_class=action_class, requires_helper_invocation=requires_helper
    ):
        return "runtime target evidence does not authorize this surface (missing/invalid evidence)"
    return None


def build_openclaw_runtime_target_manifest(
    *,
    skill: str,
    action_class: str,
    neutral_skill_md: str,
    runtime_files: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    runtime_realpath: str,
    target_realpath: str,
    managed_skills_realpath: str,
    source_commit: str,
    created_at: str,
) -> dict[str, Any]:
    """Build a content-addressed runtime/support manifest. Fail-closed: raises if the
    surface is not authorized."""
    reason = openclaw_runtime_authorization_reason(
        action_class=action_class,
        neutral_skill_md=neutral_skill_md,
        runtime_files=runtime_files,
        evidence_items=evidence_items,
    )
    if reason is not None:
        raise ValueError(f"OpenClaw runtime target not authorized: {reason}")
    for item in evidence_items:
        validate_runtime_target_evidence(item)
    records, routing = classify_runtime_files(runtime_files)
    content_id = openclaw_runtime_content_id(
        source_commit=source_commit, skill=skill, neutral_skill_md=neutral_skill_md, runtime_files=runtime_files
    )
    requires_helper = runtime_actions_require_helper_invocation(
        [{"target_relpath": f["relative_path"], "mode": str(f.get("mode", "0644"))} for f in runtime_files]
    )
    manifest = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION_V3,
        "manifest_id": "target_manifest_pending",
        "generator_version": GENERATOR_VERSION_V3,
        "phase": PHASE_V3,
        "created_at": created_at,
        "target_agent_refs": ["openclaw"],
        "skill": skill,
        "action_class": action_class,
        "content_id": content_id,
        "source_commit": source_commit,
        "neutral_skill_md": neutral_skill_md,
        "neutral_skill_md_sha256": _sha256_text(neutral_skill_md),
        "files": records,
        "routing": routing,
        "requires_helper_invocation": requires_helper,
        "runtime_realpath": runtime_realpath,
        "target_realpath": target_realpath,
        "managed_skills_realpath": managed_skills_realpath,
        "target_evidence": evidence_items,
        "real_write_status": "approval-required",
        "approval": {"review_status": "unreviewed"},
    }
    # manifest_id is machine/path-bound (includes the realpaths + content_id).
    seed = json.dumps(
        {
            "content_id": content_id,
            "action_class": action_class,
            "runtime_realpath": runtime_realpath,
            "target_realpath": target_realpath,
            "managed_skills_realpath": managed_skills_realpath,
            "evidence_ids": sorted(e.get("evidence_id", "") for e in evidence_items),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest["manifest_id"] = "target_manifest_" + stable_digest(seed)
    return manifest


def validate_runtime_target_manifest(manifest: dict[str, Any], *, require_approved: bool = False) -> None:
    if manifest.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION_V3:
        raise ValueError("OpenClaw runtime target manifest schema version is not supported")
    required = {
        "manifest_id", "content_id", "skill", "action_class", "neutral_skill_md", "files",
        "routing", "runtime_realpath", "target_realpath", "managed_skills_realpath", "target_evidence", "approval",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ValueError(f"OpenClaw runtime target manifest is missing fields: {', '.join(missing)}")
    if manifest["action_class"] not in RUNTIME_ACTION_CLASSES:
        raise ValueError("OpenClaw runtime target manifest action class is not supported")
    # Defense in depth: re-derive authorization from the stored manifest content.
    reason = openclaw_runtime_authorization_reason(
        action_class=manifest["action_class"],
        neutral_skill_md=manifest["neutral_skill_md"],
        runtime_files=[
            {"relative_path": r["relative_path"], "mode": r.get("mode_policy", "0644")} for r in manifest["files"]
        ],
        evidence_items=manifest["target_evidence"],
    )
    if reason is not None:
        raise ValueError(f"OpenClaw runtime target manifest is not authorized: {reason}")
    approval = manifest.get("approval")
    if not isinstance(approval, dict):
        raise ValueError("OpenClaw runtime target manifest approval must be an object")
    if require_approved:
        if approval.get("review_status") != "approved":
            raise ValueError("OpenClaw runtime target manifest must be approved before apply")
        if approval.get("approval_hash") != manifest["manifest_id"]:
            raise ValueError("OpenClaw runtime target manifest approval hash does not match manifest_id")


def approve_runtime_target_manifest(
    manifest: dict[str, Any], *, reviewer: str, reviewed_at: str | None = None
) -> dict[str, Any]:
    validate_runtime_target_manifest(manifest)
    if not reviewer.strip():
        raise ValueError("reviewer is required")
    approved = json.loads(json.dumps(manifest))
    approved["approval"] = {
        "review_status": "approved",
        "reviewer": reviewer,
        "reviewed_at": reviewed_at or now_utc(),
        "approval_hash": approved["manifest_id"],
    }
    validate_runtime_target_manifest(approved, require_approved=True)
    return approved


def load_runtime_target_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("OpenClaw runtime target manifest file is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("OpenClaw runtime target manifest file must contain a JSON object")
    validate_runtime_target_manifest(manifest)
    return manifest
