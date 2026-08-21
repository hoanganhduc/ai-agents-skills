from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .capabilities import (
    existing_parents,
    normalized_path_within,
    resolved_path_within,
)
from .json_merge import extract_hook_entry, load_json_object, merge_hook_entry
from .lifecycle import mark_created_instruction_file_groups
from .managed_permissions import normalize_managed_parent_chain, restore_managed_modes
from .openclaw_target_gate import real_openclaw_path_block_reason
from .render import replace_or_append_block
from .runtime import apply_runtime_file_action, preflight_runtime_action
from .source_integrity import AttestedSource, open_attested_source
from .state import (
    artifact_signature,
    backup_file,
    load_state,
    now_run_id,
    save_state,
    sha256_file,
    sha256_text,
    signatures_match,
    symlink_atomic,
    upsert_artifact,
    upsert_run,
    upsert_uninstall_record,
    write_text_atomic,
    write_run_record,
)
from .windows_security import require_handle_bound_mutation


def apply_plan(root: Path, plan: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
    run_id = now_run_id()
    applied: list[dict[str, Any]] = []
    if dry_run:
        preflight_plan(root, plan["actions"])
        return {"run_id": run_id, "dry_run": True, "actions": plan["actions"]}
    require_handle_bound_mutation("install apply")
    if not plan["actions"]:
        return {"run_id": run_id, "dry_run": False, "actions": []}

    # Preflight before the relocation is written: a plan refused here applies
    # nothing, so it must also leave nothing behind, and re-keyed records are as
    # much a change to the installed state as a written file is.
    preflight_plan(root, plan["actions"])
    state = load_state(root)
    relocated = relocate_moved_artifact_records(state, plan["actions"])
    if relocated:
        save_state(root, state)
    for action in plan["actions"]:
        previous_state_artifact = find_state_artifact(state, action)
        result = apply_action(root, run_id, action)
        recorded_result = dict(result)
        if previous_state_artifact is not None:
            recorded_result["previous_state_artifact"] = previous_state_artifact
        recorded_result["permission_origin"] = merge_permission_origin(
            previous_state_artifact,
            recorded_result,
        )
        if not recorded_result["permission_origin"]:
            recorded_result.pop("permission_origin")
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
        upsert_run(state, run_id, len(applied))
        save_state(root, state)
        write_run_record(root, run_id, applied)
    # An instructions file holds a block per skill, and only the block that
    # created it records having done so.  Uninstall is scoped, so the block
    # removed last is usually not that one, and by then the record holding the
    # fact is gone from the state: the file survives every skill it ever held,
    # empty and owned by nobody.  Recording the fact on each of the file's
    # blocks keeps it available to whichever removal turns out to be the last.
    mark_created_instruction_file_groups(state.get("artifacts", []))
    upsert_run(state, run_id, len(applied))
    save_state(root, state)
    write_run_record(root, run_id, applied)
    result = {"run_id": run_id, "dry_run": False, "actions": applied}
    if relocated:
        result["relocated_records"] = relocated
    return result


def relocate_moved_artifact_records(
    state: dict[str, Any],
    actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Repoint state records at artifacts an agent has since moved, and report them.

    A record is keyed by the path it was installed to, so a target whose managed
    directory moves is no longer recognised at its own artifacts: the run adds a
    second record at the new path and the first survives, describing the same
    file under its former name.  Vendors do move these directories, and they
    leave a symlink behind, so the stale record keeps resolving -- to the file
    this run just rewrote.  Nothing then reconciles the two, and ``verify``
    reports a signature mismatch for every artifact that changed, against a
    record no installed file disagrees with.

    A record matches an action only when the two paths name one file on disk and
    the record's key is the one this action would have had at the old path.  The
    path alone is not identity: an agent's instructions file holds a managed
    block per skill, so every one of that agent's block records names the same
    file and a path-only match would claim whichever came first.  The claimed
    record is then rewritten with this action's key, which is how a record for
    one skill silently becomes a duplicate of another and the block it described
    is left in the file with nothing recording it.  Rebuilding the key at the
    recorded path costs nothing when no link is involved, because a path with no
    link in it resolves to itself and the keys already agree.

    Only the record moves.  Its origin travels with it, so a later uninstall
    still reverses the artifact the way it was installed, and the file itself is
    not read, written, or unlinked here.
    """
    records = state.get("artifacts", [])
    if not records:
        return []
    by_key = {item.get("key") for item in records}
    claimed: set[int] = set()
    relocated: list[dict[str, Any]] = []
    for action in actions:
        path = action.get("path")
        agent = action.get("agent")
        if not path or not agent:
            continue
        try:
            key = artifact_key(action)
        except KeyError:
            continue
        if key in by_key:
            continue
        real = os.path.realpath(path)
        for index, item in enumerate(records):
            if index in claimed or item.get("agent") != agent:
                continue
            recorded = item.get("artifact")
            if not recorded or recorded == path:
                continue
            if os.path.realpath(recorded) != real:
                continue
            if item.get("key") != artifact_key({**action, "path": recorded}):
                continue
            claimed.add(index)
            by_key.add(key)
            relocated.append({"agent": agent, "from": recorded, "to": path, "key": key})
            item["key"] = key
            item["artifact"] = path
            break
    return relocated


def find_state_artifact(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any] | None:
    key = artifact_key(action)
    for item in state.get("artifacts", []):
        if item.get("key") == key:
            return dict(item)
    return None


def apply_action(root: Path, run_id: str, action: dict[str, Any]) -> dict[str, Any]:
    expected_repairs = action.get("planned_parent_mode_changes")
    repairs = normalize_managed_parent_chain(
        root,
        action,
        create=True,
        expected=expected_repairs if isinstance(expected_repairs, list) else None,
    )
    normalized_created_dirs = [
        Path(item["path"]).relative_to(root).as_posix()
        for item in repairs
        if item.get("created_directory")
    ]
    result: dict[str, Any] | None = None
    try:
        if action["kind"] == "file":
            result = apply_file_action(root, run_id, action)
        elif action["kind"] == "runtime-file":
            result = apply_runtime_file_action(root, run_id, action, base_result(run_id, action))
        elif action["kind"] == "managed-block":
            result = apply_block_action(root, run_id, action)
        elif action["kind"] == "legacy-dir":
            result = apply_legacy_dir_action(root, run_id, action)
        elif action["kind"] == "managed-file-remove":
            result = apply_managed_file_remove_action(root, run_id, action)
        elif action["kind"] == "json-merge":
            result = apply_json_merge_action(root, run_id, action)
        elif action["kind"] == "toml-merge":
            result = apply_toml_merge_action(root, run_id, action)
        else:
            raise ValueError(f"unknown action kind: {action['kind']}")
        repairs.extend(normalize_managed_parent_chain(root, action, create=False))
    except Exception:
        if result and result.get("normalized_file_mode"):
            restore_managed_modes(root, [result["normalized_file_mode"]])
        restore_managed_modes(root, repairs)
        cleanup_created_parent_dirs(root, normalized_created_dirs)
        raise
    if normalized_created_dirs:
        result["created_parent_dirs"] = list(
            dict.fromkeys([*result.get("created_parent_dirs", []), *normalized_created_dirs])
        )
    if repairs:
        unique = {item["path"]: item for item in repairs}
        result["normalized_parent_modes"] = list(unique.values())
    return result


def merge_permission_origin(
    previous_state_artifact: dict[str, Any] | None,
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Preserve the earliest reversible mode while advancing installed mode."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    if previous_state_artifact:
        for item in previous_state_artifact.get("permission_origin", []):
            if isinstance(item, dict) and item.get("path"):
                key = (str(item.get("object_type") or "directory"), str(item["path"]))
                merged[key] = dict(item)
    changes = list(result.get("normalized_parent_modes", []))
    if isinstance(result.get("normalized_file_mode"), dict):
        file_change = {"object_type": "file", **result["normalized_file_mode"]}
        changes.append(file_change)
    for item in changes:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        normalized = {"object_type": item.get("object_type", "directory"), **item}
        key = (str(normalized["object_type"]), str(normalized["path"]))
        if key in merged:
            existing = merged[key]
            existing["installed_mode"] = normalized.get("installed_mode", existing.get("installed_mode"))
            if normalized.get("created_directory"):
                existing["created_directory"] = True
        else:
            merged[key] = normalized
    return list(merged.values())


def preflight_plan(root: Path, actions: list[dict[str, Any]]) -> None:
    for action in actions:
        preflight_action(root, action)


def preflight_action(root: Path, action: dict[str, Any]) -> None:
    if action["kind"] == "runtime-file":
        preflight_runtime_action(root, action)
        return
    path = Path(action["path"])
    openclaw_block = real_openclaw_path_block_reason(root, path, operation="apply", agent=str(action.get("agent")))
    if openclaw_block is not None:
        raise ValueError(openclaw_block)
    if not normalized_path_within(root, path) or not resolved_path_within(root, path.parent):
        raise ValueError(f"refusing to apply artifact outside selected root: {path}")
    for parent in existing_parents(path.parent, root):
        if parent.is_symlink():
            raise ValueError(f"refusing to apply through symlinked parent: {parent}")
        if not parent.is_dir():
            raise ValueError(f"refusing to apply through non-directory parent: {parent}")
    if action["kind"] in {"file", "managed-block", "managed-file-remove", "json-merge", "toml-merge"}:
        if path.exists() and path.is_dir() and not path.is_symlink():
            raise ValueError(f"refusing to write managed file over directory: {path}")
    if action["kind"] == "json-merge" and path.is_symlink():
        raise ValueError(f"refusing to merge into symlinked settings file: {path}")
    if action["kind"] == "toml-merge" and path.is_symlink():
        raise ValueError(f"refusing to merge into symlinked config file: {path}")
    if action["kind"] == "managed-block" and path.is_symlink():
        raise ValueError(f"refusing to read or replace symlinked instruction file: {path}")
    if action["kind"] == "legacy-dir":
        legacy_path = Path(action["legacy_path"])
        if path.is_symlink():
            raise ValueError(f"refusing to remove symlinked legacy path: {path}")
        if (
            not normalized_path_within(root, path)
            or not normalized_path_within(root, legacy_path)
            or not resolved_path_within(root, legacy_path.parent)
        ):
            raise ValueError(f"refusing to remove legacy path outside selected root: {path}")
    if action.get("operation") != "skip" and not planned_state_unchanged(path, action):
        raise ValueError(f"refusing to apply artifact because target changed since plan: {path}")


def planned_state_unchanged(path: Path, action: dict[str, Any]) -> bool:
    planned = action.get("current_signature")
    if planned is None:
        return True
    return signatures_match(artifact_signature(path), planned)


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
        if action.get("source_path"):
            with open_attested_source(
                Path(action["source_path"]), action.get("canonical_source_sha256")
            ):
                pass
        result["managed"] = True
        result["applied"] = False
        result["adopted"] = True
        result["new_hash"] = sha256_file(path)
        result["installed_signature"] = artifact_signature(path)
        return result
    source_path = action.get("source_path")
    if not source_path:
        return _apply_file_write(root, run_id, action, result, None)
    with open_attested_source(
        Path(source_path), action.get("canonical_source_sha256")
    ) as attested:
        return _apply_file_write(root, run_id, action, result, attested)


def _apply_file_write(
    root: Path,
    run_id: str,
    action: dict[str, Any],
    result: dict[str, Any],
    attested: AttestedSource | None,
) -> dict[str, Any]:
    path = Path(action["path"])
    backup = backup_file(root, run_id, path)
    created_parent_dirs = missing_parent_dirs(root, path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    actual_mode = action.get("install_mode", "copy")
    try:
        if actual_mode == "symlink":
            if attested is None:
                raise ValueError("symlink installation requires an attested canonical source")
            if not attested.matches_path(Path(action["source_path"])):
                raise ValueError("canonical symlink source changed before link creation")
            replace_with_symlink(path, Path(action["source_path"]))
            if not attested.matches_path(path):
                if path.exists() or path.is_symlink():
                    path.unlink()
                if backup is not None:
                    from .lifecycle import restore_backup

                    restore_backup(backup, path)
                raise ValueError("canonical symlink target changed while the link was installed")
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
    if attested is not None:
        result["source_binding"] = (
            "mutable-symlink" if actual_mode == "symlink" else "held-descriptor"
        )
    result["installed_signature"] = artifact_signature(path)
    if created_parent_dirs:
        result["created_parent_dirs"] = [item.as_posix() for item in created_parent_dirs]
    return result


def apply_json_merge_action(root: Path, run_id: str, action: dict[str, Any]) -> dict[str, Any]:
    path = Path(action["path"])
    result = base_result(run_id, action)
    result["created_file"] = not path.exists()
    result["previous_signature"] = artifact_signature(path)
    if action.get("operation") in {"skip", "noop"}:
        result["managed"] = action.get("operation") == "noop"
        result["applied"] = False
        result["installed_signature"] = artifact_signature(path)
        if action.get("reason"):
            result["reason"] = action["reason"]
        return result
    before, _existed = load_json_object(path)
    merged, changed, created = merge_hook_entry(
        before, action["event"], action["entry"], action["managed_id"]
    )
    result["managed"] = True
    result["event"] = action["event"]
    result["managed_id"] = action["managed_id"]
    result["created_containers"] = created
    if not changed:
        result["applied"] = False
        result["installed_signature"] = artifact_signature(path)
        return result
    backup = backup_file(root, run_id, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps(merged, indent=2, sort_keys=True) + "\n")
    # The installer owns one hook entry, never the whole settings file, so verify
    # needs the entry itself to compare against; a whole-file hash would report
    # every ordinary user edit as drift.
    result["managed_entry"] = extract_hook_entry(merged, action["event"], action["managed_id"])
    result["applied"] = True
    result["backup"] = str(backup) if backup else None
    result["installed_signature"] = artifact_signature(path)
    return result


def apply_toml_merge_action(root: Path, run_id: str, action: dict[str, Any]) -> dict[str, Any]:
    from .toml_merge import load_toml_text, merge_managed_block

    path = Path(action["path"])
    result = base_result(run_id, action)
    result["created_file"] = not path.exists()
    result["previous_signature"] = artifact_signature(path)
    if action.get("operation") in {"skip", "noop"}:
        result["managed"] = action.get("operation") == "noop"
        result["applied"] = False
        result["installed_signature"] = artifact_signature(path)
        if action.get("reason"):
            result["reason"] = action["reason"]
        return result
    before, _existed = load_toml_text(path)
    merged, changed = merge_managed_block(before, action["managed_id"], action["body"])
    result["managed"] = True
    result["managed_id"] = action["managed_id"]
    if not changed:
        result["applied"] = False
        result["installed_signature"] = artifact_signature(path)
        return result
    backup = backup_file(root, run_id, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, merged)
    # Same reason as the hook merge above: the managed region is what verify can
    # legitimately hold the user's config file to.
    result["managed_body"] = action["body"]
    result["applied"] = True
    result["backup"] = str(backup) if backup else None
    result["installed_signature"] = artifact_signature(path)
    return result


def replace_with_text(path: Path, content: str) -> None:
    write_text_atomic(path, content)


def replace_with_symlink(path: Path, source_path: Path) -> None:
    if not source_path.exists():
        raise FileNotFoundError(f"symlink source does not exist: {source_path}")
    symlink_atomic(path, source_path)


def apply_block_action(root: Path, run_id: str, action: dict[str, Any]) -> dict[str, Any]:
    path = Path(action["path"])
    result = base_result(run_id, action)
    result["created_file"] = not path.exists()
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    result["previous_hash"] = sha256_text(before)
    result["previous_signature"] = artifact_signature(path)
    if action.get("operation") in {"skip", "noop"}:
        result["managed"] = action.get("operation") == "noop"
        result["applied"] = False
        result["installed_signature"] = artifact_signature(path)
        if action.get("operation") == "noop":
            result["block_id"] = action.get("block_id")
            result["managed_block"] = action.get("content", "").strip()
        return result
    backup = backup_file(root, run_id, path)
    after = replace_or_append_block(before, action["skill"], action["content"])
    created_parent_dirs = missing_parent_dirs(root, path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, after)
    result["managed"] = True
    result["applied"] = before != after
    result["backup"] = str(backup) if backup else None
    result["new_hash"] = sha256_file(path)
    result["installed_signature"] = artifact_signature(path)
    result["block_id"] = action.get("block_id")
    result["managed_block"] = action["content"].strip()
    if created_parent_dirs:
        result["created_parent_dirs"] = [item.as_posix() for item in created_parent_dirs]
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
        result["managed"] = False
        result["state_operation"] = None
        result["installed_signature"] = artifact_signature(path)
        return result
    expected_signature = action.get("installed_signature")
    current_signature = artifact_signature(path)
    if expected_signature and current_signature.get("exists") and not signatures_match(current_signature, expected_signature):
        result["operation"] = "skip-conflict"
        result["applied"] = False
        result["managed"] = False
        result["state_operation"] = None
        result["reason"] = "managed file changed since install"
        result["installed_signature"] = current_signature
        return result
    backup = backup_file(root, run_id, path)
    result["backup"] = str(backup) if backup else None
    if path.exists() or path.is_symlink():
        path.unlink()
        if action.get("artifact_type") == "skill-support-file":
            cleanup_created_parent_dirs(root, action.get("created_parent_dirs", []))
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


def missing_parent_dirs(root: Path, parent: Path) -> list[Path]:
    missing: list[Path] = []
    current = parent
    while current != root and not current.exists():
        missing.append(current.relative_to(root))
        current = current.parent
    return missing


def cleanup_created_parent_dirs(root: Path, relative_dirs: list[str]) -> None:
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
    if action.get("mode_reason"):
        result["mode_reason"] = action["mode_reason"]
    if action.get("capability_evidence"):
        result["capability_evidence"] = action["capability_evidence"]
    if action.get("fallback_mode"):
        result["fallback_mode"] = action["fallback_mode"]
    if action.get("created_parent_dirs"):
        result["created_parent_dirs"] = action["created_parent_dirs"]
    for key in (
        "owner",
        "source_relpath",
        "target_relpath",
        "source_sha256",
        "canonical_source_sha256",
        "mode",
        "newline_policy",
        "file_type",
        "platforms",
        "runtime_root",
        "runtime_skill",
        "reason",
        "declared_exclusion",
        "exclusion_code",
    ):
        if key in action:
            result[key] = action[key]
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
    if result.get("artifact_type") == "settings-hook-merge" and result.get("applied"):
        return {
            "action": "merge-remove",
            "event": result.get("event"),
            "managed_id": result.get("managed_id"),
            "created_containers": result.get("created_containers"),
            "created_file": result.get("created_file"),
            "backup": result.get("backup"),
        }
    if result.get("artifact_type") == "settings-compat-merge" and result.get("applied"):
        return {
            "action": "toml-block-remove",
            "managed_id": result.get("managed_id"),
            "created_file": result.get("created_file"),
            "backup": result.get("backup"),
        }
    if result.get("state_operation") == "remove":
        # The guard above lets only an ``unmanage-only`` origin reach here, and
        # only alongside a backup this removal just took.  That origin describes
        # a file left where it was found, which the file no longer is: carrying
        # it forward records the one outcome that cannot bring it back, and
        # because ``unmanage-only`` keeps no tombstone the backup holding the
        # user's own text becomes unreachable.  What makes a removal reversible
        # is the backup, so the backup decides.
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
