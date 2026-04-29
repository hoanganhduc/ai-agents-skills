from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .state import load_state, run_record_path, save_state, sha256_file


def uninstall(
    root: Path,
    skills: set[str] | None = None,
    agents: set[str] | None = None,
    artifacts: set[str] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    state = load_state(root)
    targets = filter_artifacts(state.get("artifacts", []), skills, agents, artifacts)
    if dry_run:
        return {"dry_run": True, "actions": [{"operation": "remove", **item} for item in targets]}
    remaining = []
    removed = []
    for item in state.get("artifacts", []):
        if item in targets:
            remove_artifact(item)
            removed.append(item)
        else:
            remaining.append(item)
    state["artifacts"] = remaining
    save_state(root, state)
    return {"dry_run": False, "removed": removed}


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


def remove_artifact(item: dict[str, Any]) -> None:
    path = Path(item["artifact"])
    if item.get("artifact_type") == "skill-file":
        skill_dir = path.parent
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
        return
    if item.get("artifact_type") == "skill-support-file":
        if path.exists():
            path.unlink()
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
        if path.exists():
            path.unlink()
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


def rollback_artifact(item: dict[str, Any]) -> None:
    path = Path(item["artifact"])
    backup = item.get("backup")
    current_hash = sha256_file(path) if path.exists() and path.is_file() else None
    expected_hash = item.get("new_hash")
    if expected_hash and current_hash and current_hash != expected_hash:
        raise ValueError(f"refusing rollback because artifact changed since install: {path}")
    if backup:
        backup_path = Path(backup)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_path, path)
    else:
        remove_artifact(item)


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
