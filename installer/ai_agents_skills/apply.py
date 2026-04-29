from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .render import replace_or_append_block
from .state import (
    backup_file,
    load_state,
    now_run_id,
    save_state,
    sha256_file,
    sha256_text,
    upsert_artifact,
    write_run_record,
)


def apply_plan(root: Path, plan: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
    run_id = now_run_id()
    applied: list[dict[str, Any]] = []
    if dry_run:
        return {"run_id": run_id, "dry_run": True, "actions": plan["actions"]}

    state = load_state(root)
    for action in plan["actions"]:
        result = apply_action(root, run_id, action)
        applied.append(result)
        if result.get("managed"):
            upsert_artifact(state, result)
    state.setdefault("runs", []).append({"run_id": run_id, "action_count": len(applied)})
    save_state(root, state)
    write_run_record(root, run_id, applied)
    return {"run_id": run_id, "dry_run": False, "actions": applied}


def apply_action(root: Path, run_id: str, action: dict[str, Any]) -> dict[str, Any]:
    if action["kind"] == "file":
        return apply_file_action(root, run_id, action)
    if action["kind"] == "managed-block":
        return apply_block_action(root, run_id, action)
    raise ValueError(f"unknown action kind: {action['kind']}")


def apply_file_action(root: Path, run_id: str, action: dict[str, Any]) -> dict[str, Any]:
    path = Path(action["path"])
    op = action["operation"]
    result = base_result(run_id, action)
    result["previous_hash"] = sha256_file(path)
    if op in {"skip", "noop"}:
        result["managed"] = op == "noop"
        result["applied"] = False
        return result
    if op == "adopt":
        result["managed"] = True
        result["applied"] = False
        result["new_hash"] = sha256_file(path)
        return result
    backup = backup_file(root, run_id, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(action["content"], encoding="utf-8")
    os.replace(tmp, path)
    result["managed"] = True
    result["applied"] = True
    result["backup"] = str(backup) if backup else None
    result["new_hash"] = sha256_file(path)
    return result


def apply_block_action(root: Path, run_id: str, action: dict[str, Any]) -> dict[str, Any]:
    path = Path(action["path"])
    result = base_result(run_id, action)
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    result["previous_hash"] = sha256_text(before)
    if action.get("operation") in {"skip", "noop"}:
        result["managed"] = False
        result["applied"] = False
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
    result["new_hash"] = sha256_text(after)
    result["block_id"] = action.get("block_id")
    return result


def base_result(run_id: str, action: dict[str, Any]) -> dict[str, Any]:
    result = {
        "key": artifact_key(action),
        "run_id": run_id,
        "agent": action["agent"],
        "skill": action["skill"],
        "artifact": action["path"],
        "artifact_type": action.get("artifact_type"),
        "classification": action.get("classification"),
        "operation": action.get("operation", action["kind"]),
    }
    if action.get("legacy_path"):
        result["legacy_path"] = action["legacy_path"]
    return result


def artifact_key(action: dict[str, Any]) -> str:
    if action["kind"] == "managed-block":
        return f"{action['agent']}:{action['skill']}:{action['block_id']}:{action['path']}"
    return f"{action['agent']}:{action['skill']}:{action['path']}"
