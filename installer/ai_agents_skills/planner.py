from __future__ import annotations

from pathlib import Path
from typing import Any

from .agents import AgentTarget
from .render import MANAGED_MARKER, block_id, render_instruction_block, render_skill_md
from .state import sha256_file, sha256_text


def build_plan(
    root: Path,
    manifests: dict[str, Any],
    skills: list[str],
    agents: list[AgentTarget],
    adopt: bool = False,
    backup_replace: bool = False,
    migrate: bool = False,
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    skipped_agents = []
    skill_specs = manifests["skills"]["skills"]
    detected_agent_names = {agent.name for agent in agents}
    for agent_name in ("codex", "claude", "deepseek"):
        if agent_name not in detected_agent_names:
            skipped_agents.append({"agent": agent_name, "reason": "agent home not detected"})

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
            block = render_instruction_block(skill, spec)
            actions.append(classify_instruction_block(agent, skill, block, file_action))
    return {"actions": actions, "skipped_agents": skipped_agents, "root": str(root)}


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
        if MANAGED_MARKER in current:
            classification = "managed"
            operation = "noop" if sha256_text(current) == expected_hash else "update"
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
    operation = "upsert"
    reason = None
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
