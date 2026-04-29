from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping


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
        if target.home.exists():
            targets.append(target)
    return targets


def all_agent_names() -> list[str]:
    return ["codex", "claude", "deepseek"]
