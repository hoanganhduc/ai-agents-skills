from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capabilities import looks_like_real_system_root, normalized_path_within, resolved_path_within
from .discovery import current_platform
from .openclaw_target_evidence import build_authorizing_target_evidence
from .openclaw_target_manifest import (
    load_target_manifest,
    target_manifest_authorizes_real_writes,
    validate_target_manifest,
)
from .openclaw_target_paths import (
    OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE,
    checked_openclaw_target_relative_path,
    openclaw_home,
    openclaw_managed_skills_dir,
    openclaw_skill_file_attestation,
    openclaw_skill_file_attestation_issue,
    openclaw_target_path,
    validate_openclaw_target_home,
)
from .state import (
    artifact_signature,
    existing_contained_parents,
    now_run_id,
    preflight_state_path,
    sha256_text,
    signatures_match,
    write_text_atomic,
)
from .windows_security import require_handle_bound_mutation


OPENCLAW_TARGET_STATE_VERSION = 1
OPENCLAW_TARGET_STATE_NAME = "openclaw-target-state.json"
OPENCLAW_EXECUTABLE_NAMES = frozenset({"openclaw"})
OPENCLAW_CHILD_PATH = "/usr/bin:/bin"


@dataclass(frozen=True)
class AttestedOpenClawExecutable:
    path: Path
    entry_identity: tuple[int, int, int, int, int, int, int]
    entry_target: str
    target_path: Path
    target_identity: tuple[int, int, int, int, int, int, int]
    node_path: Path
    node_identity: tuple[int, int, int, int, int, int, int]


def _executable_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
        int(info.st_mode),
        int(info.st_uid),
    )


def _attest_posix_parent_chain(path: Path, *, allow_current_user: bool) -> None:
    current = path
    while True:
        info = os.lstat(current)
        allowed_owners = {0, int(os.geteuid())} if allow_current_user else {0}
        writable = bool(stat.S_IMODE(info.st_mode) & 0o022)
        protected_shared_parent = bool(
            int(info.st_uid) == 0
            and stat.S_IMODE(info.st_mode) & stat.S_ISVTX
        )
        if (
            not stat.S_ISDIR(info.st_mode)
            or int(info.st_uid) not in allowed_owners
            or (writable and not protected_shared_parent)
        ):
            raise ValueError("OpenClaw executable has an unsafe parent chain")
        if current.parent == current:
            break
        current = current.parent


def _attest_regular_executable(path: Path, *, root_only: bool) -> tuple[int, int, int, int, int, int, int]:
    info = os.lstat(path)
    allowed_owners = {0} if root_only else {0, int(os.geteuid())}
    if (
        not stat.S_ISREG(info.st_mode)
        or int(info.st_nlink) != 1
        or int(info.st_uid) not in allowed_owners
        or stat.S_IMODE(info.st_mode) & 0o022
        or not stat.S_IMODE(info.st_mode) & 0o111
    ):
        raise ValueError("OpenClaw executable failed ownership or mode checks")
    _attest_posix_parent_chain(path.parent, allow_current_user=not root_only)
    return _executable_identity(info)


def attest_openclaw_executable(value: str | os.PathLike[str] | None) -> AttestedOpenClawExecutable:
    """Admit one absolute, owner-controlled OpenClaw npm entrypoint.

    Native Windows execution remains fail-closed until the executable can be
    launched through the same validated handle.  POSIX launches below bind the
    admitted inode to an inherited descriptor immediately before every exec.
    """

    if value is None or not str(value).strip():
        raise ValueError("--openclaw-bin must name an attested absolute executable")
    supplied = Path(str(value))
    if not supplied.is_absolute() or supplied != Path(os.path.abspath(supplied)):
        raise ValueError("--openclaw-bin must be an absolute normalized path")
    if supplied.name.casefold() not in OPENCLAW_EXECUTABLE_NAMES:
        raise ValueError("--openclaw-bin basename must be exactly 'openclaw'")
    if os.name == "nt":
        raise ValueError(
            "native Windows OpenClaw execution is disabled until handle-bound launch is available"
        )
    try:
        entry_info = os.lstat(supplied)
    except OSError as exc:
        raise ValueError("--openclaw-bin is unavailable") from exc
    _attest_posix_parent_chain(supplied.parent, allow_current_user=True)
    entry_target = ""
    if stat.S_ISLNK(entry_info.st_mode):
        entry_target = os.readlink(supplied)
        if not entry_target or "\x00" in entry_target:
            raise ValueError("--openclaw-bin symlink target is invalid")
        target_path = Path(os.path.abspath(supplied.parent / entry_target))
        package_suffix = ("lib", "node_modules", "openclaw", "openclaw.mjs")
        if target_path.parts[-len(package_suffix):] != package_suffix:
            raise ValueError("--openclaw-bin must resolve to the pinned npm OpenClaw entrypoint")
    elif stat.S_ISREG(entry_info.st_mode):
        target_path = supplied
    else:
        raise ValueError("--openclaw-bin must be a regular file or one pinned npm symlink")
    target_identity = _attest_regular_executable(target_path, root_only=False)
    target_descriptor = os.open(
        target_path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0),
    )
    try:
        if _executable_identity(os.fstat(target_descriptor)) != target_identity:
            raise ValueError("OpenClaw npm entrypoint changed while binding")
        shebang = os.read(target_descriptor, 128).splitlines()[0]
    finally:
        os.close(target_descriptor)
    if shebang not in {b"#!/usr/bin/env node", b"#!/usr/bin/node"}:
        raise ValueError("OpenClaw entrypoint must use the approved Node interpreter")
    node_path = Path("/usr/bin/node")
    try:
        node_identity = _attest_regular_executable(node_path, root_only=True)
    except (OSError, ValueError) as exc:
        raise ValueError("the attested /usr/bin/node interpreter is unavailable") from exc
    return AttestedOpenClawExecutable(
        supplied,
        _executable_identity(entry_info),
        entry_target,
        target_path,
        target_identity,
        node_path,
        node_identity,
    )


def apply_target_manifest_file(
    manifest_path: Path,
    root: Path,
    *,
    dry_run: bool = True,
    real_system: bool = False,
    confirm_phrase: str | None = None,
    post_apply_check: bool = True,
    openclaw_bin: str | None = None,
) -> dict[str, Any]:
    manifest = load_target_manifest(manifest_path)
    return apply_target_manifest(
        manifest,
        root,
        dry_run=dry_run,
        real_system=real_system,
        confirm_phrase=confirm_phrase,
        post_apply_check=post_apply_check,
        openclaw_bin=openclaw_bin,
    )


def apply_target_manifest(
    manifest: dict[str, Any],
    root: Path,
    *,
    dry_run: bool = True,
    real_system: bool = False,
    confirm_phrase: str | None = None,
    post_apply_check: bool = True,
    openclaw_bin: str | None = None,
) -> dict[str, Any]:
    if not dry_run:
        require_handle_bound_mutation("OpenClaw real-target apply")
    validate_target_manifest(manifest, require_approved=not dry_run)
    expanded = root.expanduser()
    planned = [plan_apply_action(expanded, manifest, action) for action in manifest["actions"]]
    if dry_run:
        return {"dry_run": True, "manifest_id": manifest["manifest_id"], "actions": planned}
    require_real_system_ack(expanded, real_system=real_system, confirm_phrase=confirm_phrase)
    if not target_manifest_authorizes_real_writes(manifest):
        raise ValueError("OpenClaw target manifest is not approved for real writes")
    preflight_apply(expanded, manifest, planned)
    state = load_openclaw_target_state(expanded)
    run_id = now_run_id()
    append_transaction(
        expanded,
        state,
        {
            "run_id": run_id,
            "manifest_id": manifest["manifest_id"],
            "status": "pending",
            "actions": planned,
        },
    )
    applied = []
    superseded_records: list[dict[str, Any]] = []
    try:
        for planned_action, action in zip(planned, manifest["actions"], strict=True):
            result = apply_one_action(expanded, run_id, action, planned_action)
            applied.append(result)
            if result_is_state_recordable(result):
                record = state_record(result)
                superseded_records.append(
                    {
                        "new_key": record["key"],
                        "records": replace_artifact_record(state, record),
                    }
                )
                save_openclaw_target_state(expanded, state)
        verify_installed_attestations(expanded, [item for item in applied if result_is_state_recordable(item)])
        if post_apply_check:
            post_apply_native_check(
                expanded,
                [item for item in applied if result_is_state_recordable(item)],
                openclaw_bin=openclaw_bin,
            )
        verify_installed_attestations(expanded, [item for item in applied if result_is_state_recordable(item)])
        complete_transaction(expanded, state, run_id, status="applied")
    except Exception:
        rollback_applied_actions(expanded, applied)
        state = load_openclaw_target_state(expanded)
        remove_artifact_records(
            state,
            {item["key"] for item in applied if result_is_state_recordable(item)},
        )
        for change in reversed(superseded_records):
            for record in change["records"]:
                replace_artifact_record(state, record)
        complete_transaction(expanded, state, run_id, status="rolled-back-after-failure")
        raise
    state.setdefault("runs", []).append(
        {
            "run_id": run_id,
            "manifest_id": manifest["manifest_id"],
            "action_count": len(applied),
        }
    )
    save_openclaw_target_state(expanded, state)
    return {
        "dry_run": False,
        "run_id": run_id,
        "manifest_id": manifest["manifest_id"],
        "actions": applied,
    }


def uninstall_target_manifest(
    root: Path,
    *,
    manifest_id: str | None = None,
    dry_run: bool = True,
    real_system: bool = False,
    confirm_phrase: str | None = None,
) -> dict[str, Any]:
    if not dry_run:
        require_handle_bound_mutation("OpenClaw real-target uninstall")
    expanded = root.expanduser()
    validate_openclaw_target_home(expanded)
    state = load_openclaw_target_state(expanded)
    records = [
        item
        for item in state.get("artifacts", [])
        if manifest_id is None or item.get("manifest_id") == manifest_id
    ]
    actions = [plan_uninstall_action(expanded, record) for record in records]
    if dry_run:
        return {"dry_run": True, "manifest_id": manifest_id, "actions": actions}
    require_real_system_ack(expanded, real_system=real_system, confirm_phrase=confirm_phrase)
    results = [apply_uninstall_action(expanded, action) for action in actions]
    completed = {action["key"] for action in results if action.get("completed")}
    cleanup_created_parents(
        expanded,
        [
            relative_dir
            for action in results
            if action.get("completed")
            for relative_dir in action.get("created_parent_dirs", [])
        ],
    )
    remove_artifact_records(state, completed)
    state.setdefault("runs", []).append(
        {
            "run_id": now_run_id(),
            "manifest_id": manifest_id,
            "operation": "uninstall",
            "action_count": len(results),
        }
    )
    save_openclaw_target_state(expanded, state)
    return {
        "dry_run": False,
        "manifest_id": manifest_id,
        "actions": results,
        "removed": sorted(completed),
    }


def plan_apply_action(root: Path, manifest: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    relative_path = checked_openclaw_target_relative_path(
        action["target"]["relative_path"],
        action_class=action["action_class"],
    )
    path = openclaw_target_path(root, relative_path, action_class=action["action_class"])
    current = openclaw_skill_file_attestation(path)
    planned = {
        "key": f"{manifest['manifest_id']}:{action['action_id']}",
        "manifest_id": manifest["manifest_id"],
        "action_id": action["action_id"],
        "action_class": action["action_class"],
        "operation": action["operation"],
        "skill": action["skill"],
        "relative_path": relative_path,
        "canonical_source_hash": action["canonical_source_hash"],
        "expected_hash": action["expected_hash"],
        "pre_state": action["pre_state"],
        "current_pre_state": current,
        "drift": not signatures_match(current, action["pre_state"]),
    }
    reason = target_path_safety_reason(
        root,
        path,
        action_class=action["action_class"],
        signature=current,
    )
    if reason is not None:
        planned["blocked"] = True
        planned["reason"] = reason
    elif planned["drift"]:
        planned["blocked"] = True
        planned["reason"] = "target-pre-state-drift"
    elif action["operation"] == "no-op":
        planned["blocked"] = False
        planned["reason"] = "ready-to-adopt"
    else:
        planned["blocked"] = False
        planned["reason"] = "ready"
    return planned


def apply_one_action(
    root: Path,
    run_id: str,
    action: dict[str, Any],
    planned_action: dict[str, Any],
) -> dict[str, Any]:
    result = dict(planned_action)
    result["run_id"] = run_id
    result["applied"] = False
    if planned_action.get("blocked"):
        raise ValueError(f"OpenClaw target apply action is blocked: {planned_action['reason']}")
    path = openclaw_target_path(root, planned_action["relative_path"], action_class=action["action_class"])
    if action["operation"] == "no-op":
        current = openclaw_skill_file_attestation(path)
        if current != action["pre_state"]:
            raise ValueError("OpenClaw target pre-state changed before adoption")
        reason = target_path_safety_reason(
            root,
            path,
            action_class=action["action_class"],
            signature=current,
        )
        if reason is not None:
            raise ValueError(f"OpenClaw target path is unsafe: {reason}")
        if sha256_text(action["content"]) != action["expected_hash"]:
            raise ValueError("OpenClaw target action content hash changed before adoption")
        if current.get("kind") != "file" or current.get("hash") != action["expected_hash"]:
            raise ValueError("OpenClaw target file content does not match approved managed content")
        result["adopted"] = True
        result["attestation"] = "adopted-identical"
        result["source_hash"] = action["expected_hash"]
        result["canonical_source_hash"] = action["canonical_source_hash"]
        result["path"] = str(path)
        result["installed_hash"] = current["hash"]
        result["installed_signature"] = current
        result["created_parent_dirs"] = []
        return result
    current = openclaw_skill_file_attestation(path)
    if not signatures_match(current, action["pre_state"]):
        raise ValueError("OpenClaw target pre-state changed before write")
    reason = target_path_safety_reason(
        root,
        path,
        action_class=action["action_class"],
        signature=current,
    )
    if reason is not None:
        raise ValueError(f"OpenClaw target path is unsafe: {reason}")
    created_parents = missing_parent_dirs(openclaw_home(root), path.parent)
    content = action["content"]
    if sha256_text(content) != action["expected_hash"]:
        raise ValueError("OpenClaw target action content hash changed before write")
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        for relative_parent in created_parents:
            (openclaw_home(root) / relative_parent).chmod(0o700)
    write_text_atomic(path, content)
    result["applied"] = True
    result["path"] = str(path)
    result["installed_hash"] = sha256_text(content)
    result["installed_signature"] = openclaw_skill_file_attestation(path)
    result["source_hash"] = action["expected_hash"]
    result["canonical_source_hash"] = action["canonical_source_hash"]
    result["attestation"] = "created"
    result["created_parent_dirs"] = [item.as_posix() for item in created_parents]
    return result


def preflight_apply(root: Path, manifest: dict[str, Any], planned: list[dict[str, Any]]) -> None:
    validate_openclaw_target_home(root)
    if str(openclaw_home(root).resolve(strict=False)) != str(manifest["target_realpath"]):
        raise ValueError("OpenClaw target manifest does not match selected root")
    if str(openclaw_managed_skills_dir(root).resolve(strict=False)) != str(manifest["managed_skills_realpath"]):
        raise ValueError("OpenClaw target manifest managed skills root does not match selected root")
    preflight_state_path(root, openclaw_target_state_file(root))
    blocked = [action for action in planned if action.get("blocked")]
    if blocked:
        reasons = ", ".join(sorted({str(action["reason"]) for action in blocked}))
        raise ValueError(f"OpenClaw target manifest apply preflight failed: {reasons}")


def require_real_system_ack(root: Path, *, real_system: bool, confirm_phrase: str | None) -> None:
    if looks_like_real_system_root(root) and not real_system:
        raise ValueError("real-system OpenClaw target writes require --real-system")
    if confirm_phrase != OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE:
        raise ValueError("OpenClaw real-system write confirmation phrase did not match")


def target_path_safety_reason(
    root: Path,
    path: Path,
    *,
    action_class: str,
    signature: dict[str, object] | None = None,
) -> str | None:
    try:
        relative_path = path.relative_to(openclaw_home(root)).as_posix()
        checked_openclaw_target_relative_path(relative_path, action_class=action_class)
    except ValueError as exc:
        return str(exc)
    if not normalized_path_within(openclaw_home(root), path):
        return "target path escapes .openclaw"
    current = signature if signature is not None else openclaw_skill_file_attestation(path)
    issue = openclaw_skill_file_attestation_issue(root, current, path=path)
    if issue is not None:
        return issue
    if not resolved_path_within(openclaw_home(root), path.parent):
        return "target path resolves outside .openclaw"
    for parent in existing_contained_parents(path.parent, openclaw_home(root)):
        if parent.is_symlink():
            return "target path has a symlinked parent"
        if not parent.is_dir():
            return "target path has a non-directory parent"
        if os.name == "posix":
            parent_info = os.lstat(parent)
            root_info = os.lstat(root)
            if parent_info.st_uid != root_info.st_uid:
                return "target path parent owner does not match target root owner"
            if parent_info.st_mode & 0o022:
                return "target path parent is group/world writable"
        elif os.name == "nt":
            from .windows_security import private_directory_issue

            issue = private_directory_issue(parent)
            if issue is not None:
                return f"target path parent DACL is unsafe: {issue}"
    return None


def plan_uninstall_action(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    relative_path = checked_openclaw_target_relative_path(
        record["relative_path"],
        action_class=record["action_class"],
    )
    path = openclaw_target_path(root, relative_path, action_class=record["action_class"])
    current = openclaw_skill_file_attestation(path)
    operation = "delete-created"
    reason = "ready"
    if record.get("attestation") == "adopted-identical":
        operation = "forget-adopted"
        reason = "preexisting-identical-file"
    elif not current.get("exists"):
        operation = "forget-missing"
        reason = "already-missing"
    elif current != record.get("installed_signature"):
        operation = "skip-conflict"
        reason = "artifact-changed-since-openclaw-target-apply"
    safety = target_path_safety_reason(
        root,
        path,
        action_class=record["action_class"],
        signature=current,
    )
    if operation == "delete-created" and safety is not None:
        operation = "skip-conflict"
        reason = safety
    return {
        "key": record["key"],
        "manifest_id": record["manifest_id"],
        "action_id": record["action_id"],
        "action_class": record["action_class"],
        "skill": record["skill"],
        "relative_path": relative_path,
        "operation": operation,
        "reason": reason,
        "installed_hash": record.get("installed_hash"),
        "installed_signature": record.get("installed_signature"),
        "created_parent_dirs": record.get("created_parent_dirs", []),
    }


def apply_uninstall_action(root: Path, action: dict[str, Any]) -> dict[str, Any]:
    result = dict(action)
    result["completed"] = False
    operation = action["operation"]
    if operation in {"forget-adopted", "forget-missing", "skip-conflict"}:
        result["completed"] = operation in {"forget-adopted", "forget-missing"}
        return result
    if operation != "delete-created":
        raise ValueError(f"unsupported OpenClaw target uninstall operation: {operation}")
    path = openclaw_target_path(root, action["relative_path"], action_class=action["action_class"])
    reason = target_path_safety_reason(root, path, action_class=action["action_class"])
    if reason is not None:
        result["operation"] = "skip-conflict"
        result["reason"] = reason
        return result
    expected_signature = action.get("installed_signature")
    if expected_signature is not None and openclaw_skill_file_attestation(path) != expected_signature:
        result["operation"] = "skip-conflict"
        result["reason"] = "artifact-changed-since-openclaw-target-apply"
        return result
    path.unlink()
    result["completed"] = True
    return result


def state_record(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": result["key"],
        "manifest_id": result["manifest_id"],
        "action_id": result["action_id"],
        "action_class": result["action_class"],
        "skill": result["skill"],
        "relative_path": result["relative_path"],
        "installed_hash": result["installed_hash"],
        "installed_signature": result["installed_signature"],
        "source_hash": result["source_hash"],
        "canonical_source_hash": result["canonical_source_hash"],
        "attestation": result["attestation"],
        "created_parent_dirs": result.get("created_parent_dirs", []),
        "run_id": result["run_id"],
    }


def result_is_state_recordable(result: dict[str, Any]) -> bool:
    return result.get("applied") is True or result.get("adopted") is True


def replace_artifact_record(
    state: dict[str, Any],
    record: dict[str, Any],
) -> list[dict[str, Any]]:
    artifacts = state.setdefault("artifacts", [])
    replaced = [
        item
        for item in artifacts
        if item.get("key") == record["key"]
        or item.get("relative_path") == record["relative_path"]
    ]
    state["artifacts"] = [item for item in artifacts if item not in replaced]
    state["artifacts"].append(record)
    return replaced


def rollback_applied_actions(root: Path, applied: list[dict[str, Any]]) -> None:
    for action in reversed(applied):
        if not action.get("applied"):
            continue
        path = openclaw_target_path(root, action["relative_path"], action_class=action["action_class"])
        if openclaw_skill_file_attestation(path) == action.get("installed_signature"):
            path.unlink()
    cleanup_created_parents(
        root,
        [
            relative_dir
            for action in applied
            if action.get("applied")
            for relative_dir in action.get("created_parent_dirs", [])
        ],
    )


def post_apply_native_check(
    root: Path,
    applied: list[dict[str, Any]],
    *,
    openclaw_bin: str | None,
) -> None:
    for action in applied:
        verify_installed_attestations(root, [action])
        if not openclaw_skill_visible(root, action["skill"], openclaw_bin=openclaw_bin):
            raise ValueError(f"OpenClaw native loader did not report managed skill: {action['skill']}")
        verify_installed_attestations(root, [action])


def verify_installed_attestations(root: Path, applied: list[dict[str, Any]]) -> None:
    """Rehash managed bytes and recheck path authority immediately around use."""
    for action in applied:
        path = openclaw_target_path(
            root,
            action["relative_path"],
            action_class=action["action_class"],
        )
        current = openclaw_skill_file_attestation(path)
        if current != action.get("installed_signature"):
            raise ValueError(
                f"OpenClaw target file changed after apply/adoption: {action['skill']}"
            )
        if current.get("hash") != action.get("installed_hash"):
            raise ValueError(
                f"OpenClaw target file hash changed after apply/adoption: {action['skill']}"
            )
        reason = target_path_safety_reason(
            root,
            path,
            action_class=action["action_class"],
            signature=current,
        )
        if reason is not None:
            raise ValueError(f"OpenClaw target path became unsafe: {reason}")


def openclaw_skill_visible(
    root: Path,
    skill: str,
    *,
    openclaw_bin: str | AttestedOpenClawExecutable | None,
) -> bool:
    executable = (
        openclaw_bin
        if isinstance(openclaw_bin, AttestedOpenClawExecutable)
        else attest_openclaw_executable(openclaw_bin)
    )
    result = run_openclaw_json(root, executable, ["skills", "list", "--json"])
    skills = result.get("skills", [])
    if not isinstance(skills, list):
        return False
    for item in skills:
        if not isinstance(item, dict):
            continue
        if item.get("name") == skill and item.get("source") == "openclaw-managed":
            return True
    return False


def probe_openclaw_target(
    root: Path,
    *,
    openclaw_bin: str | None,
    skill: str | None = None,
    include_canary: bool = False,
    platform: str | None = None,
    path_style: str = "posix",
    captured_at: str | None = None,
) -> dict[str, Any]:
    expanded = root.expanduser()
    paths = validate_openclaw_target_home(expanded)
    executable = attest_openclaw_executable(openclaw_bin)
    platform_name = target_evidence_platform(platform or current_platform())
    version = run_openclaw_text(expanded, executable, ["--version"])
    skills_help = run_openclaw_text(expanded, executable, ["skills", "--help"])
    skills_list = run_openclaw_json(expanded, executable, ["skills", "list", "--json"])
    managed_dir = str(Path(str(skills_list.get("managedSkillsDir", ""))).expanduser().resolve(strict=False))
    expected_managed_dir = paths["managed_skills_realpath"]
    if managed_dir != expected_managed_dir:
        raise ValueError("OpenClaw native managed skills directory does not match selected target root")
    quiescence = quiescence_checks(expanded, openclaw_bin=executable)
    if not quiescence["quiescent"]:
        raise ValueError("OpenClaw target is not quiescent")
    target_skill = skill or "ai-agents-skills-canary"
    target_path = openclaw_target_path(
        expanded,
        f"skills/{target_skill}/SKILL.md",
        action_class="canary-skill-file" if target_skill == "ai-agents-skills-canary" else "managed-skill-file",
    )
    evidence = [
        build_authorizing_target_evidence(
            evidence_type="native-loader",
            platform=platform_name,
            path_style=path_style,
            observed_behavior="OpenClaw executable and skills command are available",
            target_realpath=paths["home_realpath"],
            managed_skills_realpath=paths["managed_skills_realpath"],
            checks={
                "openclaw_version": version,
                "skills_help_contains_list": "list" in skills_help,
            },
            captured_at=captured_at,
            openclaw_version=version,
        ),
        build_authorizing_target_evidence(
            evidence_type="native-managed-skill-root",
            platform=platform_name,
            path_style=path_style,
            observed_behavior="OpenClaw reports the selected managed skills directory",
            target_realpath=paths["home_realpath"],
            managed_skills_realpath=paths["managed_skills_realpath"],
            checks={
                "reported_managedSkillsDir": managed_dir,
                "expected_managedSkillsDir": expected_managed_dir,
            },
            captured_at=captured_at,
            openclaw_version=version,
        ),
        build_authorizing_target_evidence(
            evidence_type="target-pre-state",
            platform=platform_name,
            path_style=path_style,
            observed_behavior=f"OpenClaw target pre-state captured for {target_skill}",
            target_realpath=paths["home_realpath"],
            managed_skills_realpath=paths["managed_skills_realpath"],
            checks={
                "relative_path": f"skills/{target_skill}/SKILL.md",
                "pre_state": artifact_signature(target_path),
            },
            captured_at=captured_at,
            openclaw_version=version,
        ),
        build_authorizing_target_evidence(
            evidence_type="quiescence-lock",
            platform=platform_name,
            path_style=path_style,
            observed_behavior="No active OpenClaw process or known OpenClaw lock file was detected",
            target_realpath=paths["home_realpath"],
            managed_skills_realpath=paths["managed_skills_realpath"],
            checks=quiescence,
            captured_at=captured_at,
            openclaw_version=version,
        ),
    ]
    if include_canary:
        if not openclaw_skill_visible(expanded, target_skill, openclaw_bin=executable):
            raise ValueError("OpenClaw managed canary skill is not visible to the native loader")
        evidence.append(
            build_authorizing_target_evidence(
                evidence_type="native-managed-skill-canary",
                platform=platform_name,
                path_style=path_style,
                observed_behavior=f"OpenClaw native skills list reports managed canary skill {target_skill}",
                target_realpath=paths["home_realpath"],
                managed_skills_realpath=paths["managed_skills_realpath"],
                checks={
                    "canary_skill": target_skill,
                    "source": "openclaw-managed",
                },
                captured_at=captured_at,
                openclaw_version=version,
            )
        )
    return {
        "status": "ok",
        "target": "openclaw",
        "root": str(expanded),
        "target_realpath": paths["home_realpath"],
        "managed_skills_realpath": paths["managed_skills_realpath"],
        "evidence": evidence,
    }


def run_openclaw_json(
    root: Path,
    executable: AttestedOpenClawExecutable,
    arguments: list[str],
) -> dict[str, Any]:
    text = run_openclaw_text(root, executable, arguments)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("OpenClaw command did not return valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("OpenClaw command returned non-object JSON")
    return data


def openclaw_env(root: Path) -> dict[str, str]:
    return {
        "HOME": str(root),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NO_COLOR": "1",
        "OPENCLAW_STATE_DIR": str(openclaw_home(root)),
        "PATH": OPENCLAW_CHILD_PATH,
    }


def run_openclaw_text(
    root: Path,
    executable: AttestedOpenClawExecutable,
    arguments: list[str],
) -> str:
    current = attest_openclaw_executable(executable.path)
    if current != executable:
        raise ValueError("OpenClaw executable changed after attestation")
    target_descriptor = os.open(
        executable.target_path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0),
    )
    node_descriptor = os.open(
        executable.node_path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0),
    )
    try:
        if _executable_identity(os.fstat(target_descriptor)) != executable.target_identity:
            raise ValueError("OpenClaw npm entrypoint identity changed while binding")
        if _executable_identity(os.fstat(node_descriptor)) != executable.node_identity:
            raise ValueError("OpenClaw Node interpreter identity changed while binding")
        os.set_inheritable(target_descriptor, True)
        os.set_inheritable(node_descriptor, True)
        target_fd_path = None
        node_fd_path = None
        for prefix in ("/proc/self/fd", "/dev/fd"):
            target_candidate = f"{prefix}/{target_descriptor}"
            node_candidate = f"{prefix}/{node_descriptor}"
            if os.path.exists(target_candidate) and os.path.exists(node_candidate):
                target_fd_path = target_candidate
                node_fd_path = node_candidate
                break
        if target_fd_path is None or node_fd_path is None:
            raise ValueError("descriptor-bound OpenClaw execution is unavailable")
        completed = subprocess.run(
            [node_fd_path, target_fd_path, *arguments],
            cwd=str(root),
            env=openclaw_env(root),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(node_descriptor, target_descriptor),
            check=False,
            timeout=20,
        )
    finally:
        os.close(node_descriptor)
        os.close(target_descriptor)
    if completed.returncode != 0:
        raise ValueError("OpenClaw command failed")
    return completed.stdout.strip()


def _read_proc_value(path: Path, *, max_bytes: int) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0),
    )
    try:
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(4096, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) > max_bytes:
            raise OSError("process metadata exceeds its bound")
        return value
    finally:
        os.close(descriptor)


def _trusted_process_snapshot() -> tuple[str, list[tuple[str, str, list[str]]]]:
    """Enumerate Linux procfs directly; never execute a PATH-selected process tool."""

    proc = Path("/proc")
    if os.name != "posix" or not proc.is_dir():
        return "unsupported", []
    rows: list[tuple[str, str, list[str]]] = []
    try:
        entries = list(os.scandir(proc))
    except OSError:
        return "error", []
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            comm = _read_proc_value(proc / entry.name / "comm", max_bytes=256).decode(
                "utf-8", errors="replace"
            ).strip()
            raw = _read_proc_value(proc / entry.name / "cmdline", max_bytes=65_536)
            argv = [
                part.decode("utf-8", errors="replace")
                for part in raw.split(b"\0")
                if part
            ]
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            continue
        rows.append((entry.name, comm, argv))
    return "ok", rows


def quiescence_checks(
    root: Path,
    *,
    openclaw_bin: str | AttestedOpenClawExecutable | None,
) -> dict[str, Any]:
    executable = (
        openclaw_bin
        if isinstance(openclaw_bin, AttestedOpenClawExecutable)
        else attest_openclaw_executable(openclaw_bin)
    )
    lock_candidates = [
        openclaw_home(root) / ".lock",
        openclaw_home(root) / "lock",
    ]
    locks_dir = openclaw_home(root) / "locks"
    if locks_dir.exists() and locks_dir.is_dir() and not locks_dir.is_symlink():
        lock_candidates.extend(sorted(item for item in locks_dir.iterdir()))
    existing_locks = [str(path) for path in lock_candidates if path.exists() or path.is_symlink()]
    process_matches: list[dict[str, str]] = []
    enumeration_status, processes = _trusted_process_snapshot()
    current_pid = str(os.getpid())
    for pid, comm_value, argv in processes:
        comm = comm_value.casefold()
        argv0 = Path(argv[0]).name.casefold() if argv else ""
        normalized_args = "\0".join(argv).replace("\\", "/").casefold()
        reason = ""
        if comm == "openclaw" or argv0 == "openclaw":
            reason = "openclaw-executable"
        elif "/openclaw/dist/index.js" in normalized_args:
            reason = "openclaw-node-entrypoint"
        elif "/.openclaw/npm/projects/" in normalized_args:
            reason = "openclaw-managed-node-project"
        if pid != current_pid and reason:
            process_matches.append({"pid": pid, "comm": comm_value, "reason": reason})
    return {
        "quiescent": enumeration_status == "ok" and not existing_locks and not process_matches,
        "openclaw_bin": str(executable.path),
        "process_enumeration": enumeration_status,
        "lock_candidates_checked": [str(path) for path in lock_candidates],
        "existing_lock_paths": existing_locks,
        "process_matches": process_matches,
    }


def target_evidence_platform(platform: str) -> str:
    if platform == "wsl":
        return "wsl-native"
    return platform


def openclaw_target_state_file(root: Path) -> Path:
    return root / ".ai-agents-skills" / OPENCLAW_TARGET_STATE_NAME


def default_openclaw_target_state() -> dict[str, Any]:
    return {
        "schema_version": OPENCLAW_TARGET_STATE_VERSION,
        "artifacts": [],
        "runs": [],
        "transactions": [],
    }


def load_openclaw_target_state(root: Path) -> dict[str, Any]:
    path = openclaw_target_state_file(root)
    preflight_state_path(root, path)
    if not path.exists():
        return default_openclaw_target_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("OpenClaw target state file is not valid JSON") from exc
    if not isinstance(state, dict) or state.get("schema_version") != OPENCLAW_TARGET_STATE_VERSION:
        raise ValueError("OpenClaw target state file has unsupported schema")
    for key in ("artifacts", "runs", "transactions"):
        if key in state and not isinstance(state[key], list):
            raise ValueError(f"OpenClaw target state field must be a list: {key}")
    return state


def save_openclaw_target_state(root: Path, state: dict[str, Any]) -> None:
    path = openclaw_target_state_file(root)
    preflight_state_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def append_transaction(root: Path, state: dict[str, Any], transaction: dict[str, Any]) -> None:
    state.setdefault("transactions", []).append(transaction)
    save_openclaw_target_state(root, state)


def complete_transaction(root: Path, state: dict[str, Any], run_id: str, *, status: str) -> None:
    for transaction in reversed(state.setdefault("transactions", [])):
        if transaction.get("run_id") == run_id:
            transaction["status"] = status
            break
    save_openclaw_target_state(root, state)


def remove_artifact_records(state: dict[str, Any], keys: set[str]) -> None:
    state["artifacts"] = [item for item in state.get("artifacts", []) if item.get("key") not in keys]


def missing_parent_dirs(root: Path, parent: Path) -> list[Path]:
    missing = []
    current = parent
    while current != root and not current.exists():
        missing.append(current.relative_to(root))
        current = current.parent
    return missing


def cleanup_created_parents(root: Path, relative_dirs: list[str]) -> None:
    base = openclaw_home(root)
    for relative in sorted(relative_dirs, key=lambda item: item.count("/"), reverse=True):
        path = base / Path(relative)
        if not normalized_path_within(base, path) or not resolved_path_within(base, path):
            continue
        if not path.exists() or path.is_symlink() or not path.is_dir():
            continue
        try:
            path.rmdir()
        except OSError:
            continue
