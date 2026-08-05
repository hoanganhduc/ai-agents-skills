"""File-backed reservation ledger for remote-compute budgets (GitHub Actions minutes,
Modal dollars). The ledger is the *live* gate: reservations are written BEFORE dispatch
and reconciled to actuals on completion, so concurrent submits can never collectively
exceed a budget even while an external billing API lags (see the experiment-runner plan).

Generic over the unit: GHA reserves in "minutes", Modal in "usd".
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .windows_acl import private_path_guard

try:
    import fcntl  # POSIX
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


MAX_LEDGER_BYTES = 16 * 1024 * 1024
BACKEND_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}\Z")


def _ledger_path(state_root: Path, backend: str) -> Path:
    backend_name = str(backend)
    if not BACKEND_RE.fullmatch(backend_name):
        raise ValueError(f"invalid reservation backend {backend_name!r}")
    return Path(state_root) / f"{backend_name}-reservations.jsonl"


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


def _private_regular(info: os.stat_result) -> bool:
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


def _owned_single_regular(info: os.stat_result) -> bool:
    if (
        not stat.S_ISREG(info.st_mode)
        or int(getattr(info, "st_nlink", 1)) != 1
        or _is_reparse_point(info)
    ):
        return False
    return os.name != "posix" or int(info.st_uid) == int(os.geteuid())


def _windows_state_directory(state_root: Path, *, create: bool) -> Path:
    absolute = Path(os.path.abspath(Path(state_root).expanduser()))
    current = Path(absolute.anchor)
    if not current:
        raise OSError("reservation state root is not absolute")
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
            raise OSError("reservation state root contains an unsafe component")
        current = candidate
    with private_path_guard(absolute, directory=True):
        pass
    return absolute


@contextmanager
def _state_directory(
    state_root: Path,
    *,
    create: bool,
) -> Iterator[tuple[int | None, Path]]:
    """Open the state root through no-follow directory descriptors."""

    absolute = Path(os.path.abspath(Path(state_root).expanduser()))
    if os.name != "posix":  # pragma: no cover - native Windows CI exercises callers
        yield None, _windows_state_directory(absolute, create=create)
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
                    # Concurrent reservations may initialize the same
                    # private state root. The constrained re-open below is
                    # the authority for accepting the resulting component.
                    pass
                next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        directory_info = os.fstat(descriptor)
        if not stat.S_ISDIR(directory_info.st_mode):
            raise OSError("reservation state root is not a directory")
        if int(directory_info.st_uid) != int(os.geteuid()):
            raise OSError("reservation state root is not current-user owned")
        if stat.S_IMODE(directory_info.st_mode) & 0o077:
            if not create:
                raise OSError("reservation state root is not owner-private")
            os.fchmod(descriptor, 0o700)
            directory_info = os.fstat(descriptor)
            if stat.S_IMODE(directory_info.st_mode) & 0o077:
                raise OSError("reservation state root could not be made owner-private")
        yield descriptor, absolute
    finally:
        os.close(descriptor)


def _stat_entry(directory_fd: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)


def _secure_read_bytes(
    directory_fd: int,
    name: str,
    *,
    missing_ok: bool,
    repair_permissions: bool = False,
) -> bytes | None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    try:
        before = os.fstat(descriptor)
        linked_before = _stat_entry(directory_fd, name)
        if (
            os.name == "posix"
            and _owned_single_regular(before)
            and _owned_single_regular(linked_before)
            and (before.st_dev, before.st_ino)
            == (linked_before.st_dev, linked_before.st_ino)
            and repair_permissions
            and (
                stat.S_IMODE(before.st_mode) & 0o077
                or stat.S_IMODE(linked_before.st_mode) & 0o077
            )
        ):
            os.fchmod(descriptor, 0o600)
            before = os.fstat(descriptor)
            linked_before = _stat_entry(directory_fd, name)
        if (
            not _private_regular(before)
            or not _private_regular(linked_before)
            or (before.st_dev, before.st_ino)
            != (linked_before.st_dev, linked_before.st_ino)
            or int(before.st_size) > MAX_LEDGER_BYTES
        ):
            raise OSError("reservation ledger is not a bounded owner-private single-link file")
        chunks: list[bytes] = []
        remaining = MAX_LEDGER_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        linked_after = _stat_entry(directory_fd, name)
        if len(payload) > MAX_LEDGER_BYTES:
            raise OSError("reservation ledger is oversized")
        if (
            _stable_stat(before) != _stable_stat(after)
            or not _private_regular(linked_after)
            or (after.st_dev, after.st_ino)
            != (linked_after.st_dev, linked_after.st_ino)
        ):
            raise OSError("reservation ledger changed while reading")
        return payload
    finally:
        os.close(descriptor)


def _secure_read_path(
    path: Path,
    *,
    missing_ok: bool,
) -> bytes | None:
    """Native-Windows bounded read with reparse and identity checks."""

    try:
        with private_path_guard(path, directory=False):
            return _secure_read_path_guarded(path, missing_ok=missing_ok)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise


def _secure_read_path_guarded(
    path: Path,
    *,
    missing_ok: bool,
) -> bytes | None:

    try:
        linked_initial = os.lstat(path)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
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
            not _private_regular(linked_initial)
            or not _private_regular(before)
            or not _private_regular(linked_before)
            or (before.st_dev, before.st_ino)
            != (linked_before.st_dev, linked_before.st_ino)
            or int(before.st_size) > MAX_LEDGER_BYTES
        ):
            raise OSError("reservation ledger is not a bounded single-link file")
        chunks: list[bytes] = []
        remaining = MAX_LEDGER_BYTES + 1
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
            len(payload) > MAX_LEDGER_BYTES
            or _stable_stat(before) != _stable_stat(after)
            or not _private_regular(linked_after)
            or (after.st_dev, after.st_ino)
            != (linked_after.st_dev, linked_after.st_ino)
        ):
            raise OSError("reservation ledger changed while reading")
        return payload
    finally:
        os.close(descriptor)


def _read(
    path: Path,
    *,
    directory_fd: int | Path | None = None,
) -> list[dict[str, Any]]:
    if isinstance(directory_fd, Path):  # pragma: no cover - native Windows
        payload = _secure_read_path(directory_fd / path.name, missing_ok=True)
    elif directory_fd is not None:
        payload = _secure_read_bytes(
            directory_fd,
            path.name,
            missing_ok=True,
            repair_permissions=True,
        )
    else:
        try:
            with _state_directory(path.parent, create=False) as (opened, root):
                if opened is None:  # pragma: no cover - native Windows
                    payload = _secure_read_path(root / path.name, missing_ok=True)
                else:
                    payload = _secure_read_bytes(opened, path.name, missing_ok=True)
        except FileNotFoundError:
            return []
    if payload is None:
        return []
    text = payload.decode("utf-8")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("reservation ledger rows must be JSON objects")
    return rows


def _write_at(directory_fd: int, name: str, rows: list[dict[str, Any]]) -> None:
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")
    if len(payload) > MAX_LEDGER_BYTES:
        raise ValueError("reservation ledger is oversized")

    try:
        existing = _stat_entry(directory_fd, name)
    except FileNotFoundError:
        existing = None
    if existing is not None and not _private_regular(existing):
        raise OSError("refusing to replace an unsafe reservation ledger")

    temporary_name = f".{name}.{secrets.token_hex(16)}.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )
    descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
    replaced = False
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        written = os.fstat(descriptor)
        if not _private_regular(written) or int(written.st_size) != len(payload):
            raise OSError("reservation ledger temporary file is unsafe")
        os.close(descriptor)
        descriptor = -1

        try:
            current = _stat_entry(directory_fd, name)
        except FileNotFoundError:
            current = None
        if existing is None:
            if current is not None:
                raise OSError("reservation ledger appeared during atomic write")
        elif (
            current is None
            or (current.st_dev, current.st_ino) != (existing.st_dev, existing.st_ino)
            or not _private_regular(current)
        ):
            raise OSError("reservation ledger changed before atomic replace")
        os.replace(
            temporary_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        replaced = True
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not replaced:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass


def _serialize_rows(rows: list[dict[str, Any]]) -> bytes:
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")
    if len(payload) > MAX_LEDGER_BYTES:
        raise ValueError("reservation ledger is oversized")
    return payload


def _write_path(path: Path, rows: list[dict[str, Any]]) -> None:
    """Native-Windows same-directory atomic ledger replacement."""

    with private_path_guard(path.parent, directory=True):
        _write_path_guarded(path, rows)


def _write_path_guarded(path: Path, rows: list[dict[str, Any]]) -> None:

    payload = _serialize_rows(rows)
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        existing = None
    if existing is not None and not _private_regular(existing):
        raise OSError("refusing to replace an unsafe reservation ledger")
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
        if not _private_regular(written) or int(written.st_size) != len(payload):
            raise OSError("reservation ledger temporary file is unsafe")
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
                raise OSError("reservation ledger appeared during atomic write")
        else:
            if current is None or not _private_regular(current):
                raise OSError("reservation ledger changed before atomic replace")
            with private_path_guard(path, directory=False) as guarded_current:
                current_identity = (
                    guarded_current["volume"],
                    guarded_current["index"],
                )
            if current_identity != existing_identity:
                raise OSError("reservation ledger identity changed before atomic replace")
        os.replace(temporary, path)
        replaced = True
        with private_path_guard(path, directory=False) as guarded_installed:
            installed_identity = (
                guarded_installed["volume"],
                guarded_installed["index"],
            )
        if installed_identity != temporary_identity:
            raise OSError("reservation replacement installed an unexpected file identity")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not replaced:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _write(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    directory_fd: int | Path | None = None,
) -> None:
    if isinstance(directory_fd, Path):  # pragma: no cover - native Windows
        _write_path(directory_fd / path.name, rows)
        return
    if directory_fd is not None:
        _write_at(directory_fd, path.name, rows)
        return
    with _state_directory(path.parent, create=True) as (opened, root):
        if opened is None:  # pragma: no cover - native Windows
            _write_path(root / path.name, rows)
        else:
            _write_at(opened, path.name, rows)


@contextmanager
def _windows_lock(path: Path) -> Iterator[Path]:
    if msvcrt is None:  # pragma: no cover - defensive native-Windows gate
        raise OSError("native Windows reservation locking is unavailable")
    root = _windows_state_directory(path.parent, create=True)
    lock_path = root / path.with_suffix(".lock").name
    try:
        existing = os.lstat(lock_path)
    except FileNotFoundError:
        existing = None
    if existing is not None and not _private_regular(existing):
        raise OSError("reservation lock is not a single-link file")
    if existing is not None:
        with private_path_guard(lock_path, directory=False):
            pass
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
    )
    descriptor = os.open(lock_path, flags, 0o600)
    locked = False
    try:
        opened = os.fstat(descriptor)
        linked = os.lstat(lock_path)
        if (
            not _private_regular(opened)
            or not _private_regular(linked)
            or (opened.st_dev, opened.st_ino) != (linked.st_dev, linked.st_ino)
            or (
                existing is not None
                and (opened.st_dev, opened.st_ino)
                != (existing.st_dev, existing.st_ino)
            )
        ):
            raise OSError("reservation lock changed while opening")
        if int(opened.st_size) < 1:
            if os.write(descriptor, b"\0") != 1:
                raise OSError("could not initialize reservation lock")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        locked = True
        linked_after = os.lstat(lock_path)
        if (
            not _private_regular(linked_after)
            or (opened.st_dev, opened.st_ino)
            != (linked_after.st_dev, linked_after.st_ino)
        ):
            raise OSError("reservation lock changed while acquiring")
        yield root
    finally:
        if locked:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        os.close(descriptor)
        with private_path_guard(lock_path, directory=False):
            pass


@contextmanager
def _lock(path: Path) -> Iterator[int | Path]:
    if os.name != "posix":  # pragma: no cover - native Windows
        with _windows_lock(path) as root:
            yield root
        return
    lock_name = path.with_suffix(".lock").name
    state_context = _state_directory(path.parent, create=True)
    directory_fd, _root = state_context.__enter__()
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(lock_name, flags, 0o600, dir_fd=directory_fd)
        opened = os.fstat(descriptor)
        linked = _stat_entry(directory_fd, lock_name)
        if (
            os.name == "posix"
            and _owned_single_regular(opened)
            and _owned_single_regular(linked)
            and (opened.st_dev, opened.st_ino) == (linked.st_dev, linked.st_ino)
            and (
                stat.S_IMODE(opened.st_mode) & 0o077
                or stat.S_IMODE(linked.st_mode) & 0o077
            )
        ):
            os.fchmod(descriptor, 0o600)
            opened = os.fstat(descriptor)
            linked = _stat_entry(directory_fd, lock_name)
        if (
            not _private_regular(opened)
            or not _private_regular(linked)
            or (opened.st_dev, opened.st_ino) != (linked.st_dev, linked.st_ino)
        ):
            raise OSError("reservation lock is not an owner-private single-link file")
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked_path = _stat_entry(directory_fd, lock_name)
        if (opened.st_dev, opened.st_ino) != (locked_path.st_dev, locked_path.st_ino):
            raise OSError("reservation lock changed while acquiring")
        yield directory_fd
    finally:
        if descriptor >= 0:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        state_context.__exit__(None, None, None)


def _current_cycle() -> str:
    """UTC monthly cycle key used for provider usage that can lag locally."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _committed_amount(row: dict[str, Any], cycle: str) -> float:
    if row.get("state") == "reserved":
        return float(row["amount"])
    if row.get("state") == "accrued" and row.get("cycle") == cycle:
        return float(row.get("actual", row.get("amount", 0.0)))
    return 0.0


def outstanding(state_root: Path, backend: str) -> float:
    """Sum active reservations plus locally verified usage in the current UTC cycle.

    Accrued actuals remain committed until the cycle rolls over. This is conservative when
    the provider usage API has already caught up, but prevents reporting lag from reopening
    headroom that a just-completed job already consumed.
    """
    path = _ledger_path(state_root, backend)
    cycle = _current_cycle()
    return sum(_committed_amount(row, cycle) for row in _read(path))


def reserved_job_ids(state_root: Path, backend: str) -> set[str]:
    """Set of job ids with an outstanding (reserved, not reconciled) reservation.

    Callers that need destructive authority must use ``authoritative_reserved_job_ids`` so a
    missing or malformed ledger is not confused with an authoritative empty set."""
    path = _ledger_path(state_root, backend)
    return {str(r["job_id"]) for r in _read(path)
            if r.get("state") == "reserved" and r.get("job_id") is not None}


def authoritative_reserved_job_ids(state_root: Path, backend: str) -> set[str] | None:
    """Return active job ids only when an existing ledger is safe to trust.

    A missing, linked, non-regular, unreadable, or malformed ledger is not evidence that
    there are zero active jobs. Destructive orphan cleanup must treat those states as
    unavailable authority and keep relying on independent TTL / powered-off safeguards.
    """
    path = _ledger_path(state_root, backend)
    try:
        with _state_directory(path.parent, create=False) as (directory_fd, root):
            if directory_fd is None:  # pragma: no cover - native Windows
                payload = _secure_read_path(root / path.name, missing_ok=False)
            else:
                payload = _secure_read_bytes(directory_fd, path.name, missing_ok=False)
        assert payload is not None
        text = payload.decode("utf-8")

        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        active: set[str] = set()
        valid_states = {"reserved", "reconciled", "accrued"}
        for row in rows:
            if not isinstance(row, dict) or row.get("state") not in valid_states:
                return None
            job_id = row.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                return None
            if row["state"] == "reserved":
                active.add(job_id)
        return active
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def reserve(state_root: Path, backend: str, job_id: str, amount: float, unit: str) -> None:
    path = _ledger_path(state_root, backend)
    with _lock(path) as directory_fd:
        rows = _read(path, directory_fd=directory_fd)
        rows.append(
            {"job_id": job_id, "amount": float(amount), "unit": unit,
             "state": "reserved", "reserved_at": time.time(),
             "cycle": _current_cycle()}
        )
        _write(path, rows, directory_fd=directory_fd)


def reconcile(state_root: Path, backend: str, job_id: str, actual: float | None = None) -> None:
    """Reconcile one matching reservation.

    Reservation identifiers are expected to be attempt-unique. Reconciling at most one row
    is a defensive backstop for legacy callers that reused a job id: one completed attempt
    must never release another attempt's still-active reservation.
    """
    path = _ledger_path(state_root, backend)
    with _lock(path) as directory_fd:
        rows = _read(path, directory_fd=directory_fd)
        for row in rows:
            if row.get("job_id") == job_id and row.get("state") == "reserved":
                row["reconciled_at"] = time.time()
                if actual is not None:
                    row["state"] = "accrued"
                    row["actual"] = float(actual)
                    row["cycle"] = _current_cycle()
                else:
                    row["state"] = "reconciled"
                break
        _write(path, rows, directory_fd=directory_fd)


def check_and_reserve(
    *, state_root: Path, backend: str, job_id: str, worst_case: float,
    available: float, unit: str,
) -> dict[str, Any]:
    """Atomic gate: refuse if worst_case + outstanding would exceed `available`; else
    reserve worst_case. Returns {"ok": bool, "reserved": float, "outstanding": float,
    "available": float, "reason": str|None}."""
    path = _ledger_path(state_root, backend)
    with _lock(path) as directory_fd:
        rows = _read(path, directory_fd=directory_fd)
        cycle = _current_cycle()
        out = sum(_committed_amount(row, cycle) for row in rows)
        if worst_case + out > available:
            return {"ok": False, "reserved": 0.0, "outstanding": out, "available": available,
                    "reason": f"worst_case {worst_case} + outstanding {out} > available {available} {unit}"}
        rows.append(
            {"job_id": job_id, "amount": float(worst_case), "unit": unit,
             "state": "reserved", "reserved_at": time.time(), "cycle": cycle}
        )
        _write(path, rows, directory_fd=directory_fd)
        return {"ok": True, "reserved": float(worst_case), "outstanding": out,
                "available": available, "reason": None}
