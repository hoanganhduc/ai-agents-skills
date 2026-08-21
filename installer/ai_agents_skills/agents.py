from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .capabilities import looks_like_real_system_root, resolved_path_within
from .openclaw_target_gate import openclaw_target_capabilities, openclaw_target_decision


DEFAULT_AGENT_NAMES = [
    "codex",
    "claude",
    "deepseek",
    "copilot",
    "opencode",
    "antigravity",
    "grok",
    "kimi",
    "openclaw",
    "chatgpt-local-coder",
]
KNOWN_AGENT_NAMES = list(DEFAULT_AGENT_NAMES)
PORTABLE_MANIFEST_AGENT_NAMES = {"codex", "claude", "deepseek"}
ADAPTER_AGENT_NAMES = {
    "copilot",
    "opencode",
    "antigravity",
    "grok",
    "kimi",
    "openclaw",
    "chatgpt-local-coder",
}


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
    skill_file_layout: str = "directory"
    target_capabilities: Mapping[str, Any] = field(default_factory=dict)

    def target_dir_for(self, artifact_type: str) -> Path:
        return self.artifact_dirs.get(artifact_type, self.skills_dir)

    def skill_file_for(self, skill: str) -> Path:
        if self.skill_file_layout == "flat-md":
            return self.skills_dir / f"{skill}.md"
        return self.skills_dir / skill / "SKILL.md"

    def support_dir_for(self, skill: str) -> Path:
        if self.name == "antigravity":
            # Composed from the plugin directory this target already carries
            # rather than from ``home``: the plugin root moves with the
            # vendor's migration, and a second composition of the same path
            # would have to be kept in step with it by hand.
            return self.target_dir_for("plugin") / "skills" / skill
        return self.skills_dir / skill


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
    if agent == "antigravity":
        home = antigravity_home(root)
        plugin_home = antigravity_plugin_root(root) / "ai-agents-skills"
        skills_dir = antigravity_skills_dir(root)
        return AgentTarget(
            name="antigravity",
            home=home,
            skills_dir=skills_dir,
            instructions_file=root / ".gemini" / "GEMINI.md",
            legacy_skills_dirs=(root / ".gemini" / "skills", root / ".agents" / "skills"),
            optional_skills_dirs=(root / ".agents" / "skills", root / ".gemini" / "skills"),
            artifact_dirs={
                "agent-persona": plugin_home / "agents",
                "template": plugin_home / "templates",
                "instruction-doc": plugin_home / "rules",
                "entrypoint-alias": skills_dir,
                "command": plugin_home / "skills",
                "tool-shim": plugin_home / "tools",
                "plugin": plugin_home,
            },
            skill_file_layout="flat-md",
        )
    if agent == "grok":
        return AgentTarget(
            name="grok",
            home=root / ".grok",
            skills_dir=root / ".grok" / "skills",
            instructions_file=root / ".grok" / "AGENTS.md",
            optional_skills_dirs=(root / ".claude" / "skills", root / ".agents" / "skills"),
            artifact_dirs={
                "agent-persona": root / ".grok" / "agents",
                "template": root / ".grok" / "templates",
                "instruction-doc": root / ".grok" / "rules",
                "entrypoint-alias": root / ".grok" / "commands",
                "command": root / ".grok" / "commands",
                "tool-shim": root / ".grok" / "tools",
                "native-hook-file": root / ".grok" / "hooks",
            },
            skill_file_layout="directory",
            instruction_blocks_enabled=True,
        )
    if agent == "kimi":
        home = kimi_home(root)
        return AgentTarget(
            name="kimi",
            home=home,
            skills_dir=home / "skills",
            instructions_file=home / "AGENTS.md",
            optional_skills_dirs=(root / ".agents" / "skills",),
            artifact_dirs={
                "agent-persona": home / "agents",
                "template": home / "templates",
                "instruction-doc": home / "instructions",
                "tool-shim": home / "tools",
            },
            skill_file_layout="directory",
            instruction_blocks_enabled=True,
        )
    if agent == "chatgpt-local-coder":
        home = root / ".chatgpt-local-coder"
        return AgentTarget(
            name="chatgpt-local-coder",
            home=home,
            skills_dir=home / "skills",
            instructions_file=home / "AGENTS.md",
            optional_skills_dirs=(root / ".agents" / "skills",),
            artifact_dirs={
                "agent-persona": home / "agents",
                "template": home / "templates",
                # The host reads skills, personas and instruction docs; it has no
                # slash-command loader, so aliases install as reference docs the
                # way they do for Codex and DeepSeek rather than as commands.
                "instruction-doc": home / "instructions",
                "entrypoint-alias": home / "instructions" / "entrypoints",
                "command": home / "commands",
                "tool-shim": home / "tools",
            },
        )
    if agent == "openclaw":
        return AgentTarget(
            name="openclaw",
            home=root / ".openclaw",
            skills_dir=root / ".openclaw" / "skills",
            instructions_file=root / ".openclaw" / "AGENTS.md",
            instruction_blocks_enabled=False,
            fake_root_only=True,
            target_capabilities=openclaw_target_capabilities(),
        )
    raise ValueError(f"unknown agent: {agent}")


def opencode_home(root: Path) -> Path:
    config_base = contained_xdg_config_home(root)
    return config_base / "opencode"


def antigravity_home(root: Path) -> Path:
    return root / ".gemini" / "antigravity-cli"


def antigravity_layout_paths(root: Any, *, migrated: bool) -> dict[str, Any]:
    """Return the Antigravity directories for one of the two vendor layouts.

    The functions below choose *between* these layouts by reading the home; this
    one only composes them, so it is also what the target-surface table renders
    its documented paths from.  Before that the table spelled those paths out by
    hand, and when the vendor migration moved the trees the spelling stayed at
    the pre-migration location: on a migrated home every managed file goes to
    ``.gemini/config`` while the published table still named
    ``.gemini/antigravity-cli``.  Nothing caught it, because ``docs-check``
    compares the generated documents against this table and agrees with whatever
    the table says.

    ``root`` is only joined onto, never inspected, so the table can pass a
    ``PurePosixPath("~")`` and get the same paths an install composes from a real
    home.

    ``settings`` stays under the CLI's own home in both layouts: the migration
    moved the skill and plugin trees, not the settings file.
    """
    base = (root / ".gemini" / "config") if migrated else antigravity_home(root)
    return {
        "skills": base / "skills",
        "plugins": base / "plugins",
        "plugin-package": base / "plugins" / "ai-agents-skills",
        "settings": antigravity_home(root) / "settings.json",
    }


def antigravity_plugin_root(root: Path) -> Path:
    """Return the plugins directory the Antigravity CLI loads plugins from.

    The same migration that moved ``skills`` also moved this tree, but it moved
    it differently: ``skills`` was replaced by a compatibility symlink, while
    ``plugins`` was copied and both directories were left real.  Both are still
    scanned -- in the vendor's own logs every session has loader threads on each
    -- so a payload written to only the pre-migration path is read by a small
    minority of them, and the copy left at the migrated path keeps being served
    to the rest under the same plugin name.  Which of two same-named plugins
    wins is not something those logs answer, so the installer keeps exactly one:
    it writes to the migrated path and, through
    ``antigravity_legacy_plugin_dir`` below, removes what it previously wrote to
    the other.

    As with the skills directory, the path returned is a literal composed here.
    A symlink at the migrated path is refused rather than followed, since a link
    is exactly how a managed write gets redirected somewhere the installer never
    chose.
    """
    legacy = antigravity_layout_paths(root, migrated=False)["plugins"]
    if not (root / ".gemini" / "config" / ".migrated").exists():
        return legacy
    migrated = antigravity_layout_paths(root, migrated=True)["plugins"]
    if migrated.is_symlink():
        return legacy
    return migrated


def antigravity_legacy_plugin_dir(root: Path) -> Path | None:
    """Return the abandoned plugin payload directory, when there is one.

    ``None`` unless the migrated root is in use and the pre-migration directory
    is a distinct place on disk, so a home that never migrated -- and one whose
    two paths resolve to the same directory -- yields nothing to remove.
    """
    legacy = antigravity_layout_paths(root, migrated=False)["plugins"]
    if antigravity_plugin_root(root) == legacy:
        return None
    try:
        if legacy.exists() and legacy.samefile(antigravity_plugin_root(root)):
            return None
    except OSError:
        return None
    return legacy / "ai-agents-skills"


def antigravity_skills_dir(root: Path) -> Path:
    """Return the flat skills directory the Antigravity CLI reads.

    The vendor migrated its own layout, replacing ``antigravity-cli/skills``
    with a compatibility symlink to ``.gemini/config/skills`` and leaving a
    ``.migrated`` marker beside the new tree.  The installer refuses to write
    through a symlinked managed directory -- correctly, since a link is exactly
    how a write gets redirected -- so on a migrated home it must target the
    migrated directory itself or install nothing at all.

    The returned path is always a literal this function composes, never the
    ``readlink`` of the compatibility link.  The installer decides where it
    writes; following the link would let whatever the vendor points it at
    receive managed writes.  The link is read only to confirm it goes where the
    migration is documented to put it, and any other value falls back to the
    unmigrated location rather than trusting it.
    """
    legacy = antigravity_layout_paths(root, migrated=False)["skills"]
    migrated = antigravity_layout_paths(root, migrated=True)["skills"]
    if not (root / ".gemini" / "config" / ".migrated").exists():
        return legacy
    try:
        if not legacy.is_symlink():
            # A real directory beside the marker means the migration did not
            # move this tree, so the files are still where they always were.
            return legacy if legacy.exists() else migrated
        link = Path(os.readlink(legacy))
        target = link if link.is_absolute() else (legacy.parent / link)
    except OSError:
        return legacy
    if os.path.normpath(target) != os.path.normpath(migrated):
        return legacy
    return migrated


def kimi_home(root: Path) -> Path:
    """Resolve the Kimi Code data root under the selected install root.

    Default is ``root/.kimi-code``. When ``KIMI_CODE_HOME`` is set and resolves
    *inside* the selected root (fake-root or intentional relocation), that path
    is used. Relocated homes outside the selected root are unsupported for
    real-system installs; callers keep the default and prechecks may warn.
    """
    configured = os.environ.get("KIMI_CODE_HOME")
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        if resolved_path_within(root, candidate):
            return candidate
    return root / ".kimi-code"


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


def agent_home_statuses(root: Path, requested: Iterable[str] | None = None) -> list[dict[str, Any]]:
    candidates = list(requested) if requested else DEFAULT_AGENT_NAMES
    return [agent_home_status(root, target_for(root, agent)) for agent in candidates]


def agent_home_status(root: Path, target: AgentTarget) -> dict[str, Any]:
    if not target.home.exists() and not target.home.is_symlink():
        return {"agent": target.name, "eligible": False, "reason": "agent home not detected"}
    if target.home.is_symlink():
        return {"agent": target.name, "eligible": False, "reason": "agent home is a symlink"}
    if not target.home.is_dir():
        return {"agent": target.name, "eligible": False, "reason": "agent home is not a directory"}
    if not resolved_path_within(root, target.home):
        return {"agent": target.name, "eligible": False, "reason": "agent home resolves outside selected root"}
    if target.name == "openclaw":
        decision = openclaw_target_decision(root, operation="detect", path=target.home)
        if not decision["allowed"]:
            return {
                "agent": target.name,
                "eligible": False,
                "reason": decision["reason"],
                "target_gate": decision,
            }
    elif target.fake_root_only and looks_like_real_system_root(root):
        return {
            "agent": target.name,
            "eligible": False,
            "reason": "target is fake-root only",
        }
    return {"agent": target.name, "eligible": True, "reason": "agent home detected"}


def agent_home_is_eligible(root: Path, target: AgentTarget) -> bool:
    return bool(agent_home_status(root, target)["eligible"])


def all_agent_names() -> list[str]:
    return list(DEFAULT_AGENT_NAMES)


def known_agent_names() -> list[str]:
    return list(KNOWN_AGENT_NAMES)


def skill_path_is_agent_visible(agent: str, path: Path, skill: str) -> bool:
    if agent == "antigravity":
        return path.name == f"{skill}.md"
    return path.name == "SKILL.md" and path.parent.name == skill


def agent_supports_manifest_entry(agent: str, supported_agents: Iterable[str]) -> bool:
    declared = set(supported_agents)
    if agent in declared:
        return True
    if agent in ADAPTER_AGENT_NAMES:
        return bool(declared.intersection(PORTABLE_MANIFEST_AGENT_NAMES))
    return False
