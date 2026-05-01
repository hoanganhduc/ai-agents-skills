from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from .capabilities import normalized_path_within, resolved_path_within
from .state import (
    artifact_signature,
    load_state,
    run_record_path,
    save_state,
    signatures_match,
    state_dir,
    upsert_artifact,
    write_text_atomic,
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
    mark_created_instruction_file_groups(actions)
    if dry_run:
        return {"dry_run": True, "actions": actions}
    preflight_uninstall_actions(root, actions)
    completed_keys: set[str | None] = set()
    removed = []
    results = []
    created_parent_dirs: list[str] = []
    for action in actions:
        result = apply_uninstall_action(action, root)
        results.append(result)
        if result.get("completed"):
            completed_keys.add(action.get("key"))
            removed.append(action)
            created_parent_dirs.extend(action.get("created_parent_dirs", []))
            state["artifacts"] = [
                item for item in state.get("artifacts", [])
                if item.get("key") != action.get("key")
            ]
            state["uninstall_records"] = [
                item for item in state.get("uninstall_records", [])
                if item.get("key") != action.get("key")
            ]
            save_state(root, state)
    cleanup_created_dirs(root, created_parent_dirs)
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
    mark_created_instruction_file_groups(targets)
    if dry_run:
        return {"dry_run": True, "actions": [{"operation": "rollback", **item} for item in targets]}
    preflight_rollback_targets(root, state, targets)
    restored = []
    created_parent_dirs: list[str] = []
    for item in targets:
        rollback_artifact(item, root)
        restored.append(item)
        created_parent_dirs.extend(item.get("created_parent_dirs", []))
    remaining = [
        item for item in state.get("artifacts", [])
        if item.get("key") not in {target.get("key") for target in targets}
    ]
    state["artifacts"] = remaining
    for target in targets:
        previous = target.get("previous_state_artifact")
        if previous:
            upsert_artifact(state, previous)
    cleanup_created_dirs(root, created_parent_dirs)
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
        remove_artifact(action, root)
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


def remove_artifact(item: dict[str, Any], root: Path | None = None) -> None:
    path = Path(item["artifact"])
    if item.get("artifact_type") == "skill-file":
        remove_file(path)
        cleanup_recorded_parent_dirs(root, item)
        return
    if item.get("artifact_type") == "skill-support-file":
        remove_file(path)
        cleanup_recorded_parent_dirs(root, item)
        return
    if item.get("artifact_type") in {"instruction-block", "management-notice"}:
        remove_managed_block(path, item["skill"], delete_if_empty=item.get("created_file", False))
        cleanup_recorded_parent_dirs(root, item)
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
        cleanup_recorded_parent_dirs(root, item)


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
        write_text_atomic(path, cleaned + "\n")


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
    if (
        item.get("uninstall", {}).get("action") == "delete-created"
        or item.get("delete_instruction_file_if_empty")
    ) and not cleaned.strip():
        path.unlink()
        return
    write_text_atomic(path, cleaned)


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


def rollback_artifact(item: dict[str, Any], root: Path | None = None) -> None:
    path = Path(item["artifact"])
    if item.get("artifact_type") in {"instruction-block", "management-notice"}:
        previous = item.get("previous_state_artifact")
        if previous and item.get("backup"):
            restore_backup(Path(item["backup"]), path)
        else:
            remove_managed_block_precise(item)
        return
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
        remove_artifact(item, root)


def restore_backup(backup_path: Path, path: Path) -> None:
    temp = materialize_backup_temp(backup_path, path)
    if path.exists() or path.is_symlink():
        previous = unique_sibling(path, "previous")
        path.rename(previous)
        try:
            temp.rename(path)
        except Exception:
            previous.rename(path)
            raise
        remove_any(previous)
        return
    temp.rename(path)


def materialize_backup_temp(backup_path: Path, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = unique_sibling(path, "restore")
    if backup_path.is_symlink():
        os.symlink(os.readlink(backup_path), temp)
    elif backup_path.is_dir():
        copy_tree(backup_path, temp)
    else:
        shutil.copy2(backup_path, temp)
    return temp


def unique_sibling(path: Path, label: str) -> Path:
    for _ in range(100):
        candidate = path.parent / f".{path.name}.{label}.{uuid.uuid4().hex}"
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    raise FileExistsError(f"could not allocate temporary path near {path}")


def remove_any(path: Path) -> None:
    if path.exists() or path.is_symlink():
        if path.is_dir() and not path.is_symlink():
            remove_tree(path)
        else:
            path.unlink()


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


def cleanup_recorded_parent_dirs(root: Path | None, item: dict[str, Any]) -> None:
    if root is None:
        return
    cleanup_created_dirs(root, item.get("created_parent_dirs", []))


def cleanup_created_dirs(root: Path, relative_dirs: list[str]) -> None:
    for relative in sorted(relative_dirs, key=lambda value: value.count("/"), reverse=True):
        path = root / relative
        if not normalized_path_within(root, path) or not resolved_path_within(root, path):
            continue
        if not path.exists() or path.is_symlink() or not path.is_dir():
            continue
        try:
            path.rmdir()
        except OSError:
            continue


def path_within(root: Path, path: Path) -> bool:
    return normalized_path_within(root, path) and resolved_path_within(root, path.parent)


def preflight_uninstall_actions(root: Path, actions: list[dict[str, Any]]) -> None:
    for action in actions:
        operation = action.get("operation")
        if operation in {"skip-conflict", "unmanage-only", "forget-missing"}:
            continue
        path = Path(action["artifact"])
        if not path_within(root, path):
            raise ValueError(f"refusing uninstall for artifact outside selected root: {path}")
        if operation in {"restore-backup", "restore-removed"}:
            backup = action.get("uninstall", {}).get("backup") or action.get("backup")
            if not backup:
                raise ValueError(f"refusing uninstall because backup is missing: {path}")
            backup_path = Path(backup)
            if not path_within(state_dir(root) / "backups", backup_path):
                raise ValueError(f"refusing uninstall because backup is outside installer state: {backup}")
            if not backup_path.exists() and not backup_path.is_symlink():
                raise ValueError(f"refusing uninstall because backup is missing: {backup}")


def mark_created_instruction_file_groups(actions: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for action in actions:
        if action.get("artifact_type") in {"instruction-block", "management-notice"}:
            grouped.setdefault(action["artifact"], []).append(action)
    for group in grouped.values():
        if any(item.get("created_file") for item in group):
            for item in group:
                item["delete_instruction_file_if_empty"] = True


def preflight_rollback_targets(root: Path, state: dict[str, Any], targets: list[dict[str, Any]]) -> None:
    block_groups: dict[str, list[dict[str, Any]]] = {}
    for item in targets:
        path = Path(item["artifact"])
        if not path_within(root, path):
            raise ValueError(f"refusing rollback for artifact outside selected root: {path}")
        backup = item.get("backup")
        if backup and not path_within(state_dir(root) / "backups", Path(backup)):
            raise ValueError(f"refusing rollback because backup is outside installer state: {backup}")
        if backup and not Path(backup).exists() and not Path(backup).is_symlink():
            raise ValueError(f"refusing rollback because backup is missing: {backup}")
        if item.get("artifact_type") in {"instruction-block", "management-notice"}:
            block_groups.setdefault(item["artifact"], []).append(item)
            continue
        installed_signature = item.get("installed_signature")
        current_signature = artifact_signature(path)
        if installed_signature and current_signature.get("exists") and not signatures_match(current_signature, installed_signature):
            raise ValueError(f"refusing rollback because artifact changed since install: {path}")
    for path_text, group in block_groups.items():
        path = Path(path_text)
        if not path.exists():
            raise ValueError(f"refusing rollback because instruction file is missing: {path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        for item in group:
            current_block = extract_managed_block(text, item["skill"])
            expected_block = item.get("managed_block")
            if current_block is None:
                raise ValueError(f"refusing rollback because managed block is missing: {path}")
            if expected_block is not None and current_block.strip() != expected_block.strip():
                raise ValueError(f"refusing rollback because managed block changed since install: {path}")
        known_skills = [
            item["skill"]
            for item in state.get("artifacts", [])
            if item.get("artifact") == path_text
            and item.get("artifact_type") in {"instruction-block", "management-notice"}
        ]
        if any(item.get("created_file") for item in group) and strip_managed_blocks(text, known_skills).strip():
            raise ValueError(f"refusing rollback because instruction file changed since install: {path}")


def strip_managed_blocks(text: str, skills: list[str]) -> str:
    spans = [managed_block_span(text, skill) for skill in skills]
    existing_spans = sorted((span for span in spans if span is not None), reverse=True)
    stripped = text
    for start, end in existing_spans:
        if end < len(stripped) and stripped[end] == "\n":
            end += 1
        stripped = stripped[:start] + stripped[end:]
    return stripped
