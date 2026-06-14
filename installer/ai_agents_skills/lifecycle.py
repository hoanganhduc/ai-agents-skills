from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from .capabilities import normalized_path_within, resolved_path_within
from .openclaw_target_gate import real_openclaw_path_block_reason
from .state import (
    artifact_signature,
    load_state,
    run_record_path,
    save_state,
    signatures_match,
    state_dir,
    upsert_artifact,
    validate_run_id,
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
    lifecycle_scope = state.get("artifacts", [])
    if run_id:
        actions = load_run_actions(root, state, run_id)
    else:
        actions = lifecycle_scope
    targets = [
        item for item in filter_artifacts(actions, skills, agents, artifacts, lifecycle_scope=lifecycle_scope)
        if rollback_target_item(item)
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
    artifacts: list[Any],
    skills: set[str] | None,
    agents: set[str] | None,
    artifact_ids: set[str] | None = None,
    lifecycle_scope: list[Any] | None = None,
) -> list[dict[str, Any]]:
    selected = []
    for item in artifacts:
        if not isinstance(item, dict):
            if not skills and not agents and not artifact_ids:
                selected.append({"managed": False, "invalid_record": item})
            continue
        if skills and item.get("skill") not in skills:
            continue
        if agents and item.get("agent") not in agents:
            continue
        if artifact_ids and item.get("artifact_id") not in artifact_ids:
            continue
        selected.append(item)
    if artifact_ids:
        return selected
    return expand_runtime_lifecycle_scope(lifecycle_scope or artifacts, selected)


def rollback_target_item(item: dict[str, Any]) -> bool:
    if item.get("managed") is not True:
        return False
    if item.get("applied", True):
        return True
    return item.get("uninstall", {}).get("action") == "unmanage-only"


def expand_runtime_lifecycle_scope(artifacts: list[Any], selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected_keys = {item.get("key") for item in selected}
    selected_skills = {
        item.get("skill")
        for item in selected
        if item.get("artifact_type") != "runtime-file" and isinstance(item.get("skill"), str)
    }
    if not selected_keys and not selected_skills:
        return selected

    remaining_skill_consumers: set[str] = set()
    runtime_skill_records = []
    runtime_runner_records = []
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        skill = item.get("skill")
        if item.get("artifact_type") == "runtime-file":
            if skill == "runtime-runner":
                runtime_runner_records.append(item)
            else:
                runtime_skill_records.append(item)
            continue
        if item.get("key") not in selected_keys and isinstance(skill, str):
            remaining_skill_consumers.add(skill)

    for item in runtime_skill_records:
        skill = item.get("skill")
        if item.get("key") in selected_keys:
            continue
        if skill in selected_skills and skill not in remaining_skill_consumers:
            selected.append(item)
            selected_keys.add(item.get("key"))

    remaining_runtime_skills = [
        item for item in runtime_skill_records
        if item.get("key") not in selected_keys
    ]
    if remaining_runtime_skills:
        return [
            item for item in selected
            if not (item.get("artifact_type") == "runtime-file" and item.get("skill") == "runtime-runner")
        ]
    if not remaining_runtime_skills:
        for item in runtime_runner_records:
            if item.get("key") not in selected_keys:
                selected.append(item)
                selected_keys.add(item.get("key"))
    return selected


def lifecycle_records(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *state.get("artifacts", []),
        *state.get("uninstall_records", []),
    ]


def load_run_actions(root: Path, state: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    run_id = validate_run_id(run_id)
    known_run_ids = {
        item.get("run_id")
        for item in state.get("runs", [])
        if isinstance(item, dict)
    }
    if run_id not in known_run_ids:
        raise ValueError(f"unknown run id: {run_id}")
    path = run_record_path(root, run_id)
    if not path.exists():
        raise ValueError(f"missing run record for run id: {run_id}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"run record is not valid JSON: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("actions"), list):
        raise ValueError(f"invalid run record: {path}")
    if payload.get("run_id") not in {None, run_id}:
        raise ValueError(f"run record id mismatch: {path}")
    return payload["actions"]


def lifecycle_record_schema_issue(item: Any) -> str | None:
    if not isinstance(item, dict):
        return "state record is not an object"
    if item.get("managed") is not True:
        return "state record is not marked managed"
    for key in ("key", "artifact", "artifact_type"):
        if not item.get(key):
            return f"state record is missing required field: {key}"
    return None


def plan_uninstall_action(item: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    action = dict(item)
    schema_issue = lifecycle_record_schema_issue(item)
    if schema_issue:
        action["operation"] = "skip-conflict"
        action["reason"] = schema_issue
        return action
    origin = item.get("uninstall", {})
    path = Path(item["artifact"])
    if root is not None and not path_within(root, path):
        action["operation"] = "skip-conflict"
        action["reason"] = "artifact path outside selected root"
        return action
    action["current_signature"] = artifact_signature(path)
    if item.get("artifact_type") in {"instruction-block", "management-notice"}:
        safety_reason = instruction_file_safety_reason(path)
        if safety_reason is not None:
            action["operation"] = "skip-conflict"
            action["reason"] = safety_reason
            return action
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
    text = path.read_text(encoding="utf-8", errors="replace")
    issue = managed_block_issue(text, item["skill"])
    if issue == "missing":
        return "forget-missing"
    if issue is not None:
        return "skip-conflict"
    if (
        item.get("uninstall", {}).get("action") == "restore-backup"
        and signatures_match(artifact_signature(path), item.get("installed_signature"))
    ):
        if text.count("<!-- ai-agents-skills:") == 2:
            return "restore-backup"
    current_block = extract_managed_block(text, item["skill"])
    if current_block is None:
        return "forget-missing"
    expected = item.get("managed_block")
    if expected is not None and current_block.strip() != expected.strip():
        return "skip-conflict"
    return "remove-managed-block"


def instruction_file_safety_reason(path: Path) -> str | None:
    if path.is_symlink():
        return "instruction file is symlinked"
    if path.exists() and not path.is_file():
        return "instruction file is not a regular file"
    return None


def apply_uninstall_action(action: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    result = dict(action)
    operation = action["operation"]
    result["completed"] = False
    if operation == "skip-conflict":
        result["reason"] = result.get("reason", "artifact changed since install")
        return result
    if root is not None and not path_within(root, Path(action["artifact"])):
        result["operation"] = "skip-conflict"
        result["reason"] = "artifact path outside selected root"
        return result
    if (
        operation in {"delete-created", "restore-backup", "restore-removed"}
        and action.get("artifact_type") not in {"instruction-block", "management-notice"}
        and not uninstall_pre_state_unchanged(action)
    ):
        result["operation"] = "skip-conflict"
        result["reason"] = "artifact changed since uninstall planning"
        return result
    if operation in {"unmanage-only", "forget-missing"}:
        result["completed"] = True
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
        if not backup_integrity_ok(action, Path(backup)):
            result["operation"] = "skip-conflict"
            result["reason"] = "backup signature changed since it was recorded"
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
    if item.get("artifact_type") == "runtime-file":
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
        "plugin",
        "mcp-config",
        "hook-config",
        "settings-file",
        "legacy-skill-file",
    }:
        remove_file(path)
        cleanup_recorded_parent_dirs(root, item)


def remove_managed_block(path: Path, skill: str, delete_if_empty: bool = False) -> None:
    safety_reason = instruction_file_safety_reason(path)
    if safety_reason is not None:
        raise ValueError(f"refusing to edit instruction file because {safety_reason}: {path}")
    if not path.exists():
        return
    marker = f"ai-agents-skills:{skill}"
    start = f"<!-- {marker}:start -->"
    end = f"<!-- {marker}:end -->"
    text = path.read_text(encoding="utf-8")
    if managed_block_issue(text, skill) is not None:
        return
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    cleaned = (before.rstrip() + "\n" + after.lstrip()).strip()
    if delete_if_empty and not cleaned:
        path.unlink()
        return
    write_text_atomic(path, cleaned + "\n")


def remove_managed_block_precise(item: dict[str, Any]) -> None:
    path = Path(item["artifact"])
    safety_reason = instruction_file_safety_reason(path)
    if safety_reason is not None:
        raise ValueError(f"refusing to edit instruction file because {safety_reason}: {path}")
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
    if text.count(start_marker) != 1 or text.count(end_marker) != 1:
        return None
    start = text.find(start_marker)
    if start == -1:
        return None
    end = text.find(end_marker, start)
    if end == -1:
        return None
    return start, end + len(end_marker)


def managed_block_issue(text: str, skill: str) -> str | None:
    marker = f"ai-agents-skills:{skill}"
    start_marker = f"<!-- {marker}:start -->"
    end_marker = f"<!-- {marker}:end -->"
    start_count = text.count(start_marker)
    end_count = text.count(end_marker)
    if start_count == 0 and end_count == 0:
        return "missing"
    if start_count != 1 or end_count != 1:
        return "malformed-or-duplicated"
    if text.find(start_marker) > text.find(end_marker):
        return "malformed-or-duplicated"
    return None


def rollback_artifact(item: dict[str, Any], root: Path | None = None) -> None:
    if item.get("uninstall", {}).get("action") == "unmanage-only":
        return
    path = Path(item["artifact"])
    if item.get("artifact_type") in {"instruction-block", "management-notice"}:
        previous = item.get("previous_state_artifact")
        if previous and item.get("backup"):
            if not backup_integrity_ok(item, Path(item["backup"])):
                raise ValueError(f"refusing rollback because backup changed since it was recorded: {item['backup']}")
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
        if not backup_integrity_ok(item, backup_path):
            raise ValueError(f"refusing rollback because backup changed since it was recorded: {backup}")
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
        openclaw_block = real_openclaw_artifact_block_reason(root, action, path, operation="uninstall")
        if openclaw_block is not None:
            raise ValueError(openclaw_block)
        if action.get("artifact_type") in {"instruction-block", "management-notice"}:
            safety_reason = instruction_file_safety_reason(path)
            if safety_reason is not None:
                raise ValueError(f"refusing uninstall because {safety_reason}: {path}")
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
        schema_issue = lifecycle_record_schema_issue(item)
        if schema_issue:
            raise ValueError(f"refusing rollback for invalid state record: {schema_issue}")
        path = Path(item["artifact"])
        if not path_within(root, path):
            raise ValueError(f"refusing rollback for artifact outside selected root: {path}")
        openclaw_block = real_openclaw_artifact_block_reason(root, item, path, operation="rollback")
        if openclaw_block is not None:
            raise ValueError(openclaw_block)
        if item.get("uninstall", {}).get("action") == "unmanage-only":
            continue
        backup = item.get("backup")
        if backup and not path_within(state_dir(root) / "backups", Path(backup)):
            raise ValueError(f"refusing rollback because backup is outside installer state: {backup}")
        if backup and not Path(backup).exists() and not Path(backup).is_symlink():
            raise ValueError(f"refusing rollback because backup is missing: {backup}")
        if backup and not backup_integrity_ok(item, Path(backup)):
            raise ValueError(f"refusing rollback because backup changed since it was recorded: {backup}")
        if item.get("artifact_type") in {"instruction-block", "management-notice"}:
            block_groups.setdefault(item["artifact"], []).append(item)
            continue
        installed_signature = item.get("installed_signature")
        current_signature = artifact_signature(path)
        if installed_signature and current_signature.get("exists") and not signatures_match(current_signature, installed_signature):
            raise ValueError(f"refusing rollback because artifact changed since install: {path}")
    for path_text, group in block_groups.items():
        path = Path(path_text)
        safety_reason = instruction_file_safety_reason(path)
        if safety_reason is not None:
            raise ValueError(f"refusing rollback because {safety_reason}: {path}")
        if not path.exists():
            raise ValueError(f"refusing rollback because instruction file is missing: {path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        for item in group:
            issue = managed_block_issue(text, item["skill"])
            if issue == "missing":
                raise ValueError(f"refusing rollback because managed block is missing: {path}")
            if issue is not None:
                raise ValueError(f"refusing rollback because managed block is malformed or duplicated: {path}")
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


def real_openclaw_artifact_blocked(root: Path, item: dict[str, Any], path: Path) -> bool:
    return real_openclaw_artifact_block_reason(root, item, path, operation="apply") is not None


def real_openclaw_artifact_block_reason(
    root: Path,
    item: dict[str, Any],
    path: Path,
    *,
    operation: str,
) -> str | None:
    return real_openclaw_path_block_reason(root, path, operation=operation, agent=str(item.get("agent")))


def backup_integrity_ok(item: dict[str, Any], backup_path: Path) -> bool:
    expected = item.get("uninstall", {}).get("original_signature") or item.get("previous_signature")
    if expected is None:
        return True
    return signatures_match(artifact_signature(backup_path), expected)


def uninstall_pre_state_unchanged(action: dict[str, Any]) -> bool:
    planned = action.get("current_signature")
    if planned is None:
        return True
    return signatures_match(artifact_signature(Path(action["artifact"])), planned)


def strip_managed_blocks(text: str, skills: list[str]) -> str:
    spans = [managed_block_span(text, skill) for skill in skills]
    existing_spans = sorted((span for span in spans if span is not None), reverse=True)
    stripped = text
    for start, end in existing_spans:
        if end < len(stripped) and stripped[end] == "\n":
            end += 1
        stripped = stripped[:start] + stripped[end:]
    return stripped
