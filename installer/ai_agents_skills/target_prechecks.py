from __future__ import annotations

from pathlib import Path
from typing import Any

from .agents import AgentTarget, agent_home_status, target_for
from .capabilities import (
    AGENT_SKILL_LOADER_POLICY,
    existing_parents,
    normalized_path_within,
    resolved_path_within,
)
from .copilot import COPILOT_CLI_TOOL_SPEC, build_copilot_precheck
from .discovery import discover_tool


TARGET_STATUS_BY_HOME_REASON = {
    "agent home not detected": "home-missing",
    "agent home is a symlink": "home-symlink",
    "agent home is not a directory": "home-invalid",
    "agent home resolves outside selected root": "home-outside-root",
    "OpenClaw target is fake-root only before native target evidence": "blocked-real-system",
}


def build_target_prechecks(
    root: Path,
    platform: str,
    requested_agents: list[str] | None,
    agents: list[AgentTarget],
) -> list[dict[str, Any]]:
    targets = _precheck_targets(root, requested_agents, agents)
    return [_build_target_precheck(root, platform, target) for target in targets]


def _precheck_targets(
    root: Path,
    requested_agents: list[str] | None,
    agents: list[AgentTarget],
) -> list[AgentTarget]:
    if requested_agents is not None:
        return [target_for(root, agent) for agent in requested_agents]
    return agents


def _build_target_precheck(root: Path, platform: str, target: AgentTarget) -> dict[str, Any]:
    base = build_base_target_precheck(root, platform, target)
    if target.name != "copilot":
        return base

    cli_result = discover_tool("copilot-cli", COPILOT_CLI_TOOL_SPEC, platform, root)
    copilot = build_copilot_precheck(root, platform, cli_result)
    copilot_status = copilot["status"]
    if "status_reduction" in copilot:
        copilot["status_reduction"]["result"] = copilot_status
    copilot.update(base)
    copilot["base"] = base
    copilot["copilot_status"] = copilot_status
    return copilot


def build_base_target_precheck(root: Path, platform: str, target: AgentTarget) -> dict[str, Any]:
    home = agent_home_status(root, target)
    policy = AGENT_SKILL_LOADER_POLICY.get(target.name, {})
    return {
        "target": target.name,
        "status": target_status(target, home),
        "platform": platform,
        "path_style": path_style_for_platform(platform),
        "target_home": path_status(root, target.home),
        "skills_dir": path_status(root, target.skills_dir),
        "instructions_file": path_status(root, target.instructions_file),
        "artifact_dirs": {
            kind: path_status(root, path)
            for kind, path in sorted(target.artifact_dirs.items())
        },
        "optional_skills_dirs": [
            path_status(root, path)
            for path in target.optional_skills_dirs
        ],
        "legacy_skills_dirs": [
            path_status(root, path)
            for path in target.legacy_skills_dirs
        ],
        "capabilities": {
            "detect_by_default": target.detect_by_default,
            "instruction_blocks_enabled": target.instruction_blocks_enabled,
            "fake_root_only": target.fake_root_only,
            "default_install_mode": policy.get("default_mode"),
            "symlink_skill_file": policy.get("symlink_skill_file"),
            "install_mode_reason": policy.get("reason"),
        },
        "read_policy": {
            "file_contents_read": False,
            "secret_values_read": False,
        },
        "home_status": home,
        "notes": target_notes(target),
    }


def target_status(target: AgentTarget, home: dict[str, str | bool]) -> str:
    if not home["eligible"]:
        return TARGET_STATUS_BY_HOME_REASON.get(str(home["reason"]), "home-invalid")
    if target.fake_root_only:
        return "fake-root-only"
    return "ready"


def target_notes(target: AgentTarget) -> list[str]:
    if target.name == "openclaw":
        return [
            "OpenClaw is explicit-only and fake-root-only before native target evidence.",
            "Runtime-backed skills, support files, symlink/reference modes, and real-system writes remain blocked.",
        ]
    if target.name == "copilot":
        return [
            "Copilot participates in default detection when ~/.copilot exists; repository-level .github surfaces do not activate this personal target.",
        ]
    if target.name == "opencode":
        return [
            "OpenCode participates in default detection when ~/.config/opencode exists.",
            "OpenCode auto mode copies regular SKILL.md files and support files for cross-platform parity.",
            "OpenCode native smoke uses isolated XDG directories when the opencode CLI is available.",
        ]
    if target.name == "codex":
        return [
            "Codex auto mode uses reference adapters because symlinked SKILL.md discovery is not assumed.",
        ]
    if target.name == "deepseek":
        return [
            "DeepSeek auto mode uses reference adapters and workspace-local skill paths may shadow global skills.",
        ]
    return []


def path_style_for_platform(platform: str) -> str:
    if platform == "windows":
        return "windows"
    if platform == "wsl":
        return "wsl-posix"
    return "posix"


def path_status(root: Path, path: Path) -> dict[str, Any]:
    if not normalized_path_within(root, path) or not resolved_path_within(root, path.parent):
        return {"path": str(path), "status": "blocked", "reason": "path resolves outside selected root"}
    for parent in existing_parents(path.parent, root):
        if parent.is_symlink():
            return {"path": str(path), "status": "blocked", "reason": f"path has symlinked parent: {parent}"}
        if not parent.is_dir():
            return {"path": str(path), "status": "blocked", "reason": f"path has non-directory parent: {parent}"}
    if path.is_symlink():
        return {"path": str(path), "status": "blocked", "reason": "path is a symlink"}
    if not path.exists():
        return {"path": str(path), "status": "missing"}
    if path.is_dir():
        return {"path": str(path), "status": "directory"}
    if path.is_file():
        return {"path": str(path), "status": "file"}
    return {"path": str(path), "status": "blocked", "reason": "path is neither regular file nor directory"}
