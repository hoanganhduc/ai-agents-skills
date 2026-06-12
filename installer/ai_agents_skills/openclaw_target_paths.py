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
