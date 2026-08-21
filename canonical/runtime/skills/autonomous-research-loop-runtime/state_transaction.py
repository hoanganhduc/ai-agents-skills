"""Recoverable, revision-checked file transactions for autonomous loop state.

The runtime stores a complete post-image for every target in a write-ahead
journal before replacing any live file.  A later caller can therefore finish
an interrupted transaction idempotently.  Callers must recover transactions
before reading a group of loop-control files; :func:`commit_transaction` and
the Goal-Focus pre-dispatch gate do this automatically.

Only Python's standard library is used.  Locks use ``flock`` on POSIX and
``msvcrt.locking`` on Windows.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Mapping, Sequence

TRANSACTION_DIRNAME = ".goal_focus_transactions"
TRANSACTION_QUARANTINE_DIRNAME = ".goal_focus_transactions_quarantine"
LOCK_FILENAME = ".goal_focus.lock"
MAX_TRANSACTION_BYTES = 64_000_000
WINDOWS_SHARING_RETRY_SECONDS = 2.0


def _is_windows() -> bool:
    """Whether the descriptor-free filesystem fallbacks apply.

    Every ``os`` call in this module that Windows cannot serve has a fallback
    beside it, and those fallbacks used to be reachable only on Windows: pinning
    ``os.name`` is not an option, because ``pathlib`` dispatches on it and
    cannot build a ``WindowsPath`` on a POSIX host.  Routing the choice through
    one predicate lets a test drive the fallbacks on either platform, which is
    how the retry below is proven.  The two ``msvcrt`` sites keep testing
    ``os.name`` directly, because a lock the platform does not implement cannot
    be simulated.
    """

    return os.name == "nt"


class TransactionError(RuntimeError):
    """Base error for the recoverable transaction layer."""


class RevisionConflict(TransactionError):
    """Raised when compare-and-swap expectations no longer match disk."""


class LockTimeout(TransactionError):
    """Raised when another process holds the loop-state lock too long."""


class InjectedCrash(TransactionError):
    """Test-only interruption raised at a named transaction checkpoint."""


class TransactionQuarantined(TransactionError):
    """Raised when a journal entry could not be finished and was moved aside."""


ITERATION_LEDGER_FILE = "iterations.jsonl"
# Rotate well below the 16 MB cap every ledger reader enforces, so a campaign
# reaches a shard boundary long before it reaches a wall it cannot cross.
ITERATION_LEDGER_ROTATE_BYTES = 8_000_000
_ITERATION_SHARD = re.compile(r"^iterations\.([0-9]+)\.jsonl$")


def iteration_shard_name(index: int) -> str:
    """Name the rotated ledger shard holding the ``index``-th rotation."""

    return f"iterations.{int(index)}.jsonl"


def iteration_ledger_paths(run_dir: str | Path) -> list[Path]:
    """List the ledger files in record order: rotated shards, then the live file.

    Every reader must span the shard set.  A reader that sees only the live
    file silently restarts iteration numbering at the first rotation, so this
    is the one place the ordering is defined.
    """

    root = Path(run_dir)
    shards: list[tuple[int, Path]] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        match = _ITERATION_SHARD.match(entry.name)
        if match is not None and entry.is_file():
            shards.append((int(match.group(1)), entry))
    ordered = [path for _index, path in sorted(shards, key=lambda item: item[0])]
    ordered.append(root / ITERATION_LEDGER_FILE)
    return ordered


def next_iteration_shard_index(run_dir: str | Path) -> int:
    """Return the next unused shard number for ``run_dir``."""

    root = Path(run_dir)
    highest = 0
    try:
        entries = list(root.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        match = _ITERATION_SHARD.match(entry.name)
        if match is not None:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _fsync_dir(path: Path) -> None:
    """Best-effort directory fsync (not supported by every platform)."""

    if _is_windows():
        return
    try:
        fd = _open_directory_nofollow(path)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _tolerate_windows_sharing(operation: Callable[[], Any]) -> Any:
    """Run one filesystem step, retrying the contention Windows reports as denial.

    Windows refuses to open, stat, replace, or unlink a file while any other
    handle still holds it, and reports that refusal as ``ERROR_ACCESS_DENIED``
    -- the code a real access-control denial also carries, so the two cannot be
    told apart at the call site.  Such a handle appears without a second writer
    of ours: the platform opens a file for scanning as soon as it is created or
    renamed, so holding the loop lock is no defence.  Retrying for a bounded
    moment turns the transient case into a short wait, and leaves a genuine
    denial failing exactly as it did before, one bounded delay later.  POSIX is
    passed straight through, where ``EACCES`` is a decision rather than a race.
    """

    if not _is_windows():
        return operation()
    deadline = time.monotonic() + WINDOWS_SHARING_RETRY_SECONDS
    while True:
        try:
            return operation()
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.025)


def _ensure_directory_chain_by_lstat(absolute: Path, *, create: bool = False) -> None:
    """Validate a directory chain component by component, without descriptors."""

    for component in [*reversed(absolute.parents), absolute]:
        try:
            info = os.lstat(component)
        except FileNotFoundError:
            if not create:
                raise
            try:
                os.mkdir(component, 0o700)
            except FileExistsError:
                # A second writer creating the same chain is expected here:
                # this runs before the loop lock is taken, because the lock
                # file lives inside the chain.  ``_open_directory_nofollow``
                # tolerates the same race on POSIX.
                pass
            info = os.lstat(component)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise TransactionError(
                f"transaction directory is not a real directory: {component}"
            )


def _ensure_directory_chain(path: Path, *, create: bool = False) -> None:
    """Require a real, symlink-free directory chain, optionally creating it.

    POSIX walks the chain with descriptors so a component cannot be swapped for
    a symlink between the check and the use. Windows has no ``os`` call that
    opens a directory, so it validates each component with ``lstat`` instead.
    """

    absolute = Path(os.path.abspath(path))
    if _is_windows():
        _ensure_directory_chain_by_lstat(absolute, create=create)
        return
    os.close(_open_directory_nofollow(absolute, create=create))


def _open_directory_nofollow(path: Path, *, create: bool = False) -> int:
    """Open a real directory chain, optionally creating missing components.

    POSIX only: Windows rejects ``os.open`` on a directory, so callers that just
    need the chain validated use :func:`_ensure_directory_chain`.
    """

    if _is_windows():
        raise TransactionError(
            "directory descriptors are not available on this platform"
        )
    absolute = Path(os.path.abspath(path))

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(absolute.anchor or os.sep, flags)
    try:
        for component in absolute.parts[1:]:
            try:
                next_fd = os.open(component, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def _lstat_nofollow(path: Path) -> os.stat_result | None:
    """Stat a leaf without following it or any parent symlink."""

    if _is_windows():
        try:
            return _tolerate_windows_sharing(lambda: os.lstat(path))
        except FileNotFoundError:
            return None
    try:
        directory_fd = _open_directory_nofollow(path.parent)
    except FileNotFoundError:
        return None
    try:
        try:
            return os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
    finally:
        os.close(directory_fd)


def _read_bytes_nofollow(
    path: Path,
    *,
    max_bytes: int = MAX_TRANSACTION_BYTES,
    require_single_link: bool = False,
    require_current_owner: bool = False,
    require_private: bool = False,
) -> bytes:
    """Read one bounded regular file through no-follow descriptors."""

    if _is_windows():
        info = _tolerate_windows_sharing(lambda: os.lstat(path))
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or (require_single_link and int(getattr(info, "st_nlink", 1)) != 1)
        ):
            raise TransactionError(f"transaction input is not a regular file: {path}")
        if (
            require_current_owner
            and hasattr(os, "geteuid")
            and int(getattr(info, "st_uid", -1)) != int(os.geteuid())
        ):
            raise TransactionError(f"transaction input is not host-owned: {path}")
        # ``require_private`` is not checkable here.  Windows synthesises the
        # POSIX mode bits rather than storing them, so reading them would
        # reject every file instead of the world-readable ones; privacy on this
        # platform is a property of the inherited ACL.  The guard used to test
        # ``os.name == "posix"`` inside this Windows-only branch, which never
        # held, and so read as a check while being none.
        payload = _tolerate_windows_sharing(path.read_bytes)
    else:
        directory_fd = _open_directory_nofollow(path.parent)
        try:
            file_fd = os.open(
                path.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
        finally:
            os.close(directory_fd)
        try:
            info = os.fstat(file_fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size > max_bytes
                or (require_single_link and int(info.st_nlink) != 1)
                or (require_current_owner and int(info.st_uid) != int(os.geteuid()))
                or (require_private and stat.S_IMODE(info.st_mode) & 0o077)
            ):
                raise TransactionError(
                    f"transaction input is unsafe or oversized: {path}"
                )
            with os.fdopen(file_fd, "rb", closefd=False) as handle:
                payload = handle.read(max_bytes + 1)
        finally:
            os.close(file_fd)
    if len(payload) > max_bytes:
        raise TransactionError(f"transaction input exceeds {max_bytes} bytes: {path}")
    return payload


def _validate_private_transaction_directory(path: Path, *, label: str) -> None:
    """Require a real, host-owned, non-shared transaction directory."""

    info = _lstat_nofollow(path)
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TransactionError(f"{label} is not a real directory: {path}")
    if os.name == "posix" and (
        int(info.st_uid) != int(os.geteuid())
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise TransactionError(f"{label} is not private and host-owned: {path}")


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    if _is_windows():
        _ensure_directory_chain(path.parent, create=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _tolerate_windows_sharing(lambda: os.replace(tmp_name, path))
            _fsync_dir(path.parent)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise
        return

    directory_fd = _open_directory_nofollow(path.parent, create=True)
    tmp_name = f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    fd = os.open(
        tmp_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            tmp_name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    except BaseException:
        try:
            os.unlink(tmp_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(directory_fd)


def _unlink_nofollow(path: Path) -> None:
    """Unlink a leaf relative to a no-follow parent descriptor."""

    if _is_windows():
        _ensure_directory_chain(path.parent)
        _tolerate_windows_sharing(path.unlink)
        _fsync_dir(path.parent)
        return
    directory_fd = _open_directory_nofollow(path.parent)
    try:
        os.unlink(path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _safe_relative_path(value: str | Path) -> Path:
    raw = str(value)
    rel = Path(raw)
    windows = PureWindowsPath(raw)
    if (
        rel.is_absolute()
        or bool(windows.drive)
        or bool(windows.root)
        or "\\" in raw
        or any(ord(char) < 32 or ord(char) == 127 for char in raw)
        or not rel.parts
        or any(part in {"", ".", ".."} for part in rel.parts)
    ):
        raise TransactionError(f"transaction target must be a safe relative path: {value!r}")
    if rel.parts[0] in {LOCK_FILENAME, TRANSACTION_DIRNAME}:
        raise TransactionError(
            f"transaction target uses the reserved lock/journal namespace: {value!r}"
        )
    return rel


def _safe_transaction_id(value: Any) -> str:
    raw = str(value or "")
    rel = _safe_relative_path(raw)
    if len(rel.parts) != 1 or len(raw) > 128:
        raise TransactionError(
            f"transaction id must be one safe bounded path component: {value!r}"
        )
    return raw


def _validate_lock_file(file_fd: int, path: Path) -> None:
    info = os.fstat(file_fd)
    if not stat.S_ISREG(info.st_mode):
        raise TransactionError(f"loop lock is not a regular file: {path}")
    if int(getattr(info, "st_nlink", 1)) != 1:
        raise TransactionError(f"loop lock must have exactly one link: {path}")
    if os.name == "posix":
        if int(info.st_uid) != int(os.geteuid()):
            raise TransactionError(f"loop lock is not owned by the current user: {path}")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise TransactionError(f"loop lock permissions are not private: {path}")


class LoopLock:
    """Exclusive cross-process lock scoped to one loop directory."""

    def __init__(self, run_dir: str | Path, timeout_seconds: float = 10.0) -> None:
        self.run_dir = Path(run_dir)
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self._handle: Any = None

    def _open_lock_handle(self, path: Path) -> Any:
        """Open and validate the lock file, returning a buffered handle.

        A fresh directory descriptor is taken on every call because the caller
        retries this step while the lock path keeps vanishing.
        """

        if _is_windows():
            _ensure_directory_chain(self.run_dir, create=True)
            try:
                before = os.lstat(path)
            except FileNotFoundError:
                before = None
            if before is not None and stat.S_ISLNK(before.st_mode):
                raise TransactionError(f"loop lock is a symlink: {path}")
            # Deliberately not wrapped in _tolerate_windows_sharing.  Every
            # other wrapped call is a step inside a commit the caller has
            # already been granted; a denial there is the platform's, not a
            # decision about this process.  Opening the lock is where that
            # decision is made, and the acquire loop above already waits for a
            # lock another writer holds.  Retrying a refusal here would only
            # stall an unauthorised writer for the whole lock timeout before
            # telling it the same thing.
            lock_fd = os.open(
                path,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0),
                0o600,
            )
            after = os.fstat(lock_fd)
            if before is not None and (
                int(getattr(before, "st_dev", 0)),
                int(getattr(before, "st_ino", 0)),
            ) != (
                int(getattr(after, "st_dev", 0)),
                int(getattr(after, "st_ino", 0)),
            ):
                os.close(lock_fd)
                raise TransactionError(f"loop lock changed while opening: {path}")
            _validate_lock_file(lock_fd, path)
            return os.fdopen(lock_fd, "a+b")
        directory_fd = _open_directory_nofollow(self.run_dir, create=True)
        try:
            lock_fd = os.open(
                LOCK_FILENAME,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory_fd,
            )
            try:
                _validate_lock_file(lock_fd, path)
            except BaseException:
                os.close(lock_fd)
                raise
            return os.fdopen(lock_fd, "a+b")
        finally:
            os.close(directory_fd)

    def __enter__(self) -> "LoopLock":
        path = self.run_dir / LOCK_FILENAME
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self._handle = self._open_lock_handle(path)
                break
            except FileNotFoundError as exc:
                # ``O_CREAT`` makes this open succeed whether or not the lock
                # file exists, so a missing path means the chain was in flux:
                # the directory holding the lock went away between the walk and
                # the open, or the platform failed the create while a second
                # writer created the same lock.  macOS reports the second case
                # when two writers contend on one revision.  Only a missing
                # path is retried; every other failure still fails closed.
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"timed out opening loop lock: {path}") from exc
                time.sleep(0.025)
        self._handle.seek(0, os.SEEK_END)
        if self._handle.tell() == 0:
            self._handle.write(b"0")
            self._handle.flush()
        while True:
            try:
                if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                    import msvcrt

                    self._handle.seek(0)
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise LockTimeout(f"timed out acquiring loop lock: {path}")
                time.sleep(0.025)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def read_revision(path: str | Path, field: str) -> int:
    """Read a non-negative integer revision; a missing file has revision zero."""

    target = Path(path)
    if _lstat_nofollow(target) is None:
        return 0
    try:
        value = json.loads(_read_bytes_nofollow(target).decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransactionError(f"cannot read revision from {target}: {exc}") from exc
    if not isinstance(value, dict):
        raise TransactionError(f"cannot read revision from non-object {target}")
    raw = value.get(field, 0)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise TransactionError(f"revision field {field!r} in {target} is not an integer")
    revision = raw
    if revision < 0:
        raise TransactionError(f"revision field {field!r} in {target} is negative")
    return revision


def _infer_revision_field(rel: Path) -> str:
    return {
        "current_plan.json": "plan_revision",
        "goal_contract.json": "goal_revision",
        "approach_registry.json": "registry_revision",
        "loop_state.json": "revision",
        "budget.json": "revision",
    }.get(rel.name, "revision")


def _parse_expectation(rel: Path, value: Any) -> tuple[str, int]:
    if isinstance(value, Mapping):
        field = str(value.get("field") or _infer_revision_field(rel))
        expected = value.get("value", value.get("revision"))
    elif isinstance(value, (tuple, list)) and len(value) == 2:
        field, expected = str(value[0]), value[1]
    else:
        field, expected = _infer_revision_field(rel), value
    if (
        isinstance(expected, bool)
        or not isinstance(expected, int)
        or expected < 0
    ):
        raise TransactionError(f"invalid expected revision for {rel}")
    return field, expected


def _event_key(record: Mapping[str, Any]) -> str:
    for key in ("event_id", "decision_id", "candidate_id", "transaction_event_id"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def jsonl_text(records: Sequence[Mapping[str, Any]]) -> str:
    """Serialize rows exactly as an appended post-image would serialize them.

    Kept byte-identical to :func:`_jsonl_postimage` so a caller that rewrites a
    ledger wholesale (rotation) produces the same file an append would.
    """

    lines = [json.dumps(dict(record), sort_keys=True) for record in records]
    return ("\n".join(lines) + "\n") if lines else ""


def _jsonl_postimage(path: Path, records: Sequence[Mapping[str, Any]]) -> bytes:
    existing_lines: list[str] = []
    existing_keys: set[str] = set()
    if _lstat_nofollow(path) is not None:
        try:
            text = _read_bytes_nofollow(path).decode("utf-8")
            for index, raw in enumerate(text.splitlines(), start=1):
                if not raw.strip():
                    continue
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise TransactionError(f"{path.name} line {index} is not a JSON object")
                existing_lines.append(json.dumps(value, sort_keys=True))
                existing_keys.add(_event_key(value))
        except json.JSONDecodeError as exc:
            raise TransactionError(f"invalid JSONL target {path}: {exc}") from exc
    for raw_record in records:
        record = dict(raw_record)
        key = _event_key(record)
        if key in existing_keys:
            continue
        if not any(record.get(name) for name in ("event_id", "decision_id", "candidate_id")):
            record["transaction_event_id"] = key
        existing_lines.append(json.dumps(record, sort_keys=True))
        existing_keys.add(key)
    text = "\n".join(existing_lines)
    return ((text + "\n") if text else "").encode("utf-8")


def _write_manifest(tx_dir: Path, manifest: Mapping[str, Any]) -> None:
    _validate_private_transaction_directory(tx_dir, label="transaction journal entry")
    _atomic_write_bytes(tx_dir / "manifest.json", _json_bytes(dict(manifest)))


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != "goal_focus_transaction.v1":
        raise TransactionError("transaction manifest schema is invalid")
    _safe_transaction_id(manifest.get("transaction_id"))
    phase = str(manifest.get("phase") or "")
    if phase not in {
        "prepared",
        "applying",
        "committed",
    }:
        raise TransactionError("transaction manifest phase is invalid")
    committed_at = manifest.get("committed_at")
    if phase == "committed":
        raw_committed_at = str(committed_at or "")
        try:
            from datetime import datetime

            parsed = datetime.fromisoformat(
                raw_committed_at[:-1] + "+00:00"
                if raw_committed_at.endswith("Z")
                else raw_committed_at
            )
        except ValueError as exc:
            raise TransactionError(
                "committed transaction manifest lacks a valid commit timestamp"
            ) from exc
        if (
            not raw_committed_at
            or len(raw_committed_at) > 40
            or parsed.tzinfo is None
            or parsed.utcoffset() is None
        ):
            raise TransactionError(
                "committed transaction manifest lacks a valid commit timestamp"
            )
    elif committed_at not in (None, ""):
        raise TransactionError(
            "uncommitted transaction manifest carries a commit timestamp"
        )
    targets = manifest.get("targets")
    if not isinstance(targets, list) or len(targets) > 10_000:
        raise TransactionError("transaction manifest targets must be a bounded list")
    seen: set[Path] = set()
    for index, entry in enumerate(targets):
        if not isinstance(entry, Mapping):
            raise TransactionError(
                f"transaction manifest target {index} must be an object"
            )
        rel = _safe_relative_path(str(entry.get("path") or ""))
        if rel in seen:
            raise TransactionError(f"duplicate transaction manifest target: {rel}")
        seen.add(rel)
        delete = entry.get("delete")
        if not isinstance(delete, bool):
            raise TransactionError(
                f"transaction manifest target delete flag is invalid: {rel}"
            )
        blob_name = str(entry.get("blob") or "")
        digest = str(entry.get("sha256") or "")
        if delete:
            if blob_name or digest:
                raise TransactionError(
                    f"delete target carries an unexpected post-image: {rel}"
                )
            continue
        blob_rel = _safe_relative_path(blob_name)
        if len(blob_rel.parts) != 1 or len(blob_name) > 200:
            raise TransactionError(
                f"transaction post-image must be one safe bounded filename: {blob_name!r}"
            )
        if len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise TransactionError(
                f"transaction post-image digest is invalid for {rel}"
            )


def _validate_committed_poststate(
    run_dir: Path, manifest: Mapping[str, Any]
) -> None:
    """Prove a committed marker matches every live target before journal deletion."""

    for entry in manifest.get("targets") or []:
        rel = _safe_relative_path(str(entry.get("path") or ""))
        target = run_dir / rel
        info = _lstat_nofollow(target)
        if entry.get("delete"):
            if info is not None:
                raise TransactionError(
                    f"committed transaction delete post-state is not absent: {rel}"
                )
            continue
        if info is None or not stat.S_ISREG(info.st_mode):
            raise TransactionError(
                f"committed transaction post-state is missing or unsafe: {rel}"
            )
        observed = hashlib.sha256(_read_bytes_nofollow(target)).hexdigest()
        if observed != entry.get("sha256"):
            raise TransactionError(
                f"committed transaction post-state digest mismatch: {rel}"
            )


def _apply_manifest(run_dir: Path, tx_dir: Path, manifest: dict[str, Any], crash_after: Any = None) -> None:
    _validate_manifest(manifest)
    _validate_private_transaction_directory(tx_dir, label="transaction journal entry")
    post_dir = tx_dir / "postimages"
    _validate_private_transaction_directory(post_dir, label="transaction post-image directory")
    manifest["phase"] = "applying"
    _write_manifest(tx_dir, manifest)
    if crash_after in {"prepared", "before_apply"}:
        raise InjectedCrash(f"injected crash at {crash_after}")

    targets = manifest.get("targets") or []
    for index, entry in enumerate(targets, start=1):
        rel = _safe_relative_path(str(entry.get("path") or ""))
        target = run_dir / rel
        if entry.get("delete"):
            try:
                _unlink_nofollow(target)
            except FileNotFoundError:
                pass
        else:
            blob_name = str(entry.get("blob") or "")
            blob_rel = _safe_relative_path(blob_name)
            if len(blob_rel.parts) != 1:
                raise TransactionError(
                    f"transaction post-image must be a single safe filename: {blob_name!r}"
                )
            blob = post_dir / blob_rel
            payload = _read_bytes_nofollow(
                blob,
                require_single_link=True,
                require_current_owner=True,
                require_private=True,
            )
            digest = hashlib.sha256(payload).hexdigest()
            if digest != entry.get("sha256"):
                raise TransactionError(f"post-image hash mismatch for {rel}")
            _atomic_write_bytes(target, payload)
        if crash_after == index or crash_after == f"apply:{rel.as_posix()}":
            raise InjectedCrash(f"injected crash after applying {rel}")

    if crash_after == "after_apply":
        raise InjectedCrash("injected crash after all post-images")
    manifest["phase"] = "committed"
    manifest["committed_at"] = _utc_now()
    _write_manifest(tx_dir, manifest)
    if crash_after == "committed":
        raise InjectedCrash("injected crash after commit marker")


def _recover_locked(run_dir: Path) -> list[dict[str, Any]]:
    root = run_dir / TRANSACTION_DIRNAME
    root_info = _lstat_nofollow(root)
    if root_info is None:
        return []
    _validate_private_transaction_directory(root, label="transaction journal root")
    recovered: list[dict[str, Any]] = []
    tx_dirs: list[Path] = []
    for path in root.iterdir():
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise TransactionError(f"transaction journal entry is unsafe: {path}")
        _validate_private_transaction_directory(path, label="transaction journal entry")
        tx_dirs.append(path)
    for tx_dir in sorted(tx_dirs):
        manifest_path = tx_dir / "manifest.json"
        if not manifest_path.exists():
            shutil.rmtree(tx_dir, ignore_errors=True)
            continue
        try:
            manifest = json.loads(
                _read_bytes_nofollow(
                    manifest_path,
                    require_single_link=True,
                    require_current_owner=True,
                    require_private=True,
                ).decode("utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise TransactionError(f"unreadable transaction manifest {manifest_path}: {exc}") from exc
        if not isinstance(manifest, dict):
            raise TransactionError(f"transaction manifest is not an object: {manifest_path}")
        _validate_manifest(manifest)
        if _safe_transaction_id(manifest.get("transaction_id")) != tx_dir.name:
            raise TransactionError(
                f"transaction manifest id disagrees with journal directory: {manifest_path}"
            )
        phase = str(manifest.get("phase") or "")
        try:
            if phase != "committed":
                _apply_manifest(run_dir, tx_dir, manifest)
                recovered.append(
                    {
                        "transaction_id": manifest.get("transaction_id"),
                        "previous_phase": phase,
                        "status": "recovered",
                    }
                )
            else:
                _validate_committed_poststate(run_dir, manifest)
        except TransactionError as exc:
            # Leaving the entry in place re-arms the same failure on every
            # later command, and no command in the kit can clear it.  Move it
            # aside so the failure is loud once: the manifest carries the
            # expected-versus-observed digests and must never be auto-deleted.
            quarantine = run_dir / TRANSACTION_QUARANTINE_DIRNAME / tx_dir.name
            _ensure_directory_chain(quarantine.parent, create=True)
            _tolerate_windows_sharing(lambda: shutil.move(str(tx_dir), str(quarantine)))
            try:
                # Only succeeds when this was the last entry, which is the
                # case that would otherwise leave an empty journal behind.
                root.rmdir()
            except OSError:
                pass
            raise TransactionQuarantined(
                f"transaction journal quarantined at {quarantine}: {exc}"
            ) from exc
        _tolerate_windows_sharing(lambda: shutil.rmtree(tx_dir))
    try:
        root.rmdir()
    except OSError:
        pass
    return recovered


def recover_transactions(run_dir: str | Path, *, lock_timeout_seconds: float = 10.0) -> list[dict[str, Any]]:
    """Finish all prepared transactions and remove completed journals."""

    root = Path(run_dir)
    with LoopLock(root, timeout_seconds=lock_timeout_seconds):
        return _recover_locked(root)


def commit_transaction(
    run_dir: str | Path,
    *,
    json_files: Mapping[str | Path, Any] | None = None,
    text_files: Mapping[str | Path, str] | None = None,
    binary_files: Mapping[str | Path, bytes] | None = None,
    jsonl_appends: Mapping[str | Path, Sequence[Mapping[str, Any]]] | None = None,
    deletes: Sequence[str | Path] | None = None,
    expected_revisions: Mapping[str | Path, Any] | None = None,
    expected_absent: Sequence[str | Path] | None = None,
    expected_hashes: Mapping[str | Path, str] | None = None,
    transaction_id: str | None = None,
    lock_timeout_seconds: float = 10.0,
    crash_after: Any = None,
) -> dict[str, Any]:
    """Commit a recoverable group of file replacements.

    JSON, text, and binary maps are keyed by run-directory-relative paths.
    ``expected_revisions`` values may be integers, ``(field, value)`` pairs, or
    ``{"field": ..., "value": ...}`` objects. ``expected_absent`` and
    ``expected_hashes`` provide compare-and-swap guards for unrevisioned files.
    ``crash_after`` exists solely for deterministic recovery tests.
    """

    root = Path(run_dir)
    json_files = json_files or {}
    text_files = text_files or {}
    binary_files = binary_files or {}
    jsonl_appends = jsonl_appends or {}
    expected_revisions = expected_revisions or {}
    expected_absent = expected_absent or []
    expected_hashes = expected_hashes or {}
    deletes = deletes or []
    transaction_id = _safe_transaction_id(transaction_id or uuid.uuid4())

    with LoopLock(root, timeout_seconds=lock_timeout_seconds):
        recovered = _recover_locked(root)
        for raw_rel, expectation in expected_revisions.items():
            rel = _safe_relative_path(raw_rel)
            field, expected = _parse_expectation(rel, expectation)
            observed = read_revision(root / rel, field)
            if observed != expected:
                raise RevisionConflict(
                    f"revision conflict for {rel}: expected {field}={expected}, observed {observed}"
                )
        for raw_rel in expected_absent:
            rel = _safe_relative_path(raw_rel)
            if _lstat_nofollow(root / rel) is not None:
                raise RevisionConflict(f"expected transaction target to be absent: {rel}")
        for raw_rel, expected_hash in expected_hashes.items():
            rel = _safe_relative_path(raw_rel)
            target = root / rel
            info = _lstat_nofollow(target)
            if info is None or not stat.S_ISREG(info.st_mode):
                raise RevisionConflict(f"expected transaction preimage is missing: {rel}")
            observed_hash = hashlib.sha256(_read_bytes_nofollow(target)).hexdigest()
            if observed_hash != str(expected_hash):
                raise RevisionConflict(f"transaction preimage changed: {rel}")

        payloads: dict[Path, bytes] = {}
        delete_set: set[Path] = set()
        for raw_rel, value in json_files.items():
            rel = _safe_relative_path(raw_rel)
            payloads[rel] = _json_bytes(value)
        for raw_rel, value in text_files.items():
            rel = _safe_relative_path(raw_rel)
            if rel in payloads:
                raise TransactionError(f"duplicate transaction target: {rel}")
            payloads[rel] = str(value).encode("utf-8")
        for raw_rel, value in binary_files.items():
            rel = _safe_relative_path(raw_rel)
            if rel in payloads:
                raise TransactionError(f"duplicate transaction target: {rel}")
            if not isinstance(value, bytes):
                raise TransactionError(f"binary transaction target must be bytes: {rel}")
            payloads[rel] = value
        for raw_rel, records in jsonl_appends.items():
            rel = _safe_relative_path(raw_rel)
            if rel in payloads:
                raise TransactionError(f"duplicate transaction target: {rel}")
            payloads[rel] = _jsonl_postimage(root / rel, records)
        for raw_rel in deletes:
            rel = _safe_relative_path(raw_rel)
            if rel in payloads:
                raise TransactionError(f"target cannot be replaced and deleted: {rel}")
            delete_set.add(rel)
        if not payloads and not delete_set:
            return {
                "status": "noop",
                "transaction_id": transaction_id,
                "recovered": recovered,
                "targets": [],
            }

        # A post-image the reader cannot read back would prepare cleanly and
        # then fail every recovery pass, so refuse it before the journal
        # directory exists rather than after it is armed.
        oversized = [
            rel
            for rel, payload in payloads.items()
            if len(payload) > MAX_TRANSACTION_BYTES
        ]
        if oversized:
            raise TransactionError(
                f"transaction post-image exceeds {MAX_TRANSACTION_BYTES} bytes: "
                f"{sorted(item.as_posix() for item in oversized)[0]}"
            )

        tx_root = root / TRANSACTION_DIRNAME
        tx_dir = tx_root / transaction_id
        if _lstat_nofollow(tx_dir) is not None:
            raise TransactionError(f"transaction id already exists: {transaction_id}")
        post_dir = tx_dir / "postimages"
        _ensure_directory_chain(post_dir, create=True)
        _validate_private_transaction_directory(tx_root, label="transaction journal root")
        _validate_private_transaction_directory(tx_dir, label="transaction journal entry")
        _validate_private_transaction_directory(
            post_dir, label="transaction post-image directory"
        )
        target_entries: list[dict[str, Any]] = []
        for index, rel in enumerate(sorted(payloads, key=lambda item: item.as_posix())):
            payload = payloads[rel]
            blob_name = f"{index:04d}-{hashlib.sha256(rel.as_posix().encode()).hexdigest()[:16]}.bin"
            blob = post_dir / blob_name
            _atomic_write_bytes(blob, payload)
            target_entries.append(
                {
                    "path": rel.as_posix(),
                    "blob": blob_name,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "delete": False,
                }
            )
        for rel in sorted(delete_set, key=lambda item: item.as_posix()):
            target_entries.append({"path": rel.as_posix(), "delete": True})

        manifest: dict[str, Any] = {
            "schema_version": "goal_focus_transaction.v1",
            "transaction_id": transaction_id,
            "phase": "prepared",
            "created_at": _utc_now(),
            "targets": target_entries,
        }
        _write_manifest(tx_dir, manifest)
        _fsync_dir(tx_root)
        _apply_manifest(root, tx_dir, manifest, crash_after=crash_after)
        _tolerate_windows_sharing(lambda: shutil.rmtree(tx_dir))
        try:
            tx_root.rmdir()
        except OSError:
            pass
        return {
            "status": "committed",
            "transaction_id": transaction_id,
            "recovered": recovered,
            "targets": [entry["path"] for entry in target_entries],
        }
