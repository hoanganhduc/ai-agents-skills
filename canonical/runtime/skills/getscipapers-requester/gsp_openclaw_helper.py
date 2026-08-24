#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import hashlib
import html
import http.client
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote_plus, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

DOI_RE = re.compile(
    r"(?<!\w)10\.\d{4,9}/[-._;()/:A-Za-z0-9]+(?!\w)",
)
DOI_FULL_RE = re.compile(
    r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+",
)
DATACITE_ARXIV_DOI_RE = re.compile(
    r"10\.48550/arxiv\.(\d{4}\.\d{4,5})(?:v\d+)?",
    re.IGNORECASE | re.ASCII,
)
ISBN_FULL_RE = re.compile(r"[0-9Xx]+(?:[- ][0-9Xx]+)*")
ISBN_CAND_RE = re.compile(
    r"(?<!\w)(?:97[89][- ]?)?[0-9][0-9 -]{8,16}[0-9Xx](?!\w)"
)
DEFAULT_TIMEOUT = 45
UA = "openclaw-getscipapers-skill/2.0"
MAX_WATCH_STORE_BYTES = 32 * 1024 * 1024
MAX_WATCH_ITEMS = 10_000
MAX_WATCH_NOTES = 100
MAX_WATCH_SENT_HASHES = 10_000
MAX_WATCH_LABEL_CHARS = 500
MAX_WATCH_IDENTIFIER_CHARS = 500
MAX_WATCH_NOTE_CHARS = 2_000
MAX_WATCH_SERVICES = 20
MAX_WATCH_SERVICE_CHARS = 100
MAX_WATCH_TIMESTAMP = 10_000_000_000
MAX_WATCH_FUTURE_SKEW_SECONDS = 5 * 60
MAX_WATCH_CHECK_COUNT = 1_000_000_000
WATCH_KINDS = frozenset({"paper", "book"})
WATCH_IDENTIFIER_TYPES = frozenset({"doi", "isbn", "search"})
WATCH_STATUSES = frozenset({"active", "waiting", "posted", "found", "expired", "failed"})
WATCH_LOCK_TIMEOUT_SECONDS = 60.0
MAX_SETTINGS_CONFIG_BYTES = 1024 * 1024
DEFAULT_TELEGRAM_MAX_BYTES = 50 * 1024 * 1024
MAX_TELEGRAM_MAX_BYTES = 2 * 1024 * 1024 * 1024
MAX_METADATA_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_METADATA_RESPONSE_SECONDS = 60.0
_HTTP_WORKER_COMMAND = "__bounded-metadata-http-worker"
_MAX_HTTP_WORKER_SPEC_BYTES = 16 * 1024
_MAX_HTTP_WORKER_ERROR_BYTES = 2_000
MAX_METADATA_RESULTS = 50
MAX_METADATA_QUERY_CHARS = 2_000
MAX_TEXT_SOURCE_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_LINES = 3_000
MAX_MANIFEST_ITEMS = 3_000
MAX_MANIFEST_RESOLUTION_LINES = 100
MAX_MANIFEST_OUTPUT_BYTES = 16 * 1024 * 1024
CROSSREF_ORIGIN = ("https", "api.crossref.org", 443)
GOOGLE_BOOKS_ORIGIN = ("https", "www.googleapis.com", 443)
OPENLIBRARY_ORIGIN = ("https", "openlibrary.org", 443)
METADATA_TAG_RE = re.compile(r"<[^>]*>", re.DOTALL)
METADATA_SPACE_RE = re.compile(r"\s+")


class MetadataSourceError(RuntimeError):
    """A metadata service violated the bounded fixed-origin contract."""


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _reject_duplicate_json_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object member: {key}")
        value[key] = item
    return value


def _is_link_like_stat(info) -> bool:
    """Treat POSIX symlinks and Windows reparse points alike."""
    return bool(stat.S_ISLNK(info.st_mode)) or bool(
        int(getattr(info, "st_file_attributes", 0))
        & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    )


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def default_host_workspace() -> Path:
    env_workspace = os.environ.get("AAS_RUNTIME_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE")
    if env_workspace:
        return Path(env_workspace)
    return Path(__file__).resolve().parents[2]


HOST_WORKSPACE = default_host_workspace()


def read_json(path: Path, default: Any) -> Any:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return default
    except OSError:
        return default
    if (
        _is_link_like_stat(info)
        or not stat.S_ISREG(info.st_mode)
        or info.st_size > MAX_SETTINGS_CONFIG_BYTES
    ):
        return default
    try:
        payload = path.read_bytes()
        if len(payload) > MAX_SETTINGS_CONFIG_BYTES:
            return default
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except Exception:
        return default


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_fd = -1
    stage_name = None
    try:
        file_fd, stage_name = tempfile.mkstemp(
            prefix=f"{path.name}.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(file_fd, "w", encoding="utf-8", newline="") as handle:
            file_fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(stage_name, path)
        stage_name = None
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if stage_name is not None:
            try:
                os.unlink(stage_name)
            except FileNotFoundError:
                pass


def write_json(path: Path, data: Any) -> None:
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))


def _validate_watch_text(
    value: Any,
    *,
    label: str,
    limit: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"watch store item {label} is invalid")
    if any(
        char in "\r\n"
        or unicodedata.category(char) in {"Cc", "Cf", "Cs"}
        for char in value
    ):
        raise ValueError(f"watch store item {label} contains control characters")
    text = value.strip()
    if not text and not allow_empty:
        raise ValueError(f"watch store item {label} is invalid")
    return text


def _validate_watch_store(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("watch store must be an object with an items list")
    items = data["items"]
    if len(items) > MAX_WATCH_ITEMS:
        raise ValueError(f"watch store exceeds the {MAX_WATCH_ITEMS}-item limit")
    seen_ids: set[str] = set()
    latest_event_timestamp = int(time.time()) + MAX_WATCH_FUTURE_SKEW_SECONDS
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("watch store items must be objects")
        for field, limit in (("id", 300), ("watch_key", 100)):
            _validate_watch_text(item.get(field), label=field, limit=limit)
        item_id = item.get("id")
        if item_id in seen_ids:
            raise ValueError("watch store contains duplicate item ids")
        seen_ids.add(item_id)
        if item.get("kind") not in WATCH_KINDS:
            raise ValueError("watch store item kind is invalid")
        if item.get("identifier_type") not in WATCH_IDENTIFIER_TYPES:
            raise ValueError("watch store item identifier_type is invalid")
        if item.get("status") not in WATCH_STATUSES:
            raise ValueError("watch store item status is invalid")
        for field, limit in (
            ("label", MAX_WATCH_LABEL_CHARS),
            ("notes", MAX_WATCH_NOTE_CHARS),
            ("last_note", MAX_WATCH_NOTE_CHARS),
        ):
            value = item.get(field)
            if value is not None:
                _validate_watch_text(
                    value,
                    label=field,
                    limit=limit,
                    allow_empty=True,
                )
        _validate_watch_text(
            item.get("identifier"),
            label="identifier",
            limit=MAX_WATCH_IDENTIFIER_CHARS,
        )
        identifier = item["identifier"]
        identifier_type = item["identifier_type"]
        if identifier_type == "doi":
            if valid_doi(identifier) != identifier:
                raise ValueError("watch store item DOI is not canonical")
        elif identifier_type == "isbn":
            if valid_isbn(identifier) != identifier:
                raise ValueError("watch store item ISBN is not canonical")
        else:
            try:
                canonical_search = _bounded_search_query(identifier)
            except ValueError as exc:
                raise ValueError(
                    "watch store item search identifier is invalid"
                ) from exc
            if canonical_search != identifier:
                raise ValueError(
                    "watch store item search identifier is not canonical"
                )
        services = item.get("services")
        if (
            not isinstance(services, list)
            or len(services) > MAX_WATCH_SERVICES
        ):
            raise ValueError("watch store item services are invalid or oversized")
        for service in services:
            _validate_watch_text(
                service,
                label="service",
                limit=MAX_WATCH_SERVICE_CHARS,
            )
        if item["watch_key"] not in {
            _watch_key(item),
            _legacy_watch_key(item),
        }:
            raise ValueError("watch store item watch_key does not match identity")
        for field in ("created_at", "updated_at", "check_count"):
            value = item.get(field)
            maximum = (
                MAX_WATCH_CHECK_COUNT
                if field == "check_count"
                else MAX_WATCH_TIMESTAMP
            )
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= maximum
            ):
                raise ValueError(f"watch store item {field} is invalid")
        for field in ("deadline_ts", "last_checked_at"):
            value = item.get(field)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= MAX_WATCH_TIMESTAMP
            ):
                raise ValueError(f"watch store item {field} is invalid")
        notes = item.get("notes_history", [])
        if not isinstance(notes, list) or len(notes) > MAX_WATCH_NOTES:
            raise ValueError("watch store notes history is invalid or oversized")
        for note in notes:
            if not isinstance(note, dict):
                raise ValueError("watch store notes history entries must be objects")
            timestamp = note.get("ts")
            note_text = note.get("note")
            if (
                isinstance(timestamp, bool)
                or not isinstance(timestamp, int)
                or not 0 <= timestamp <= MAX_WATCH_TIMESTAMP
            ):
                raise ValueError(
                    "watch store notes history entries are invalid or oversized"
                )
            _validate_watch_text(
                note_text,
                label="notes history entry",
                limit=MAX_WATCH_NOTE_CHARS,
                allow_empty=True,
            )
        hashes = item.get("sent_file_hashes")
        if (
            not isinstance(hashes, list)
            or len(hashes) > MAX_WATCH_SENT_HASHES
        ):
            raise ValueError("watch store sent-file hashes are invalid or oversized")
        for digest in hashes:
            _validate_watch_text(
                digest,
                label="sent-file hash",
                limit=300,
                allow_empty=True,
            )
        created_at = item["created_at"]
        updated_at = item["updated_at"]
        if created_at > latest_event_timestamp or updated_at > latest_event_timestamp:
            raise ValueError("watch store item timestamps are in the future")
        if updated_at < created_at:
            raise ValueError("watch store item timestamps are inconsistent")
        deadline = item.get("deadline_ts")
        if deadline is not None and deadline < created_at:
            raise ValueError("watch store item deadline is inconsistent")
        last_checked = item.get("last_checked_at")
        if last_checked is not None and last_checked > latest_event_timestamp:
            raise ValueError("watch store item check timestamp is in the future")
        if last_checked is not None and not created_at <= last_checked <= updated_at:
            raise ValueError("watch store item check timestamp is inconsistent")
        if any(note["ts"] > latest_event_timestamp for note in notes):
            raise ValueError("watch store item notes timestamps are in the future")
        if any(
            not created_at <= note["ts"] <= updated_at
            for note in notes
        ):
            raise ValueError("watch store item notes timestamps are inconsistent")
    return data


def read_watch_store(path: Path) -> dict[str, Any]:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return {"items": []}
    except OSError as exc:
        raise SystemExit("watch store metadata is unreadable") from exc
    if (
        _is_link_like_stat(info)
        or not stat.S_ISREG(info.st_mode)
        or info.st_size > MAX_WATCH_STORE_BYTES
    ):
        raise SystemExit("watch store is unsafe or oversized")
    try:
        payload = path.read_bytes()
        if len(payload) > MAX_WATCH_STORE_BYTES:
            raise ValueError("watch store exceeds its byte limit")
        data = json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
        return _validate_watch_store(data)
    except (UnicodeError, json.JSONDecodeError, OSError, RecursionError, ValueError) as exc:
        raise SystemExit("watch store is unreadable or invalid") from exc


def write_watch_store(path: Path, data: Any) -> None:
    try:
        validated = _validate_watch_store(data)
        content = json.dumps(
            validated,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ) + "\n"
    except (RecursionError, TypeError, ValueError) as exc:
        raise SystemExit("watch store update is invalid") from exc
    if len(content.encode("utf-8")) > MAX_WATCH_STORE_BYTES:
        raise SystemExit("watch store update exceeds its byte limit")

    write_text_atomic(path, content)


@contextmanager
def watch_store_lock(
    settings: "Settings", timeout: float = WATCH_LOCK_TIMEOUT_SECONDS
):
    lock_path = settings.state_dir / "watches.json.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = os.lstat(lock_path)
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise SystemExit("watch store lock metadata is unreadable") from exc
    if existing is not None and (
        _is_link_like_stat(existing) or not stat.S_ISREG(existing.st_mode)
    ):
        raise SystemExit("watch store lock path is unsafe")

    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        file_fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise SystemExit("watch store lock is unavailable") from exc
    try:
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise SystemExit("watch store lock path is not a regular file")
        if os.fstat(file_fd).st_size == 0:
            os.write(file_fd, b"\0")
            os.fsync(file_fd)
        deadline = time.monotonic() + max(0.0, float(timeout))
        if os.name == "nt":
            import msvcrt

            def try_lock() -> None:
                os.lseek(file_fd, 0, os.SEEK_SET)
                msvcrt.locking(file_fd, msvcrt.LK_NBLCK, 1)

            def unlock() -> None:
                os.lseek(file_fd, 0, os.SEEK_SET)
                msvcrt.locking(file_fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def try_lock() -> None:
                fcntl.flock(file_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def unlock() -> None:
                fcntl.flock(file_fd, fcntl.LOCK_UN)

        while True:
            try:
                try_lock()
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise SystemExit("timed out waiting for the watch store lock") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            unlock()
    finally:
        os.close(file_fd)


def json_print(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))


@dataclass
class Settings:
    download_dir: Path
    state_dir: Path
    manifest_dir: Path
    telegram_max_bytes: int


def _load_settings_data(
    cfg_path: Path, *, explicit: bool, strict: bool
) -> dict[str, Any]:
    if not strict:
        data = read_json(cfg_path, {})
        return data if isinstance(data, dict) else {}
    try:
        info = os.lstat(cfg_path)
    except FileNotFoundError as exc:
        if explicit:
            raise SystemExit("explicit getscipapers config is missing") from exc
        return {}
    except OSError as exc:
        raise SystemExit("getscipapers config metadata is unreadable") from exc
    if (
        _is_link_like_stat(info)
        or not stat.S_ISREG(info.st_mode)
        or info.st_size > MAX_SETTINGS_CONFIG_BYTES
    ):
        raise SystemExit("getscipapers config is unsafe or oversized")
    try:
        payload = cfg_path.read_bytes()
        if len(payload) > MAX_SETTINGS_CONFIG_BYTES:
            raise ValueError("config exceeds its byte limit")
        data = json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeError, json.JSONDecodeError, OSError, RecursionError, ValueError) as exc:
        raise SystemExit("getscipapers config is unreadable or invalid") from exc
    if not isinstance(data, dict):
        raise SystemExit("getscipapers config must be a JSON object")
    for field in ("download_dir", "state_dir", "manifest_dir"):
        value = data.get(field)
        if value is not None and not isinstance(value, str):
            raise SystemExit(f"getscipapers config field {field} must be a string")
    if "telegram_max_bytes" in data:
        value = data["telegram_max_bytes"]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= MAX_TELEGRAM_MAX_BYTES
        ):
            raise SystemExit(
                "getscipapers config field telegram_max_bytes must be an integer "
                f"from 1 to {MAX_TELEGRAM_MAX_BYTES}"
            )
    return data


def load_settings(*, strict_config: bool = False) -> Settings:
    config = os.environ.get("GETSCIPAPERS_SKILL_CONFIG")
    if config:
        cfg_path = Path(config)
    else:
        if HOST_WORKSPACE.is_dir():
            cfg_path = HOST_WORKSPACE / "data" / "research" / "getscipapers_bot" / "state" / "config.json"
        else:
            cfg_path = Path(__file__).resolve().parent.parent.parent / "data" / "research" / "getscipapers_bot" / "state" / "config.json"
    data = _load_settings_data(
        cfg_path,
        explicit=bool(config),
        strict=strict_config,
    )
    # sanitize host paths to container-visible /workspace only when running inside a container
    _in_container = Path("/workspace").is_dir() and not HOST_WORKSPACE.is_dir()
    def _sanitize(p):
        if not p or not _in_container:
            return p
        s = str(p)
        host_runtime = str(HOST_WORKSPACE.parent).replace("\\", "/")
        s_norm = s.replace("\\", "/")
        if s_norm.startswith(host_runtime):
            return s_norm.replace(host_runtime, "/workspace")
        return s
    default_base_raw = os.environ.get("OPENCLAW_WORKSPACE_OVERRIDE")
    default_base = Path(default_base_raw or ("/workspace" if _in_container else str(HOST_WORKSPACE)))
    base_norm = str(default_base).replace("\\", "/").rstrip("/")
    if base_norm.endswith("/data"):
        default_root = default_base / "research" / "getscipapers_bot"
    else:
        default_root = default_base / "data" / "research" / "getscipapers_bot"
    config_base = cfg_path.parent.resolve()

    def _configured_path(field: str, fallback: Any) -> Path:
        configured = data.get(field)
        path = Path(_sanitize(configured if configured else fallback))
        if configured and not path.is_absolute():
            path = config_base / path
        return path

    download_dir = _configured_path(
        "download_dir",
        os.environ.get("GETSCIPAPERS_DOWNLOAD_DIR") or (default_root / "downloads"),
    )
    state_dir = _configured_path(
        "state_dir",
        os.environ.get("GETSCIPAPERS_STATE_DIR") or (default_root / "state"),
    )
    manifest_dir = _configured_path("manifest_dir", state_dir / "manifests")
    raw_telegram_max_bytes = data.get("telegram_max_bytes")
    if raw_telegram_max_bytes is None:
        raw_telegram_max_bytes = os.environ.get("SCI_PAPERS_TELEGRAM_MAX_BYTES")
    if raw_telegram_max_bytes is None:
        raw_telegram_max_bytes = DEFAULT_TELEGRAM_MAX_BYTES
    try:
        if isinstance(raw_telegram_max_bytes, bool):
            raise ValueError("boolean byte limit")
        telegram_max_bytes = int(raw_telegram_max_bytes)
        if not 1 <= telegram_max_bytes <= MAX_TELEGRAM_MAX_BYTES:
            raise ValueError("byte limit out of range")
    except (TypeError, ValueError, OverflowError) as exc:
        if strict_config:
            raise SystemExit(
                "telegram byte limit must be an integer from 1 to "
                f"{MAX_TELEGRAM_MAX_BYTES}"
            ) from exc
        telegram_max_bytes = DEFAULT_TELEGRAM_MAX_BYTES
    return Settings(download_dir=download_dir, state_dir=state_dir, manifest_dir=manifest_dir, telegram_max_bytes=telegram_max_bytes)


def fallback_settings(settings: Settings) -> Settings:
    root = Path(os.environ.get("GETSCIPAPERS_FALLBACK_ROOT") or (Path(tempfile.gettempdir()) / "getscipapers_bot"))
    state_dir = root / "state"
    return Settings(
        download_dir=root / "downloads",
        state_dir=state_dir,
        manifest_dir=state_dir / "manifests",
        telegram_max_bytes=settings.telegram_max_bytes,
    )


def _admit_storage_directory(directory: Path, *, label: str) -> None:
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        directory.mkdir(parents=True, exist_ok=True)
        info = os.lstat(directory)
    if _is_link_like_stat(info) or not stat.S_ISDIR(info.st_mode):
        raise OSError(f"{label} is unsafe")


def ensure_settings_dirs(
    settings: Settings,
    *,
    allow_fallback: bool = True,
    required: tuple[str, ...] | None = None,
) -> Settings:
    required_fields = list(
        required or ("download_dir", "state_dir", "manifest_dir")
    )
    if "manifest_dir" in required_fields:
        try:
            settings.manifest_dir.relative_to(settings.state_dir)
        except ValueError:
            pass
        else:
            required_fields.insert(0, "state_dir")
    required_fields = list(dict.fromkeys(required_fields))
    try:
        for field in required_fields:
            directory = getattr(settings, field)
            _admit_storage_directory(
                directory,
                label=f"configured {field}",
            )
        return settings
    except OSError as exc:
        if not allow_fallback:
            raise SystemExit(
                "configured getscipapers storage is unavailable"
            ) from exc
        fallback = fallback_settings(settings)
        _admit_storage_directory(
            fallback.state_dir.parent,
            label="fallback storage root",
        )
        for field in ("download_dir", "state_dir", "manifest_dir"):
            _admit_storage_directory(
                getattr(fallback, field),
                label=f"fallback {field}",
            )
        return fallback


def norm_doi(raw: str) -> str:
    raw = raw.strip()
    prefixes = [
        "doi:",
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
    ]
    for p in prefixes:
        if raw.lower().startswith(p):
            raw = raw[len(p):]
            break
    return raw.strip()


def valid_doi(raw: Any) -> str:
    if not isinstance(raw, str) or len(raw) > MAX_WATCH_IDENTIFIER_CHARS:
        return ""
    value = norm_doi(raw)
    if not DOI_FULL_RE.fullmatch(value):
        return ""
    canonical = value.lower()
    arxiv_match = DATACITE_ARXIV_DOI_RE.fullmatch(canonical)
    if arxiv_match:
        return f"10.48550/arXiv.{arxiv_match.group(1)}"
    return canonical


def norm_isbn(raw: str) -> str:
    return raw.replace("-", "").replace(" ", "").upper()


def isbn10_checksum_ok(code: str) -> bool:
    if not re.fullmatch(r"\d{9}[\dX]", code):
        return False
    total = sum((10 - i) * (10 if ch == "X" else int(ch)) for i, ch in enumerate(code))
    return total % 11 == 0


def isbn13_checksum_ok(code: str) -> bool:
    if not re.fullmatch(r"\d{13}", code):
        return False
    total = 0
    for i, ch in enumerate(code[:12]):
        total += int(ch) * (1 if i % 2 == 0 else 3)
    check = (10 - (total % 10)) % 10
    return check == int(code[-1])


def valid_isbn(raw: Any) -> str:
    if not isinstance(raw, str) or len(raw) > MAX_WATCH_IDENTIFIER_CHARS:
        return ""
    if not ISBN_FULL_RE.fullmatch(raw):
        return ""
    value = norm_isbn(raw)
    if len(value) == 10 and isbn10_checksum_ok(value):
        return value
    if len(value) == 13 and isbn13_checksum_ok(value):
        return value
    return ""


def extract_isbns(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in ISBN_CAND_RE.finditer(text):
        token = valid_isbn(m.group(0))
        if token and token not in seen:
            out.append(token)
            seen.add(token)
    return out


def extract_dois(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    exact = valid_doi(text)
    if exact:
        return [exact]
    for m in DOI_RE.finditer(text):
        candidate = m.group(0).rstrip(".,;")
        while candidate.endswith(")") and candidate.count(")") > candidate.count("("):
            candidate = candidate[:-1]
        token = valid_doi(candidate)
        if token and token not in seen:
            out.append(token)
            seen.add(token)
    return out


def _decode_bounded_source(payload: bytes, *, label: str) -> str:
    if len(payload) > MAX_TEXT_SOURCE_BYTES:
        raise ValueError(f"{label} exceeds the {MAX_TEXT_SOURCE_BYTES}-byte limit")
    return payload.decode("utf-8", errors="replace")


def _read_bounded_stdin() -> str:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    payload = stream.read(MAX_TEXT_SOURCE_BYTES + 1)
    if isinstance(payload, str):
        payload = payload.encode("utf-8", errors="replace")
    if not isinstance(payload, bytes):
        raise ValueError("stdin did not provide text or bytes")
    return _decode_bounded_source(payload, label="stdin source")


def _read_bounded_regular_file(path: Path, initial: os.stat_result) -> str:
    if _is_link_like_stat(initial) or not stat.S_ISREG(initial.st_mode):
        raise ValueError("source path must be a regular, non-symlink file")
    if initial.st_size > MAX_TEXT_SOURCE_BYTES:
        raise ValueError(
            f"source file exceeds the {MAX_TEXT_SOURCE_BYTES}-byte limit"
        )
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    file_fd = os.open(path, flags)
    try:
        opened = os.fstat(file_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size > MAX_TEXT_SOURCE_BYTES
            or (initial.st_dev, initial.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise ValueError("source file changed or exceeds its byte limit")
        chunks = []
        total = 0
        while True:
            chunk = os.read(
                file_fd,
                min(64 * 1024, MAX_TEXT_SOURCE_BYTES + 1 - total),
            )
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_TEXT_SOURCE_BYTES:
                raise ValueError(
                    f"source file exceeds the {MAX_TEXT_SOURCE_BYTES}-byte limit"
                )
            chunks.append(chunk)
    finally:
        os.close(file_fd)
    return _decode_bounded_source(b"".join(chunks), label="source file")


def read_text_source(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("text source must be a string")
    if value == "-":
        return _read_bounded_stdin()
    try:
        initial = os.lstat(Path(value))
    except FileNotFoundError:
        initial = None
    except ValueError:
        initial = None
    except OSError as exc:
        if exc.errno == errno.ENAMETOOLONG:
            initial = None
        else:
            raise ValueError(f"source path cannot be inspected: {exc}") from exc
    if initial is not None:
        return _read_bounded_regular_file(Path(value), initial)
    return _decode_bounded_source(
        value.encode("utf-8", errors="replace"),
        label="inline source",
    )


def _bounded_search_limit(limit: int) -> int:
    if isinstance(limit, bool):
        raise ValueError("metadata result limit must be an integer")
    try:
        value = int(limit)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("metadata result limit must be an integer") from exc
    return max(0, min(value, MAX_METADATA_RESULTS))


def _bounded_search_query(query: str) -> str:
    if not isinstance(query, str):
        raise ValueError("metadata query must be text")
    value = query.strip()
    if not value or len(value) > MAX_METADATA_QUERY_CHARS or any(
        char in "\r\n\0" for char in value
    ):
        raise ValueError("metadata query is empty, oversized, or multiline")
    return value


def _metadata_text(value: Any, limit: int = 2_000) -> str:
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKC", html.unescape(value))
    text = METADATA_TAG_RE.sub(" ", text)
    text = "".join(
        " " if unicodedata.category(char) in {"Cc", "Cf", "Cs"} else char
        for char in text
    )
    return METADATA_SPACE_RE.sub(" ", text).strip()[:limit]


def _metadata_year(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1_000 <= value <= 3_000 else None


def _metadata_score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1_000_000.0:
        return None
    return score


def _metadata_authors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        text
        for raw in value[:100]
        if (text := _metadata_text(raw, 1_000))
    ]


def _sanitize_resolver_candidate(
    value: Any,
    score_type: str,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if score_type == "paper":
        doi = valid_doi(value.get("doi"))
        if not doi:
            return None
        return {
            "doi": doi,
            "title": _metadata_text(value.get("title")),
            "container": _metadata_text(value.get("container")),
            "authors": _metadata_authors(value.get("authors")),
            "year": _metadata_year(value.get("year")),
            "type": _metadata_text(value.get("type"), 100),
            "score": _metadata_score(value.get("score")),
        }
    if score_type == "book":
        raw_isbns = value.get("isbn")
        isbns = []
        for raw in raw_isbns[:20] if isinstance(raw_isbns, list) else []:
            isbn = valid_isbn(raw)
            if isbn and isbn not in isbns:
                isbns.append(isbn)
        if not isbns:
            return None
        return {
            "title": _metadata_text(value.get("title")),
            "authors": _metadata_authors(value.get("authors")),
            "year": _metadata_year(value.get("year")),
            "publishedDate": _metadata_text(value.get("publishedDate"), 100),
            "isbn": isbns[:5],
            "publisher": _metadata_text(value.get("publisher"), 1_000),
        }
    return None


def _sanitize_manifest_identifier_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    identifier_type = value.get("identifier_type")
    if identifier_type == "doi":
        identifier = valid_doi(value.get("identifier"))
    elif identifier_type == "isbn":
        identifier = valid_isbn(value.get("identifier"))
    else:
        return None
    if not identifier:
        return None

    out: dict[str, Any] = {
        "identifier_type": identifier_type,
        "identifier": identifier,
    }
    for field, limit in (
        ("source", 100),
        ("reason", 100),
        ("title", 2_000),
        ("container", 2_000),
        ("publisher", 1_000),
        ("type", 100),
        ("publishedDate", 100),
    ):
        if field in value:
            out[field] = _metadata_text(value.get(field), limit)
    confidence = _metadata_text(value.get("confidence"), 20)
    out["confidence"] = (
        confidence
        if confidence in {"very_high", "high", "medium", "low"}
        else "low"
    )
    if "authors" in value:
        out["authors"] = _metadata_authors(value.get("authors"))
    if "year" in value:
        raw_year = value.get("year")
        year = _metadata_year(raw_year)
        out["year"] = year if year is not None else _metadata_text(raw_year, 100)
    score = _metadata_score(value.get("score"))
    if score is not None:
        out["score"] = score
    return out


def _sanitize_manifest_ranked_identifiers(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out = []
    for raw in value[:5]:
        if (candidate := _sanitize_manifest_identifier_summary(raw)) is not None:
            out.append(candidate)
    return out


def _http_json_bytes_in_process(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    *,
    expected_origin: tuple[str, str, int],
    opener=None,
    max_bytes: int = MAX_METADATA_RESPONSE_BYTES,
) -> bytes:
    try:
        parsed = urlsplit(url)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("userinfo is forbidden")
        port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
        origin = (parsed.scheme.casefold(), (parsed.hostname or "").casefold(), port)
    except ValueError as exc:
        raise MetadataSourceError("metadata URL is invalid") from exc
    if origin != expected_origin:
        raise MetadataSourceError("metadata URL is outside its fixed HTTPS origin")
    if max_bytes <= 0:
        raise ValueError("metadata response limit must be positive")
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    client = opener or build_opener(_NoRedirectHandler())
    try:
        response = client.open(req, timeout=timeout)
    except HTTPError as exc:
        try:
            exc.close()
        except OSError:
            pass
        raise MetadataSourceError(
            f"metadata service refused HTTP status {exc.code}"
        ) from exc
    with response:
        status_code = getattr(response, "status", None)
        if status_code is None:
            status_code = response.getcode()
        if not 200 <= int(status_code) < 300:
            raise MetadataSourceError(
                f"metadata service refused HTTP status {status_code}"
            )
        raw_length = response.headers.get("Content-Length")
        if raw_length is not None:
            try:
                declared = int(raw_length)
            except (TypeError, ValueError) as exc:
                raise MetadataSourceError(
                    "metadata service returned an invalid Content-Length"
                ) from exc
            if declared < 0 or declared > max_bytes:
                raise MetadataSourceError(
                    f"metadata response exceeds the {max_bytes}-byte limit"
                )
        chunks = []
        total = 0
        try:
            while True:
                # ``HTTPResponse.read`` enforces declared framing and raises
                # ``IncompleteRead`` for a short Content-Length body. Using
                # ``read1`` here can turn the same truncation into plain EOF.
                chunk = response.read(min(64 * 1024, max_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise MetadataSourceError(
                        f"metadata response exceeds the {max_bytes}-byte limit"
                    )
                chunks.append(chunk)
        except http.client.HTTPException as exc:
            raise MetadataSourceError("metadata response framing is incomplete") from exc
        if raw_length is not None and total != declared:
            raise MetadataSourceError("metadata response framing is incomplete")
    return b"".join(chunks)


def _metadata_worker_spec_bytes(spec: dict) -> bytes:
    try:
        payload = json.dumps(
            spec,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MetadataSourceError(
            "metadata worker request is not serializable"
        ) from exc
    if len(payload) > _MAX_HTTP_WORKER_SPEC_BYTES:
        raise MetadataSourceError("metadata worker request is oversized")
    return payload


def _run_metadata_http_worker(
    spec: dict,
    *,
    deadline_seconds: float,
    max_bytes: int,
) -> bytes:
    try:
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), _HTTP_WORKER_COMMAND],
            input=_metadata_worker_spec_bytes(spec),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=deadline_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MetadataSourceError(
            "metadata response exceeded its wall-clock deadline"
        ) from exc
    except OSError as exc:
        raise MetadataSourceError("metadata HTTP worker could not start") from exc
    if len(result.stdout) > max_bytes:
        raise MetadataSourceError(
            f"metadata response exceeds the {max_bytes}-byte limit"
        )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise MetadataSourceError(
            error[-_MAX_HTTP_WORKER_ERROR_BYTES:]
            or "metadata HTTP worker failed"
        )
    return result.stdout


def _metadata_http_worker_main() -> int:
    try:
        raw = sys.stdin.buffer.read(_MAX_HTTP_WORKER_SPEC_BYTES + 1)
        if len(raw) > _MAX_HTTP_WORKER_SPEC_BYTES:
            raise MetadataSourceError("metadata worker request is oversized")
        spec = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(spec, dict):
            raise MetadataSourceError("metadata worker request is invalid")
        url = spec.get("url")
        timeout = spec.get("timeout")
        max_bytes = spec.get("max_bytes")
        origin = spec.get("expected_origin")
        if (
            not isinstance(url, str)
            or isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
            or isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or not 0 < max_bytes <= MAX_METADATA_RESPONSE_BYTES
            or not isinstance(origin, list)
            or len(origin) != 3
            or not isinstance(origin[0], str)
            or not isinstance(origin[1], str)
            or isinstance(origin[2], bool)
            or not isinstance(origin[2], int)
        ):
            raise MetadataSourceError("metadata worker request is invalid")
        payload = _http_json_bytes_in_process(
            url,
            timeout=timeout,
            expected_origin=(origin[0], origin[1], origin[2]),
            max_bytes=max_bytes,
        )
        sys.stdout.buffer.write(payload)
        return 0
    except (MetadataSourceError, OSError, TypeError, UnicodeError, ValueError) as exc:
        message = str(exc).replace("\r", " ").replace("\n", " ")
        sys.stderr.write(message[-_MAX_HTTP_WORKER_ERROR_BYTES:])
        return 2


def http_json(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    *,
    expected_origin: tuple[str, str, int],
    opener=None,
    max_bytes: int = MAX_METADATA_RESPONSE_BYTES,
    deadline_seconds: float = MAX_METADATA_RESPONSE_SECONDS,
) -> Any:
    try:
        parsed = urlsplit(url)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("userinfo is forbidden")
        port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
        origin = (parsed.scheme.casefold(), (parsed.hostname or "").casefold(), port)
    except ValueError as exc:
        raise MetadataSourceError("metadata URL is invalid") from exc
    if origin != expected_origin:
        raise MetadataSourceError("metadata URL is outside its fixed HTTPS origin")
    if max_bytes <= 0:
        raise ValueError("metadata response limit must be positive")
    if deadline_seconds <= 0:
        raise ValueError("metadata response deadline must be positive")
    if opener is not None:
        payload = _http_json_bytes_in_process(
            url,
            timeout=timeout,
            expected_origin=expected_origin,
            opener=opener,
            max_bytes=max_bytes,
        )
    else:
        payload = _run_metadata_http_worker(
            {
                "url": url,
                "timeout": timeout,
                "expected_origin": list(expected_origin),
                "max_bytes": max_bytes,
            },
            deadline_seconds=deadline_seconds,
            max_bytes=max_bytes,
        )
    try:
        return json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise MetadataSourceError("metadata response is not valid JSON") from exc


def search_crossref(query: str, limit: int = 5) -> list[dict[str, Any]]:
    query = _bounded_search_query(query)
    limit = _bounded_search_limit(limit)
    if limit == 0:
        return []
    url = (
        "https://api.crossref.org/works?rows=%d&select=DOI,title,author,issued,container-title,type,score"
        "&query.bibliographic=%s" % (limit, quote_plus(query))
    )
    data = http_json(url, expected_origin=CROSSREF_ORIGIN)
    message = data.get("message") if isinstance(data, dict) else None
    items = message.get("items") if isinstance(message, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        raw_titles = item.get("title")
        title = " ".join(
            _metadata_text(value) for value in raw_titles[:10]
            if _metadata_text(value)
        ) if isinstance(raw_titles, list) else _metadata_text(raw_titles)
        raw_containers = item.get("container-title")
        container = " ".join(
            _metadata_text(value) for value in raw_containers[:10]
            if _metadata_text(value)
        ) if isinstance(raw_containers, list) else _metadata_text(raw_containers)
        authors = []
        raw_authors = item.get("author")
        for a in raw_authors[:100] if isinstance(raw_authors, list) else []:
            if not isinstance(a, dict):
                continue
            name = " ".join(
                value for value in (
                    _metadata_text(a.get("given"), 500),
                    _metadata_text(a.get("family"), 500),
                ) if value
            )
            if name:
                authors.append(name)
        year = None
        issued = item.get("issued")
        parts = issued.get("date-parts") if isinstance(issued, dict) else None
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            year = parts[0][0]
        candidate = _sanitize_resolver_candidate({
            "doi": item.get("DOI"),
            "title": title,
            "container": container,
            "authors": authors,
            "year": year,
            "type": item.get("type"),
            "score": item.get("score"),
        }, "paper")
        if candidate is not None:
            out.append(candidate)
    return out


def search_google_books(query: str, limit: int = 5) -> list[dict[str, Any]]:
    query = _bounded_search_query(query)
    limit = _bounded_search_limit(limit)
    if limit == 0:
        return []
    url = f"https://www.googleapis.com/books/v1/volumes?q={quote_plus(query)}&maxResults={limit}"
    data = http_json(url, expected_origin=GOOGLE_BOOKS_ORIGIN)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        info = item.get("volumeInfo")
        if not isinstance(info, dict):
            continue
        ids = []
        raw_ids = info.get("industryIdentifiers")
        for ident in raw_ids[:20] if isinstance(raw_ids, list) else []:
            if not isinstance(ident, dict):
                continue
            t = ident.get("type")
            v = ident.get("identifier")
            if isinstance(v, str) and t in {"ISBN_10", "ISBN_13"}:
                isbn = valid_isbn(v)
                if isbn and isbn not in ids:
                    ids.append(isbn)
        raw_authors = info.get("authors")
        candidate = _sanitize_resolver_candidate({
            "title": _metadata_text(info.get("title")),
            "authors": raw_authors,
            "publishedDate": _metadata_text(info.get("publishedDate"), 100),
            "isbn": ids,
            "publisher": _metadata_text(info.get("publisher"), 1_000),
        }, "book")
        if candidate is not None:
            out.append(candidate)
    return out


def search_openlibrary(query: str, limit: int = 5) -> list[dict[str, Any]]:
    query = _bounded_search_query(query)
    limit = _bounded_search_limit(limit)
    if limit == 0:
        return []
    url = f"https://openlibrary.org/search.json?q={quote_plus(query)}&limit={limit}"
    data = http_json(url, expected_origin=OPENLIBRARY_ORIGIN)
    items = data.get("docs") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        raw_isbns = item.get("isbn")
        isbns = []
        for value in raw_isbns[:20] if isinstance(raw_isbns, list) else []:
            isbn = valid_isbn(value)
            if isbn and isbn not in isbns:
                isbns.append(isbn)
        raw_authors = item.get("author_name")
        raw_publishers = item.get("publisher")
        candidate = _sanitize_resolver_candidate({
            "title": _metadata_text(item.get("title")),
            "authors": raw_authors,
            "year": item.get("first_publish_year"),
            "isbn": isbns[:5],
            "publisher": _metadata_text(raw_publishers[0], 1_000)
            if isinstance(raw_publishers, list) and raw_publishers else "",
        }, "book")
        if candidate is not None:
            out.append(candidate)
    return out


def clean_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def token_overlap(a: str, b: str) -> float:
    sa = set(clean_text(a).split())
    sb = set(clean_text(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))


def similarity(a: str, b: str) -> float:
    aa = clean_text(a)
    bb = clean_text(b)
    if not aa or not bb:
        return 0.0
    return SequenceMatcher(None, aa, bb).ratio()


def year_from_query(text: str) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return int(m.group(0)) if m else None


def score_paper_candidate(query: str, cand: dict[str, Any]) -> dict[str, Any]:
    qy = year_from_query(query)
    title = cand.get("title") or ""
    score = 0.55 * similarity(query, title) + 0.25 * token_overlap(query, title)
    crossref_score = _metadata_score(cand.get("score"))
    if crossref_score is not None:
        score += min(crossref_score / 100.0, 0.12)
    if qy and cand.get("year") == qy:
        score += 0.08
    if "doi" in cand and cand.get("doi"):
        score += 0.05
    return {"score": round(score, 4), "confidence": confidence_band(score)}


def score_book_candidate(query: str, cand: dict[str, Any]) -> dict[str, Any]:
    title = cand.get("title") or ""
    score = 0.62 * similarity(query, title) + 0.25 * token_overlap(query, title)
    if cand.get("isbn"):
        score += 0.08
    if cand.get("authors"):
        score += 0.03
    return {"score": round(score, 4), "confidence": confidence_band(score)}


def confidence_band(score: float) -> str:
    if score >= 0.88:
        return "very_high"
    if score >= 0.74:
        return "high"
    if score >= 0.58:
        return "medium"
    return "low"


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def run_subprocess(argv: list[str], timeout: int = DEFAULT_TIMEOUT, cwd: str | None = None) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            check=False,
        )
        return {
            "argv": argv,
            "returncode": proc.returncode,
            "stdout": _coerce_text(proc.stdout),
            "stderr": _coerce_text(proc.stderr),
            "elapsed": round(time.time() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": argv,
            "returncode": 124,
            "stdout": _coerce_text(exc.stdout),
            "stderr": _coerce_text(exc.stderr) + "\n[timeout]",
            "elapsed": round(time.time() - started, 3),
        }


def find_getscipapers() -> str | None:
    candidates = [
        os.environ.get("GETSCIPAPERS_BIN"),
        str(Path.home() / ".local" / "bin" / "getscipapers"),
        str(Path.home() / ".getscipapers_venv" / "bin" / "getscipapers"),
        str(Path.home() / ".getscipapers_venv" / "Scripts" / "getscipapers.exe"),
        shutil.which("getscipapers"),
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        # os.access(..., X_OK) is unreliable on Windows, where executability is
        # determined by file extension rather than a POSIX permission bit.
        if os.name == "nt" or os.access(candidate, os.X_OK):
            return candidate
    return None


def introspect() -> dict[str, Any]:
    exe = find_getscipapers()
    info: dict[str, Any] = {"getscipapers_path": exe, "available": bool(exe)}
    if not exe:
        return info
    top = run_subprocess([exe, "--help"], timeout=20)
    info["top_help"] = top
    subcommands: list[str] = []

    # Rather than only scanning the top-level help text, try invoking
    # potential subcommands with --help to detect which modules actually
    # exist on this installation. Some distributions hide subcommand
    # names from the top help text but support them when called directly.
    candidates = ["getpapers", "requestpapers", "request", "gui", "getpaper", "get"]
    for name in candidates:
        res = run_subprocess([exe, name, "--help"], timeout=10)
        # treat returncode 0 as available; some tools print usage and exit 0
        # others may return nonzero but still include help text — check stdout/stderr
        helptext = (res.get("stdout") or "") + "\n" + (res.get("stderr") or "")
        if res.get("returncode") == 0 or "usage:" in helptext.lower() or name in helptext:
            subcommands.append(name)
    info["subcommands_seen"] = subcommands
    subhelp: dict[str, Any] = {}
    features: dict[str, list[str]] = {}
    for name in subcommands:
        # We already ran --help above; call again to capture full help payload
        subhelp[name] = run_subprocess([exe, name, "--help"], timeout=20)
        helptext = (subhelp[name].get("stdout") or "") + "\n" + (subhelp[name].get("stderr") or "")
        present = []
        for flag in ["--doi", "--doi-file", "--search", "--isbn", "--extract-doi-from-pdf", "--no-download", "--non-interactive", "--download-folder"]:
            if flag in helptext:
                present.append(flag)
        features[name] = present
    info["subhelp"] = subhelp
    info["features"] = features
    return info


def latest_files(download_dir: Path, limit: int = 10) -> list[dict[str, Any]]:
    out = []
    if not download_dir.exists():
        return out
    for p in download_dir.rglob("*"):
        if p.is_file():
            st = p.stat()
            out.append({
                "path": str(p),
                "name": p.name,
                "size": st.st_size,
                "mtime": st.st_mtime,
            })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:limit]


def sha256_file(path: Path, max_bytes: int = 50_000_000) -> str | None:
    if not path.is_file():
        return None
    if path.stat().st_size > max_bytes:
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def file_info(path: str, settings: Settings) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"exists": False, "path": str(p)}
    st = p.stat()
    return {
        "exists": True,
        "path": str(p),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "telegram_auto_send_possible": st.st_size <= settings.telegram_max_bytes,
        "sha256": sha256_file(p),
    }


def resolve_auto(kind: str, query: str) -> dict[str, Any]:
    text = _bounded_search_query(query)
    dois = extract_dois(text)
    isbns = extract_isbns(text)
    out: dict[str, Any] = {
        "kind": kind,
        "query": query,
        "embedded_dois": dois,
        "embedded_isbns": isbns,
        "candidates": {},
    }
    # Parallel API queries (I/O-bound: CrossRef, Google Books, OpenLibrary)
    from concurrent.futures import ThreadPoolExecutor
    tasks = []
    if kind in {"auto", "paper"} and not dois:
        tasks.append(("crossref", lambda: search_crossref(text), "paper"))
    if kind in {"auto", "book"} and not isbns:
        tasks.append(("google_books", lambda: search_google_books(text), "book"))
        tasks.append(("openlibrary", lambda: search_openlibrary(text), "book"))

    def _run_search(task):
        name, fn, score_type = task
        try:
            raw_candidates = fn()
            if not isinstance(raw_candidates, list):
                raise ValueError("metadata candidates must be a list")
            cands = []
            scorer = score_paper_candidate if score_type == "paper" else score_book_candidate
            for raw_candidate in raw_candidates[:MAX_METADATA_RESULTS]:
                c = _sanitize_resolver_candidate(raw_candidate, score_type)
                if c is None:
                    continue
                c["rank"] = scorer(text, c)
                cands.append(c)
            cands.sort(key=lambda x: x["rank"]["score"], reverse=True)
            return name, cands, None
        except Exception as exc:
            return name, None, f"{name}: {exc}"

    if len(tasks) > 1:
        _cpus = os.cpu_count() or 2
        _workers = min(_cpus * 2, len(tasks), 8)
        with ThreadPoolExecutor(max_workers=_workers) as pool:
            results = list(pool.map(_run_search, tasks))
    else:
        results = [_run_search(t) for t in tasks]

    for name, cands, error in results:
        if error:
            out.setdefault("errors", []).append(error)
        elif cands is not None:
            out["candidates"][name] = cands
    return out


def choose_best_identifier(kind: str, query: str) -> dict[str, Any]:
    data = resolve_auto(kind, query)
    if data["embedded_dois"]:
        data["selected"] = {
            "identifier_type": "doi",
            "identifier": data["embedded_dois"][0],
            "reason": "embedded_doi",
            "confidence": "very_high",
        }
        return data
    if data["embedded_isbns"]:
        data["selected"] = {
            "identifier_type": "isbn",
            "identifier": data["embedded_isbns"][0],
            "reason": "embedded_isbn",
            "confidence": "very_high",
        }
        return data

    pooled: list[dict[str, Any]] = []
    if kind in {"auto", "paper"}:
        for c in data.get("candidates", {}).get("crossref", []):
            doi = valid_doi(c.get("doi"))
            score = _metadata_score(c.get("rank", {}).get("score"))
            if doi and score is not None:
                pooled.append({
                    "identifier_type": "doi",
                    "identifier": doi,
                    "source": "crossref",
                    "score": score,
                    "confidence": c["rank"]["confidence"],
                    "title": c.get("title"),
                    "authors": c.get("authors"),
                    "year": c.get("year"),
                })
    if kind in {"auto", "book"}:
        for src in ("google_books", "openlibrary"):
            for c in data.get("candidates", {}).get(src, []):
                for isbn in c.get("isbn") or []:
                    isbn = valid_isbn(isbn)
                    score = _metadata_score(c.get("rank", {}).get("score"))
                    if not isbn or score is None:
                        continue
                    pooled.append({
                        "identifier_type": "isbn",
                        "identifier": isbn,
                        "source": src,
                        "score": score,
                        "confidence": c["rank"]["confidence"],
                        "title": c.get("title"),
                        "authors": c.get("authors"),
                        "year": c.get("year") or c.get("publishedDate"),
                    })
                    break

    pooled.sort(key=lambda x: x["score"], reverse=True)
    data["ranked_identifiers"] = pooled[:5]
    if not pooled:
        data["selected"] = None
        data["selection_status"] = "none"
        return data

    top = pooled[0]
    second = pooled[1] if len(pooled) > 1 else None
    if top["score"] >= 0.74 and (second is None or (top["score"] - second["score"] >= 0.06)):
        data["selected"] = top
        data["selection_status"] = "auto"
    else:
        data["selected"] = None
        data["selection_status"] = "ambiguous"
    return data


def iter_meaningful_lines(text: str) -> list[tuple[int, str]]:
    out = []
    raw_lines = text.splitlines()
    if len(raw_lines) > MAX_MANIFEST_LINES:
        raise ValueError(f"manifest source exceeds the {MAX_MANIFEST_LINES}-line limit")
    for i, raw in enumerate(raw_lines, start=1):
        line = raw.strip()
        if len(line) < 8:
            continue
        if line.startswith("#"):
            continue
        if len(line) > MAX_METADATA_QUERY_CHARS:
            raise ValueError(
                f"manifest source line {i} exceeds the "
                f"{MAX_METADATA_QUERY_CHARS}-character limit"
            )
        out.append((i, line))
    return out


def build_manifest(kind: str, source: str, settings: Settings) -> dict[str, Any]:
    text = read_text_source(source)
    prepared_lines = []
    potential_items: set[tuple[str, str]] = set()
    resolution_lines = 0
    for lineno, line in iter_meaningful_lines(text):
        dois = extract_dois(line)
        isbns = extract_isbns(line)
        if dois:
            potential_items.update(("doi", doi) for doi in dois)
        elif isbns:
            potential_items.update(("isbn", isbn) for isbn in isbns)
        else:
            resolution_lines += 1
        if len(potential_items) + resolution_lines > MAX_MANIFEST_ITEMS:
            raise ValueError(
                f"manifest source exceeds the {MAX_MANIFEST_ITEMS}-item limit"
            )
        prepared_lines.append((lineno, line, dois, isbns))
    if resolution_lines > MAX_MANIFEST_RESOLUTION_LINES:
        raise ValueError(
            "manifest source exceeds the "
            f"{MAX_MANIFEST_RESOLUTION_LINES}-metadata-resolution-line limit"
        )

    items = []
    seen_keys: set[tuple[str, str]] = set()
    for lineno, line, dois, isbns in prepared_lines:
        if dois:
            for doi in dois:
                key = ("doi", doi)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                items.append({
                    "source_line": lineno,
                    "source_text": line,
                    "identifier_type": "doi",
                    "identifier": doi,
                    "confidence": "very_high",
                    "status": "embedded",
                })
            continue
        if isbns:
            for isbn in isbns:
                key = ("isbn", isbn)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                items.append({
                    "source_line": lineno,
                    "source_text": line,
                    "identifier_type": "isbn",
                    "identifier": isbn,
                    "confidence": "very_high",
                    "status": "embedded",
                })
            continue
        best = choose_best_identifier(kind, line)
        selected = _sanitize_manifest_identifier_summary(best.get("selected"))
        itype = selected.get("identifier_type") if isinstance(selected, dict) else None
        ident = (
            valid_doi(selected.get("identifier"))
            if itype == "doi" and isinstance(selected, dict)
            else valid_isbn(selected.get("identifier"))
            if itype == "isbn" and isinstance(selected, dict)
            else ""
        )
        if ident:
            key = (itype, ident)
            if key not in seen_keys:
                seen_keys.add(key)
                items.append({
                    "source_line": lineno,
                    "source_text": line,
                    "identifier_type": itype,
                    "identifier": ident,
                    "confidence": selected.get("confidence", "medium"),
                    "status": "resolved",
                    "resolution": selected,
                })
        else:
            items.append({
                "source_line": lineno,
                "source_text": line,
                "identifier_type": None,
                "identifier": None,
                "confidence": "low",
                "status": best.get("selection_status", "unresolved"),
                "ranked_identifiers": _sanitize_manifest_ranked_identifiers(
                    best.get("ranked_identifiers")
                ),
                "errors": best.get("errors", []),
            })

    manifest = {
        "kind": kind,
        "created_at": int(time.time()),
        "source": source,
        "items": items,
        "counts": {
            "total_items": len(items),
            "dois": sum(1 for x in items if x.get("identifier_type") == "doi"),
            "isbns": sum(1 for x in items if x.get("identifier_type") == "isbn"),
            "unresolved": sum(1 for x in items if not x.get("identifier")),
        },
    }
    digest = hashlib.sha256(
        (kind + "\n" + text).encode("utf-8", errors="replace")
    ).hexdigest()
    manifest_path = settings.manifest_dir / f"manifest-{digest}.json"

    doi_values = [x["identifier"] for x in items if x.get("identifier_type") == "doi" and x.get("identifier")]
    doi_file = None
    doi_content = None
    if doi_values:
        doi_file = settings.manifest_dir / f"manifest-{digest}.doi.txt"
        doi_content = "\n".join(doi_values) + "\n"
    manifest["manifest_path"] = str(manifest_path)
    manifest["doi_file"] = str(doi_file) if doi_file else None
    encoded_manifest = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )
    if len(encoded_manifest.encode("utf-8")) > MAX_MANIFEST_OUTPUT_BYTES:
        raise ValueError(
            f"manifest output exceeds the {MAX_MANIFEST_OUTPUT_BYTES}-byte limit"
        )
    write_text_atomic(manifest_path, encoded_manifest)
    if doi_file is not None and doi_content is not None:
        write_text_atomic(doi_file, doi_content)
    return manifest


def ensure_watch_store(settings: Settings) -> Path:
    path = settings.state_dir / "watches.json"
    try:
        os.lstat(path)
    except FileNotFoundError:
        write_watch_store(path, {"items": []})
    else:
        read_watch_store(path)
    return path


def _watch_identity(payload: dict[str, Any]) -> tuple[str, str, str, tuple[str, ...]]:
    services = tuple(sorted(
        x.strip()
        for x in payload.get("services", [])
        if isinstance(x, str) and x.strip()
    ))
    return (
        str(payload.get("kind", "")),
        str(payload.get("identifier_type", "")),
        str(payload.get("identifier", "")),
        services,
    )


def _legacy_watch_key(payload: dict[str, Any]) -> str:
    kind, identifier_type, identifier, services = _watch_identity(payload)
    base = f"{kind}|{identifier_type}|{identifier}|{','.join(services)}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _watch_key(payload: dict[str, Any]) -> str:
    identity = _watch_identity(payload)
    encoded = json.dumps(
        [identity[0], identity[1], identity[2], list(identity[3])],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()


def create_watch(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    with watch_store_lock(settings):
        store = ensure_watch_store(settings)
        data = read_watch_store(store)
        payload = dict(payload)
        payload["services"] = sorted([x.strip() for x in payload.get("services", []) if x.strip()])
        key = _watch_key(payload)
        legacy_key = _legacy_watch_key(payload)
        identity = _watch_identity(payload)
        now = int(time.time())
        for item in data.get("items", []):
            if (
                item.get("watch_key") in {key, legacy_key}
                and _watch_identity(item) == identity
                and item.get("status") in {"active", "waiting", "posted", "found"}
            ):
                prior_key = item.get("watch_key")
                item["watch_key"] = key
                if item.get("status") != "found":
                    mutation_time = max(
                        now,
                        item["created_at"],
                        item["updated_at"],
                    )
                    item["updated_at"] = mutation_time
                    history = item.setdefault("notes_history", [])
                    history.append({
                        "ts": mutation_time,
                        "note": "duplicate create-watch reused existing record",
                    })
                    if len(history) > MAX_WATCH_NOTES:
                        del history[:-MAX_WATCH_NOTES]
                    write_watch_store(store, data)
                elif prior_key != key:
                    write_watch_store(store, data)
                reused = dict(item)
                reused["reused"] = True
                return reused
        item_id = f"watch-{now}-{key}"
        used_ids = {item["id"] for item in data.get("items", [])}
        suffix = 2
        while item_id in used_ids:
            item_id = f"watch-{now}-{key}-{suffix}"
            suffix += 1
        payload.update({
            "id": item_id,
            "watch_key": key,
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "sent_file_hashes": [],
            "check_count": 0,
        })
        data.setdefault("items", []).append(payload)
        write_watch_store(store, data)
        return payload


def list_watches(settings: Settings) -> dict[str, Any]:
    with watch_store_lock(settings):
        store = ensure_watch_store(settings)
        return read_watch_store(store)


def update_watch(
    settings: Settings,
    watch_id: str,
    patch: dict[str, Any],
    *,
    bump_check: bool = False,
) -> dict[str, Any]:
    with watch_store_lock(settings):
        store = ensure_watch_store(settings)
        data = read_watch_store(store)
        for item in data.get("items", []):
            if item.get("id") == watch_id:
                mutation_time = max(
                    int(time.time()),
                    item["created_at"],
                    item["updated_at"],
                )
                if patch.get("sent_file_hash"):
                    item.setdefault("sent_file_hashes", [])
                    if patch["sent_file_hash"] not in item["sent_file_hashes"]:
                        item["sent_file_hashes"].append(patch["sent_file_hash"])
                for k, v in patch.items():
                    if k != "sent_file_hash" and v not in ("", None):
                        item[k] = (
                            mutation_time
                            if k == "last_checked_at" and bump_check
                            else v
                        )
                if bump_check:
                    try:
                        count = int(item.get("check_count", 0))
                    except (TypeError, ValueError, OverflowError):
                        count = 0
                    item["check_count"] = max(0, count) + 1
                item["updated_at"] = mutation_time
                write_watch_store(store, data)
                return item
    raise SystemExit(f"unknown watch id: {watch_id}")


def cmd_doctor(args: argparse.Namespace, settings: Settings) -> None:
    payload = introspect()
    payload.update({
        "python": sys.executable,
        "download_dir": str(settings.download_dir),
        "state_dir": str(settings.state_dir),
        "manifest_dir": str(settings.manifest_dir),
        "telegram_max_bytes": settings.telegram_max_bytes,
        "openclaw_path": shutil.which("openclaw"),
        "message_help": run_subprocess(["openclaw", "message", "--help"], timeout=20) if shutil.which("openclaw") else None,
    })
    if args.network:
        checks = {}
        try:
            checks["crossref"] = {"ok": bool(search_crossref("graph theory", limit=1))}
        except Exception as exc:
            checks["crossref"] = {"ok": False, "error": str(exc)}
        try:
            checks["google_books"] = {"ok": bool(search_google_books("introduction to algorithms", limit=1))}
        except Exception as exc:
            checks["google_books"] = {"ok": False, "error": str(exc)}
        try:
            checks["openlibrary"] = {"ok": bool(search_openlibrary("graph theory", limit=1))}
        except Exception as exc:
            checks["openlibrary"] = {"ok": False, "error": str(exc)}
        payload["network_checks"] = checks
    json_print(payload)


def cmd_extract(args: argparse.Namespace, settings: Settings) -> None:
    text = read_text_source(args.source)
    json_print({"dois": extract_dois(text), "isbns": extract_isbns(text)})


def cmd_resolve(args: argparse.Namespace, settings: Settings) -> None:
    if args.best:
        json_print(choose_best_identifier(args.kind, args.query))
    else:
        json_print(resolve_auto(args.kind, args.query))


def cmd_manifest(args: argparse.Namespace, settings: Settings) -> None:
    json_print(build_manifest(args.kind, args.source, settings))


def cmd_introspect(args: argparse.Namespace, settings: Settings) -> None:
    json_print(introspect())


def _apply_runner_proxy_default(module_argv: list[str]) -> list[str]:
    """Default the getpapers module to ``--no-proxy``.

    A stale or invalid getscipapers proxy configuration makes the getpapers
    doi.org DOI-resolution step fail ("not a valid DOI"), even when direct
    access works. The runner therefore defaults getpapers to ``--no-proxy``;
    callers may still override with an explicit ``--proxy`` / ``--no-proxy`` /
    ``--auto-proxy``.
    """
    if module_argv and module_argv[0] == "getpapers" and not (
        {"--proxy", "--no-proxy", "--auto-proxy"} & set(module_argv)
    ):
        return module_argv + ["--no-proxy"]
    return module_argv


def cmd_run(args: argparse.Namespace, settings: Settings) -> None:
    exe = find_getscipapers()
    if not exe:
        raise SystemExit("getscipapers not found in PATH")

    # Sanitize argv: callers sometimes pass a leading '--' or duplicate the
    # getscipapers token. Normalize so we pass: [exe, <module>, <flags...>]
    raw = list(args.argv)
    if raw and raw[0] == "--":
        raw = raw[1:]

    # If the caller accidentally included the executable name or the
    # 'getscipapers' token, drop it.
    if raw and (os.path.basename(raw[0]) == os.path.basename(exe) or raw[0] == "getscipapers"):
        raw = raw[1:]

    raw = _apply_runner_proxy_default(raw)
    argv = [exe] + raw
    payload = {
        "argv": argv,
        "dry_run": bool(args.dry_run),
    }
    if args.dry_run:
        json_print(payload)
        return
    result = run_subprocess(argv, timeout=args.timeout, cwd=args.cwd)
    payload.update(result)
    json_print(payload)
    raise SystemExit(result["returncode"])


def cmd_latest(args: argparse.Namespace, settings: Settings) -> None:
    json_print({"files": latest_files(settings.download_dir, limit=args.limit)})


def cmd_file_info(args: argparse.Namespace, settings: Settings) -> None:
    json_print(file_info(args.path, settings))


def cmd_create_watch(args: argparse.Namespace, settings: Settings) -> None:
    payload = {
        "kind": args.kind,
        "label": args.label,
        "identifier_type": args.identifier_type,
        "identifier": args.identifier,
        "services": [x.strip() for x in (args.services or "").split(",") if x.strip()],
        "notes": args.notes,
        "deadline_ts": int(time.time()) + max(0, args.deadline_hours) * 3600 if args.deadline_hours else None,
    }
    json_print(create_watch(settings, payload))


def cmd_list_watches(args: argparse.Namespace, settings: Settings) -> None:
    json_print(list_watches(settings))


def cmd_update_watch(args: argparse.Namespace, settings: Settings) -> None:
    patch = {
        "status": args.status,
        "last_note": args.last_note,
        "last_checked_at": int(time.time()) if args.bump_check else None,
        "sent_file_hash": args.sent_file_hash,
    }
    json_print(
        update_watch(
            settings,
            args.watch_id,
            patch,
            bump_check=args.bump_check,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gsp_openclaw_helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("doctor")
    sp.add_argument("--network", action="store_true")

    sp = sub.add_parser("extract")
    sp.add_argument("source")

    sp = sub.add_parser("resolve")
    sp.add_argument("kind", choices=["auto", "paper", "book"])
    sp.add_argument("query")
    sp.add_argument("--best", action="store_true")

    sp = sub.add_parser("make-manifest")
    sp.add_argument("kind", choices=["auto", "paper", "book"])
    sp.add_argument("source")

    sub.add_parser("introspect")

    sp = sub.add_parser("run-getscipapers")
    sp.add_argument("--timeout", type=int, default=180)
    sp.add_argument("--cwd", default=None)
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("argv", nargs=argparse.REMAINDER)

    sp = sub.add_parser("latest-downloads")
    sp.add_argument("--limit", type=int, default=10)

    sp = sub.add_parser("file-info")
    sp.add_argument("path")

    sp = sub.add_parser("create-watch")
    sp.add_argument("--kind", choices=["paper", "book"], required=True)
    sp.add_argument("--label", required=True)
    sp.add_argument("--identifier-type", choices=["doi", "isbn", "search"], required=True)
    sp.add_argument("--identifier", required=True)
    sp.add_argument("--services", default="")
    sp.add_argument("--notes", default="")
    sp.add_argument("--deadline-hours", type=int, default=72)

    sub.add_parser("list-watches")

    sp = sub.add_parser("update-watch")
    sp.add_argument("watch_id")
    sp.add_argument("--status", choices=sorted(WATCH_STATUSES), default="")
    sp.add_argument("--last-note", default="")
    sp.add_argument("--sent-file-hash", default="")
    sp.add_argument("--bump-check", action="store_true")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    durable_dirs = {
        "make-manifest": ("manifest_dir",),
        "create-watch": ("state_dir",),
        "list-watches": ("state_dir",),
        "update-watch": ("state_dir",),
    }
    required = durable_dirs.get(args.cmd)
    settings = ensure_settings_dirs(
        load_settings(strict_config=required is not None),
        allow_fallback=required is None,
        required=required,
    )
    dispatch = {
        "doctor": cmd_doctor,
        "extract": cmd_extract,
        "resolve": cmd_resolve,
        "make-manifest": cmd_manifest,
        "introspect": cmd_introspect,
        "run-getscipapers": cmd_run,
        "latest-downloads": cmd_latest,
        "file-info": cmd_file_info,
        "create-watch": cmd_create_watch,
        "list-watches": cmd_list_watches,
        "update-watch": cmd_update_watch,
    }
    dispatch[args.cmd](args, settings)


if __name__ == "__main__":
    if sys.argv[1:] == [_HTTP_WORKER_COMMAND]:
        raise SystemExit(_metadata_http_worker_main())
    main()
