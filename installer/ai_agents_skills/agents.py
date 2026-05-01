from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from .capabilities import resolved_path_within


@dataclass(frozen=True)
class AgentTarget:
    name: str
    home: Path
    skills_dir: Path
    instructions_file: Path
    legacy_skills_dirs: tuple[Path, ...] = ()
    optional_skills_dirs: tuple[Path, ...] = ()
    artifact_dirs: Mapping[str, Path] = field(default_factory=dict)

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
    raise ValueError(f"unknown agent: {agent}")


def detect_agents(root: Path, requested: Iterable[str] | None = None) -> list[AgentTarget]:
    candidates = list(requested) if requested else ["codex", "claude", "deepseek"]
    targets: list[AgentTarget] = []
    for agent in candidates:
        target = target_for(root, agent)
        if agent_home_is_eligible(root, target):
            targets.append(target)
    return targets


def agent_home_statuses(root: Path, requested: Iterable[str] | None = None) -> list[dict[str, str | bool]]:
    candidates = list(requested) if requested else ["codex", "claude", "deepseek"]
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
    return {"agent": target.name, "eligible": True, "reason": "agent home detected"}


def agent_home_is_eligible(root: Path, target: AgentTarget) -> bool:
    return bool(agent_home_status(root, target)["eligible"])


def all_agent_names() -> list[str]:
    return ["codex", "claude", "deepseek"]
