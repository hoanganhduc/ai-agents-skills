from __future__ import annotations

from pathlib import Path
from typing import Any

from .agents import AgentTarget
from .manifest import REPO_ROOT
from .render import (
    MANAGED_MARKER,
    add_managed_support_header,
    block_id,
    render_artifact_content,
    render_instruction_block,
    render_management_notice,
    render_skill_md,
)
from .state import sha256_file, sha256_text


def build_plan(
    root: Path,
    manifests: dict[str, Any],
    skills: list[str],
    agents: list[AgentTarget],
    adopt: bool = False,
    backup_replace: bool = False,
    migrate: bool = False,
    artifacts: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    skipped_agents = []
    skill_specs = manifests["skills"]["skills"]
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
            content = render_skill_md(skill, spec, agent.name)
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
            )
            actions.append(file_action)
            skill_actions[(agent.name, skill)] = file_action
            if skill_action_is_active(file_action):
                actions.extend(
                    support_file_actions(
                        agent=agent,
                        skill=skill,
                        adopt=adopt,
                        backup_replace=backup_replace,
                    )
                )
            block = render_instruction_block(skill, spec)
            actions.append(classify_instruction_block(agent, skill, block, file_action))
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
    current = path.read_text(encoding="utf-8", errors="replace")
    if MANAGED_MARKER in current:
        return True
    return sha256_text(current) == sha256_text(render_skill_md(skill, skill_specs[skill], agent.name))


def support_file_actions(
    agent: AgentTarget,
    skill: str,
    adopt: bool,
    backup_replace: bool,
) -> list[dict[str, Any]]:
    canonical_dir = REPO_ROOT / "canonical" / "skills" / skill
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
            )
        )
    return actions


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
) -> dict[str, Any]:
    expected_hash = sha256_text(content)
    if not path.exists():
        if legacy_path is not None:
            classification = "legacy"
            operation = "migrate-copy" if migrate else "skip"
        else:
            classification = "missing"
            operation = "create"
    else:
        current = path.read_text(encoding="utf-8", errors="replace")
        current_hash = sha256_text(current)
        if current_hash == expected_hash:
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
    result = {
        "kind": "file",
        "agent": agent,
        "skill": skill,
        "path": str(path),
        "content": content,
        "expected_hash": expected_hash,
        "current_hash": sha256_file(path),
        "classification": classification,
        "operation": operation,
        "artifact_type": artifact_type,
    }
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
        "migrate-copy",
    }
