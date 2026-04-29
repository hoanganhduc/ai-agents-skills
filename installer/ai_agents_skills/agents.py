from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class AgentTarget:
    name: str
    home: Path
    skills_dir: Path
    instructions_file: Path
    legacy_skills_dirs: tuple[Path, ...] = ()


def target_for(root: Path, agent: str) -> AgentTarget:
    if agent == "codex":
        return AgentTarget(
            name="codex",
            home=root / ".codex",
            skills_dir=root / ".agents" / "skills",
            instructions_file=root / ".codex" / "AGENTS.md",
            legacy_skills_dirs=(root / ".codex" / "skills",),
        )
    if agent == "claude":
        return AgentTarget(
            name="claude",
            home=root / ".claude",
            skills_dir=root / ".claude" / "skills",
            instructions_file=root / ".claude" / "CLAUDE.md",
        )
    if agent == "deepseek":
        return AgentTarget(
            name="deepseek",
            home=root / ".deepseek",
            skills_dir=root / ".deepseek" / "skills",
            instructions_file=root / ".deepseek" / "AGENTS.md",
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
