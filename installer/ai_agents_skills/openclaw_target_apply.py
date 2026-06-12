from __future__ import annotations

import json
import os
import subprocess
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


OPENCLAW_TARGET_STATE_VERSION = 1
OPENCLAW_TARGET_STATE_NAME = "openclaw-target-state.json"


def apply_target_manifest_file(
    manifest_path: Path,
    root: Path,
    *,
    dry_run: bool = True,
    real_system: bool = False,
    confirm_phrase: str | None = None,
    post_apply_check: bool = True,
) -> dict[str, Any]:
    manifest = load_target_manifest(manifest_path)
    return apply_target_manifest(
        manifest,
        root,
        dry_run=dry_run,
        real_system=real_system,
        confirm_phrase=confirm_phrase,
        post_apply_check=post_apply_check,
    )


def apply_target_manifest(
    manifest: dict[str, Any],
    root: Path,
    *,
    dry_run: bool = True,
    real_system: bool = False,
    confirm_phrase: str | None = None,
    post_apply_check: bool = True,
) -> dict[str, Any]:
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
    try:
        for planned_action, action in zip(planned, manifest["actions"], strict=True):
            result = apply_one_action(expanded, run_id, manifest, action, planned_action)
            applied.append(result)
            if result.get("applied"):
                state.setdefault("artifacts", []).append(state_record(result))
                save_openclaw_target_state(expanded, state)
        if post_apply_check:
            post_apply_native_check(expanded, [item for item in applied if item.get("applied")])
        complete_transaction(expanded, state, run_id, status="applied")
    except Exception:
        rollback_applied_actions(expanded, applied)
        state = load_openclaw_target_state(expanded)
        remove_artifact_records(state, {item["key"] for item in applied if item.get("applied")})
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
    current = artifact_signature(path)
    planned = {
        "key": f"{manifest['manifest_id']}:{action['action_id']}",
        "manifest_id": manifest["manifest_id"],
        "action_id": action["action_id"],
        "action_class": action["action_class"],
        "operation": action["operation"],
        "skill": action["skill"],
        "relative_path": relative_path,
        "expected_hash": action["expected_hash"],
        "pre_state": action["pre_state"],
        "current_pre_state": current,
        "drift": not signatures_match(current, action["pre_state"]),
    }
    reason = target_path_safety_reason(root, path, action_class=action["action_class"])
    if action["operation"] == "no-op":
        planned["blocked"] = False
        planned["reason"] = "no-op"
    elif reason is not None:
        planned["blocked"] = True
        planned["reason"] = reason
    elif planned["drift"]:
        planned["blocked"] = True
        planned["reason"] = "target-pre-state-drift"
    else:
        planned["blocked"] = False
        planned["reason"] = "ready"
    return planned


def apply_one_action(
    root: Path,
    run_id: str,
    manifest: dict[str, Any],
    action: dict[str, Any],
    planned_action: dict[str, Any],
) -> dict[str, Any]:
    result = dict(planned_action)
    result["run_id"] = run_id
    result["applied"] = False
    if planned_action.get("blocked"):
        raise ValueError(f"OpenClaw target apply action is blocked: {planned_action['reason']}")
    if action["operation"] == "no-op":
        result["reason"] = "no-op"
        return result
    path = openclaw_target_path(root, planned_action["relative_path"], action_class=action["action_class"])
    current = artifact_signature(path)
    if not signatures_match(current, action["pre_state"]):
        raise ValueError("OpenClaw target pre-state changed before write")
    reason = target_path_safety_reason(root, path, action_class=action["action_class"])
    if reason is not None:
        raise ValueError(f"OpenClaw target path is unsafe: {reason}")
    created_parents = missing_parent_dirs(openclaw_home(root), path.parent)
    content = action["content"]
    if sha256_text(content) != action["expected_hash"]:
        raise ValueError("OpenClaw target action content hash changed before write")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, content)
    result["applied"] = True
    result["path"] = str(path)
    result["installed_hash"] = sha256_text(content)
    result["installed_signature"] = artifact_signature(path)
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


def target_path_safety_reason(root: Path, path: Path, *, action_class: str) -> str | None:
    try:
        relative_path = path.relative_to(openclaw_home(root)).as_posix()
        checked_openclaw_target_relative_path(relative_path, action_class=action_class)
    except ValueError as exc:
        return str(exc)
    if not normalized_path_within(openclaw_home(root), path):
        return "target path escapes .openclaw"
    if path.exists() and path.is_symlink():
        return "target path is a symlink"
    if not resolved_path_within(openclaw_home(root), path.parent):
        return "target path resolves outside .openclaw"
    for parent in existing_contained_parents(path.parent, openclaw_home(root)):
        if parent.is_symlink():
            return "target path has a symlinked parent"
        if not parent.is_dir():
            return "target path has a non-directory parent"
    return None


def plan_uninstall_action(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    relative_path = checked_openclaw_target_relative_path(
        record["relative_path"],
        action_class=record["action_class"],
    )
    path = openclaw_target_path(root, relative_path, action_class=record["action_class"])
    current = artifact_signature(path)
    operation = "delete-created"
    reason = "ready"
    if not current.get("exists"):
        operation = "forget-missing"
        reason = "already-missing"
    elif current.get("kind") != "file" or current.get("hash") != record.get("installed_hash"):
        operation = "skip-conflict"
        reason = "artifact-changed-since-openclaw-target-apply"
    safety = target_path_safety_reason(root, path, action_class=record["action_class"])
    if safety is not None:
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
        raise ValueError(f"unsupported OpenClaw target uninstall operation: {operation}")
    path = openclaw_target_path(root, action["relative_path"], action_class=action["action_class"])
    reason = target_path_safety_reason(root, path, action_class=action["action_class"])
    if reason is not None:
        result["operation"] = "skip-conflict"
        result["reason"] = reason
        return result
    if artifact_signature(path).get("hash") != action.get("installed_hash"):
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
        "created_parent_dirs": result.get("created_parent_dirs", []),
        "run_id": result["run_id"],
    }


def rollback_applied_actions(root: Path, applied: list[dict[str, Any]]) -> None:
    for action in reversed(applied):
        if not action.get("applied"):
            continue
        path = openclaw_target_path(root, action["relative_path"], action_class=action["action_class"])
        if artifact_signature(path).get("hash") == action.get("installed_hash"):
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


def post_apply_native_check(root: Path, applied: list[dict[str, Any]]) -> None:
    for action in applied:
        if not openclaw_skill_visible(root, action["skill"]):
            raise ValueError(f"OpenClaw native loader did not report managed skill: {action['skill']}")


def openclaw_skill_visible(root: Path, skill: str, *, openclaw_bin: str = "openclaw") -> bool:
    result = run_openclaw_json(root, [openclaw_bin, "skills", "list", "--json"])
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
    openclaw_bin: str = "openclaw",
    skill: str | None = None,
    include_canary: bool = False,
    platform: str | None = None,
    path_style: str = "posix",
    captured_at: str | None = None,
) -> dict[str, Any]:
    expanded = root.expanduser()
    paths = validate_openclaw_target_home(expanded)
    platform_name = target_evidence_platform(platform or current_platform())
    version = run_text([openclaw_bin, "--version"], cwd=expanded)
    skills_help = run_text([openclaw_bin, "skills", "--help"], cwd=expanded)
    skills_list = run_openclaw_json(expanded, [openclaw_bin, "skills", "list", "--json"])
    managed_dir = str(Path(str(skills_list.get("managedSkillsDir", ""))).expanduser().resolve(strict=False))
    expected_managed_dir = paths["managed_skills_realpath"]
    if managed_dir != expected_managed_dir:
        raise ValueError("OpenClaw native managed skills directory does not match selected target root")
    quiescence = quiescence_checks(expanded, openclaw_bin=openclaw_bin)
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
        if not openclaw_skill_visible(expanded, target_skill, openclaw_bin=openclaw_bin):
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


def run_openclaw_json(root: Path, command: list[str]) -> dict[str, Any]:
    text = run_text(command, cwd=root, env=openclaw_env(root))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("OpenClaw command did not return valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("OpenClaw command returned non-object JSON")
    return data


def run_text(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=20,
    )
    if completed.returncode != 0:
        raise ValueError(f"OpenClaw command failed: {' '.join(command)}")
    return completed.stdout.strip()


def openclaw_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["OPENCLAW_STATE_DIR"] = str(openclaw_home(root))
    return env


def quiescence_checks(root: Path, *, openclaw_bin: str = "openclaw") -> dict[str, Any]:
    lock_candidates = [
        openclaw_home(root) / ".lock",
        openclaw_home(root) / "lock",
    ]
    locks_dir = openclaw_home(root) / "locks"
    if locks_dir.exists() and locks_dir.is_dir() and not locks_dir.is_symlink():
        lock_candidates.extend(sorted(item for item in locks_dir.iterdir()))
    existing_locks = [str(path) for path in lock_candidates if path.exists() or path.is_symlink()]
    process_matches: list[str] = []
    try:
        completed = subprocess.run(
            ["ps", "-eo", "pid=,comm=,args="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        completed = None
    if completed is not None and completed.returncode == 0:
        current_pid = str(os.getpid())
        openclaw_command_name = Path(openclaw_bin).name.lower()
        for line in completed.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split(maxsplit=2)
            if len(parts) < 2:
                continue
            pid = parts[0]
            comm = parts[1].lower()
            args = parts[2] if len(parts) == 3 else ""
            argv0 = Path(args.split(maxsplit=1)[0]).name.lower() if args else ""
            normalized_args = args.replace("\\", "/").lower()
            if (
                pid != current_pid
                and (
                    comm == "openclaw"
                    or comm == openclaw_command_name
                    or argv0 == openclaw_command_name
                    or "/openclaw/dist/index.js" in normalized_args
                    or "/.openclaw/npm/projects/" in normalized_args
                )
            ):
                process_matches.append(stripped)
    return {
        "quiescent": not existing_locks and not process_matches,
        "openclaw_bin": openclaw_bin,
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
