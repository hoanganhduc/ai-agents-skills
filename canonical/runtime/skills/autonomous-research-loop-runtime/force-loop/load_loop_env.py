#!/usr/bin/env python3
"""Descriptor-bound strict policy loader for force-loop."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Mapping

MAX_POLICY_BYTES = 16_384
POLICY_KEYS = frozenset(
    {
        "AAS_AUTOLOOP_GOAL_PRIORITY",
        "AAS_AUTOLOOP_NOTIFY",
        "AAS_AUTOLOOP_FORMAL_POLICY",
        "AAS_AUTOLOOP_FORMAL_TYPECHECK",
        "AAS_FORCE_LOOP_COMPUTE_LANES",
    }
)
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_VALUE_RE = re.compile(r"^[A-Za-z0-9_.,:+/@-]*$")


class EnvLoadError(ValueError):
    """A force-loop policy file failed closed."""


def parse_env_text(
    text: str,
    *,
    source: str = "<memory>",
    allowed_keys: frozenset[str] = POLICY_KEYS,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw or raw.startswith("#"):
            continue
        if raw != raw.strip() or "=" not in raw:
            raise EnvLoadError(f"{source}:{lineno}: malformed assignment")
        key, value = raw.split("=", 1)
        if not _KEY_RE.fullmatch(key) or key not in allowed_keys:
            raise EnvLoadError(f"{source}:{lineno}: unsupported policy key")
        if key in out:
            raise EnvLoadError(f"{source}:{lineno}: duplicate policy key")
        if not value or not _VALUE_RE.fullmatch(value):
            raise EnvLoadError(f"{source}:{lineno}: invalid policy value")
        out[key] = value
    return out


def _open_directory_nofollow(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = os.open(absolute.anchor or os.sep, flags)
    try:
        for component in absolute.parts[1:]:
            child = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except Exception:
        os.close(fd)
        raise


def _within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def load_env_file(
    path: Path | str,
    *,
    allowed_keys: frozenset[str] = POLICY_KEYS,
    required: bool = True,
    forbidden_root: Path | None = None,
) -> dict[str, str]:
    """Read one absolute owner-private single-link policy through one fd."""
    supplied = Path(path)
    if not supplied.is_absolute():
        raise EnvLoadError("force-loop policy path must be absolute")
    absolute = Path(os.path.abspath(supplied))
    if forbidden_root is not None:
        blocked = Path(os.path.abspath(forbidden_root))
        if _within(absolute, blocked):
            raise EnvLoadError("force-loop policy must be outside the loop tree")
    if os.name != "posix":  # pragma: no cover - PowerShell owns native Windows policy loading
        raise EnvLoadError("native Windows force-loop policy must be loaded by PowerShell")
    parent_fd: int | None = None
    file_fd: int | None = None
    try:
        parent_fd = _open_directory_nofollow(absolute.parent)
        try:
            path_info = os.stat(absolute.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            if not required:
                return {}
            raise EnvLoadError("force-loop policy file is missing")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        file_fd = os.open(absolute.name, flags, dir_fd=parent_fd)
        before = os.fstat(file_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or int(before.st_nlink) != 1
            or int(before.st_uid) != int(os.geteuid())
            or stat.S_IMODE(before.st_mode) & 0o077
            or int(before.st_size) > MAX_POLICY_BYTES
            or (int(path_info.st_dev), int(path_info.st_ino))
            != (int(before.st_dev), int(before.st_ino))
        ):
            raise EnvLoadError("force-loop policy must be an owner-private single-link file")
        chunks: list[bytes] = []
        remaining = MAX_POLICY_BYTES + 1
        while remaining:
            chunk = os.read(file_fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(file_fd)
        stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        if len(payload) > MAX_POLICY_BYTES or any(
            getattr(before, field) != getattr(after, field) for field in stable
        ):
            raise EnvLoadError("force-loop policy changed while reading")
    except EnvLoadError:
        raise
    except OSError as exc:
        raise EnvLoadError("could not securely read force-loop policy") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if parent_fd is not None:
            os.close(parent_fd)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EnvLoadError("force-loop policy must be UTF-8") from exc
    return parse_env_text(text, source=str(absolute), allowed_keys=allowed_keys)


def merge_env_files(
    paths: list[Path | str],
    *,
    allowed_keys: frozenset[str] = POLICY_KEYS,
) -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in paths:
        values = load_env_file(path, allowed_keys=allowed_keys)
        overlap = set(merged) & set(values)
        if overlap:
            raise EnvLoadError("duplicate force-loop policy key across files")
        merged.update(values)
    return merged


def apply_to_environ(
    mapping: Mapping[str, str],
    environ: dict[str, str] | None = None,
    *,
    override: bool = True,
) -> dict[str, str]:
    target = environ if environ is not None else os.environ  # type: ignore[assignment]
    for key, value in mapping.items():
        if override or key not in target:
            target[key] = value
    return target  # type: ignore[return-value]


__all__ = [
    "EnvLoadError",
    "MAX_POLICY_BYTES",
    "POLICY_KEYS",
    "apply_to_environ",
    "load_env_file",
    "merge_env_files",
    "parse_env_text",
]
