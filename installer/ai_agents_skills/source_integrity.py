"""Descriptor-bound source attestation for installer apply operations.

Planning records a digest, but apply must bind that digest to the exact file
descriptor whose bytes are consumed.  This module deliberately avoids
``Path.resolve`` and ordinary reopen-by-name flows so an attacker cannot swap a
symlink or source file between approval and use.
"""
from __future__ import annotations

import hashlib
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


MAX_SOURCE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class AttestedSource:
    path: Path
    fd: int
    data: bytes
    sha256: str
    device: int
    inode: int

    def matches_path(self, path: Path) -> bool:
        """Return true only when *path* still resolves to this held inode."""
        try:
            current = os.stat(path, follow_symlinks=True)
        except OSError:
            return False
        return int(current.st_dev) == self.device and int(current.st_ino) == self.inode


def _absolute_components(path: Path) -> tuple[Path, tuple[str, ...]]:
    absolute = path if path.is_absolute() else Path.cwd() / path
    absolute = Path(os.path.abspath(os.fspath(absolute)))
    anchor = Path(absolute.anchor)
    components = absolute.parts[1:] if absolute.anchor else absolute.parts
    if not absolute.anchor or any(part in {"", ".", ".."} for part in components):
        raise ValueError(f"source path is not an absolute normalized path: {path}")
    return anchor, tuple(components)


def _open_nofollow(path: Path) -> int:
    """Open a regular file through a no-follow descriptor walk on POSIX."""
    if os.name != "posix":
        # Windows apply paths are separately guarded by the Windows security
        # layer.  Keep a stable handle and reject the obvious link/type cases.
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ValueError(f"canonical source must be a regular non-link file: {path}")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            os.close(fd)
            raise ValueError(f"canonical source changed while it was opened: {path}")
        return fd

    anchor, components = _absolute_components(path)
    if not components:
        raise ValueError(f"canonical source is not a file: {path}")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    parent_fd = os.open(anchor, directory_flags)
    try:
        for component in components[:-1]:
            child_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = child_fd
        return os.open(components[-1], file_flags, dir_fd=parent_fd)
    except OSError as exc:
        raise ValueError(f"canonical source has an unsafe or missing path component: {path}") from exc
    finally:
        os.close(parent_fd)


def _read_bounded_stable(fd: int, path: Path, max_bytes: int) -> tuple[bytes, os.stat_result]:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or int(before.st_nlink) != 1:
        raise ValueError(f"canonical source must be a single-link regular file: {path}")
    if int(before.st_size) < 0 or int(before.st_size) > max_bytes:
        raise ValueError(f"canonical source exceeds the {max_bytes}-byte limit: {path}")
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(1024 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"canonical source exceeds the {max_bytes}-byte limit: {path}")
    after = os.fstat(fd)
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
    if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
        raise ValueError(f"canonical source changed while it was read: {path}")
    data = b"".join(chunks)
    if len(data) != int(after.st_size):
        raise ValueError(f"canonical source size changed while it was read: {path}")
    return data, after


@contextmanager
def open_attested_source(
    path: Path,
    expected_sha256: str | None,
    *,
    max_bytes: int = MAX_SOURCE_BYTES,
) -> Iterator[AttestedSource]:
    """Yield bytes from one held source descriptor after digest validation."""
    fd = _open_nofollow(path)
    try:
        data, info = _read_bounded_stable(fd, path, max_bytes)
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if not isinstance(expected_sha256, str) or digest != expected_sha256:
            raise ValueError(
                "canonical source changed after planning: "
                f"{path} (approved {expected_sha256}, found {digest})"
            )
        yield AttestedSource(
            path=path,
            fd=fd,
            data=data,
            sha256=digest,
            device=int(info.st_dev),
            inode=int(info.st_ino),
        )
    finally:
        os.close(fd)
