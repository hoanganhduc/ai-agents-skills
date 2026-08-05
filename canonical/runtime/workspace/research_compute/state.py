from __future__ import annotations

import json
import os
import re
import secrets
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .windows_acl import private_path_guard

try:
    import fcntl
except ImportError:  # pragma: no cover - native Windows
    fcntl = None  # type: ignore[assignment]


MAX_STATE_FILE_BYTES = 16 * 1024 * 1024
JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
ATTEMPT_ID_RE = re.compile(r"attempt-[0-9]{3,9}(?:-[a-f0-9]{8})?\Z")


def validate_job_id(value: Any) -> str:
    if not isinstance(value, str) or not JOB_ID_RE.fullmatch(value):
        raise ValueError(
            "job_id must be a 1-128 character slug using letters, digits, '.', '_', or '-' "
            "and must start alphanumeric"
        )
    return value


def validate_attempt_id(value: Any) -> str:
    if not isinstance(value, str) or not ATTEMPT_ID_RE.fullmatch(value):
        raise ValueError(
            "attempt_id must match attempt-NNN or attempt-NNN-<8 lowercase hex>"
        )
    return value


def _stable_stat(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
    )


def _is_reparse_point(info: os.stat_result) -> bool:
    return bool(
        int(getattr(info, "st_file_attributes", 0))
        & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    )


def _owned_regular(info: os.stat_result) -> bool:
    if (
        not stat.S_ISREG(info.st_mode)
        or int(getattr(info, "st_nlink", 1)) != 1
        or _is_reparse_point(info)
    ):
        return False
    if os.name == "posix":
        return (
            int(info.st_uid) == int(os.geteuid())
            and not stat.S_IMODE(info.st_mode) & 0o077
        )
    return True


def _windows_directory(path: Path, *, create: bool) -> Path:
    """Validate each native-Windows component without following reparse points.

    Python's ``dir_fd`` operations are not available on native Windows. This
    component walk, paired with descriptor/path identity checks for every file
    operation below, provides the portable fail-closed branch rather than
    silently falling back to ``Path.read_text``/``write_text``.
    """

    absolute = Path(os.path.abspath(Path(path).expanduser()))
    current = Path(absolute.anchor)
    if not current:
        raise OSError(f"state directory is not absolute: {absolute}")
    for component in absolute.parts[1:]:
        candidate = current / component
        try:
            info = os.lstat(candidate)
        except FileNotFoundError:
            if not create:
                raise
            try:
                os.mkdir(candidate, mode=0o700)
            except FileExistsError:
                pass
            info = os.lstat(candidate)
        if _is_reparse_point(info) or not stat.S_ISDIR(info.st_mode):
            raise OSError(f"unsafe state directory component: {candidate}")
        current = candidate
    with private_path_guard(absolute, directory=True):
        pass
    return absolute


@contextmanager
def _directory(path: Path, *, create: bool) -> Iterator[tuple[int | None, Path]]:
    absolute = Path(os.path.abspath(Path(path).expanduser()))
    if os.name != "posix":  # pragma: no cover - native Windows
        yield None, _windows_directory(absolute, create=create)
        return

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(absolute.anchor or os.sep, flags)
    try:
        for component in absolute.parts[1:]:
            try:
                next_descriptor = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    # Another submitter may have created the shared state
                    # component after our failed open. Re-open it with the
                    # same no-follow constraints below.
                    pass
                next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise OSError(f"state path is not a directory: {absolute}")
        if int(info.st_uid) != int(os.geteuid()):
            raise OSError(f"state directory is not current-user owned: {absolute}")
        if stat.S_IMODE(info.st_mode) & 0o077:
            if not create:
                raise OSError(f"state directory is not owner-private: {absolute}")
            os.fchmod(descriptor, 0o700)
        yield descriptor, absolute
    finally:
        os.close(descriptor)


def ensure_root(path: Path) -> Path:
    with _directory(path, create=True) as (_descriptor, absolute):
        return absolute


def _contained_child(root: Path, *components: str) -> Path:
    absolute_root = Path(os.path.abspath(Path(root).expanduser()))
    candidate = absolute_root.joinpath(*components)
    try:
        candidate.relative_to(absolute_root)
    except ValueError as exc:  # defensive: validated components should make this unreachable
        raise ValueError("state path escapes its root") from exc
    return candidate


def job_dir(state_root: Path, job_id: str) -> Path:
    return _contained_child(Path(state_root), "jobs", validate_job_id(job_id))


def attempt_dir(state_root: Path, job_id: str, attempt_id: str) -> Path:
    return _contained_child(
        job_dir(state_root, job_id),
        "attempts",
        validate_attempt_id(attempt_id),
    )


def _write_atomic(path: Path, payload: bytes) -> None:
    if len(payload) > MAX_STATE_FILE_BYTES:
        raise ValueError("state file is oversized")
    with _directory(path.parent, create=True) as (directory_fd, root):
        if directory_fd is None:  # pragma: no cover - native Windows
            _write_atomic_windows(root / path.name, payload)
            return
        try:
            existing = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and not _owned_regular(existing):
            raise OSError(f"refusing to replace unsafe state file: {path}")
        temporary = f".{path.name}.{secrets.token_hex(16)}.tmp"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0)
        )
        descriptor = os.open(temporary, flags, 0o600, dir_fd=directory_fd)
        replaced = False
        try:
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
            written = os.fstat(descriptor)
            if not _owned_regular(written) or int(written.st_size) != len(payload):
                raise OSError("state temporary file is unsafe")
            os.close(descriptor)
            descriptor = -1
            try:
                current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                current = None
            if existing is None:
                if current is not None:
                    raise OSError("state file appeared during atomic write")
            elif (
                current is None
                or not _owned_regular(current)
                or (current.st_dev, current.st_ino) != (existing.st_dev, existing.st_ino)
            ):
                raise OSError("state file changed before atomic replace")
            os.replace(
                temporary,
                path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            replaced = True
            if os.name == "posix":
                os.fsync(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if not replaced:
                try:
                    os.unlink(temporary, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass


def _write_atomic_windows(path: Path, payload: bytes) -> None:
    # Hold the full parent/ancestor handle chain throughout creation and
    # replacement.  File guards are reacquired immediately before replace
    # because Windows replacement requires the old file's deny-delete handle
    # to be closed; exact file IDs still detect any intervening substitution.
    with private_path_guard(path.parent, directory=True):
        _write_atomic_windows_guarded(path, payload)


def _write_atomic_windows_guarded(path: Path, payload: bytes) -> None:
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        existing = None
    if existing is not None and not _owned_regular(existing):
        raise OSError(f"refusing to replace unsafe state file: {path}")
    existing_identity = None
    if existing is not None:
        with private_path_guard(path, directory=False) as guarded:
            existing_identity = (guarded["volume"], guarded["index"])

    temporary = path.parent / f".{path.name}.{secrets.token_hex(16)}.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
    )
    descriptor = os.open(temporary, flags, 0o600)
    replaced = False
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        written = os.fstat(descriptor)
        if not _owned_regular(written) or int(written.st_size) != len(payload):
            raise OSError("state temporary file is unsafe")
        os.close(descriptor)
        descriptor = -1

        with private_path_guard(temporary, directory=False) as guarded_temporary:
            temporary_identity = (
                guarded_temporary["volume"],
                guarded_temporary["index"],
            )

        try:
            current = os.lstat(path)
        except FileNotFoundError:
            current = None
        if existing is None:
            if current is not None:
                raise OSError("state file appeared during atomic write")
        else:
            if current is None or not _owned_regular(current):
                raise OSError("state file changed before atomic replace")
            with private_path_guard(path, directory=False) as guarded_current:
                current_identity = (
                    guarded_current["volume"],
                    guarded_current["index"],
                )
            if current_identity != existing_identity:
                raise OSError("state file identity changed before atomic replace")
        os.replace(temporary, path)
        replaced = True
        with private_path_guard(path, directory=False) as guarded_installed:
            installed_identity = (
                guarded_installed["volume"],
                guarded_installed["index"],
            )
        if installed_identity != temporary_identity:
            raise OSError("state replacement installed an unexpected file identity")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not replaced:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def write_json(path: Path, data: dict[str, Any]) -> None:
    payload = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_atomic(path, payload)


def read_json(path: Path) -> dict[str, Any]:
    with _directory(path.parent, create=False) as (directory_fd, root):
        if directory_fd is None:  # pragma: no cover - native Windows
            payload = _read_windows(root / path.name)
            parsed = json.loads(payload.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError(f"state file must contain a JSON object: {path}")
            return parsed
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0)
        )
        descriptor = os.open(path.name, flags, dir_fd=directory_fd)
        try:
            before = os.fstat(descriptor)
            linked = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
            if (
                not _owned_regular(before)
                or not _owned_regular(linked)
                or (before.st_dev, before.st_ino) != (linked.st_dev, linked.st_ino)
                or int(before.st_size) > MAX_STATE_FILE_BYTES
            ):
                raise OSError(f"unsafe state file: {path}")
            chunks: list[bytes] = []
            remaining = MAX_STATE_FILE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            after = os.fstat(descriptor)
            linked_after = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
            if (
                len(payload) > MAX_STATE_FILE_BYTES
                or _stable_stat(before) != _stable_stat(after)
                or not _owned_regular(linked_after)
                or (after.st_dev, after.st_ino)
                != (linked_after.st_dev, linked_after.st_ino)
            ):
                raise OSError(f"state file changed while reading: {path}")
        finally:
            os.close(descriptor)
    parsed = json.loads(payload.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"state file must contain a JSON object: {path}")
    return parsed


def _read_windows(path: Path) -> bytes:
    with private_path_guard(path, directory=False):
        return _read_windows_guarded(path)


def _read_windows_guarded(path: Path) -> bytes:
    linked = os.lstat(path)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
    )
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        linked_before = os.lstat(path)
        if (
            not _owned_regular(linked)
            or not _owned_regular(before)
            or not _owned_regular(linked_before)
            or (before.st_dev, before.st_ino)
            != (linked_before.st_dev, linked_before.st_ino)
            or int(before.st_size) > MAX_STATE_FILE_BYTES
        ):
            raise OSError(f"unsafe state file: {path}")
        chunks: list[bytes] = []
        remaining = MAX_STATE_FILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        linked_after = os.lstat(path)
        if (
            len(payload) > MAX_STATE_FILE_BYTES
            or _stable_stat(before) != _stable_stat(after)
            or not _owned_regular(linked_after)
            or (after.st_dev, after.st_ino)
            != (linked_after.st_dev, linked_after.st_ino)
        ):
            raise OSError(f"state file changed while reading: {path}")
        return payload
    finally:
        os.close(descriptor)


def append_event(path: Path, event: dict[str, Any]) -> None:
    event = dict(event)
    event.setdefault("ts", datetime.now(timezone.utc).isoformat())
    payload = (json.dumps(event, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > MAX_STATE_FILE_BYTES:
        raise ValueError("state event is oversized")
    with _directory(path.parent, create=True) as (directory_fd, root):
        if directory_fd is None:  # pragma: no cover - native Windows
            _append_event_windows(root / path.name, payload)
            return
        flags = (
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_BINARY", 0)
        )
        descriptor = os.open(path.name, flags, 0o600, dir_fd=directory_fd)
        try:
            opened = os.fstat(descriptor)
            linked = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
            if (
                not _owned_regular(opened)
                or not _owned_regular(linked)
                or (opened.st_dev, opened.st_ino) != (linked.st_dev, linked.st_ino)
            ):
                raise OSError(f"unsafe state event file: {path}")
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            if os.write(descriptor, payload) != len(payload):
                raise OSError("short state event write")
            os.fsync(descriptor)
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _append_event_windows(path: Path, payload: bytes) -> None:
    with private_path_guard(path.parent, directory=True):
        existing_identity = None
        if path.exists() or path.is_symlink():
            with private_path_guard(path, directory=False) as guarded:
                existing_identity = (guarded["volume"], guarded["index"])
        _append_event_windows_guarded(path, payload)
        with private_path_guard(path, directory=False) as guarded_after:
            after_identity = (guarded_after["volume"], guarded_after["index"])
        if existing_identity is not None and after_identity != existing_identity:
            raise OSError("state event file identity changed while appending")


def _append_event_windows_guarded(path: Path, payload: bytes) -> None:
    try:
        before_link = os.lstat(path)
    except FileNotFoundError:
        before_link = None
    if before_link is not None and not _owned_regular(before_link):
        raise OSError(f"unsafe state event file: {path}")
    flags = (
        os.O_WRONLY
        | os.O_APPEND
        | os.O_CREAT
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        linked = os.lstat(path)
        if (
            not _owned_regular(opened)
            or not _owned_regular(linked)
            or (opened.st_dev, opened.st_ino) != (linked.st_dev, linked.st_ino)
            or (
                before_link is not None
                and (opened.st_dev, opened.st_ino)
                != (before_link.st_dev, before_link.st_ino)
            )
        ):
            raise OSError(f"unsafe state event file: {path}")
        try:
            import msvcrt  # type: ignore[import-not-found]

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        except ImportError:  # pragma: no cover - non-Windows fallback
            msvcrt = None  # type: ignore[assignment]
        try:
            os.lseek(descriptor, 0, os.SEEK_END)
            if os.write(descriptor, payload) != len(payload):
                raise OSError("short state event write")
            os.fsync(descriptor)
        finally:
            if msvcrt is not None:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(descriptor)


def next_attempt_id(
    state_root: Path,
    job_id: str,
    *,
    suffix: str | None = None,
) -> str:
    job = validate_job_id(job_id)
    if suffix is not None and not re.fullmatch(r"[a-f0-9]{8}", suffix):
        raise ValueError("attempt suffix must be 8 lowercase hexadecimal characters")
    attempts_root = ensure_root(job_dir(state_root, job) / "attempts")
    with _directory(attempts_root, create=False) as (directory_fd, root):
        for number in range(1, 1_000_000_000):
            candidate = f"attempt-{number:03d}"
            if suffix is not None:
                candidate = f"{candidate}-{suffix}"
            try:
                if directory_fd is None:  # pragma: no cover - native Windows
                    os.mkdir(root / candidate, mode=0o700)
                    created = os.lstat(root / candidate)
                    if _is_reparse_point(created) or not stat.S_ISDIR(created.st_mode):
                        raise OSError("unsafe attempt directory")
                    with private_path_guard(root / candidate, directory=True):
                        pass
                else:
                    os.mkdir(candidate, mode=0o700, dir_fd=directory_fd)
            except FileExistsError:
                continue
            return validate_attempt_id(candidate)
    raise RuntimeError("attempt id space exhausted")


def status_path(state_root: Path, job_id: str) -> Path:
    return job_dir(state_root, job_id) / "status.json"


def manifest_path(state_root: Path, job_id: str) -> Path:
    return job_dir(state_root, job_id) / "manifest.json"


def plan_path(state_root: Path, job_id: str) -> Path:
    return job_dir(state_root, job_id) / "plan.json"
