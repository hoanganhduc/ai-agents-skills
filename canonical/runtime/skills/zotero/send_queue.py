#!/usr/bin/env python3
"""Authenticated, at-most-once host file-delivery queue.

The agent-facing producer and the host worker share a protected capability file.
Queue records contain no credential values; HMACs bind the exact request, media
snapshot digest, nonce, and expiry.  The worker claims each record atomically and
passes a held media descriptor to the delivery CLI.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator


AUTHORITY_ENV = "AAS_FILE_DELIVERY_SECRETS_FILE"
DEFAULT_AUTHORITY_RELATIVE = Path(".config/ai-agents-skills/file-delivery-queue.json")
REPLAY_LEDGER_TOKEN = "aas-host-state:file-delivery-replay"
DEFAULT_REPLAY_RELATIVE = Path(".local/state/ai-agents-skills/file-delivery-replay")
AUTHORIZED_EXPORT_RELATIVES = (
    Path("data/exports"),
    Path("data/research/zotero/staging"),
    Path("data/calibre/staging"),
)
MAX_AUTHORITY_BYTES = 65_536
MAX_REQUEST_BYTES = 16_384
MAX_JOB_BYTES = 32_768
MAX_RESULT_BYTES = 16_384
MAX_CAPTION_BYTES = 8_192
MAX_TARGET_BYTES = 1_024
MAX_DELIVERY_OUTPUT_BYTES = 4_096
MAX_DELIVERY_REQUEST_BYTES = 16_384
JOB_RE = re.compile(r"job-[0-9]{10}-[0-9a-f]{16}\Z")
NONCE_RE = re.compile(r"[0-9a-f]{64}\Z")
CHANNEL_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
MEDIA_RE = re.compile(r"job-[0-9]{10}-[0-9a-f]{16}(?:\.[A-Za-z0-9]{1,10})?\Z")
USED_RE = re.compile(r"[0-9a-f]{64}\.used\Z")
AUTHORITY_KEYS = frozenset(
    {
        "version", "hmac_key_hex", "allowed", "max_job_age_seconds",
        "max_media_bytes", "replay_ledger_dir", "replay_retention_seconds",
        "max_replay_entries",
    }
)
JOB_KEYS = frozenset(
    {
        "version", "id", "nonce", "created_at", "expires_at", "channel",
        "target", "media_name", "media_size", "media_sha256", "caption", "mac",
    }
)
RESULT_KEYS = frozenset(
    {
        "version", "job_id", "nonce", "media_sha256", "status", "error_code",
        "completed_at", "mac",
    }
)
USED_KEYS = frozenset({"version", "job_id", "used_at"})
REQUEST_KEYS = frozenset({"channel", "target", "media", "caption"})


class QueueSecurityError(ValueError):
    """The queue request or one of its protected artifacts failed closed."""


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise QueueSecurityError("protected file-delivery JSON contains a duplicate key")
        result[key] = value
    return result


@dataclass(frozen=True)
class QueuePolicy:
    key: bytes
    allowed: dict[str, frozenset[str]]
    max_job_age_seconds: int
    max_media_bytes: int
    replay_ledger_dir: Path
    replay_retention_seconds: int
    max_replay_entries: int


def _stable(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        int(info.st_dev), int(info.st_ino), int(info.st_size),
        int(info.st_mtime_ns), int(info.st_ctime_ns), int(info.st_nlink),
    )


def _absolute(path: Path | str, label: str) -> Path:
    supplied = Path(path)
    if not supplied.is_absolute():
        raise QueueSecurityError(f"{label} must be absolute")
    return Path(os.path.abspath(supplied))


def _open_directory_nofollow(path: Path, *, private: bool = False) -> int:
    absolute = _absolute(path, "directory path")
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
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise QueueSecurityError("queue path is not a directory")
        if private and (
            int(info.st_uid) != int(os.geteuid())
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise QueueSecurityError("queue directory must be owner-private")
        return fd
    except Exception:
        os.close(fd)
        raise


def _open_bound_file(
    path: Path,
    *,
    max_bytes: int,
    owner_private: bool,
) -> tuple[int, os.stat_result]:
    absolute = _absolute(path, "protected file path")
    parent_fd: int | None = None
    file_fd: int | None = None
    try:
        parent_fd = _open_directory_nofollow(absolute.parent)
        path_info = os.stat(absolute.name, dir_fd=parent_fd, follow_symlinks=False)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        file_fd = os.open(absolute.name, flags, dir_fd=parent_fd)
        info = os.fstat(file_fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or int(info.st_nlink) != 1
            or int(info.st_size) > max_bytes
            or (int(path_info.st_dev), int(path_info.st_ino))
            != (int(info.st_dev), int(info.st_ino))
            or int(info.st_uid) not in {0, int(os.geteuid())}
            or stat.S_IMODE(info.st_mode) & 0o022
            or (owner_private and stat.S_IMODE(info.st_mode) & 0o077)
        ):
            raise QueueSecurityError("protected file failed ownership or identity checks")
        return file_fd, info
    except Exception:
        if file_fd is not None:
            os.close(file_fd)
        raise
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def _read_fd(fd: int, before: os.stat_result, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining:
        chunk = os.read(fd, min(65_536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    after = os.fstat(fd)
    if len(payload) > max_bytes or _stable(before) != _stable(after):
        raise QueueSecurityError("protected file changed while reading")
    return payload


def _read_path(path: Path, *, max_bytes: int, owner_private: bool) -> bytes:
    fd, before = _open_bound_file(
        path, max_bytes=max_bytes, owner_private=owner_private
    )
    try:
        return _read_fd(fd, before, max_bytes=max_bytes)
    finally:
        os.close(fd)


def load_policy(path_value: str | None = None) -> QueuePolicy:
    selected = path_value or os.environ.get(AUTHORITY_ENV)
    if selected is None:
        home = os.environ.get("HOME")
        if not home or not Path(home).is_absolute():
            raise QueueSecurityError(
                f"{AUTHORITY_ENV} must name a protected authority when HOME is unavailable"
            )
        selected = os.fspath(Path(home) / DEFAULT_AUTHORITY_RELATIVE)
    value = str(selected)
    if not value or value != value.strip():
        raise QueueSecurityError(f"{AUTHORITY_ENV} must name a protected authority")
    authority_path = _absolute(value, "file-delivery authority")
    payload = _read_path(
        authority_path,
        max_bytes=MAX_AUTHORITY_BYTES,
        owner_private=True,
    )
    try:
        raw = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_strict_json_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QueueSecurityError("file-delivery authority is not valid UTF-8 JSON") from exc
    if not isinstance(raw, dict) or set(raw) != AUTHORITY_KEYS or raw.get("version") != 1:
        raise QueueSecurityError("file-delivery authority schema is unsupported")
    key_hex = raw.get("hmac_key_hex")
    if not isinstance(key_hex, str) or not re.fullmatch(r"[0-9a-f]{64}", key_hex):
        raise QueueSecurityError("file-delivery authority HMAC key is invalid")
    allowed_raw = raw.get("allowed")
    if not isinstance(allowed_raw, dict) or not allowed_raw:
        raise QueueSecurityError("file-delivery authority allowlist is empty")
    allowed: dict[str, frozenset[str]] = {}
    for channel, targets in allowed_raw.items():
        if not isinstance(channel, str) or not CHANNEL_RE.fullmatch(channel):
            raise QueueSecurityError("file-delivery authority contains an invalid channel")
        if (
            not isinstance(targets, list)
            or not targets
            or any(
                not isinstance(target, str)
                or not target
                or target != target.strip()
                or len(target.encode("utf-8")) > MAX_TARGET_BYTES
                or any(ord(char) < 32 or ord(char) == 127 for char in target)
                for target in targets
            )
            or len(set(targets)) != len(targets)
        ):
            raise QueueSecurityError("file-delivery authority contains an invalid target allowlist")
        allowed[channel] = frozenset(targets)
    age = raw.get("max_job_age_seconds")
    media_limit = raw.get("max_media_bytes")
    replay_value = raw.get("replay_ledger_dir")
    if isinstance(age, bool) or not isinstance(age, int) or not 5 <= age <= 300:
        raise QueueSecurityError("file-delivery job age limit is invalid")
    if (
        isinstance(media_limit, bool)
        or not isinstance(media_limit, int)
        or not 1 <= media_limit <= 100 * 1024 * 1024
    ):
        raise QueueSecurityError("file-delivery media limit is invalid")
    if replay_value != REPLAY_LEDGER_TOKEN:
        raise QueueSecurityError("file-delivery replay ledger path is invalid")
    canonical_suffix = DEFAULT_AUTHORITY_RELATIVE.parts
    if authority_path.parts[-len(canonical_suffix):] == canonical_suffix:
        authority_home = authority_path.parents[len(canonical_suffix) - 1]
    else:
        home = os.environ.get("HOME")
        if not home or not Path(home).is_absolute():
            raise QueueSecurityError("could not resolve the host replay ledger")
        authority_home = Path(home)
    replay_ledger = _absolute(
        authority_home / DEFAULT_REPLAY_RELATIVE,
        "file-delivery replay ledger",
    )
    retention = raw.get("replay_retention_seconds")
    max_entries = raw.get("max_replay_entries")
    if (
        isinstance(retention, bool)
        or not isinstance(retention, int)
        or retention < age + 60
        or retention > 7 * 24 * 60 * 60
    ):
        raise QueueSecurityError("file-delivery replay retention is invalid")
    if (
        isinstance(max_entries, bool)
        or not isinstance(max_entries, int)
        or not 100 <= max_entries <= 100_000
    ):
        raise QueueSecurityError("file-delivery replay entry bound is invalid")
    return QueuePolicy(
        bytes.fromhex(key_hex),
        allowed,
        age,
        media_limit,
        replay_ledger,
        retention,
        max_entries,
    )


def _canonical_mac(record: dict[str, object], key: bytes) -> str:
    unsigned = {name: value for name, value in record.items() if name != "mac"}
    payload = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _validate_request(policy: QueuePolicy, channel: str, target: str, caption: str) -> None:
    if channel not in policy.allowed or target not in policy.allowed[channel]:
        raise QueueSecurityError("file-delivery channel or target is not allowlisted")
    if (
        not isinstance(caption, str)
        or len(caption.encode("utf-8")) > MAX_CAPTION_BYTES
        or any(char == "\x00" for char in caption)
    ):
        raise QueueSecurityError("file-delivery caption is invalid")


@contextmanager
def _queue_layout(workspace: Path) -> Iterator[dict[str, int]]:
    workspace = _absolute(workspace, "runtime workspace")
    data_fd = _open_directory_nofollow(workspace / "data", private=True)
    opened: list[int] = [data_fd]
    try:
        try:
            os.mkdir("send-queue", 0o700, dir_fd=data_fd)
        except FileExistsError:
            pass
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        queue_fd = os.open("send-queue", flags, dir_fd=data_fd)
        opened.append(queue_fd)
        queue_info = os.fstat(queue_fd)
        if (
            not stat.S_ISDIR(queue_info.st_mode)
            or int(queue_info.st_uid) != int(os.geteuid())
            or stat.S_IMODE(queue_info.st_mode) & 0o077
        ):
            raise QueueSecurityError("send queue must be owner-private")
        result: dict[str, int] = {"queue": queue_fd}
        for name in ("media",):
            try:
                os.mkdir(name, 0o700, dir_fd=queue_fd)
            except FileExistsError:
                pass
            child = os.open(name, flags, dir_fd=queue_fd)
            opened.append(child)
            info = os.fstat(child)
            if (
                not stat.S_ISDIR(info.st_mode)
                or int(info.st_uid) != int(os.geteuid())
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise QueueSecurityError("send queue child directory must be owner-private")
            result[name] = child
        yield result
    except OSError as exc:
        raise QueueSecurityError("could not securely access the send queue") from exc
    finally:
        for fd in reversed(opened):
            os.close(fd)


def _write_exclusive_json(directory_fd: int, name: str, record: dict[str, object]) -> None:
    payload = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short queue write")
            view = view[written:]
        os.fsync(fd)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or int(info.st_nlink) != 1
            or int(info.st_uid) != int(os.geteuid())
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise QueueSecurityError("queue record failed private-file checks")
    except Exception:
        try:
            os.unlink(name, dir_fd=directory_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(fd)


def _snapshot_media(media_path: Path, policy: QueuePolicy, media_fd: int, name: str) -> tuple[int, str]:
    source_fd, before = _open_bound_file(
        media_path, max_bytes=policy.max_media_bytes, owner_private=False
    )
    target_fd: int | None = None
    try:
        target_fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=media_fd,
        )
        digest = hashlib.sha256()
        copied = 0
        while True:
            chunk = os.read(source_fd, 65_536)
            if not chunk:
                break
            copied += len(chunk)
            if copied > policy.max_media_bytes:
                raise QueueSecurityError("file-delivery media exceeds the configured limit")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(target_fd, view)
                if written <= 0:
                    raise OSError("short media snapshot write")
                view = view[written:]
        after = os.fstat(source_fd)
        if _stable(before) != _stable(after) or copied != int(before.st_size):
            raise QueueSecurityError("file-delivery media changed during snapshot")
        os.fsync(target_fd)
        target = os.fstat(target_fd)
        if (
            not stat.S_ISREG(target.st_mode)
            or int(target.st_nlink) != 1
            or int(target.st_uid) != int(os.geteuid())
            or stat.S_IMODE(target.st_mode) & 0o077
            or int(target.st_size) != copied
        ):
            raise QueueSecurityError("media snapshot failed private-file checks")
        return copied, digest.hexdigest()
    except Exception:
        try:
            os.unlink(name, dir_fd=media_fd)
        except OSError:
            pass
        raise
    finally:
        if target_fd is not None:
            os.close(target_fd)
        os.close(source_fd)


def _authorized_export_media(workspace: Path, media: Path) -> Path:
    """Admit only files deliberately staged in one exact workflow export root."""
    workspace_absolute = _absolute(workspace, "runtime workspace")
    media_absolute = _absolute(media, "file-delivery media")
    for relative_root in AUTHORIZED_EXPORT_RELATIVES:
        export_root = workspace_absolute / relative_root
        try:
            relative = media_absolute.relative_to(export_root)
        except ValueError:
            continue
        if not relative.parts:
            raise QueueSecurityError("file-delivery media must name an exported file")
        export_fd = _open_directory_nofollow(export_root, private=True)
        os.close(export_fd)
        return media_absolute
    raise QueueSecurityError(
        "file-delivery media is outside the authorized export roots"
    )


def publish_job(
    workspace: Path,
    *,
    channel: str,
    target: str,
    media: Path,
    caption: str = "",
    authority: str | None = None,
    now: int | None = None,
    nonce: str | None = None,
) -> dict[str, object]:
    policy = load_policy(authority)
    _validate_request(policy, channel, target, caption)
    authorized_media = _authorized_export_media(workspace, media)
    created = int(time.time()) if now is None else int(now)
    nonce_value = nonce or secrets.token_hex(32)
    if not NONCE_RE.fullmatch(nonce_value):
        raise QueueSecurityError("file-delivery nonce is invalid")
    job_id = f"job-{created:010d}-{nonce_value[:16]}"
    if not JOB_RE.fullmatch(job_id):
        raise QueueSecurityError("file-delivery job identity is invalid")
    suffix = media.suffix.removeprefix(".")
    media_name = job_id + (f".{suffix}" if re.fullmatch(r"[A-Za-z0-9]{1,10}", suffix) else "")
    with _queue_layout(workspace) as layout:
        media_size, media_digest = _snapshot_media(
            authorized_media, policy, layout["media"], media_name
        )
        record: dict[str, object] = {
            "version": 1,
            "id": job_id,
            "nonce": nonce_value,
            "created_at": created,
            "expires_at": created + policy.max_job_age_seconds,
            "channel": channel,
            "target": target,
            "media_name": media_name,
            "media_size": media_size,
            "media_sha256": media_digest,
            "caption": caption,
        }
        record["mac"] = _canonical_mac(record, policy.key)
        try:
            _write_exclusive_json(layout["queue"], f"{job_id}.json", record)
            os.fsync(layout["queue"])
        except Exception:
            os.unlink(media_name, dir_fd=layout["media"])
            raise
    return {
        "job_id": job_id,
        "nonce": nonce_value,
        "media_sha256": media_digest,
        "expires_at": created + policy.max_job_age_seconds,
    }


def _read_named_json(directory_fd: int, name: str, *, max_bytes: int) -> dict[str, object]:
    try:
        path_info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise QueueSecurityError("could not securely open queue record") from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or int(before.st_nlink) != 1
            or int(before.st_uid) != int(os.geteuid())
            or stat.S_IMODE(before.st_mode) & 0o077
            or (int(path_info.st_dev), int(path_info.st_ino))
            != (int(before.st_dev), int(before.st_ino))
        ):
            raise QueueSecurityError("queue record failed identity checks")
        payload = _read_fd(fd, before, max_bytes=max_bytes)
    finally:
        os.close(fd)
    try:
        record = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_strict_json_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QueueSecurityError("queue record is not valid UTF-8 JSON") from exc
    if not isinstance(record, dict):
        raise QueueSecurityError("queue record must be an object")
    return record


@contextmanager
def _locked_replay_ledger(replay_fd: int) -> Iterator[None]:
    """Serialize marker admission and expiry pruning across host workers."""
    import fcntl

    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    lock_fd = os.open(".ledger.lock", flags, 0o600, dir_fd=replay_fd)
    try:
        os.fchmod(lock_fd, 0o600)
        info = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or int(info.st_nlink) != 1
            or int(info.st_uid) != int(os.geteuid())
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise QueueSecurityError("replay ledger lock failed private-file checks")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _prune_replay_ledger(
    replay_fd: int, policy: QueuePolicy, *, now: int
) -> int:
    """Remove only authenticated-format markers whose retention has elapsed.

    The caller holds ``_locked_replay_ledger``. Invalid or concurrently changed
    entries are retained and count against the configured bound.
    """
    active = 0
    for name in sorted(os.listdir(replay_fd)):
        if not USED_RE.fullmatch(name):
            continue
        try:
            before_path = os.stat(name, dir_fd=replay_fd, follow_symlinks=False)
            record = _read_named_json(replay_fd, name, max_bytes=MAX_RESULT_BYTES)
            after_path = os.stat(name, dir_fd=replay_fd, follow_symlinks=False)
        except (OSError, QueueSecurityError):
            active += 1
            continue
        used_at = record.get("used_at")
        expired = (
            set(record) == USED_KEYS
            and record.get("version") == 1
            and isinstance(record.get("job_id"), str)
            and JOB_RE.fullmatch(str(record["job_id"])) is not None
            and isinstance(used_at, int)
            and not isinstance(used_at, bool)
            and used_at <= now
            and used_at + policy.replay_retention_seconds < now
        )
        same_identity = (
            int(before_path.st_dev),
            int(before_path.st_ino),
            int(before_path.st_size),
            int(before_path.st_mtime_ns),
        ) == (
            int(after_path.st_dev),
            int(after_path.st_ino),
            int(after_path.st_size),
            int(after_path.st_mtime_ns),
        )
        if expired and same_identity:
            try:
                os.unlink(name, dir_fd=replay_fd)
            except FileNotFoundError:
                pass
            continue
        active += 1
    return active


def _verify_job(record: dict[str, object], policy: QueuePolicy, *, now: int) -> None:
    if set(record) != JOB_KEYS or record.get("version") != 1:
        raise QueueSecurityError("queue job schema is unsupported")
    job_id = record.get("id")
    nonce = record.get("nonce")
    channel = record.get("channel")
    target = record.get("target")
    caption = record.get("caption")
    created = record.get("created_at")
    expires = record.get("expires_at")
    media_name = record.get("media_name")
    media_size = record.get("media_size")
    media_digest = record.get("media_sha256")
    mac = record.get("mac")
    if (
        not isinstance(job_id, str) or not JOB_RE.fullmatch(job_id)
        or not isinstance(nonce, str) or not NONCE_RE.fullmatch(nonce)
        or not isinstance(channel, str) or not isinstance(target, str)
        or not isinstance(caption, str)
        or isinstance(created, bool) or not isinstance(created, int)
        or isinstance(expires, bool) or not isinstance(expires, int)
        or not isinstance(media_name, str) or not MEDIA_RE.fullmatch(media_name)
        or isinstance(media_size, bool) or not isinstance(media_size, int)
        or not 0 <= media_size <= policy.max_media_bytes
        or not isinstance(media_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", media_digest)
        or not isinstance(mac, str) or not re.fullmatch(r"[0-9a-f]{64}", mac)
    ):
        raise QueueSecurityError("queue job contains an invalid field")
    _validate_request(policy, channel, target, caption)
    if (
        created > now + 5
        or expires <= now
        or expires <= created
        or expires - created > policy.max_job_age_seconds
    ):
        raise QueueSecurityError("queue job is expired or outside its capability window")
    if not hmac.compare_digest(mac, _canonical_mac(record, policy.key)):
        raise QueueSecurityError("queue job authentication failed")


def _open_verified_media(
    media_fd: int, record: dict[str, object], policy: QueuePolicy
) -> int:
    name = str(record["media_name"])
    path_info = os.stat(name, dir_fd=media_fd, follow_symlinks=False)
    fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=media_fd)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or int(before.st_nlink) != 1
            or int(before.st_uid) != int(os.geteuid())
            or stat.S_IMODE(before.st_mode) & 0o077
            or int(before.st_size) != int(record["media_size"])
            or int(before.st_size) > policy.max_media_bytes
            or (int(path_info.st_dev), int(path_info.st_ino))
            != (int(before.st_dev), int(before.st_ino))
        ):
            raise QueueSecurityError("media snapshot failed identity checks")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 65_536)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(fd)
        if (
            _stable(before) != _stable(after)
            or not hmac.compare_digest(digest.hexdigest(), str(record["media_sha256"]))
        ):
            raise QueueSecurityError("media snapshot digest mismatch")
        os.lseek(fd, 0, os.SEEK_SET)
        os.set_inheritable(fd, True)
        return fd
    except Exception:
        os.close(fd)
        raise


def _fd_path(fd: int) -> str:
    for prefix in ("/proc/self/fd", "/dev/fd"):
        candidate = f"{prefix}/{fd}"
        if os.path.exists(candidate):
            return candidate
    raise QueueSecurityError("descriptor media paths are unavailable")


Sender = Callable[[str, str, str, str, tuple[int, ...]], bool]


def _open_trusted_delivery_cli() -> int:
    """Open one fixed root-controlled OpenClaw entry module and bind its inode."""
    for candidate in (
        Path("/usr/local/lib/node_modules/openclaw/openclaw.mjs"),
        Path("/usr/lib/node_modules/openclaw/openclaw.mjs"),
        Path("/usr/local/bin/openclaw"),
        Path("/usr/bin/openclaw"),
    ):
        try:
            path_info = candidate.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(path_info.st_mode):
            continue
        current = candidate.parent
        trusted_chain = True
        while True:
            try:
                info = current.stat(follow_symlinks=False)
            except OSError:
                trusted_chain = False
                break
            if (
                not stat.S_ISDIR(info.st_mode)
                or int(info.st_uid) != 0
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                trusted_chain = False
                break
            if current.parent == current:
                break
            current = current.parent
        if not trusted_chain:
            continue
        try:
            descriptor = os.open(
                candidate,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
        except OSError:
            continue
        bound = os.fstat(descriptor)
        if (
            not stat.S_ISREG(bound.st_mode)
            or int(bound.st_uid) != 0
            or stat.S_IMODE(bound.st_mode) & 0o022
            or int(bound.st_nlink) != 1
            or (int(path_info.st_dev), int(path_info.st_ino))
            != (int(bound.st_dev), int(bound.st_ino))
            or not (stat.S_IMODE(bound.st_mode) & 0o111)
        ):
            os.close(descriptor)
            continue
        os.set_inheritable(descriptor, True)
        return descriptor
    raise QueueSecurityError(
        "a fixed root-controlled OpenClaw delivery entry is unavailable"
    )


def _open_trusted_node_runtime() -> int:
    """Bind the fixed root-controlled Node runtime used by the stdin adapter."""
    candidate = Path("/usr/bin/node")
    try:
        path_info = candidate.stat(follow_symlinks=False)
        descriptor = os.open(
            candidate,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise QueueSecurityError("the fixed Node delivery runtime is unavailable") from exc
    try:
        bound = os.fstat(descriptor)
        if (
            not stat.S_ISREG(path_info.st_mode)
            or not stat.S_ISREG(bound.st_mode)
            or int(path_info.st_uid) != 0
            or int(bound.st_uid) != 0
            or stat.S_IMODE(path_info.st_mode) & 0o022
            or stat.S_IMODE(bound.st_mode) & 0o022
            or int(bound.st_nlink) != 1
            or (int(path_info.st_dev), int(path_info.st_ino))
            != (int(bound.st_dev), int(bound.st_ino))
            or not (stat.S_IMODE(bound.st_mode) & 0o111)
        ):
            raise QueueSecurityError("the fixed Node delivery runtime is untrusted")
        os.set_inheritable(descriptor, True)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


# The OS-visible Node argv is fixed. The authenticated delivery record crosses
# the final process boundary only through bounded stdin; the adapter constructs
# OpenClaw's in-process argv after startup and disables OpenClaw's argv-copying
# compile-cache respawn path.
OPENCLAW_STDIN_ADAPTER = r"""
import { readFileSync } from "node:fs";
const maximum = 16384;
const payload = readFileSync(0);
if (payload.length > maximum) throw new Error("delivery request exceeds the stdin limit");
const request = JSON.parse(payload.toString("utf8"));
const keys = Object.keys(request).sort().join(",");
if (keys !== "caption,channel,media,target") throw new Error("invalid delivery request schema");
for (const key of ["caption", "channel", "media", "target"]) {
  if (typeof request[key] !== "string") throw new Error("invalid delivery request field");
}
const entry = process.argv[1];
process.argv = [process.execPath, entry, "message", "send", "--channel", request.channel,
  "--target", request.target, "--media", request.media];
if (request.caption) process.argv.push("-m", request.caption);
await import(`file://${entry}`);
""".strip()


def _safe_sender(
    channel: str, target: str, media_path: str, caption: str, pass_fds: tuple[int, ...]
) -> bool:
    executable_fd = _open_trusted_delivery_cli()
    try:
        node_fd = _open_trusted_node_runtime()
        try:
            request = json.dumps(
                {
                    "channel": channel,
                    "target": target,
                    "media": media_path,
                    "caption": caption,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(request) > MAX_DELIVERY_REQUEST_BYTES:
                raise QueueSecurityError("delivery request exceeds the stdin byte limit")
            command = [
                _fd_path(node_fd),
                "--input-type=module",
                "-e",
                OPENCLAW_STDIN_ADAPTER,
                _fd_path(executable_fd),
            ]
            child_env = {
                key: str(os.environ[key])
                for key in ("HOME", "LANG", "LC_ALL", "TZ")
                if os.environ.get(key)
            }
            child_env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
            child_env["NODE_DISABLE_COMPILE_CACHE"] = "1"
            with open(os.devnull, "wb") as output:
                try:
                    result = subprocess.run(
                        command,
                        input=request,
                        stdout=output,
                        stderr=output,
                        env=child_env,
                        pass_fds=(*pass_fds, executable_fd, node_fd),
                        timeout=120,
                        check=False,
                    )
                except (OSError, subprocess.SubprocessError):
                    return False
            return result.returncode == 0
        finally:
            os.close(node_fd)
    finally:
        os.close(executable_fd)


def _result_record(
    record: dict[str, object], policy: QueuePolicy, *, ok: bool, error_code: str, now: int
) -> dict[str, object]:
    result: dict[str, object] = {
        "version": 1,
        "job_id": record["id"],
        "nonce": record["nonce"],
        "media_sha256": record["media_sha256"],
        "status": "ok" if ok else "error",
        "error_code": "" if ok else error_code,
        "completed_at": now,
    }
    result["mac"] = _canonical_mac(result, policy.key)
    return result


def process_once(
    workspace: Path,
    *,
    authority: str | None = None,
    sender: Sender | None = None,
    now: int | None = None,
) -> dict[str, object]:
    policy = load_policy(authority)
    current = int(time.time()) if now is None else int(now)
    delivery = sender or _safe_sender
    workspace_absolute = _absolute(workspace, "runtime workspace")
    try:
        policy.replay_ledger_dir.relative_to(workspace_absolute)
    except ValueError:
        pass
    else:
        raise QueueSecurityError("replay ledger must be outside the agent-writable workspace")
    replay_fd = _open_directory_nofollow(policy.replay_ledger_dir, private=True)
    try:
        return _process_once_with_replay_ledger(
            workspace_absolute,
            policy=policy,
            replay_fd=replay_fd,
            delivery=delivery,
            current=current,
        )
    finally:
        os.close(replay_fd)


def _process_once_with_replay_ledger(
    workspace: Path,
    *,
    policy: QueuePolicy,
    replay_fd: int,
    delivery: Sender,
    current: int,
) -> dict[str, object]:
    with _queue_layout(workspace) as layout:
        names = sorted(
            name for name in os.listdir(layout["queue"])
            if name.endswith(".json") and JOB_RE.fullmatch(name[:-5])
        )
        if not names:
            return {"status": "idle"}
        source_name = names[0]
        job_id = source_name[:-5]
        claim_name = f".{job_id}.claim-{os.getpid()}-{secrets.token_hex(4)}"
        try:
            os.rename(
                source_name, claim_name,
                src_dir_fd=layout["queue"], dst_dir_fd=layout["queue"],
            )
        except FileNotFoundError:
            return {"status": "contended"}
        media_name: str | None = None
        try:
            record = _read_named_json(layout["queue"], claim_name, max_bytes=MAX_JOB_BYTES)
            _verify_job(record, policy, now=current)
            if record["id"] != job_id:
                raise QueueSecurityError("queue job filename does not match its identity")
            media_name = str(record["media_name"])
            used_name = hashlib.sha256(str(record["nonce"]).encode("ascii")).hexdigest() + ".used"
            with _locked_replay_ledger(replay_fd):
                active_markers = _prune_replay_ledger(
                    replay_fd, policy, now=current
                )
                try:
                    os.stat(used_name, dir_fd=replay_fd, follow_symlinks=False)
                except FileNotFoundError:
                    if active_markers >= policy.max_replay_entries:
                        raise QueueSecurityError(
                            "replay ledger reached its configured entry bound"
                        )
                try:
                    _write_exclusive_json(
                        replay_fd,
                        used_name,
                        {"version": 1, "job_id": job_id, "used_at": current},
                    )
                except FileExistsError:
                    result = _result_record(
                        record, policy, ok=False, error_code="replay", now=current
                    )
                    try:
                        _write_exclusive_json(layout["queue"], f"{job_id}.result", result)
                    except FileExistsError:
                        pass
                    return {"status": "replay", "job_id": job_id}
            media_descriptor = _open_verified_media(layout["media"], record, policy)
            try:
                ok = bool(
                    delivery(
                        str(record["channel"]), str(record["target"]),
                        _fd_path(media_descriptor), str(record["caption"]),
                        (media_descriptor,),
                    )
                )
            finally:
                os.close(media_descriptor)
            result = _result_record(
                record,
                policy,
                ok=ok,
                error_code="delivery_failed",
                now=current,
            )
            _write_exclusive_json(layout["queue"], f"{job_id}.result", result)
            os.fsync(layout["queue"])
            return {"status": "ok" if ok else "error", "job_id": job_id}
        except QueueSecurityError:
            return {"status": "rejected", "job_id": job_id}
        finally:
            try:
                os.unlink(claim_name, dir_fd=layout["queue"])
            except FileNotFoundError:
                pass
            if media_name and MEDIA_RE.fullmatch(media_name):
                try:
                    os.unlink(media_name, dir_fd=layout["media"])
                except FileNotFoundError:
                    pass


def read_result(
    workspace: Path,
    *,
    job_id: str,
    nonce: str,
    media_sha256: str,
    authority: str | None = None,
) -> dict[str, object] | None:
    if not JOB_RE.fullmatch(job_id) or not NONCE_RE.fullmatch(nonce):
        raise QueueSecurityError("result expectation is invalid")
    policy = load_policy(authority)
    with _queue_layout(workspace) as layout:
        name = f"{job_id}.result"
        try:
            record = _read_named_json(layout["queue"], name, max_bytes=MAX_RESULT_BYTES)
        except FileNotFoundError:
            return None
        if (
            set(record) != RESULT_KEYS
            or record.get("version") != 1
            or record.get("job_id") != job_id
            or record.get("nonce") != nonce
            or record.get("media_sha256") != media_sha256
            or record.get("status") not in {"ok", "error"}
            or not isinstance(record.get("error_code"), str)
            or isinstance(record.get("completed_at"), bool)
            or not isinstance(record.get("completed_at"), int)
            or not isinstance(record.get("mac"), str)
            or not hmac.compare_digest(str(record["mac"]), _canonical_mac(record, policy.key))
        ):
            raise QueueSecurityError("send queue result authentication failed")
        return record


def cleanup_job(workspace: Path, job_id: str) -> None:
    if not JOB_RE.fullmatch(job_id):
        raise QueueSecurityError("cleanup job identity is invalid")
    with _queue_layout(workspace) as layout:
        for name in (f"{job_id}.json", f"{job_id}.result"):
            try:
                os.unlink(name, dir_fd=layout["queue"])
            except FileNotFoundError:
                pass
        for name in os.listdir(layout["media"]):
            if name == job_id or name.startswith(job_id + "."):
                try:
                    os.unlink(name, dir_fd=layout["media"])
                except FileNotFoundError:
                    pass


def _read_submit_request_stdin() -> dict[str, str]:
    binary = getattr(sys.stdin, "buffer", None)
    payload = (
        binary.read(MAX_REQUEST_BYTES + 1)
        if binary is not None
        else sys.stdin.read(MAX_REQUEST_BYTES + 1).encode("utf-8")
    )
    if len(payload) > MAX_REQUEST_BYTES:
        raise QueueSecurityError("file-delivery request exceeds the stdin byte limit")
    try:
        request = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_strict_json_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QueueSecurityError("file-delivery stdin is not valid UTF-8 JSON") from exc
    if (
        not isinstance(request, dict)
        or set(request) != REQUEST_KEYS
        or any(not isinstance(request.get(key), str) for key in REQUEST_KEYS)
    ):
        raise QueueSecurityError("file-delivery stdin request schema is invalid")
    return {key: str(request[key]) for key in REQUEST_KEYS}


def _submit(args: argparse.Namespace) -> int:
    published: dict[str, object] | None = None
    try:
        request = _read_submit_request_stdin()
        published = publish_job(
            Path(args.workspace),
            channel=request["channel"],
            target=request["target"],
            media=Path(request["media"]),
            caption=request["caption"],
        )
        deadline = time.monotonic() + int(args.timeout)
        while time.monotonic() < deadline:
            result = read_result(
                Path(args.workspace),
                job_id=str(published["job_id"]),
                nonce=str(published["nonce"]),
                media_sha256=str(published["media_sha256"]),
            )
            if result is not None:
                public = {
                    "status": result["status"],
                    "channel": request["channel"],
                    "job_id": published["job_id"],
                }
                if result["status"] != "ok":
                    public["error_code"] = result["error_code"]
                print(json.dumps(public, separators=(",", ":")))
                return 0 if result["status"] == "ok" else 1
            time.sleep(1)
        print(json.dumps({"status": "error", "message": "Send queue timeout", "channel": request["channel"], "job_id": published["job_id"]}, separators=(",", ":")))
        return 1
    except QueueSecurityError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, separators=(",", ":")))
        return 2
    finally:
        if published is not None:
            cleanup_job(Path(args.workspace), str(published["job_id"]))


def _worker(args: argparse.Namespace) -> int:
    while True:
        try:
            result = process_once(Path(args.workspace))
        except QueueSecurityError as exc:
            print(f"send queue worker stopped: {exc}", file=sys.stderr)
            return 2
        if args.once:
            print(json.dumps(result, separators=(",", ":")))
            return 0 if result["status"] in {"idle", "contended", "ok"} else 1
        if result["status"] == "idle":
            time.sleep(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    submit = sub.add_parser("submit")
    submit.add_argument("--workspace", required=True)
    submit.add_argument("--request-json-stdin", action="store_true", required=True)
    submit.add_argument("--timeout", type=int, choices=range(1, 301), default=60)
    submit.set_defaults(func=_submit)
    worker = sub.add_parser("worker")
    worker.add_argument("--workspace", required=True)
    worker.add_argument("--once", action="store_true")
    worker.set_defaults(func=_worker)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
