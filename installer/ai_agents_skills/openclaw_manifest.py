from __future__ import annotations

import hashlib
import json
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .openclaw_inventory import DENYLIST_VERSION, REDACTION_VERSION, SCHEMA_VERSION as INVENTORY_SCHEMA_VERSION


MANIFEST_SCHEMA_VERSION = "openclaw.apply-manifest.v1"
GENERATOR_VERSION = "openclaw-manifest.phase2.v1"
TARGET_AGENTS = ("codex", "claude", "deepseek")
PATH_STYLES = ("posix", "windows-drive", "windows-unc", "wsl-posix", "mounted-windows")
DEFAULT_TARGET_AGENTS = TARGET_AGENTS

CRITICAL_DENIALS = {
    "case-or-unicode-collision-denied",
    "hardlink-denied",
    "lstat-failed",
    "max-entries-exceeded",
    "outside-root-path-denied",
    "source-root-absent",
    "source-root-not-directory",
    "source-root-symlink-prefix-denied",
    "special-file-denied",
    "traversal-path-denied",
    "walk-failed",
}

AGENT_HOME_DIR = {
    "codex": ".codex",
    "claude": ".claude",
    "deepseek": ".deepseek",
}

OPERATION_BY_CATEGORY = {
    "skill-metadata": "create-reference-doc",
    "instruction-metadata": "create-reference-doc",
    "alias-metadata": "create-reference-doc",
    "template-metadata": "create-template",
    "hook-metadata-detected-only": "no-op",
    "unknown-count-only": "no-op",
}


def load_inventory(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("inventory file is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("inventory file must contain a JSON object")
    validate_inventory(data)
    return data


def load_manifest(path: Path, *, require_approved: bool = False) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("OpenClaw manifest file is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("OpenClaw manifest file must contain a JSON object")
    validate_manifest(data, require_approved=require_approved)
    return data


def build_manifest(
    inventory: dict[str, Any],
    target_root: Path,
    *,
    target_agents: list[str] | tuple[str, ...] = DEFAULT_TARGET_AGENTS,
    path_style: str = "posix",
    created_at: str | None = None,
) -> dict[str, Any]:
    validate_inventory(inventory)
    agents = validate_target_agents(target_agents)
    if path_style not in PATH_STYLES:
        raise ValueError(f"unsupported target path style: {path_style}")

    root = target_root.expanduser()
    if not root.exists() or not root.is_dir():
        raise ValueError("target root must be an existing directory")
    root_resolved = root.resolve()

    actions = [
        action
        for item in sorted(inventory.get("items", []), key=lambda value: value["item_id"])
        for agent in agents
        for action in [build_action(item, agent, root, root_resolved, path_style)]
    ]
    actions.sort(key=lambda action: action["action_id"])
    manifest = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "manifest_id": "manifest_pending",
        "generator_version": GENERATOR_VERSION,
        "created_at": created_at or now_utc(),
        "source_inventory_id": inventory["inventory_id"],
        "denylist_version": inventory["denylist_version"],
        "redaction_version": inventory["redaction_version"],
        "source_agent_refs": ["openclaw"],
        "target_agent_refs": agents,
        "apply_policy": {
            "canonical_serialization": "json-canonical-sorted-actions",
            "no_recompute": True,
            "fail_closed_on_drift": True,
            "content_addressed": True,
        },
        "actions": actions,
        "approval": {
            "review_status": "unreviewed",
        },
    }
    manifest["manifest_id"] = f"manifest_{stable_digest(canonical_manifest_payload(manifest))}"
    return manifest


def approve_manifest(
    manifest: dict[str, Any],
    *,
    reviewer: str,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    validate_manifest(manifest)
    if not reviewer.strip():
        raise ValueError("reviewer is required")
    approved = dict(manifest)
    approved["approval"] = {
        "review_status": "approved",
        "reviewer": reviewer,
        "reviewed_at": reviewed_at or now_utc(),
        "approval_hash": approved["manifest_id"],
    }
    validate_manifest(approved, require_approved=True)
    return approved


def validate_manifest(manifest: dict[str, Any], *, require_approved: bool = False) -> None:
    required = {
        "manifest_schema_version",
        "manifest_id",
        "generator_version",
        "created_at",
        "source_inventory_id",
        "denylist_version",
        "redaction_version",
        "source_agent_refs",
        "target_agent_refs",
        "apply_policy",
        "actions",
        "approval",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ValueError(f"OpenClaw manifest is missing required fields: {', '.join(missing)}")
    if manifest["manifest_schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ValueError("OpenClaw manifest schema version is not supported")
    expected_manifest_id = f"manifest_{stable_digest(canonical_manifest_payload(manifest))}"
    if manifest["manifest_id"] != expected_manifest_id:
        raise ValueError("OpenClaw manifest content address does not match manifest_id")
    policy = manifest.get("apply_policy")
    if not isinstance(policy, dict):
        raise ValueError("OpenClaw manifest apply_policy must be an object")
    if policy.get("canonical_serialization") != "json-canonical-sorted-actions":
        raise ValueError("OpenClaw manifest canonical serialization is not supported")
    if policy.get("no_recompute") is not True:
        raise ValueError("OpenClaw manifest must forbid recomputing actions")
    if policy.get("fail_closed_on_drift") is not True:
        raise ValueError("OpenClaw manifest must fail closed on drift")
    if policy.get("content_addressed") is not True:
        raise ValueError("OpenClaw manifest must be content-addressed")
    if not isinstance(manifest.get("actions"), list):
        raise ValueError("OpenClaw manifest actions must be a list")
    action_ids = [action.get("action_id") for action in manifest["actions"] if isinstance(action, dict)]
    if len(action_ids) != len(set(action_ids)):
        raise ValueError("OpenClaw manifest action IDs must be unique")
    if len(action_ids) != len(manifest["actions"]):
        raise ValueError("OpenClaw manifest action entries must be objects with action_id")
    sorted_ids = sorted(action_ids)
    if action_ids != sorted_ids:
        raise ValueError("OpenClaw manifest actions must be sorted by action_id")
    for action in manifest["actions"]:
        validate_manifest_action(action)
    approval = manifest.get("approval")
    if not isinstance(approval, dict):
        raise ValueError("OpenClaw manifest approval must be an object")
    review_status = approval.get("review_status")
    if review_status not in {"unreviewed", "approved", "rejected"}:
        raise ValueError("OpenClaw manifest approval status is invalid")
    if require_approved:
        if review_status != "approved":
            raise ValueError("OpenClaw manifest must be approved before apply")
        if approval.get("approval_hash") != manifest["manifest_id"]:
            raise ValueError("OpenClaw manifest approval hash does not match manifest_id")


def validate_manifest_action(action: dict[str, Any]) -> None:
    required = {"action_id", "operation", "target", "precondition", "collision", "backup_strategy", "rollback_refs"}
    missing = sorted(required - set(action))
    if missing:
        raise ValueError(f"OpenClaw manifest action is missing required fields: {', '.join(missing)}")
    target = action["target"]
    if not isinstance(target, dict):
        raise ValueError("OpenClaw manifest action target must be an object")
    if target.get("target_agent") not in TARGET_AGENTS:
        raise ValueError("OpenClaw manifest action target agent is not supported")
    relative_path = target.get("relative_path")
    if not isinstance(relative_path, str) or not relative_path or relative_path.startswith(("/", "\\")):
        raise ValueError("OpenClaw manifest target path must be relative")
    if ".." in Path(relative_path).parts:
        raise ValueError("OpenClaw manifest target path must not contain traversal")
    if target.get("containment_policy") != "must-stay-under-target-root":
        raise ValueError("OpenClaw manifest action must require target containment")


def validate_inventory(inventory: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "inventory_id",
        "source_root",
        "denylist_version",
        "redaction_version",
        "content_read_policy",
        "contains_raw_paths",
        "items",
        "denied_categories",
    }
    missing = sorted(required - set(inventory))
    if missing:
        raise ValueError(f"inventory is missing required fields: {', '.join(missing)}")
    if inventory["schema_version"] != INVENTORY_SCHEMA_VERSION:
        raise ValueError("inventory schema version is not supported")
    if inventory["denylist_version"] != DENYLIST_VERSION:
        raise ValueError("inventory denylist version is not supported")
    if inventory["redaction_version"] != REDACTION_VERSION:
        raise ValueError("inventory redaction version is not supported")
    if inventory["content_read_policy"] != "deny-by-default":
        raise ValueError("inventory content read policy is not safe")
    if inventory["contains_raw_paths"] is not False:
        raise ValueError("inventory contains raw paths")
    source_root = inventory.get("source_root")
    if not isinstance(source_root, dict) or source_root.get("explicit_input") is not True:
        raise ValueError("inventory source root was not explicit")
    if not isinstance(inventory.get("items"), list):
        raise ValueError("inventory items must be a list")
    if not isinstance(inventory.get("denied_categories"), list):
        raise ValueError("inventory denied categories must be a list")
    critical = [
        item.get("category_id")
        for item in inventory["denied_categories"]
        if isinstance(item, dict) and item.get("category_id") in CRITICAL_DENIALS and item.get("count", 0) > 0
    ]
    if critical:
        raise ValueError(f"inventory has critical denial categories: {', '.join(sorted(set(critical)))}")
    for item in inventory["items"]:
        validate_inventory_item(item)


def validate_inventory_item(item: Any) -> None:
    if not isinstance(item, dict):
        raise ValueError("inventory item must be an object")
    for field in ("item_id", "category", "kind", "relative_path_token", "metadata"):
        if field not in item:
            raise ValueError(f"inventory item is missing {field}")
    if item["category"] not in OPERATION_BY_CATEGORY:
        raise ValueError(f"unsupported inventory item category: {item['category']}")
    if item["kind"] == "special-file-denied":
        raise ValueError("inventory contains denied special file item")
    if not str(item["relative_path_token"]).startswith("<OPENCLAW_ROOT>/"):
        raise ValueError("inventory item path token is not root-tokenized")
    metadata = item["metadata"]
    if not isinstance(metadata, dict) or metadata.get("read_policy") != "lstat-only":
        raise ValueError("inventory item metadata must be lstat-only")


def validate_target_agents(target_agents: list[str] | tuple[str, ...]) -> list[str]:
    agents = sorted(dict.fromkeys(target_agents))
    if not agents:
        raise ValueError("at least one target agent is required")
    invalid = sorted(agent for agent in agents if agent not in TARGET_AGENTS)
    if invalid:
        raise ValueError(f"unsupported target agents: {', '.join(invalid)}")
    return agents


def build_action(
    item: dict[str, Any],
    agent: str,
    target_root: Path,
    target_root_resolved: Path,
    path_style: str,
) -> dict[str, Any]:
    operation = OPERATION_BY_CATEGORY[item["category"]]
    relative_path = target_relative_path(agent, item)
    target_path = target_root / relative_path
    precondition = target_precondition(target_path, target_root_resolved)
    collision = collision_policy(operation, precondition)
    if collision["policy"] != "none":
        operation = "no-op"
    action_seed = json.dumps(
        {
            "agent": agent,
            "source_ref": item["item_id"],
            "operation": operation,
            "relative_path": relative_path,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    action_id = f"action_{stable_digest(action_seed)}"
    return {
        "action_id": action_id,
        "operation": operation,
        "source_ref": item["item_id"],
        "target": {
            "root_id": "target_explicit",
            "target_agent": agent,
            "path_style": path_style,
            "relative_path": relative_path,
            "containment_policy": "must-stay-under-target-root",
        },
        "precondition": precondition,
        "collision": collision,
        "backup_strategy": {
            "required": False,
            "location_policy": "not-needed",
        },
        "rollback_refs": [f"delete-created:{action_id}"] if operation != "no-op" else [],
    }


def target_relative_path(agent: str, item: dict[str, Any]) -> str:
    category = item["category"].replace("_", "-")
    return f"{AGENT_HOME_DIR[agent]}/openclaw-review/{category}/{item['item_id']}.md"


def target_precondition(path: Path, target_root_resolved: Path) -> dict[str, Any]:
    try:
        path.resolve(strict=False).relative_to(target_root_resolved)
    except ValueError as exc:
        raise ValueError("target path escapes target root") from exc
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {
            "exists": False,
            "kind": "absent",
            "owner_policy": "expected-absent",
            "absence_reason": "missing-at-dry-run",
        }
    except OSError:
        return {
            "exists": True,
            "kind": "special-file",
            "owner_policy": "unmanaged-preserve",
            "absence_reason": "target-lstat-failed",
        }

    if stat.S_ISREG(metadata.st_mode):
        kind = "regular-file"
    elif stat.S_ISDIR(metadata.st_mode):
        kind = "directory"
    elif stat.S_ISLNK(metadata.st_mode):
        kind = "symlink"
    else:
        kind = "special-file"
    return {
        "exists": True,
        "kind": kind,
        "owner_policy": "unmanaged-preserve",
    }


def collision_policy(operation: str, precondition: dict[str, Any]) -> dict[str, Any]:
    if operation == "no-op":
        return {"policy": "skip-report"}
    if precondition["exists"]:
        return {
            "collision_id": f"collision_{stable_digest(json.dumps(precondition, sort_keys=True))}",
            "policy": "skip-report",
        }
    return {"policy": "none"}


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
