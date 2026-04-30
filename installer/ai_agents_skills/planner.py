from __future__ import annotations

from pathlib import Path
from typing import Any

from .agents import AgentTarget
from .manifest import REPO_ROOT
from .render import (
    MANAGED_MARKER,
    add_managed_support_header,
    block_id,
    canonical_skill_dir,
    canonical_skill_path,
    render_artifact_content,
    render_instruction_block,
    render_management_notice,
    render_reference_skill_md,
    render_skill_md,
)
from .state import load_state, sha256_file, sha256_text


SKILL_FILE_SYMLINK_SUPPORTED = {
    "codex": False,
    "claude": True,
    "deepseek": True,
}


def build_plan(
    root: Path,
    manifests: dict[str, Any],
    skills: list[str],
    agents: list[AgentTarget],
    adopt: bool = False,
    backup_replace: bool = False,
    migrate: bool = False,
    artifacts: list[tuple[str, str]] | None = None,
    install_mode: str = "auto",
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    skipped_agents = []
    skill_specs = manifests["skills"]["skills"]
    state = load_state(root)
    detected_agent_names = {agent.name for agent in agents}
    for agent_name in ("codex", "claude", "deepseek"):
        if agent_name not in detected_agent_names:
            skipped_agents.append({"agent": agent_name, "reason": "agent home not detected"})

    skill_actions: dict[tuple[str, str], dict[str, Any]] = {}
    for skill in skills:
        spec = skill_specs[skill]
        for agent in agents:
            if agent.name not in spec["supported_agents"]:
                continue
            skill_file = agent.skills_dir / skill / "SKILL.md"
            source_path = canonical_skill_path(skill)
            source = source_path if source_path.exists() else None
            action_install_mode = effective_install_mode(agent.name, install_mode, source_path)
            content = skill_content_for_mode(skill, spec, agent.name, action_install_mode, source_path)
            fallback_content = (
                render_reference_skill_md(skill, spec, agent.name, source_path)
                if action_install_mode == "symlink" and source_path.exists()
                else None
            )
            file_action = classify_file_action(
                agent=agent.name,
                skill=skill,
                path=skill_file,
                content=content,
                artifact_type="skill-file",
                adopt=adopt,
                backup_replace=backup_replace,
                legacy_path=find_legacy_skill(agent, skill, manifests),
                migrate=migrate,
                install_mode=action_install_mode,
                source_path=source,
                fallback_content=fallback_content,
            )
            actions.append(file_action)
            skill_actions[(agent.name, skill)] = file_action
            if skill_action_is_active(file_action):
                if file_action["install_mode"] == "reference":
                    actions.extend(obsolete_support_file_actions(state, agent.name, skill))
                actions.extend(
                    support_file_actions(
                        agent=agent,
                        skill=skill,
                        adopt=adopt,
                        backup_replace=backup_replace,
                        install_mode=file_action["install_mode"],
                    )
                )
            block = render_instruction_block(skill, spec)
            actions.append(classify_instruction_block(agent, skill, block, file_action))
            if migrate and skill_action_is_active(file_action) and file_action.get("legacy_path"):
                actions.append(
                    legacy_removal_action(
                        agent=agent,
                        skill=skill,
                        legacy_path=Path(file_action["legacy_path"]),
                        canonical_path=skill_file,
                    )
                )
    for artifact_type, name in artifacts or []:
        spec = manifests["artifacts"]["artifacts"][artifact_type][name]
        for agent in agents:
            if agent.name not in spec["supported_agents"]:
                continue
            actions.append(
                artifact_action(
                    agent=agent,
                    artifact_type=artifact_type,
                    name=name,
                    spec=spec,
                    skill_specs=skill_specs,
                    skill_actions=skill_actions,
                    adopt=adopt,
                    backup_replace=backup_replace,
                )
            )
    return {"actions": actions, "skipped_agents": skipped_agents, "root": str(root)}


def effective_install_mode(agent: str, requested_mode: str, source_path: Path) -> str:
    if requested_mode == "auto":
        if not source_path.exists():
            return "copy"
        if not SKILL_FILE_SYMLINK_SUPPORTED.get(agent, True):
            return "reference"
        return "symlink"
    if requested_mode != "symlink":
        return requested_mode
    if not source_path.exists():
        return "copy"
    return "symlink"


def obsolete_support_file_actions(state: dict[str, Any], agent: str, skill: str) -> list[dict[str, Any]]:
    actions = []
    for item in state.get("artifacts", []):
        if item.get("agent") != agent:
            continue
        if item.get("skill") != skill:
            continue
        if item.get("artifact_type") != "skill-support-file":
            continue
        actions.append(
            {
                "kind": "managed-file-remove",
                "agent": agent,
                "skill": skill,
                "path": item["artifact"],
                "classification": "managed",
                "operation": "remove-obsolete",
                "artifact_type": "skill-support-file",
                "install_mode": item.get("install_mode"),
                "source_path": item.get("source_path"),
                "reason": "reference install mode does not use installed support files",
            }
        )
    return actions


def artifact_action(
    agent: AgentTarget,
    artifact_type: str,
    name: str,
    spec: dict[str, Any],
    skill_specs: dict[str, Any],
    skill_actions: dict[tuple[str, str], dict[str, Any]],
    adopt: bool,
    backup_replace: bool,
) -> dict[str, Any]:
    if artifact_type == "management-notice":
        action = {
            "kind": "managed-block",
            "agent": agent.name,
            "skill": "repo-management",
            "path": str(agent.instructions_file),
            "block_id": block_id("repo-management"),
            "content": render_management_notice(agent.name),
            "classification": classify_block(agent.instructions_file, "repo-management"),
            "operation": "upsert",
            "artifact_type": "management-notice",
            "artifact_id": f"{artifact_type}:{name}",
            "artifact_name": name,
        }
        return action
    dependencies = spec.get("depends_on_skills", [])
    missing = [
        skill for skill in dependencies
        if not backing_skill_available(agent, skill, skill_specs, skill_actions)
    ]
    path = artifact_target_path(agent, artifact_type, name, spec)
    content = render_artifact_content(artifact_type, name, spec, agent.name)
    action = classify_file_action(
        agent=agent.name,
        skill=dependencies[0] if len(dependencies) == 1 else name,
        path=path,
        content=content,
        artifact_type=artifact_type,
        adopt=adopt,
        backup_replace=backup_replace,
    )
    action["artifact_id"] = f"{artifact_type}:{name}"
    action["artifact_name"] = name
    if missing:
        action["operation"] = "skip"
        action["classification"] = "blocked"
        action["reason"] = "missing managed backing skill: " + ", ".join(missing)
    return action


def artifact_target_path(
    agent: AgentTarget,
    artifact_type: str,
    name: str,
    spec: dict[str, Any],
) -> Path:
    target_dir = agent.target_dir_for(artifact_type)
    if artifact_type == "agent-persona":
        suffix = ".toml" if agent.name == "codex" else ".md"
        return target_dir / f"{name}{suffix}"
    if artifact_type == "entrypoint-alias":
        return target_dir / f"{name}.md"
    return target_dir / spec["source"]


def backing_skill_available(
    agent: AgentTarget,
    skill: str,
    skill_specs: dict[str, Any],
    skill_actions: dict[tuple[str, str], dict[str, Any]],
) -> bool:
    action = skill_actions.get((agent.name, skill))
    if action and skill_action_is_active(action):
        return True
    path = agent.skills_dir / skill / "SKILL.md"
    if not path.exists():
        return False
    source_path = canonical_skill_path(skill)
    if path.is_symlink() and source_path.exists() and path.resolve() == source_path.resolve():
        return True
    current = path.read_text(encoding="utf-8", errors="replace")
    if MANAGED_MARKER in current:
        return True
    return sha256_text(current) == sha256_text(render_skill_md(skill, skill_specs[skill], agent.name))


def support_file_actions(
    agent: AgentTarget,
    skill: str,
    adopt: bool,
    backup_replace: bool,
    install_mode: str,
) -> list[dict[str, Any]]:
    if install_mode == "reference":
        return []
    canonical_dir = canonical_skill_dir(skill)
    if not canonical_dir.exists():
        return []
    actions: list[dict[str, Any]] = []
    for source in sorted(canonical_dir.rglob("*")):
        if not source.is_file() or source.name == "SKILL.md":
            continue
        relative = source.relative_to(canonical_dir)
        try:
            raw = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        content = add_managed_support_header(raw, agent.name, str(relative).replace("\\", "/"))
        path = agent.skills_dir / skill / relative
        actions.append(
            classify_file_action(
                agent=agent.name,
                skill=skill,
                path=path,
                content=content,
                artifact_type="skill-support-file",
                adopt=adopt,
                backup_replace=backup_replace,
                install_mode=install_mode,
                source_path=source,
            )
        )
    return actions


def skill_content_for_mode(
    skill: str,
    spec: dict[str, Any],
    agent: str,
    install_mode: str,
    source_path: Path,
) -> str:
    if install_mode == "reference" and source_path.exists():
        return render_reference_skill_md(skill, spec, agent, source_path)
    return render_skill_md(skill, spec, agent)


def classify_file_action(
    agent: str,
    skill: str,
    path: Path,
    content: str,
    artifact_type: str,
    adopt: bool,
    backup_replace: bool,
    legacy_path: Path | None = None,
    migrate: bool = False,
    install_mode: str = "copy",
    source_path: Path | None = None,
    fallback_content: str | None = None,
) -> dict[str, Any]:
    can_symlink = install_mode == "symlink" and source_path is not None
    expected_hash = sha256_file(source_path) if can_symlink else sha256_text(content)
    if not path.exists():
        if legacy_path is not None:
            classification = "legacy"
            operation = "migrate-install" if migrate else "skip"
        else:
            classification = "missing"
            operation = "create"
    else:
        current_hash = sha256_file(path)
        is_canonical_symlink = (
            source_path is not None
            and path.is_symlink()
            and path.resolve() == source_path.resolve()
        )
        if can_symlink and is_canonical_symlink:
            classification = "managed"
            operation = "noop"
        elif install_mode != "symlink" and is_canonical_symlink:
            classification = "managed"
            operation = "update"
        else:
            current = path.read_text(encoding="utf-8", errors="replace")
            if not path.is_symlink():
                current_hash = sha256_text(current)
            if install_mode != "symlink" and current_hash == expected_hash:
                classification = "managed"
                operation = "noop"
            elif MANAGED_MARKER in current:
                classification = "managed"
                operation = "update"
            elif adopt:
                classification = "unmanaged"
                operation = "adopt"
            elif backup_replace:
                classification = "conflict"
                operation = "backup-replace"
            else:
                classification = "unmanaged"
                operation = "skip"
    if expected_hash is None:
        expected_hash = sha256_text(content)
    result = {
        "kind": "file",
        "agent": agent,
        "skill": skill,
        "path": str(path),
        "content": content,
        "expected_hash": expected_hash,
        "current_hash": current_hash if path.exists() else None,
        "classification": classification,
        "operation": operation,
        "artifact_type": artifact_type,
        "install_mode": install_mode,
    }
    if source_path is not None:
        result["source_path"] = str(source_path)
    if install_mode == "symlink":
        result["fallback_mode"] = "reference" if artifact_type == "skill-file" else "copy"
        if fallback_content is not None:
            result["fallback_content"] = fallback_content
    if legacy_path is not None:
        result["legacy_path"] = str(legacy_path)
    return result


def find_legacy_skill(agent: AgentTarget, skill: str, manifests: dict[str, Any]) -> Path | None:
    aliases = manifests["skills"].get("legacy_aliases", {}).get(skill, [])
    for name in aliases:
        candidate = agent.skills_dir / name / "SKILL.md"
        if candidate.exists():
            return candidate
    names = [skill, *aliases]
    for skills_dir in agent.legacy_skills_dirs:
        for name in names:
            candidate = skills_dir / name / "SKILL.md"
            if candidate.exists():
                return candidate
    return None


def legacy_removal_action(
    agent: AgentTarget,
    skill: str,
    legacy_path: Path,
    canonical_path: Path,
) -> dict[str, Any]:
    return {
        "kind": "legacy-dir",
        "agent": agent.name,
        "skill": skill,
        "path": str(legacy_path.parent),
        "legacy_path": str(legacy_path),
        "canonical_path": str(canonical_path),
        "classification": "legacy",
        "operation": "remove-legacy",
        "artifact_type": "legacy-skill-dir",
        "reason": "legacy alias removed after canonical migration",
    }


def classify_block(path: Path, skill: str) -> str:
    if not path.exists():
        return "missing"
    content = path.read_text(encoding="utf-8", errors="replace")
    if f"<!-- {block_id(skill)}:start -->" in content:
        return "managed"
    return "missing"


def classify_instruction_block(
    agent: AgentTarget,
    skill: str,
    block: str,
    file_action: dict[str, Any],
) -> dict[str, Any]:
    operation = "upsert" if skill_action_is_active(file_action) else "skip"
    reason = None if operation == "upsert" else "skill artifact is not installed or adopted"
    legacy_path = file_action.get("legacy_path")
    if (
        file_action["classification"] == "legacy"
        and file_action["operation"] == "skip"
        and legacy_path
        and Path(legacy_path).parent.name != skill
    ):
        operation = "skip"
        reason = "canonical skill missing; legacy alias not migrated"

    action = {
        "kind": "managed-block",
        "agent": agent.name,
        "skill": skill,
        "path": str(agent.instructions_file),
        "block_id": block_id(skill),
        "content": block,
        "classification": classify_block(agent.instructions_file, skill),
        "operation": operation,
        "artifact_type": "instruction-block",
    }
    if reason:
        action["reason"] = reason
    return action


def skill_action_is_active(action: dict[str, Any]) -> bool:
    return action.get("operation") in {
        "create",
        "update",
        "noop",
        "adopt",
        "backup-replace",
        "migrate-install",
    }
