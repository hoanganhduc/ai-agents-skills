from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .render import replace_or_append_block
from .state import (
    artifact_signature,
    backup_file,
    load_state,
    now_run_id,
    save_state,
    sha256_file,
    sha256_text,
    upsert_artifact,
    upsert_uninstall_record,
    write_run_record,
)


def apply_plan(root: Path, plan: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
    run_id = now_run_id()
    applied: list[dict[str, Any]] = []
    if dry_run:
        return {"run_id": run_id, "dry_run": True, "actions": plan["actions"]}

    state = load_state(root)
    for action in plan["actions"]:
        previous_state_artifact = find_state_artifact(state, action)
        result = apply_action(root, run_id, action)
        recorded_result = dict(result)
        if previous_state_artifact is not None:
            recorded_result["previous_state_artifact"] = previous_state_artifact
        recorded_result["uninstall"] = uninstall_origin(recorded_result, previous_state_artifact)
        applied.append(recorded_result)
        if recorded_result.get("state_operation") == "remove":
            state["artifacts"] = [
                item for item in state.get("artifacts", [])
                if item.get("key") != recorded_result.get("key")
            ]
            if should_keep_uninstall_record(recorded_result):
                upsert_uninstall_record(state, recorded_result)
        elif recorded_result.get("managed"):
            upsert_artifact(state, recorded_result)
    state.setdefault("runs", []).append({"run_id": run_id, "action_count": len(applied)})
    save_state(root, state)
    write_run_record(root, run_id, applied)
    return {"run_id": run_id, "dry_run": False, "actions": applied}


def find_state_artifact(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any] | None:
    key = artifact_key(action)
    for item in state.get("artifacts", []):
        if item.get("key") == key:
            return dict(item)
    return None


def apply_action(root: Path, run_id: str, action: dict[str, Any]) -> dict[str, Any]:
    if action["kind"] == "file":
        return apply_file_action(root, run_id, action)
    if action["kind"] == "managed-block":
        return apply_block_action(root, run_id, action)
    if action["kind"] == "legacy-dir":
        return apply_legacy_dir_action(root, run_id, action)
    if action["kind"] == "managed-file-remove":
        return apply_managed_file_remove_action(root, run_id, action)
    raise ValueError(f"unknown action kind: {action['kind']}")


def apply_file_action(root: Path, run_id: str, action: dict[str, Any]) -> dict[str, Any]:
    path = Path(action["path"])
    op = action["operation"]
    result = base_result(run_id, action)
    result["created_file"] = not path.exists()
    result["previous_hash"] = sha256_file(path)
    result["previous_signature"] = artifact_signature(path)
    if op in {"skip", "noop"}:
        result["managed"] = op == "noop"
        result["applied"] = False
        result["installed_signature"] = artifact_signature(path)
        return result
    if op == "adopt":
        result["managed"] = True
        result["applied"] = False
        result["adopted"] = True
        result["new_hash"] = sha256_file(path)
        result["installed_signature"] = artifact_signature(path)
        return result
    backup = backup_file(root, run_id, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    actual_mode = action.get("install_mode", "copy")
    try:
        if actual_mode == "symlink":
            replace_with_symlink(path, Path(action["source_path"]))
        else:
            replace_with_text(path, action["content"])
    except OSError as exc:
        fallback_mode = action.get("fallback_mode")
        if action.get("install_mode") != "symlink" or fallback_mode not in {"reference", "copy"}:
            raise
        actual_mode = fallback_mode
        replace_with_text(path, action.get("fallback_content", action["content"]))
        result["fallback_reason"] = str(exc)
    result["managed"] = True
    result["applied"] = True
    result["backup"] = str(backup) if backup else None
    result["new_hash"] = sha256_file(path)
    result["install_mode"] = actual_mode
    result["installed_signature"] = artifact_signature(path)
    return result


def replace_with_text(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def replace_with_symlink(path: Path, source_path: Path) -> None:
    if not source_path.exists():
        raise FileNotFoundError(f"symlink source does not exist: {source_path}")
    tmp = path.with_suffix(path.suffix + ".tmp")
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    os.symlink(source_path, tmp)
    os.replace(tmp, path)


def apply_block_action(root: Path, run_id: str, action: dict[str, Any]) -> dict[str, Any]:
    path = Path(action["path"])
    result = base_result(run_id, action)
    result["created_file"] = not path.exists()
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    result["previous_hash"] = sha256_text(before)
    result["previous_signature"] = artifact_signature(path)
    if action.get("operation") in {"skip", "noop"}:
        result["managed"] = False
        result["applied"] = False
        result["installed_signature"] = artifact_signature(path)
        return result
    backup = backup_file(root, run_id, path)
    after = replace_or_append_block(before, action["skill"], action["content"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(after, encoding="utf-8")
    os.replace(tmp, path)
    result["managed"] = True
    result["applied"] = before != after
    result["backup"] = str(backup) if backup else None
    result["new_hash"] = sha256_file(path)
    result["installed_signature"] = artifact_signature(path)
    result["block_id"] = action.get("block_id")
    result["managed_block"] = action["content"].strip()
    return result


def apply_legacy_dir_action(root: Path, run_id: str, action: dict[str, Any]) -> dict[str, Any]:
    path = Path(action["path"])
    legacy_path = Path(action["legacy_path"])
    result = base_result(run_id, action)
    result["managed"] = True
    result["created_file"] = False
    result["previous_hash"] = None
    result["new_hash"] = None
    result["backup"] = None
    result["previous_signature"] = artifact_signature(path)
    result["state_operation"] = "remove"
    if action["operation"] != "remove-legacy":
        result["applied"] = False
        result["installed_signature"] = artifact_signature(path)
        return result
    if legacy_path.parent != path:
        raise ValueError(f"legacy path does not belong to planned legacy directory: {legacy_path}")
    if not path.exists():
        result["applied"] = False
        return result
    if not path.is_dir():
        raise ValueError(f"refusing to remove non-directory legacy path: {path}")
    root_resolved = root.resolve()
    path_resolved = path.resolve()
    if not path_resolved.is_relative_to(root_resolved):
        raise ValueError(f"refusing to remove legacy path outside root: {path}")
    backup = backup_file(root, run_id, path)
    result["backup"] = str(backup) if backup else None
    shutil.rmtree(path)
    result["applied"] = True
    result["installed_signature"] = artifact_signature(path)
    return result


def apply_managed_file_remove_action(root: Path, run_id: str, action: dict[str, Any]) -> dict[str, Any]:
    path = Path(action["path"])
    result = base_result(run_id, action)
    result["managed"] = True
    result["created_file"] = False
    result["previous_hash"] = sha256_file(path)
    result["previous_signature"] = artifact_signature(path)
    result["new_hash"] = None
    result["state_operation"] = "remove"
    if action["operation"] != "remove-obsolete":
        result["applied"] = False
        result["installed_signature"] = artifact_signature(path)
        return result
    backup = backup_file(root, run_id, path)
    result["backup"] = str(backup) if backup else None
    if path.exists() or path.is_symlink():
        path.unlink()
        if action.get("artifact_type") == "skill-support-file":
            skill_dir = next((parent for parent in path.parents if parent.name == action["skill"]), path.parent)
            cleanup_empty_parents(path.parent, stop_at=skill_dir)
        result["applied"] = True
    else:
        result["applied"] = False
    result["installed_signature"] = artifact_signature(path)
    return result


def cleanup_empty_parents(path: Path, stop_at: Path) -> None:
    current = path
    while current != stop_at.parent and current.exists():
        try:
            current.rmdir()
        except OSError:
            return
        if current == stop_at:
            return
        current = current.parent


def base_result(run_id: str, action: dict[str, Any]) -> dict[str, Any]:
    result = {
        "key": artifact_key(action),
        "run_id": run_id,
        "agent": action["agent"],
        "skill": action["skill"],
        "artifact": action["path"],
        "artifact_type": action.get("artifact_type"),
        "artifact_id": action.get("artifact_id"),
        "artifact_name": action.get("artifact_name"),
        "classification": action.get("classification"),
        "operation": action.get("operation", action["kind"]),
    }
    if action.get("legacy_path"):
        result["legacy_path"] = action["legacy_path"]
    if action.get("source_path"):
        result["source_path"] = action["source_path"]
    if action.get("install_mode"):
        result["install_mode"] = action["install_mode"]
    if action.get("fallback_mode"):
        result["fallback_mode"] = action["fallback_mode"]
    return result


def artifact_key(action: dict[str, Any]) -> str:
    if action["kind"] == "managed-block":
        return f"{action['agent']}:{action['skill']}:{action['block_id']}:{action['path']}"
    if action.get("artifact_id"):
        return f"{action['agent']}:{action['artifact_id']}:{action['path']}"
    return f"{action['agent']}:{action['skill']}:{action['path']}"


def uninstall_origin(
    result: dict[str, Any],
    previous_state_artifact: dict[str, Any] | None,
) -> dict[str, Any]:
    previous_origin = previous_state_artifact.get("uninstall") if previous_state_artifact else None
    if previous_origin and not (
        previous_origin.get("action") == "unmanage-only"
        and result.get("applied")
        and result.get("backup")
    ):
        return previous_origin
    if result.get("state_operation") == "remove":
        if previous_origin and previous_origin.get("action") == "delete-created":
            return {"action": "forget-missing"}
        if previous_origin and previous_origin.get("action") != "delete-created":
            return previous_origin
        if result.get("backup"):
            return {
                "action": "restore-removed",
                "backup": result["backup"],
                "original_signature": result.get("previous_signature"),
            }
        return {"action": "forget-missing"}
    if result.get("adopted") or (result.get("operation") == "noop" and not result.get("applied")):
        return {"action": "unmanage-only"}
    if result.get("backup"):
        return {
            "action": "restore-backup",
            "backup": result["backup"],
            "original_signature": result.get("previous_signature"),
        }
    if result.get("created_file"):
        return {"action": "delete-created"}
    return {"action": "unmanage-only"}


def should_keep_uninstall_record(result: dict[str, Any]) -> bool:
    if not result.get("applied"):
        return False
    origin = result.get("uninstall", {})
    return origin.get("action") in {"restore-backup", "restore-removed"}
