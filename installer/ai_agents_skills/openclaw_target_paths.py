from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path, PurePosixPath

from .state import existing_contained_parents
from .windows_security import private_directory_issue, private_file_issue


OPENCLAW_REAL_WRITE_CONFIRMATION_PHRASE = "I understand OpenClaw real-system skill-file writes"
OPENCLAW_REAL_WRITE_ACTION_CLASSES = ("canary-skill-file", "managed-skill-file")
SAFE_SKILL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
OPENCLAW_SKILL_FILE_MAX_BYTES = 4 * 1024 * 1024


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


def openclaw_skill_file_attestation(path: Path) -> dict[str, object]:
    """Return a stable, no-follow identity and content signature for a skill file."""
    try:
        initial = os.lstat(path)
    except FileNotFoundError:
        return {"exists": False, "kind": "missing"}
    if stat.S_ISLNK(initial.st_mode):
        return {"exists": True, "kind": "symlink", "target": os.readlink(path)}
    if stat.S_ISDIR(initial.st_mode):
        return {"exists": True, "kind": "directory"}
    if not stat.S_ISREG(initial.st_mode):
        return {"exists": True, "kind": "other"}
    if initial.st_size > OPENCLAW_SKILL_FILE_MAX_BYTES:
        raise ValueError("OpenClaw target skill file exceeds the attestation size limit")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise ValueError("OpenClaw target skill file changed while being attested") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or _file_identity(initial) != _file_identity(before):
            raise ValueError("OpenClaw target skill file changed while being attested")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > OPENCLAW_SKILL_FILE_MAX_BYTES:
                raise ValueError("OpenClaw target skill file exceeds the attestation size limit")
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if _file_identity(before) != _file_identity(after) or total != after.st_size:
        raise ValueError("OpenClaw target skill file changed while being attested")
    return {
        "exists": True,
        "kind": "file",
        "hash": "sha256:" + digest.hexdigest(),
        "device": int(after.st_dev),
        "inode": int(after.st_ino),
        "size": int(after.st_size),
        "mode": stat.S_IMODE(after.st_mode),
        "uid": int(getattr(after, "st_uid", 0)),
        "nlink": int(after.st_nlink),
        "mtime_ns": int(getattr(after, "st_mtime_ns", after.st_mtime * 1_000_000_000)),
        "ctime_ns": int(getattr(after, "st_ctime_ns", after.st_ctime * 1_000_000_000)),
    }


def openclaw_skill_file_attestation_issue(
    root: Path,
    signature: dict[str, object],
    *,
    path: Path | None = None,
) -> str | None:
    if signature.get("exists") is not True:
        return None
    if signature.get("kind") != "file":
        return f"target path is a {signature.get('kind', 'non-file')}"
    if signature.get("nlink") != 1:
        return "target skill file must have exactly one hard link"
    mode = signature.get("mode")
    if not isinstance(mode, int) or mode & 0o022:
        return "target skill file must not be group/world writable"
    if os.name == "posix":
        try:
            owner_uid = os.lstat(root).st_uid
        except FileNotFoundError:
            return "target root is missing"
        if signature.get("uid") != owner_uid:
            return "target skill file owner does not match target root owner"
    elif os.name == "nt" and path is not None:
        issue = private_file_issue(path)
        if issue is not None:
            return issue
    return None


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_nlink),
        int(getattr(metadata, "st_uid", 0)),
        int(metadata.st_size),
        int(getattr(metadata, "st_mtime_ns", metadata.st_mtime * 1_000_000_000)),
        int(getattr(metadata, "st_ctime_ns", metadata.st_ctime * 1_000_000_000)),
    )


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
    expected_uid = os.lstat(expanded).st_uid if os.name == "posix" else None
    for parent in existing_contained_parents(skills_dir, expanded):
        if parent.is_symlink():
            raise ValueError(f"OpenClaw target path has a symlinked parent: {parent}")
        if not parent.is_dir():
            raise ValueError(f"OpenClaw target path has a non-directory parent: {parent}")
        if os.name == "posix":
            metadata = os.lstat(parent)
            if metadata.st_uid != expected_uid:
                raise ValueError(f"OpenClaw target path owner differs from target root: {parent}")
            if stat.S_IMODE(metadata.st_mode) & 0o022:
                raise ValueError(f"OpenClaw target path is group/world writable: {parent}")
        elif os.name == "nt":
            issue = private_directory_issue(parent)
            if issue is not None:
                raise ValueError(f"OpenClaw target path DACL is unsafe: {parent}: {issue}")
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
    (re.compile(r"/home" r"/[A-Za-z0-9._-]+"), "posix-home-path"),  # split literal: sanitizer-safe
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
