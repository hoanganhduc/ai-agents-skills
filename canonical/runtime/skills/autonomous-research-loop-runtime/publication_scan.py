"""Bounded text admission for publication; evidence authorization is caller-owned."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DEFAULT_TEXT_LIMIT = 2 * 1024 * 1024
VERIFIED_TEXT_LIMIT = 16 * 1024 * 1024
LEDGER_LIMIT = 16_000_000
_READ_SIZE = 64 * 1024
_REJECTION = "publication text scan rejected artifact"
_SECRET_PATTERN = re.compile(
    rb"(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|"
    rb"github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{25,}|"
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    rb"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.|"
    rb"(?i:(?:api_key|access_token|refresh_token|password)[\"']?\s*[:=]\s*"
    rb"[\"'][A-Za-z0-9_./+\-=]{16,}[\"']))"
)


class PublicationScanError(RuntimeError):
    """A publication input failed admission without exposing its contents."""


@contextmanager
def _open_regular(path: Path) -> Iterator[int]:
    """Walk every component without following links, including parent links."""
    directory = os.open(path.anchor or ".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    try:
        parts = path.parts[1:] if path.anchor else path.parts
        if not parts:
            raise ValueError
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError
        yield descriptor
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def scan_public_text(
    path: str | os.PathLike[str],
    *,
    expected_sha256: str | None = None,
    ledger: bool = False,
    secret_values: Iterable[bytes] = (),
) -> bytes:
    """Return checked UTF-8 bytes from a stable regular file on POSIX.

    Ordinary text is limited to 2 MiB. A caller-authorized SHA-256 allows
    16 MiB, and must match even if the file is small. The caller must establish
    the digest's trust; a match is not proof verification. Ledgers keep their
    separate 16,000,000-byte limit and require a JSON object per nonempty line.
    Supplied secret values are nonempty bytes. Paths, content, and secrets
    never appear in rejection messages. Returned bytes are a checked snapshot;
    the caller owns any later publication and its protection against changes.
    """
    try:
        if os.name != "posix":
            raise ValueError
        if expected_sha256 is not None and (
            not isinstance(expected_sha256, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) is None
        ):
            raise ValueError
        secrets = tuple(secret_values)
        if any(not isinstance(value, bytes) or not value for value in secrets):
            raise ValueError
        limit = LEDGER_LIMIT if ledger else (
            VERIFIED_TEXT_LIMIT if expected_sha256 is not None else DEFAULT_TEXT_LIMIT
        )
        path = Path(path)
        with _open_regular(path) as descriptor:
            before = os.fstat(descriptor)
            if before.st_size > limit:
                raise ValueError
            chunks = []
            length = 0
            while True:
                chunk = os.read(descriptor, min(_READ_SIZE, limit + 1 - length))
                if not chunk:
                    break
                chunks.append(chunk)
                length += len(chunk)
                if length > limit:
                    raise ValueError
            data = b"".join(chunks)
            if length != before.st_size or _identity(os.fstat(descriptor)) != _identity(before):
                raise ValueError
            if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256.lower():
                raise ValueError
            data.decode("utf-8")
            if b"\0" in data or _SECRET_PATTERN.search(data) or any(value in data for value in secrets):
                raise ValueError
            if ledger:
                for line in data.splitlines():
                    if line.strip() and not isinstance(json.loads(line), dict):
                        raise ValueError
            # Reopen the path as well: a rename can leave the first descriptor
            # intact while replacing a directory entry or one of its parents.
            with _open_regular(path) as current:
                if _identity(os.fstat(current)) != _identity(before):
                    raise ValueError
            if _identity(os.fstat(descriptor)) != _identity(before):
                raise ValueError
            return data
    except (OSError, ValueError, TypeError, RecursionError):
        raise PublicationScanError(_REJECTION) from None
