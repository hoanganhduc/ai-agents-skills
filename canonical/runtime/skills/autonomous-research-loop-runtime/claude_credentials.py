"""Keep native Claude OAuth refreshes in a campaign-private checkpoint."""

from __future__ import annotations

import json
import math
import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # Native Windows has no supported session lock.
    fcntl = None


MAX_CREDENTIAL_BYTES = 64 * 1024
CREDENTIAL_NAME = ".credentials.json"
LOCK_NAME = ".credential-session.lock"


class CredentialSessionError(RuntimeError):
    """A credential session failed; messages never include credential data."""


def _absolute_path(value: Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise CredentialSessionError("credential paths must be absolute without traversal")
    return path


def _open_private_directory(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = None
    try:
        descriptor = os.open(path.anchor, flags)
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise CredentialSessionError("credential directory must be owner-only mode 0700")
        return descriptor
    except (OSError, CredentialSessionError) as exc:
        if descriptor is not None:
            os.close(descriptor)
        if isinstance(exc, CredentialSessionError):
            raise
        raise CredentialSessionError("credential directory could not be opened safely") from None


def _check_file(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise CredentialSessionError("credential file must be an owner-only single-link regular file")


def _read_credentials(directory: int, name: str, *, optional: bool = False) -> dict[str, Any] | None:
    descriptor = None
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        try:
            descriptor = os.open(name, flags, dir_fd=directory)
        except FileNotFoundError:
            if optional:
                return None
            raise
        before = os.fstat(descriptor)
        _check_file(before)
        if before.st_size > MAX_CREDENTIAL_BYTES:
            raise CredentialSessionError("credential document exceeds size limit")
        data = bytearray()
        while len(data) <= MAX_CREDENTIAL_BYTES:
            chunk = os.read(descriptor, MAX_CREDENTIAL_BYTES + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(data) > MAX_CREDENTIAL_BYTES or any(
            getattr(before, field) != getattr(after, field) for field in stable
        ):
            raise CredentialSessionError("credential document changed or exceeded size limit")
        value = json.loads(data.decode("utf-8"))
        oauth = value.get("claudeAiOauth") if isinstance(value, dict) else None
        if not isinstance(oauth, dict) or not isinstance(oauth.get("accessToken"), str) or not oauth["accessToken"].strip():
            raise CredentialSessionError("credential document has no valid OAuth access token")
        if "refreshToken" in oauth and not isinstance(oauth["refreshToken"], str):
            raise CredentialSessionError("credential document has invalid OAuth refresh token")
        if "expiresAt" in oauth and (
            type(oauth["expiresAt"]) not in (int, float)
            or not math.isfinite(oauth["expiresAt"])
        ):
            raise CredentialSessionError("credential document has invalid OAuth expiry")
        payload = {"claudeAiOauth": oauth}
        # Reject non-JSON values and invalid Unicode in native extension fields.
        json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return payload
    except (OSError, ValueError, OverflowError, RecursionError):
        raise CredentialSessionError("credential document could not be read safely") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _check_destination(directory: int, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    _check_file(info)


def _write_credentials(directory: int, payload: dict[str, Any]) -> None:
    temporary_name = ""
    descriptor = None
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_CREDENTIAL_BYTES:
            raise CredentialSessionError("credential document exceeds size limit")
        _check_destination(directory, CREDENTIAL_NAME)
        candidate = ".credentials-" + secrets.token_hex(16) + ".tmp"
        descriptor = os.open(
            candidate,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory,
        )
        temporary_name = candidate
        os.fchmod(descriptor, 0o600)
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise CredentialSessionError("credential checkpoint write was incomplete")
            remaining = remaining[written:]
        os.fsync(descriptor)
        _check_destination(directory, CREDENTIAL_NAME)
        os.replace(temporary_name, CREDENTIAL_NAME, src_dir_fd=directory, dst_dir_fd=directory)
        temporary_name = ""
        os.fsync(directory)
    except (OSError, ValueError, RecursionError):
        raise CredentialSessionError("credential checkpoint could not be written safely") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=directory)
            except FileNotFoundError:
                pass


def _acquire_lock(directory: int) -> int:
    descriptor = None
    try:
        descriptor = os.open(
            LOCK_NAME,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            0o600,
            dir_fd=directory,
        )
        _check_file(os.fstat(descriptor))
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CredentialSessionError("credential session already active") from None
        return descriptor
    except (OSError, CredentialSessionError) as exc:
        if descriptor is not None:
            os.close(descriptor)
        if isinstance(exc, CredentialSessionError):
            raise
        raise CredentialSessionError("credential session lock could not be acquired safely") from None


@contextmanager
def credential_session(
    source: Path,
    checkpoint_directory: Path,
    role_directory: Path,
) -> Iterator[Path]:
    """Prepare role credentials and retain native OAuth refreshes on context exit.

    All three directories must already exist with owner-only mode 0700. Paths
    must be absolute and contain no symlink components. Ancestor directories
    and same-user processes belong to the trusted host boundary. The host must keep the
    checkpoint directory outside the model's filesystem view. This POSIX-only
    helper locks the checkpoint through selection, the caller's native call,
    and promotion. It never writes the source or launches a child. The caller
    owns role-directory cleanup and must wait for its child before exiting.
    """
    if os.name != "posix" or fcntl is None or not hasattr(os, "O_NOFOLLOW"):
        raise CredentialSessionError("credential sessions require POSIX no-follow opens and flock")
    source = _absolute_path(source)
    checkpoint_directory = _absolute_path(checkpoint_directory)
    role_directory = _absolute_path(role_directory)
    with ExitStack() as stack:
        directories = []
        for path in (source.parent, checkpoint_directory, role_directory):
            descriptor = _open_private_directory(path)
            stack.callback(os.close, descriptor)
            directories.append(descriptor)
        identities = {(os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in directories}
        if len(identities) != 3:
            raise CredentialSessionError("source, checkpoint, and role directories must be distinct")
        source_fd, checkpoint_fd, role_fd = directories
        lock = _acquire_lock(checkpoint_fd)
        stack.callback(os.close, lock)
        payload = _read_credentials(checkpoint_fd, CREDENTIAL_NAME, optional=True)
        if payload is None:
            payload = _read_credentials(source_fd, source.name)
        _write_credentials(role_fd, payload)
        try:
            yield role_directory / CREDENTIAL_NAME
        except BaseException as primary:
            try:
                _write_credentials(checkpoint_fd, _read_credentials(role_fd, CREDENTIAL_NAME))
            except Exception:
                note = "credential checkpoint promotion failed; credential details redacted"
                if hasattr(primary, "add_note"):
                    primary.add_note(note)
                else:
                    primary.__notes__ = [*getattr(primary, "__notes__", []), note]
            raise
        else:
            _write_credentials(checkpoint_fd, _read_credentials(role_fd, CREDENTIAL_NAME))
