from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


SUPPORT_STATES = {"supported", "fallback", "degraded", "blocked", "manual", "unsupported"}
MECHANISMS = {
    "native-command",
    "native-skill",
    "reference-adapter",
    "reference-doc",
    "copy",
    "symlink",
    "fake-root-copy",
    "runtime-copy",
    "instruction-block",
    "unsupported",
}
CLAIM_BASES = {"policy", "renderer", "planner", "runtime-manifest", "fake-root-only"}


@dataclass(frozen=True)
class TargetSurface:
    target: str
    surface: str
    support: str
    mechanism: str
    execution_scope: str
    claim_basis: str
    notes: str


TARGET_SURFACES: tuple[TargetSurface, ...] = (
    TargetSurface(
        "codex",
        "skill-file",
        "supported",
        "reference-adapter",
        "agent-visible regular SKILL.md adapter pointing at canonical source",
        "policy",
        "Auto mode uses reference adapters because symlinked Codex skill loading is not assumed.",
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
        "openclaw",
        "runtime-file",
        "blocked",
        "unsupported",
        "not installed",
        "fake-root-only",
        "OpenClaw-associated shared runtime writes are blocked until separate native evidence exists.",
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
