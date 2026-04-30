from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .state import (
    artifact_signature,
    load_state,
    run_record_path,
    save_state,
    signatures_match,
    state_dir,
    upsert_artifact,
)


def uninstall(
    root: Path,
    skills: set[str] | None = None,
    agents: set[str] | None = None,
    artifacts: set[str] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    state = load_state(root)
    targets = filter_artifacts(lifecycle_records(state), skills, agents, artifacts)
    actions = [plan_uninstall_action(item, root) for item in targets]
    if dry_run:
        return {"dry_run": True, "actions": actions}
    completed_keys: set[str | None] = set()
    removed = []
    results = []
    for action in actions:
        result = apply_uninstall_action(action, root)
        results.append(result)
        if result.get("completed"):
            completed_keys.add(action.get("key"))
            removed.append(action)
    state["artifacts"] = [
        item for item in state.get("artifacts", [])
        if item.get("key") not in completed_keys
    ]
    state["uninstall_records"] = [
        item for item in state.get("uninstall_records", [])
        if item.get("key") not in completed_keys
    ]
    save_state(root, state)
    return {"dry_run": False, "actions": results, "removed": removed}


def rollback(
    root: Path,
    run_id: str | None = None,
    skills: set[str] | None = None,
    agents: set[str] | None = None,
    artifacts: set[str] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    state = load_state(root)
    if run_id:
        path = run_record_path(root, run_id)
        if not path.exists():
            raise ValueError(f"unknown run id: {run_id}")
        actions = json.loads(path.read_text(encoding="utf-8"))["actions"]
    else:
        actions = state.get("artifacts", [])
    targets = [
        item for item in filter_artifacts(actions, skills, agents, artifacts)
        if item.get("managed") and item.get("applied", True)
    ]
    if dry_run:
        return {"dry_run": True, "actions": [{"operation": "rollback", **item} for item in targets]}
    restored = []
    for item in targets:
        rollback_artifact(item)
        restored.append(item)
    remaining = [
        item for item in state.get("artifacts", [])
        if item.get("key") not in {target.get("key") for target in targets}
    ]
    state["artifacts"] = remaining
    for target in targets:
        previous = target.get("previous_state_artifact")
        if previous:
            upsert_artifact(state, previous)
    save_state(root, state)
    return {"dry_run": False, "restored": restored}


def filter_artifacts(
    artifacts: list[dict[str, Any]],
    skills: set[str] | None,
    agents: set[str] | None,
    artifact_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    selected = []
    for item in artifacts:
        if skills and item.get("skill") not in skills:
            continue
        if agents and item.get("agent") not in agents:
            continue
        if artifact_ids and item.get("artifact_id") not in artifact_ids:
            continue
        selected.append(item)
    return selected


def lifecycle_records(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *state.get("artifacts", []),
        *state.get("uninstall_records", []),
    ]


def plan_uninstall_action(item: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    action = dict(item)
    origin = item.get("uninstall", {})
    path = Path(item["artifact"])
    if root is not None and not path_within(root, path):
        action["operation"] = "skip-conflict"
        action["reason"] = "artifact path outside selected root"
        return action
    action["current_signature"] = artifact_signature(path)
    if item.get("artifact_type") in {"instruction-block", "management-notice"}:
        action["operation"] = plan_block_uninstall(item)
        return action
    origin_action = origin.get("action")
    if origin_action == "unmanage-only":
        action["operation"] = "unmanage-only"
    elif origin_action == "restore-removed":
        action["operation"] = "restore-removed" if not path.exists() and not path.is_symlink() else "skip-conflict"
    elif origin_action == "restore-backup":
        installed = item.get("installed_signature")
        current = artifact_signature(path)
        if not current.get("exists") or signatures_match(current, installed):
            action["operation"] = "restore-backup"
        else:
            action["operation"] = "skip-conflict"
    elif origin_action == "delete-created":
        current = artifact_signature(path)
        if not current.get("exists"):
            action["operation"] = "forget-missing"
        elif signatures_match(current, item.get("installed_signature")):
            action["operation"] = "delete-created"
        else:
            action["operation"] = "skip-conflict"
    elif origin_action == "forget-missing":
        action["operation"] = "forget-missing"
    else:
        action["operation"] = "skip-conflict"
        action["reason"] = "missing uninstall origin metadata"
    return action


def plan_block_uninstall(item: dict[str, Any]) -> str:
    path = Path(item["artifact"])
    if not path.exists():
        return "forget-missing"
    if (
        item.get("uninstall", {}).get("action") == "restore-backup"
        and signatures_match(artifact_signature(path), item.get("installed_signature"))
    ):
        text_for_count = path.read_text(encoding="utf-8", errors="replace")
        if text_for_count.count("<!-- ai-agents-skills:") == 2:
            return "restore-backup"
    text = path.read_text(encoding="utf-8", errors="replace")
    current_block = extract_managed_block(text, item["skill"])
    if current_block is None:
        return "forget-missing"
    expected = item.get("managed_block")
    if expected is not None and current_block.strip() != expected.strip():
        return "skip-conflict"
    return "remove-managed-block"


def apply_uninstall_action(action: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    result = dict(action)
    operation = action["operation"]
    result["completed"] = False
    if root is not None and not path_within(root, Path(action["artifact"])):
        result["operation"] = "skip-conflict"
        result["reason"] = "artifact path outside selected root"
        return result
    if operation in {"unmanage-only", "forget-missing"}:
        result["completed"] = True
        return result
    if operation == "skip-conflict":
        result["reason"] = result.get("reason", "artifact changed since install")
        return result
    if operation == "remove-managed-block":
        remove_managed_block_precise(action)
        result["completed"] = True
        return result
    if operation == "delete-created":
        remove_artifact(action)
        result["completed"] = True
        return result
    if operation in {"restore-backup", "restore-removed"}:
        backup = action.get("uninstall", {}).get("backup") or action.get("backup")
        if not backup or not Path(backup).exists() and not Path(backup).is_symlink():
            result["operation"] = "skip-conflict"
            result["reason"] = "backup missing"
            return result
        if root is not None and not path_within(state_dir(root) / "backups", Path(backup)):
            result["operation"] = "skip-conflict"
            result["reason"] = "backup path outside installer backup directory"
            return result
        restore_backup(Path(backup), Path(action["artifact"]))
        result["completed"] = True
        return result
    raise ValueError(f"unknown uninstall operation: {operation}")


def remove_artifact(item: dict[str, Any]) -> None:
    path = Path(item["artifact"])
    if item.get("artifact_type") == "skill-file":
        remove_file(path)
        cleanup_empty_parents(path.parent, stop_at=path.parent)
        return
    if item.get("artifact_type") == "skill-support-file":
        remove_file(path)
        skill_dir = next((parent for parent in path.parents if parent.name == item["skill"]), path.parent)
        cleanup_empty_parents(path.parent, stop_at=skill_dir)
        return
    if item.get("artifact_type") in {"instruction-block", "management-notice"}:
        remove_managed_block(path, item["skill"], delete_if_empty=item.get("created_file", False))
        return
    if item.get("artifact_type") in {
        "template",
        "instruction-doc",
        "agent-persona",
        "entrypoint-alias",
        "command",
        "tool-shim",
    }:
        remove_file(path)
        cleanup_empty_parents(path.parent, stop_at=path.parent)


def remove_managed_block(path: Path, skill: str, delete_if_empty: bool = False) -> None:
    if not path.exists():
        return
    marker = f"ai-agents-skills:{skill}"
    start = f"<!-- {marker}:start -->"
    end = f"<!-- {marker}:end -->"
    text = path.read_text(encoding="utf-8")
    if start in text and end in text:
        before, rest = text.split(start, 1)
        _, after = rest.split(end, 1)
        cleaned = (before.rstrip() + "\n" + after.lstrip()).strip()
        if delete_if_empty and not cleaned:
            path.unlink()
            return
        path.write_text(cleaned + "\n", encoding="utf-8")


def remove_managed_block_precise(item: dict[str, Any]) -> None:
    path = Path(item["artifact"])
    text = path.read_text(encoding="utf-8")
    span = managed_block_span(text, item["skill"])
    if span is None:
        return
    start, end = span
    if end < len(text) and text[end] == "\n":
        end += 1
    cleaned = text[:start] + text[end:]
    if item.get("uninstall", {}).get("action") == "delete-created" and not cleaned.strip():
        path.unlink()
        return
    path.write_text(cleaned, encoding="utf-8")


def extract_managed_block(text: str, skill: str) -> str | None:
    span = managed_block_span(text, skill)
    if span is None:
        return None
    start, end = span
    return text[start:end]


def managed_block_span(text: str, skill: str) -> tuple[int, int] | None:
    marker = f"ai-agents-skills:{skill}"
    start_marker = f"<!-- {marker}:start -->"
    end_marker = f"<!-- {marker}:end -->"
    start = text.find(start_marker)
    if start == -1:
        return None
    end = text.find(end_marker, start)
    if end == -1:
        return None
    return start, end + len(end_marker)


def rollback_artifact(item: dict[str, Any]) -> None:
    path = Path(item["artifact"])
    backup = item.get("backup")
    installed_signature = item.get("installed_signature")
    current_signature = artifact_signature(path)
    if installed_signature and current_signature.get("exists") and not signatures_match(current_signature, installed_signature):
        raise ValueError(f"refusing rollback because artifact changed since install: {path}")
    if backup:
        backup_path = Path(backup)
        path.parent.mkdir(parents=True, exist_ok=True)
        restore_backup(backup_path, path)
    else:
        remove_artifact(item)


def restore_backup(backup_path: Path, path: Path) -> None:
    if path.exists() or path.is_symlink():
        if path.is_dir() and not path.is_symlink():
            remove_tree(path)
        else:
            path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.is_symlink():
        os.symlink(os.readlink(backup_path), path)
    elif backup_path.is_dir():
        copy_tree(backup_path, path)
    else:
        shutil.copy2(backup_path, path)


def remove_file(path: Path) -> None:
    if path.exists() or path.is_symlink():
        if path.is_dir() and not path.is_symlink():
            raise ValueError(f"refusing to remove directory as managed file: {path}")
        path.unlink()


def remove_tree(path: Path) -> None:
    shutil.rmtree(path)


def copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, symlinks=True)


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


def path_within(root: Path, path: Path) -> bool:
    try:
        Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(root)))
    except ValueError:
        return False
    return True
