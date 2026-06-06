from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from .capabilities import looks_like_real_system_root, resolved_path_within


DEFAULT_AGENT_NAMES = ["codex", "claude", "deepseek", "copilot", "opencode"]
KNOWN_AGENT_NAMES = [*DEFAULT_AGENT_NAMES, "openclaw"]
PORTABLE_MANIFEST_AGENT_NAMES = {"codex", "claude", "deepseek"}
ADAPTER_AGENT_NAMES = {"copilot", "opencode", "openclaw"}


@dataclass(frozen=True)
class AgentTarget:
    name: str
    home: Path
    skills_dir: Path
    instructions_file: Path
    legacy_skills_dirs: tuple[Path, ...] = ()
    optional_skills_dirs: tuple[Path, ...] = ()
    artifact_dirs: Mapping[str, Path] = field(default_factory=dict)
    detect_by_default: bool = True
    instruction_blocks_enabled: bool = True
    fake_root_only: bool = False

    def target_dir_for(self, artifact_type: str) -> Path:
        return self.artifact_dirs.get(artifact_type, self.skills_dir)


def target_for(root: Path, agent: str) -> AgentTarget:
    if agent == "codex":
        return AgentTarget(
            name="codex",
            home=root / ".codex",
            skills_dir=root / ".codex" / "skills",
            instructions_file=root / ".codex" / "AGENTS.md",
            legacy_skills_dirs=(root / ".agents" / "skills",),
            optional_skills_dirs=(root / ".agents" / "skills",),
            artifact_dirs={
                "agent-persona": root / ".codex" / "agents",
                "template": root / ".codex" / "templates",
                "instruction-doc": root / ".codex" / "instructions",
                "entrypoint-alias": root / ".codex" / "instructions" / "entrypoints",
                "command": root / ".codex" / "commands",
                "tool-shim": root / ".codex" / "tools",
            },
        )
    if agent == "claude":
        return AgentTarget(
            name="claude",
            home=root / ".claude",
            skills_dir=root / ".claude" / "skills",
            instructions_file=root / ".claude" / "CLAUDE.md",
            artifact_dirs={
                "agent-persona": root / ".claude" / "agents",
                "template": root / ".claude" / "templates",
                "instruction-doc": root / ".claude" / "instructions",
                "entrypoint-alias": root / ".claude" / "commands",
                "command": root / ".claude" / "commands",
                "tool-shim": root / ".claude" / "tools",
            },
        )
    if agent == "deepseek":
        return AgentTarget(
            name="deepseek",
            home=root / ".deepseek",
            skills_dir=root / ".deepseek" / "skills",
            instructions_file=root / ".deepseek" / "AGENTS.md",
            optional_skills_dirs=(root / ".agents" / "skills", root / "skills"),
            artifact_dirs={
                "agent-persona": root / ".deepseek" / "agents",
                "template": root / ".deepseek" / "templates",
                "instruction-doc": root / ".deepseek" / "instructions",
                "entrypoint-alias": root / ".deepseek" / "instructions" / "entrypoints",
                "command": root / ".deepseek" / "commands",
                "tool-shim": root / ".deepseek" / "tools",
            },
        )
    if agent == "copilot":
        return AgentTarget(
            name="copilot",
            home=root / ".copilot",
            skills_dir=root / ".copilot" / "skills",
            instructions_file=root / ".copilot" / "AGENTS.md",
            optional_skills_dirs=(root / ".agents" / "skills",),
            artifact_dirs={
                "agent-persona": root / ".copilot" / "agents",
            },
            instruction_blocks_enabled=False,
        )
    if agent == "opencode":
        home = opencode_home(root)
        return AgentTarget(
            name="opencode",
            home=home,
            skills_dir=home / "skills",
            instructions_file=home / "AGENTS.md",
            optional_skills_dirs=(root / ".claude" / "skills", root / ".agents" / "skills"),
            artifact_dirs={
                "agent-persona": home / "agents",
                "template": home / "templates",
                "instruction-doc": home / "instructions",
                "entrypoint-alias": home / "commands",
                "command": home / "commands",
                "tool-shim": home / "tools",
                "plugin": home / "plugins",
            },
        )
    if agent == "openclaw":
        return AgentTarget(
            name="openclaw",
            home=root / ".openclaw",
            skills_dir=root / ".openclaw" / "skills",
            instructions_file=root / ".openclaw" / "AGENTS.md",
            detect_by_default=False,
            instruction_blocks_enabled=False,
            fake_root_only=True,
        )
    raise ValueError(f"unknown agent: {agent}")


def opencode_home(root: Path) -> Path:
    config_base = contained_xdg_config_home(root)
    return config_base / "opencode"


def contained_xdg_config_home(root: Path) -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        if resolved_path_within(root, candidate):
            return candidate
    return root / ".config"


def detect_agents(root: Path, requested: Iterable[str] | None = None) -> list[AgentTarget]:
    candidates = list(requested) if requested else DEFAULT_AGENT_NAMES
    targets: list[AgentTarget] = []
    for agent in candidates:
        target = target_for(root, agent)
        if agent_home_is_eligible(root, target):
            targets.append(target)
    return targets


def agent_home_statuses(root: Path, requested: Iterable[str] | None = None) -> list[dict[str, str | bool]]:
    candidates = list(requested) if requested else DEFAULT_AGENT_NAMES
    return [agent_home_status(root, target_for(root, agent)) for agent in candidates]


def agent_home_status(root: Path, target: AgentTarget) -> dict[str, str | bool]:
    if not target.home.exists() and not target.home.is_symlink():
        return {"agent": target.name, "eligible": False, "reason": "agent home not detected"}
    if target.home.is_symlink():
        return {"agent": target.name, "eligible": False, "reason": "agent home is a symlink"}
    if not target.home.is_dir():
        return {"agent": target.name, "eligible": False, "reason": "agent home is not a directory"}
    if not resolved_path_within(root, target.home):
        return {"agent": target.name, "eligible": False, "reason": "agent home resolves outside selected root"}
    if target.fake_root_only and looks_like_real_system_root(root):
        return {
            "agent": target.name,
            "eligible": False,
            "reason": "OpenClaw target is fake-root only before native target evidence",
        }
    return {"agent": target.name, "eligible": True, "reason": "agent home detected"}


def agent_home_is_eligible(root: Path, target: AgentTarget) -> bool:
    return bool(agent_home_status(root, target)["eligible"])


def all_agent_names() -> list[str]:
    return list(DEFAULT_AGENT_NAMES)


def known_agent_names() -> list[str]:
    return list(KNOWN_AGENT_NAMES)


def agent_supports_manifest_entry(agent: str, supported_agents: Iterable[str]) -> bool:
    declared = set(supported_agents)
    if agent in ADAPTER_AGENT_NAMES:
        return bool(declared.intersection(PORTABLE_MANIFEST_AGENT_NAMES))
    return agent in declared
