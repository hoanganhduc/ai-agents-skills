from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .capabilities import looks_like_real_system_root, normalized_path_within, resolved_path_within
from .openclaw_manifest import load_manifest, target_precondition, validate_manifest
from .state import existing_contained_parents, now_run_id, preflight_state_path, sha256_text, write_text_atomic


OPENCLAW_STATE_VERSION = 1
SUPPORTED_WRITE_OPERATIONS = {"create-reference-doc", "create-template", "create-file"}


def apply_manifest_file(manifest_path: Path, target_root: Path, *, dry_run: bool = True) -> dict[str, Any]:
    manifest = load_manifest(manifest_path, require_approved=not dry_run)
    return apply_manifest(manifest, target_root, dry_run=dry_run)


def apply_manifest(manifest: dict[str, Any], target_root: Path, *, dry_run: bool = True) -> dict[str, Any]:
    validate_manifest(manifest, require_approved=not dry_run)
    root = checked_fake_target_root(target_root)
    planned = [plan_apply_action(root, manifest, action) for action in manifest["actions"]]
    if dry_run:
        return {
            "dry_run": True,
            "manifest_id": manifest["manifest_id"],
            "actions": planned,
        }
    preflight_apply(root, planned)
    state = load_openclaw_state(root)
    run_id = now_run_id()
    applied = []
    for planned_action in planned:
        result = dict(planned_action)
        result["run_id"] = run_id
        if planned_action["operation"] == "no-op":
            result["applied"] = False
            applied.append(result)
            continue
        path = root / planned_action["relative_path"]
        created_parents = missing_parent_dirs(root, path.parent)
        content = render_review_file(manifest, planned_action)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, content)
        result["applied"] = True
        result["installed_hash"] = sha256_text(content)
        result["created_parent_dirs"] = [item.as_posix() for item in created_parents]
        applied.append(result)
        state.setdefault("artifacts", []).append(state_record(result))
        save_openclaw_state(root, state)
    state.setdefault("runs", []).append(
        {
            "run_id": run_id,
            "manifest_id": manifest["manifest_id"],
            "action_count": len(applied),
        }
    )
    save_openclaw_state(root, state)
    return {
        "dry_run": False,
        "run_id": run_id,
        "manifest_id": manifest["manifest_id"],
        "actions": applied,
    }


def uninstall_manifest(
    target_root: Path,
    *,
    manifest_id: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    root = checked_fake_target_root(target_root)
    state = load_openclaw_state(root)
    records = [
        item
        for item in state.get("artifacts", [])
        if manifest_id is None or item.get("manifest_id") == manifest_id
    ]
    actions = [plan_uninstall_action(root, record) for record in records]
    if dry_run:
        return {
            "dry_run": True,
            "manifest_id": manifest_id,
            "actions": actions,
        }
    results = [apply_uninstall_action(root, action) for action in actions]
    completed = {action["key"] for action in results if action["completed"]}
    cleanup_created_parents(
        root,
        [
            relative_dir
            for action in results
            if action["completed"]
            for relative_dir in action.get("created_parent_dirs", [])
        ],
    )
    state["artifacts"] = [item for item in state.get("artifacts", []) if item.get("key") not in completed]
    if not state["artifacts"]:
        remove_openclaw_state(root)
    else:
        save_openclaw_state(root, state)
    return {
        "dry_run": False,
        "manifest_id": manifest_id,
        "actions": results,
        "removed": sorted(completed),
    }


def plan_apply_action(root: Path, manifest: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    relative_path = checked_relative_path(action["target"]["relative_path"])
    path = root / relative_path
    try:
        current = target_precondition(path, root.resolve())
    except ValueError as exc:
        current = {
            "exists": True,
            "kind": "unsafe-path",
            "owner_policy": "unmanaged-preserve",
            "reason": str(exc),
        }
    planned = {
        "key": f"{manifest['manifest_id']}:{action['action_id']}",
        "manifest_id": manifest["manifest_id"],
        "action_id": action["action_id"],
        "operation": action["operation"],
        "target_agent": action["target"]["target_agent"],
        "relative_path": relative_path.as_posix(),
        "precondition": action["precondition"],
        "current_precondition": current,
        "drift": current != action["precondition"],
        "collision": action["collision"],
        "source_ref": action.get("source_ref"),
    }
    safety_reason = target_path_safety_reason(root, path)
    if action["operation"] not in SUPPORTED_WRITE_OPERATIONS and action["operation"] != "no-op":
        planned["blocked"] = True
        planned["reason"] = "unsupported-openclaw-operation"
    elif action["operation"] == "no-op":
        planned["blocked"] = False
        planned["reason"] = "no-op"
    elif safety_reason is not None:
        planned["blocked"] = True
        planned["reason"] = safety_reason
    elif planned["drift"]:
        planned["blocked"] = True
        planned["reason"] = "target-precondition-drift"
    else:
        planned["blocked"] = False
        planned["reason"] = "ready"
    return planned


def preflight_apply(root: Path, planned: list[dict[str, Any]]) -> None:
    preflight_state_path(root, openclaw_state_file(root))
    blocked = [action for action in planned if action.get("blocked")]
    if blocked:
        reasons = ", ".join(sorted({action["reason"] for action in blocked}))
        raise ValueError(f"OpenClaw manifest apply preflight failed: {reasons}")


def plan_uninstall_action(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    relative_path = checked_relative_path(record["relative_path"])
    path = root / relative_path
    exists = path.exists() or path.is_symlink()
    current_hash = sha256_file_text(path) if path.is_file() and not path.is_symlink() else None
    operation = "delete-created"
    reason = "ready"
    if not exists:
        operation = "forget-missing"
        reason = "already-missing"
    elif current_hash != record.get("installed_hash"):
        operation = "skip-conflict"
        reason = "artifact-changed-since-openclaw-apply"
    return {
        "key": record["key"],
        "manifest_id": record["manifest_id"],
        "action_id": record["action_id"],
        "relative_path": relative_path.as_posix(),
        "operation": operation,
        "reason": reason,
        "created_parent_dirs": record.get("created_parent_dirs", []),
    }


def apply_uninstall_action(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    result = dict(action)
    result["completed"] = False
    operation = action["operation"]
    if operation in {"forget-missing", "skip-conflict"}:
        result["completed"] = operation == "forget-missing"
        return result
    if operation != "delete-created":
        raise ValueError(f"unsupported OpenClaw uninstall operation: {operation}")
    path = root / checked_relative_path(action["relative_path"])
    reason = target_path_safety_reason(root, path)
    if reason is not None:
        result["operation"] = "skip-conflict"
        result["reason"] = reason
        return result
    if path.exists() or path.is_symlink():
        path.unlink()
    result["completed"] = True
    return result


def state_record(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": result["key"],
        "manifest_id": result["manifest_id"],
        "action_id": result["action_id"],
        "operation": result["operation"],
        "target_agent": result["target_agent"],
        "relative_path": result["relative_path"],
        "installed_hash": result["installed_hash"],
        "created_parent_dirs": result.get("created_parent_dirs", []),
        "run_id": result["run_id"],
    }


def render_review_file(manifest: dict[str, Any], action: dict[str, Any]) -> str:
    return (
        "<!-- ai-agents-skills:openclaw-review -->\n"
        "# OpenClaw Review Item\n\n"
        f"- manifest: `{manifest['manifest_id']}`\n"
        f"- action: `{action['action_id']}`\n"
        f"- operation: `{action['operation']}`\n"
        f"- source reference: `{action.get('source_ref', 'none')}`\n"
        f"- target agent: `{action['target_agent']}`\n"
        "\n"
        "This file was generated from sanitized OpenClaw metadata only. It does\n"
        "not contain OpenClaw file contents, credentials, provider settings,\n"
        "hooks, schedules, shell profile edits, or runtime state.\n"
    )


def checked_fake_target_root(root: Path) -> Path:
    expanded = root.expanduser()
    if not expanded.exists() or not expanded.is_dir():
        raise ValueError("OpenClaw target root must be an existing directory")
    if is_real_system_root(expanded):
        raise ValueError("OpenClaw Phase 3 refuses real-system target roots")
    if expanded.is_symlink():
        raise ValueError("OpenClaw target root must not be a symlink")
    return expanded


def is_real_system_root(root: Path) -> bool:
    return looks_like_real_system_root(root)


def checked_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("OpenClaw manifest target path must be relative and contained")
    return path


def missing_parent_dirs(root: Path, parent: Path) -> list[Path]:
    missing = []
    current = parent
    while current != root and not current.exists():
        missing.append(current.relative_to(root))
        current = current.parent
    return missing


def cleanup_created_parents(root: Path, relative_dirs: list[str]) -> None:
    for relative in sorted(relative_dirs, key=lambda item: item.count("/"), reverse=True):
        path = root / checked_relative_path(relative)
        try:
            path.rmdir()
        except OSError:
            continue


def sha256_file_text(path: Path) -> str | None:
    if not path.exists() or path.is_symlink() or not path.is_file():
        return None
    return sha256_text(path.read_text(encoding="utf-8", errors="replace"))


def openclaw_state_file(root: Path) -> Path:
    return root / ".ai-agents-skills" / "openclaw-state.json"


def load_openclaw_state(root: Path) -> dict[str, Any]:
    path = openclaw_state_file(root)
    preflight_state_path(root, path)
    if not path.exists():
        return {"schema_version": OPENCLAW_STATE_VERSION, "artifacts": [], "runs": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("OpenClaw state file is not valid JSON") from exc
    if not isinstance(state, dict):
        raise ValueError("OpenClaw state must be a JSON object")
    return state


def save_openclaw_state(root: Path, state: dict[str, Any]) -> None:
    path = openclaw_state_file(root)
    preflight_state_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def remove_openclaw_state(root: Path) -> None:
    path = openclaw_state_file(root)
    preflight_state_path(root, path)
    if path.exists():
        path.unlink()
    state_dir = path.parent
    if state_dir.exists():
        try:
            state_dir.rmdir()
        except OSError:
            pass


def target_path_safety_reason(root: Path, path: Path) -> str | None:
    if not normalized_path_within(root, path):
        return "target-path-escapes-root"
    for parent in existing_contained_parents(path.parent, root):
        if parent.is_symlink():
            return "target-parent-is-symlink"
        if not parent.is_dir():
            return "target-parent-is-not-directory"
    if not resolved_path_within(root, path.parent):
        return "target-path-escapes-root"
    if path.exists() and path.is_dir() and not path.is_symlink():
        return "target-path-is-directory"
    return None
