from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agents import AgentTarget, agent_home_statuses, agent_supports_manifest_entry
from .capabilities import effective_install_mode_with_evidence
from .discovery import current_platform
from .manifest import REPO_ROOT
from .openclaw_target_gate import openclaw_target_block_reason
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
from .runtime import build_runtime_actions
from .state import artifact_signature, load_state, sha256_file, sha256_text


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
    runtime_profile: str = "auto",
    runtime_root: Path | None = None,
    platform: str | None = None,
    requested_agents: list[str] | None = None,
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    skipped_agents = []
    skill_specs = manifests["skills"]["skills"]
    state = load_state(root)
    agents, blocked_agents = plannable_agents(root, agents)
    skipped_agents.extend(blocked_agents)
    detected_agent_names = {agent.name for agent in agents}
    skipped_agent_names = {item["agent"] for item in skipped_agents}
    for status in agent_home_statuses(root, requested_agents):
        if status["agent"] not in detected_agent_names and status["agent"] not in skipped_agent_names:
            skipped_agents.append({"agent": status["agent"], "reason": status["reason"]})
            skipped_agent_names.add(str(status["agent"]))

    skill_actions: dict[tuple[str, str], dict[str, Any]] = {}
    for skill in skills:
        spec = skill_specs[skill]
        for agent in agents:
            if not skill_supported_by_agent(spec, agent):
                continue
            skill_file = agent.skill_file_for(skill)
            source_path = canonical_skill_path(skill)
            source = source_path if source_path.exists() else None
            block_reason = target_skill_block_reason(root, agent, skill, manifests, install_mode)
            if block_reason is not None:
                file_action = blocked_file_action(
                    agent=agent.name,
                    skill=skill,
                    path=skill_file,
                    artifact_type="skill-file",
                    reason=block_reason,
                    install_mode="copy",
                    source_path=source,
                )
                actions.append(file_action)
                skill_actions[(agent.name, skill)] = file_action
                continue
            action_install_mode, mode_reason, capability_evidence = effective_install_mode(
                agent.name,
                install_mode,
                source_path,
            )
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
                mode_reason=mode_reason,
                capability_evidence=capability_evidence,
                source_path=source,
                fallback_content=fallback_content,
            )
            block_openclaw_conflict_mode(root, agent.name, file_action)
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
                        manifests=manifests,
                        platform=platform,
                    )
                )
            if agent.instruction_blocks_enabled:
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
            if not artifact_supported_by_agent(artifact_type, spec, agent):
                continue
            actions.append(
                artifact_action(
                    root=root,
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
    actions.extend(antigravity_native_scaffold_actions(agents, actions, adopt, backup_replace))
    actions.extend(
        build_runtime_actions(
            root=root,
            manifests=manifests,
            selected_skills=runtime_enabled_skills(skills, skill_actions),
            agents=agents,
            runtime_profile=runtime_profile,
            runtime_root=runtime_root,
            platform=platform,
            backup_replace=backup_replace,
        )
    )
    return {"actions": actions, "skipped_agents": skipped_agents, "root": str(root)}


def plannable_agents(root: Path, agents: list[AgentTarget]) -> tuple[list[AgentTarget], list[dict[str, str]]]:
    plannable = []
    skipped = []
    for agent in agents:
        reason = target_plan_block_reason(root, agent)
        if reason is None:
            plannable.append(agent)
        else:
            skipped.append({"agent": agent.name, "reason": reason})
    return plannable, skipped


def target_plan_block_reason(root: Path, agent: AgentTarget) -> str | None:
    if agent.name == "openclaw":
        return openclaw_target_block_reason(root, operation="plan", agent=agent.name)
    return None


def skill_supported_by_agent(spec: dict[str, Any], agent: AgentTarget) -> bool:
    return agent_supports_manifest_entry(agent.name, spec["supported_agents"])


def artifact_supported_by_agent(artifact_type: str, spec: dict[str, Any], agent: AgentTarget) -> bool:
    if agent.name == "openclaw":
        return False
    if agent.name == "copilot" and artifact_type != "agent-persona":
        return False
    return agent_supports_manifest_entry(agent.name, spec["supported_agents"])


def antigravity_native_scaffold_actions(
    agents: list[AgentTarget],
    actions: list[dict[str, Any]],
    adopt: bool,
    backup_replace: bool,
) -> list[dict[str, Any]]:
    antigravity_agents = [agent for agent in agents if agent.name == "antigravity"]
    if not antigravity_agents:
        return []
    active_antigravity_actions = [
        action for action in actions
        if action.get("agent") == "antigravity" and skill_action_is_active(action)
    ]
    if not active_antigravity_actions:
        return []
    agent = antigravity_agents[0]
    plugin_dir = agent.target_dir_for("plugin")
    scaffold_specs = [
        (
            plugin_dir / "plugin.json",
            "plugin",
            "plugin:ai-agents-skills",
            "ai-agents-skills",
            {
                "name": "ai-agents-skills",
                "version": "0.1.1",
                "description": "Managed ai-agents-skills plugin payload for Antigravity CLI.",
                "author": "ai-agents-skills",
                "components": ["skills", "agents", "rules", "templates", "tools", "mcp", "hooks"],
            },
        ),
        (
            plugin_dir / "mcp_config.json",
            "mcp-config",
            "mcp-config:ai-agents-skills",
            "ai-agents-skills",
            {"mcpServers": {}},
        ),
        (
            plugin_dir / "hooks.json",
            "hook-config",
            "hook-config:ai-agents-skills",
            "ai-agents-skills",
            {},
        ),
        (
            agent.home / "settings.json",
            "settings-file",
            "settings-file:antigravity",
            "antigravity",
            {},
        ),
    ]
    scaffold_actions = []
    for path, artifact_type, artifact_id, artifact_name, data in scaffold_specs:
        action = classify_file_action(
            agent=agent.name,
            skill="repo-management",
            path=path,
            content=json.dumps(data, indent=2, sort_keys=True) + "\n",
            artifact_type=artifact_type,
            adopt=adopt,
            backup_replace=backup_replace,
        )
        action["artifact_id"] = artifact_id
        action["artifact_name"] = artifact_name
        scaffold_actions.append(action)
    return scaffold_actions


def target_skill_block_reason(
    root: Path,
    agent: AgentTarget,
    skill: str,
    manifests: dict[str, Any],
    requested_mode: str,
) -> str | None:
    if agent.name == "copilot" and requested_mode == "symlink":
        return "Copilot symlinked skill loading has not been verified"
    if agent.name != "openclaw":
        return None
    if requested_mode in {"reference", "symlink"}:
        return openclaw_target_block_reason(
            root,
            operation="plan",
            agent=agent.name,
            action_class=requested_mode,
        )
    if skill in manifests.get("runtime", {}).get("skills", {}):
        return openclaw_target_block_reason(
            root,
            operation="plan",
            agent=agent.name,
            action_class="runtime-backed-skill",
        )
    content_reason = openclaw_skill_content_block_reason(skill)
    if content_reason is not None:
        return content_reason
    support_reason = openclaw_skill_support_block_reason(skill)
    if support_reason is not None:
        return support_reason
    return None


def openclaw_skill_content_block_reason(skill: str) -> str | None:
    source = canonical_skill_path(skill)
    if not source.exists():
        return "OpenClaw skill source is missing"
    try:
        content = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "OpenClaw skill content must be UTF-8"
    lowered = content.lower()
    denied_markers = (
        ".codex/runtime",
        "$codex_home",
        "%userprofile%\\.codex\\runtime",
        "%localappdata%\\ai-agents-skills\\runtime",
    )
    if any(marker in lowered for marker in denied_markers):
        return "OpenClaw skill content references Codex/runtime-specific paths"
    return None


def openclaw_skill_support_block_reason(skill: str) -> str | None:
    canonical_dir = canonical_skill_dir(skill)
    if not canonical_dir.exists():
        return None
    has_support_files = any(
        source.is_file() and source.name != "SKILL.md"
        for source in canonical_dir.rglob("*")
    )
    if has_support_files:
        return "OpenClaw support files require target-support-file manifest metadata"
    return None


def block_openclaw_conflict_mode(root: Path, agent_name: str, action: dict[str, Any]) -> None:
    if agent_name != "openclaw":
        return
    if action.get("operation") not in {"adopt", "backup-replace", "migrate-install"}:
        return
    action_class = "migrate" if action.get("operation") == "migrate-install" else str(action.get("operation"))
    action["classification"] = "blocked"
    action["operation"] = "skip"
    action["reason"] = openclaw_target_block_reason(
        root,
        operation="plan",
        agent=agent_name,
        action_class=action_class,
    )


def runtime_enabled_skills(
    skills: list[str],
    skill_actions: dict[tuple[str, str], dict[str, Any]],
) -> list[str]:
    enabled = []
    for skill in skills:
        if any(
            action.get("agent") != "openclaw" and skill_action_is_active(action)
            for (agent_name, action_skill), action in skill_actions.items()
            if action_skill == skill
        ):
            enabled.append(skill)
    return enabled


def effective_install_mode(agent: str, requested_mode: str, source_path: Path) -> tuple[str, str, dict[str, Any]]:
    return effective_install_mode_with_evidence(agent, requested_mode, source_path)


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
                "installed_signature": item.get("installed_signature"),
                "created_parent_dirs": item.get("created_parent_dirs", []),
                "reason": "reference install mode does not use installed support files",
            }
        )
    return actions


def artifact_action(
    root: Path,
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
        classification = classify_block(agent.instructions_file, "repo-management")
        operation = "upsert"
        reason = None
        content = render_management_notice(agent.name)
        if classification == "conflict":
            operation = "skip"
            reason = "managed instruction block is malformed or duplicated"
        elif classification == "managed" and current_block(agent.instructions_file, "repo-management") == content.strip():
            operation = "noop"
        action = {
            "kind": "managed-block",
            "agent": agent.name,
            "skill": "repo-management",
            "path": str(agent.instructions_file),
            "block_id": block_id("repo-management"),
            "content": content,
            "classification": classification,
            "operation": operation,
            "artifact_type": "management-notice",
            "artifact_id": f"{artifact_type}:{name}",
            "artifact_name": name,
        }
        if reason:
            action["reason"] = reason
        return action
    dependencies = spec.get("depends_on_skills", [])
    missing = [
        skill for skill in dependencies
        if not backing_skill_available(agent, skill, skill_specs, skill_actions)
    ]
    path = artifact_target_path(agent, artifact_type, name, spec)
    if agent.name == "antigravity" and artifact_type == "entrypoint-alias" and name in skill_specs:
        action = blocked_file_action(
            agent=agent.name,
            skill=name,
            path=path,
            artifact_type=artifact_type,
            reason="Antigravity global skill alias name conflicts with a managed skill file",
        )
        action["artifact_id"] = f"{artifact_type}:{name}"
        action["artifact_name"] = name
        return action
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
    block_openclaw_conflict_mode(root, agent.name, action)
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
        if agent.name == "copilot":
            return target_dir / f"{name}.agent.md"
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
    path = agent.skill_file_for(skill)
    if not path.exists():
        return False
    if not path.is_file() and not path.is_symlink():
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
    manifests: dict[str, Any],
    platform: str | None = None,
) -> list[dict[str, Any]]:
    if install_mode == "reference":
        return []
    canonical_dir = canonical_skill_dir(skill)
    if not canonical_dir.exists():
        return []
    if agent.name == "openclaw":
        return openclaw_support_file_actions(agent, skill, canonical_dir)
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
        path = agent.support_dir_for(skill) / relative
        platform_reason = support_file_platform_block_reason(relative, platform)
        if platform_reason is not None:
            actions.append(
                blocked_file_action(
                    agent=agent.name,
                    skill=skill,
                    path=path,
                    artifact_type="skill-support-file",
                    reason=platform_reason,
                    install_mode=install_mode,
                    source_path=source,
                )
            )
            continue
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


def support_file_platform_block_reason(relative: Path, platform: str | None = None) -> str | None:
    platform_name = current_platform(platform)
    suffix = relative.suffix.lower()
    if platform_name == "windows" and suffix == ".sh":
        return "POSIX shell support file is not installed for Windows targets"
    if platform_name in {"linux", "macos", "wsl"} and suffix in {".bat", ".cmd", ".ps1"}:
        return "Windows support file is not installed for POSIX targets"
    return None


def openclaw_support_file_actions(
    agent: AgentTarget,
    skill: str,
    canonical_dir: Path,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for source in sorted(canonical_dir.rglob("*")):
        if not source.is_file() or source.name == "SKILL.md":
            continue
        relative = source.relative_to(canonical_dir)
        actions.append(
            blocked_file_action(
                agent=agent.name,
                skill=skill,
                path=agent.skills_dir / skill / relative,
                artifact_type="skill-support-file",
                reason="OpenClaw support file lacks target-support-file manifest metadata",
                install_mode="copy",
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


def blocked_file_action(
    agent: str,
    skill: str,
    path: Path,
    artifact_type: str,
    reason: str,
    install_mode: str = "copy",
    source_path: Path | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "file",
        "agent": agent,
        "skill": skill,
        "path": str(path),
        "classification": "blocked",
        "operation": "skip",
        "artifact_type": artifact_type,
        "install_mode": install_mode,
        "reason": reason,
    }
    if source_path is not None:
        result["source_path"] = str(source_path)
    return result


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
    mode_reason: str | None = None,
    capability_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    can_symlink = install_mode == "symlink" and source_path is not None
    expected_hash = sha256_file(source_path) if can_symlink else sha256_text(content)
    current_hash = None
    reason = None
    if not path.exists() and not path.is_symlink():
        if legacy_path is not None:
            classification = "legacy"
            operation = "migrate-install" if migrate else "skip"
        else:
            classification = "missing"
            operation = "create"
    else:
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
        elif path.is_symlink():
            classification = "conflict" if backup_replace else "unmanaged"
            operation = "backup-replace" if backup_replace else "skip"
            reason = "target path is an unmanaged symlink"
        elif path.is_dir():
            classification = "conflict"
            operation = "skip"
            reason = "target path is a directory"
        elif not path.is_file():
            classification = "conflict"
            operation = "skip"
            reason = "target path is not a regular file"
        else:
            current_hash = sha256_file(path)
            current = path.read_text(encoding="utf-8", errors="replace")
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
        "mode_reason": mode_reason,
        "capability_evidence": capability_evidence,
    }
    if source_path is not None:
        result["source_path"] = str(source_path)
    if install_mode == "symlink":
        result["fallback_mode"] = "reference" if artifact_type == "skill-file" else "copy"
        if fallback_content is not None:
            result["fallback_content"] = fallback_content
    if legacy_path is not None:
        result["legacy_path"] = str(legacy_path)
    if reason is not None:
        result["reason"] = reason
    return result


def find_legacy_skill(agent: AgentTarget, skill: str, manifests: dict[str, Any]) -> Path | None:
    aliases = manifests["skills"].get("legacy_aliases", {}).get(skill, [])
    for name in aliases:
        candidate = agent.skill_file_for(name)
        if candidate.exists():
            return candidate
    names = [skill, *aliases]
    for skills_dir in agent.legacy_skills_dirs:
        for name in names:
            if agent.skill_file_layout == "flat-md":
                candidate = skills_dir / f"{name}.md"
            else:
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
    if agent.skill_file_layout == "flat-md":
        return {
            "kind": "managed-file-remove",
            "agent": agent.name,
            "skill": skill,
            "path": str(legacy_path),
            "legacy_path": str(legacy_path),
            "canonical_path": str(canonical_path),
            "classification": "legacy",
            "operation": "remove-obsolete",
            "artifact_type": "legacy-skill-file",
            "installed_signature": artifact_signature(legacy_path),
            "reason": "legacy alias removed after canonical migration",
        }
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
    start_marker = f"<!-- {block_id(skill)}:start -->"
    end_marker = f"<!-- {block_id(skill)}:end -->"
    start_count = content.count(start_marker)
    end_count = content.count(end_marker)
    if start_count == 1 and end_count == 1 and content.find(start_marker) < content.find(end_marker):
        return "managed"
    if start_count or end_count:
        return "conflict"
    return "missing"


def current_block(path: Path, skill: str) -> str | None:
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8", errors="replace")
    start_marker = f"<!-- {block_id(skill)}:start -->"
    end_marker = f"<!-- {block_id(skill)}:end -->"
    start = content.find(start_marker)
    if start == -1:
        return None
    end = content.find(end_marker, start)
    if end == -1:
        return None
    return content[start:end + len(end_marker)]


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

    classification = classify_block(agent.instructions_file, skill)
    existing_block = current_block(agent.instructions_file, skill)
    if operation == "upsert" and classification == "conflict":
        operation = "skip"
        reason = "managed instruction block is malformed or duplicated"
    if (
        operation == "upsert"
        and classification == "managed"
        and existing_block is not None
        and existing_block.strip() == block.strip()
    ):
        operation = "noop"

    action = {
        "kind": "managed-block",
        "agent": agent.name,
        "skill": skill,
        "path": str(agent.instructions_file),
        "block_id": block_id(skill),
        "content": block,
        "classification": classification,
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
