from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from .agents import antigravity_layout_paths


SUPPORT_STATES = {"supported", "fallback", "degraded", "blocked", "manual", "unsupported"}
MECHANISMS = {
    "native-command",
    "native-skill",
    "plugin",
    "settings-file",
    "mcp-config",
    "hook-config",
    "reference-adapter",
    "reference-doc",
    "copy",
    "symlink",
    "fake-root-copy",
    "runtime-copy",
    "instruction-block",
    "json-merge",
    "toml-merge",
    "unsupported",
}
CLAIM_BASES = {
    "policy",
    "renderer",
    "planner",
    "runtime-manifest",
    "fake-root-only",
    "official-docs",
    "installer-convention",
}


@dataclass(frozen=True)
class TargetSurface:
    target: str
    surface: str
    support: str
    mechanism: str
    execution_scope: str
    claim_basis: str
    notes: str


# The Antigravity trees exist in two layouts, and which one a home uses is
# decided per home by the vendor's own ``.migrated`` marker.  Both spellings are
# rendered from the same composer the installer resolves real paths with, so a
# note here cannot name a directory the installer does not write to -- which is
# what these notes did while they were typed out by hand: the migration moved the
# skill and plugin trees to ``.gemini/config`` and the table went on publishing
# ``.gemini/antigravity-cli``.  ``docs-check`` cannot catch that on its own,
# since it only asks whether the generated documents match this table.
_ANTIGRAVITY_BEFORE = antigravity_layout_paths(PurePosixPath("~"), migrated=False)
_ANTIGRAVITY_AFTER = antigravity_layout_paths(PurePosixPath("~"), migrated=True)


def antigravity_documented_dir(name: str) -> str:
    """Return both spellings of one Antigravity directory, for a note."""
    before = _ANTIGRAVITY_BEFORE[name].as_posix()
    after = _ANTIGRAVITY_AFTER[name].as_posix()
    if before == after:
        return before
    return f"{before}/ (or {after}/ on a home the vendor has migrated)"


TARGET_SURFACES: tuple[TargetSurface, ...] = (
    TargetSurface(
        "codex",
        "skill-file",
        "supported",
        "copy",
        "agent-visible regular SKILL.md file with copied support files",
        "policy",
        "Auto mode copies canonical skills and support files because symlinked Codex skill loading is not assumed and installed skills must remain self-contained.",
    ),
    TargetSurface(
        "claude",
        "skill-file",
        "supported",
        "symlink",
        "native Claude skill file",
        "policy",
        "Auto mode links to canonical SKILL.md with apply-time fallback.",
    ),
    TargetSurface(
        "deepseek",
        "skill-file",
        "supported",
        "reference-adapter",
        "agent-visible regular SKILL.md adapter pointing at canonical source",
        "policy",
        "Auto mode uses reference adapters because native symlink loading is not verified.",
    ),
    TargetSurface(
        "copilot",
        "skill-file",
        "supported",
        "reference-adapter",
        "personal GitHub Copilot skill adapter",
        "policy",
        "Copilot receives personal skill/persona surfaces only; commands/templates are unsupported.",
    ),
    TargetSurface(
        "opencode",
        "skill-file",
        "supported",
        "copy",
        "OpenCode-native regular SKILL.md file with copied support files",
        "policy",
        "Auto mode copies canonical skills for cross-platform parity; explicit reference and symlink modes remain available with evidence.",
    ),
    TargetSurface(
        "antigravity",
        "skill-file",
        "supported",
        "copy",
        "Antigravity global flat Markdown skill file with the embedded canonical body",
        "official-docs",
        f"Auto mode writes <skill>.md under {antigravity_documented_dir('skills')} with the full canonical skill body and copies support files, matching the documented global skill layout.",
    ),
    TargetSurface(
        "openclaw",
        "skill-file",
        "manual",
        "fake-root-copy",
        "fake-root install-target layout only",
        "fake-root-only",
        "OpenClaw real-system target writes stay blocked until native target evidence exists.",
    ),
    TargetSurface(
        "codex",
        "entrypoint-alias",
        "supported",
        "reference-doc",
        "non-executing quick-action reference",
        "renderer",
        "Codex does not receive native slash-command registrations from this artifact class.",
    ),
    TargetSurface(
        "claude",
        "entrypoint-alias",
        "supported",
        "native-command",
        "Claude command file",
        "renderer",
        "Claude is the only current target that receives native command files for entrypoint aliases.",
    ),
    TargetSurface(
        "deepseek",
        "entrypoint-alias",
        "supported",
        "reference-doc",
        "non-executing quick-action reference",
        "renderer",
        "DeepSeek receives entrypoint references rather than native command registrations.",
    ),
    TargetSurface(
        "copilot",
        "entrypoint-alias",
        "unsupported",
        "unsupported",
        "not installed",
        "planner",
        "Copilot optional artifacts are limited to supported personal surfaces.",
    ),
    TargetSurface(
        "opencode",
        "entrypoint-alias",
        "supported",
        "native-command",
        "OpenCode command markdown file",
        "renderer",
        "OpenCode receives native command aliases under ~/.config/opencode/commands.",
    ),
    TargetSurface(
        "antigravity",
        "entrypoint-alias",
        "supported",
        "native-skill",
        "Antigravity global flat Markdown skill alias",
        "renderer",
        f"Entry-point aliases render as global Antigravity Markdown skills under {antigravity_documented_dir('skills')}.",
    ),
    TargetSurface(
        "openclaw",
        "entrypoint-alias",
        "unsupported",
        "unsupported",
        "not installed",
        "fake-root-only",
        "OpenClaw commands/aliases remain outside target support.",
    ),
    TargetSurface(
        "codex",
        "runtime-file",
        "supported",
        "runtime-copy",
        "root-scoped managed runtime helpers",
        "runtime-manifest",
        "Runtime files are copied under the selected root runtime, not per-agent skill folders.",
    ),
    TargetSurface(
        "claude",
        "runtime-file",
        "supported",
        "runtime-copy",
        "root-scoped managed runtime helpers",
        "runtime-manifest",
        "Runtime files are copied under the selected root runtime, not per-agent skill folders.",
    ),
    TargetSurface(
        "deepseek",
        "runtime-file",
        "supported",
        "runtime-copy",
        "root-scoped managed runtime helpers",
        "runtime-manifest",
        "Runtime files are copied under the selected root runtime, not per-agent skill folders.",
    ),
    TargetSurface(
        "copilot",
        "runtime-file",
        "supported",
        "runtime-copy",
        "root-scoped managed runtime helpers",
        "runtime-manifest",
        "Runtime files are copied under the selected root runtime, not per-agent skill folders.",
    ),
    TargetSurface(
        "opencode",
        "runtime-file",
        "supported",
        "runtime-copy",
        "root-scoped managed runtime helpers",
        "runtime-manifest",
        "Runtime files are copied under the neutral shared ai-agents-skills runtime root, not inside OpenCode config.",
    ),
    TargetSurface(
        "antigravity",
        "runtime-file",
        "supported",
        "runtime-copy",
        "root-scoped managed runtime helpers",
        "runtime-manifest",
        "Runtime files use the neutral shared ai-agents-skills runtime root, not a Codex-specific runtime path.",
    ),
    TargetSurface(
        "openclaw",
        "runtime-file",
        "manual",
        "runtime-copy",
        "fake-root by default; real-system gated by an approved manifest + host broker",
        "runtime-manifest",
        "Real-system runtime files install via an approved content-addressed runtime manifest "
        "(openclaw-runtime-apply-manifest --real-system + confirmation phrase, verify-before-write): "
        "inert files under .openclaw/skills/<skill>/, executable files under the neutral runtime root, "
        "exposed to the sandbox by the openclaw-broker. Fake-root-only by default.",
    ),
    TargetSurface(
        "antigravity",
        "agent-persona",
        "supported",
        "plugin",
        "Antigravity plugin agent definition",
        "official-docs",
        f"Personas are installed under {antigravity_documented_dir('plugin-package')} in an agents/ subdirectory, with the plugin manifest.",
    ),
    TargetSurface(
        "antigravity",
        "plugin",
        "supported",
        "plugin",
        "Antigravity native plugin package",
        "official-docs",
        f"The installer creates plugin.json and a managed plugin payload under {antigravity_documented_dir('plugin-package')}.",
    ),
    TargetSurface(
        "antigravity",
        "global-context",
        "supported",
        "instruction-block",
        "managed block in ~/.gemini/GEMINI.md",
        "official-docs",
        "Skill and repo-management instruction blocks use the documented Antigravity global developer context file.",
    ),
    TargetSurface(
        "antigravity",
        "settings-file",
        "supported",
        "settings-file",
        "sparse Antigravity settings JSON",
        "official-docs",
        f"A no-op settings.json scaffold is managed under {antigravity_documented_dir('settings')} when Antigravity artifacts are installed.",
    ),
    TargetSurface(
        "antigravity",
        "mcp-config",
        "supported",
        "mcp-config",
        "plugin-scoped Antigravity MCP config",
        "official-docs",
        "A no-op mcp_config.json scaffold with an empty mcpServers map is installed inside the managed Antigravity plugin.",
    ),
    TargetSurface(
        "antigravity",
        "hook-config",
        "supported",
        "hook-config",
        "plugin-scoped Antigravity hooks config",
        "official-docs",
        "A no-op hooks.json scaffold is installed inside the managed Antigravity plugin.",
    ),
    TargetSurface(
        "claude",
        "settings-json-merge",
        "supported",
        "json-merge",
        "managed Stop-hook entry merged into ~/.claude/settings.json",
        "planner",
        "When the autonomous-research-loop runtime is installed, one managed hooks.Stop entry (tagged _managedBy/_id) is idempotently merged into the user's settings.json and removed on uninstall; user-authored hooks are preserved.",
    ),
    TargetSurface(
        "grok",
        "skill-file",
        "supported",
        "copy",
        "Grok-native directory-layout SKILL.md file with copied support files",
        "official-docs",
        "Auto mode writes ~/.grok/skills/<skill>/SKILL.md with the full canonical body, matching the documented Grok skills layout (08-skills.md).",
    ),
    TargetSurface(
        "grok",
        "entrypoint-alias",
        "supported",
        "native-command",
        "Grok command markdown file",
        "official-docs",
        "Entry-point aliases render as native Grok command files under ~/.grok/commands/.",
    ),
    TargetSurface(
        "grok",
        "runtime-file",
        "supported",
        "runtime-copy",
        "root-scoped managed runtime helpers",
        "runtime-manifest",
        "Runtime files use the neutral shared ai-agents-skills runtime root, not a Codex-specific runtime path.",
    ),
    TargetSurface(
        "grok",
        "agent-persona",
        "supported",
        "copy",
        "Grok subagent Markdown definition",
        "official-docs",
        "Personas install under ~/.grok/agents/ as name/description overlays; Claude-format tool-restriction frontmatter is not enforced on Grok.",
    ),
    TargetSurface(
        "grok",
        "instruction-block",
        "supported",
        "instruction-block",
        "managed block in ~/.grok/AGENTS.md",
        "official-docs",
        "Skill and repo-management instruction blocks use the documented Grok home-scope AGENTS.md context file; there is no GROK.md.",
    ),
    TargetSurface(
        "grok",
        "instruction-doc",
        "supported",
        "copy",
        "Grok rules-directory Markdown file",
        "installer-convention",
        "Instruction docs copy to ~/.grok/rules/; home-scope rules/ loading is unverified.",
    ),
    TargetSurface(
        "grok",
        "template",
        "supported",
        "copy",
        "inert managed template storage",
        "installer-convention",
        "Templates copy to ~/.grok/templates/ as inert support storage referenced by skill relative paths; not a Grok-loaded surface.",
    ),
    TargetSurface(
        "grok",
        "tool-shim",
        "supported",
        "copy",
        "inert managed tool storage",
        "installer-convention",
        "Tool shims copy to ~/.grok/tools/ as inert support storage referenced by skill relative paths; not a Grok-loaded surface.",
    ),
    TargetSurface(
        "grok",
        "native-hook-file",
        "supported",
        "hook-config",
        "discrete managed ~/.grok/hooks/ai-agents-skills-autoloop.json",
        "official-docs",
        "The optional autoloop Stop hook installs as a fully-owned native hook file under ~/.grok/hooks/ (10-hooks.md); ~/.grok/settings.json is never written.",
    ),
    TargetSurface(
        "grok",
        "config-compat",
        "supported",
        "toml-merge",
        "managed [compat.claude] block merged into ~/.grok/config.toml",
        "official-docs",
        "A managed [compat.claude] block (skills/agents/rules/hooks = false) is idempotently merged into ~/.grok/config.toml so Grok presents a single self-contained view; the block is removed on uninstall and user-authored TOML is preserved.",
    ),
    TargetSurface(
        "kimi",
        "skill-file",
        "supported",
        "copy",
        "Kimi Code directory-layout SKILL.md with copied support files",
        "official-docs",
        "Auto mode writes ~/.kimi-code/skills/<skill>/SKILL.md with the full canonical body (directory form; name and description required).",
    ),
    TargetSurface(
        "kimi",
        "entrypoint-alias",
        "unsupported",
        "unsupported",
        "not installed",
        "planner",
        "Kimi has no commands/ loader; skills are invoked as /skill:<name>. Planner rejects entrypoint-alias so adapter inheritance does not create stray skill files.",
    ),
    TargetSurface(
        "kimi",
        "runtime-file",
        "supported",
        "runtime-copy",
        "root-scoped managed runtime helpers",
        "runtime-manifest",
        "Runtime files use the neutral shared ai-agents-skills runtime root, not a Kimi-specific runtime path.",
    ),
    TargetSurface(
        "kimi",
        "agent-persona",
        "supported",
        "copy",
        "Kimi custom agent Markdown definition",
        "official-docs",
        "Personas install under ~/.kimi-code/agents/ as name/description Markdown; unknown frontmatter fields are ignored by Kimi.",
    ),
    TargetSurface(
        "kimi",
        "instruction-block",
        "supported",
        "instruction-block",
        "managed block in ~/.kimi-code/AGENTS.md",
        "official-docs",
        "Skill and repo-management instruction blocks use the documented Kimi home-scope AGENTS.md file.",
    ),
    TargetSurface(
        "kimi",
        "instruction-doc",
        "supported",
        "copy",
        "inert managed instruction storage",
        "installer-convention",
        "Instruction docs copy to ~/.kimi-code/instructions/ as inert support storage; not a documented Kimi-loaded surface.",
    ),
    TargetSurface(
        "kimi",
        "template",
        "supported",
        "copy",
        "inert managed template storage",
        "installer-convention",
        "Templates copy to ~/.kimi-code/templates/ as inert support storage referenced by skill relative paths.",
    ),
    TargetSurface(
        "kimi",
        "tool-shim",
        "supported",
        "copy",
        "inert managed tool storage",
        "installer-convention",
        "Tool shims copy to ~/.kimi-code/tools/ as inert support storage referenced by skill relative paths.",
    ),
    TargetSurface(
        "chatgpt-local-coder",
        "skill-file",
        "supported",
        "copy",
        "host directory-layout SKILL.md with copied support files",
        "official-docs",
        "Auto mode writes ~/.chatgpt-local-coder/skills/<skill>/SKILL.md with the full canonical body; the host discovers that directory as a skill root and copy mode keeps the install self-contained on native Windows, where symlink creation is privilege-gated.",
    ),
    TargetSurface(
        "chatgpt-local-coder",
        "entrypoint-alias",
        "supported",
        "reference-doc",
        "non-executing quick-action reference",
        "renderer",
        "The host exposes skills through skill_list/skill_run and has no slash-command loader, so aliases install as reference docs under ~/.chatgpt-local-coder/instructions/entrypoints/ instead of as native commands.",
    ),
    TargetSurface(
        "chatgpt-local-coder",
        "runtime-file",
        "supported",
        "runtime-copy",
        "root-scoped managed runtime helpers",
        "runtime-manifest",
        "Runtime files use the neutral shared ai-agents-skills runtime root, not a host-specific runtime path.",
    ),
    TargetSurface(
        "chatgpt-local-coder",
        "agent-persona",
        "supported",
        "copy",
        "inert managed persona storage",
        "installer-convention",
        "Personas copy to ~/.chatgpt-local-coder/agents/ as name/description Markdown. The host imports subagent definitions from ~/.claude/agents only; it does not yet read its own agents directory, so these files are reference material rather than registered subagents.",
    ),
    TargetSurface(
        "chatgpt-local-coder",
        "instruction-block",
        "supported",
        "instruction-block",
        "managed block in ~/.chatgpt-local-coder/AGENTS.md",
        "official-docs",
        "The host loads ~/.chatgpt-local-coder/AGENTS.md as its user-level memory file, so managed skill and repo-management blocks reach the model.",
    ),
    TargetSurface(
        "chatgpt-local-coder",
        "instruction-doc",
        "supported",
        "copy",
        "inert managed instruction storage",
        "installer-convention",
        "Instruction docs copy to ~/.chatgpt-local-coder/instructions/ as inert support storage referenced by skill relative paths; the host does not auto-load that directory.",
    ),
    TargetSurface(
        "chatgpt-local-coder",
        "template",
        "supported",
        "copy",
        "inert managed template storage",
        "installer-convention",
        "Templates copy to ~/.chatgpt-local-coder/templates/ as inert support storage referenced by skill relative paths.",
    ),
    TargetSurface(
        "chatgpt-local-coder",
        "tool-shim",
        "supported",
        "copy",
        "inert managed tool storage",
        "installer-convention",
        "Tool shims copy to ~/.chatgpt-local-coder/tools/ as inert support storage referenced by skill relative paths; they are not registered as MCP tools.",
    ),
)


def validate_target_surfaces() -> None:
    seen: set[tuple[str, str]] = set()
    for row in TARGET_SURFACES:
        if row.support not in SUPPORT_STATES:
            raise ValueError(f"invalid support state for {row.target}:{row.surface}: {row.support}")
        if row.mechanism not in MECHANISMS:
            raise ValueError(f"invalid mechanism for {row.target}:{row.surface}: {row.mechanism}")
        if row.claim_basis not in CLAIM_BASES:
            raise ValueError(f"invalid claim basis for {row.target}:{row.surface}: {row.claim_basis}")
        key = (row.target, row.surface)
        if key in seen:
            raise ValueError(f"duplicate target surface row: {row.target}:{row.surface}")
        seen.add(key)
        surface = PurePosixPath(row.surface)
        if surface.is_absolute() or ".." in surface.parts:
            raise ValueError(f"invalid target surface name: {row.surface}")


def target_surface_rows() -> list[dict[str, str]]:
    validate_target_surfaces()
    return [
        {
            "target": row.target,
            "surface": row.surface,
            "support": row.support,
            "mechanism": row.mechanism,
            "execution_scope": row.execution_scope,
            "claim_basis": row.claim_basis,
            "notes": row.notes,
        }
        for row in TARGET_SURFACES
    ]


def target_surface_for(target: str, surface: str) -> TargetSurface | None:
    validate_target_surfaces()
    for row in TARGET_SURFACES:
        if row.target == target and row.surface == surface:
            return row
    return None
