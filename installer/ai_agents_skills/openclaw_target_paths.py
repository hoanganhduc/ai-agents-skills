from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .state import existing_contained_parents


OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE = "I understand OpenClaw real-system skill-file writes"
OPENCLAW_REAL_WRITE_ACTION_CLASSES = ("canary-skill-file", "managed-skill-file")
SAFE_SKILL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


def openclaw_home(root: Path) -> Path:
    return root.expanduser() / ".openclaw"


def openclaw_managed_skills_dir(root: Path) -> Path:
    return openclaw_home(root) / "skills"


def checked_skill_slug(skill: str) -> str:
    if SAFE_SKILL_RE.fullmatch(skill) is None:
        raise ValueError("OpenClaw target skill names must be canonical kebab-case")
    return skill


def skill_file_relative_path(skill: str) -> str:
    return PurePosixPath("skills", checked_skill_slug(skill), "SKILL.md").as_posix()


def checked_openclaw_target_relative_path(value: str, *, action_class: str) -> str:
    if action_class not in OPENCLAW_REAL_WRITE_ACTION_CLASSES:
        raise ValueError("OpenClaw target action class is not allowed for real writes")
    if not isinstance(value, str) or not value:
        raise ValueError("OpenClaw target relative path is required")
    if value.startswith(("/", "\\")):
        raise ValueError("OpenClaw target path must be relative")
    if "\\" in value:
        raise ValueError("OpenClaw target path must use POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("OpenClaw target path must stay inside .openclaw")
    if len(path.parts) != 3 or path.parts[0] != "skills" or path.parts[2] != "SKILL.md":
        raise ValueError("OpenClaw real writes are limited to skills/<skill>/SKILL.md")
    checked_skill_slug(path.parts[1])
    return path.as_posix()


def openclaw_target_path(root: Path, relative_path: str, *, action_class: str) -> Path:
    checked = checked_openclaw_target_relative_path(relative_path, action_class=action_class)
    return openclaw_home(root) / Path(checked)


def validate_openclaw_target_home(root: Path) -> dict[str, str]:
    expanded = root.expanduser()
    home = openclaw_home(expanded)
    skills_dir = openclaw_managed_skills_dir(expanded)
    if not expanded.exists() or not expanded.is_dir():
        raise ValueError("OpenClaw target root must be an existing directory")
    if expanded.is_symlink():
        raise ValueError("OpenClaw target root must not be a symlink")
    if not home.exists() or not home.is_dir():
        raise ValueError("OpenClaw real-system writes require an existing .openclaw directory")
    if home.is_symlink():
        raise ValueError("OpenClaw target .openclaw directory must not be a symlink")
    if not skills_dir.exists() or not skills_dir.is_dir():
        raise ValueError("OpenClaw real-system writes require an existing .openclaw/skills directory")
    if skills_dir.is_symlink():
        raise ValueError("OpenClaw target .openclaw/skills directory must not be a symlink")
    for parent in existing_contained_parents(skills_dir, expanded):
        if parent.is_symlink():
            raise ValueError(f"OpenClaw target path has a symlinked parent: {parent}")
        if not parent.is_dir():
            raise ValueError(f"OpenClaw target path has a non-directory parent: {parent}")
    return {
        "home_realpath": str(home.resolve(strict=False)),
        "managed_skills_realpath": str(skills_dir.resolve(strict=False)),
    }


# Machine-specific / non-portable path markers that must never appear in synced
# OpenClaw content. A strict superset of the four legacy Codex-path markers.
# Portable references ($HOME, ~, $AAS_RUNTIME_ROOT, $AAS_BROKER_ENDPOINT) are
# allowed and deliberately not matched here.
# NOTE: bare %USERPROFILE%/%LOCALAPPDATA% are PORTABLE Windows env vars (the Windows
# equivalent of $HOME) and are intentionally NOT flagged; only their machine/agent-
# specific runtime suffixes are. POSIX absolute home/workspace roots ARE machine-
# specific leaks. This is a strict superset of the four legacy Codex markers.
OPENCLAW_PATH_LEAK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\.codex/runtime"), "codex-runtime-path"),
    (re.compile(r"\$codex_home", re.I), "codex-home-var"),
    (re.compile(r"%userprofile%[\\/]+\.?codex", re.I), "windows-codex-path"),
    (re.compile(r"%localappdata%[\\/]+ai-agents-skills[\\/]+runtime", re.I), "windows-aas-runtime-path"),
    (re.compile(r"/home/[A-Za-z0-9._-]+"), "posix-home-path"),
    (re.compile(r"/Users/[A-Za-z0-9._-]+"), "macos-home-path"),
    (re.compile(r"(?<![A-Za-z0-9._])/root/[A-Za-z0-9._-]"), "root-home-path"),
    # NOTE: bare "/workspace" is intentionally NOT flagged. In the OpenClaw sandbox
    # HOME=/workspace, so it is byte-identical across every sandbox (portable, like
    # $HOME), and it also appears legitimately as the runtime "workspace/" subdir
    # (<runtime_root>/workspace/...). Machine-specific host homes are caught above.
)


def path_leak_scan(content: str) -> list[str]:
    """Return sorted unique labels for machine-specific path leaks in OpenClaw content.

    Empty list means clean. Portable references (``$HOME``, ``~``,
    ``$AAS_RUNTIME_ROOT``, ``$AAS_BROKER_ENDPOINT``) are allowed. This is the shared
    replacement for the legacy four-marker Codex-path check used by the manifest
    validator and the planner content gate.
    """
    leaks = {label for pattern, label in OPENCLAW_PATH_LEAK_PATTERNS if pattern.search(content)}
    return sorted(leaks)


def path_leak_block_reason(content: str) -> str | None:
    """Reason string for the first machine-specific path leak, or None if clean."""
    leaks = path_leak_scan(content)
    if not leaks:
        return None
    return "OpenClaw content references machine-specific paths: " + ", ".join(leaks)
