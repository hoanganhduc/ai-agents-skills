#!/usr/bin/env python3
import argparse
import calendar
import csv
import hashlib
import html
import http.client
import itertools
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from xml.parsers import expat
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, unquote_plus, urlparse, urlunparse
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

_WORKSPACE = Path(os.environ.get("AAS_RUNTIME_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE") or os.path.join(os.path.expanduser("~"), ".codex", "runtime", "workspace"))
DEFAULT_FEEDS_TSV = _WORKSPACE / "data" / "research" / "rss" / "feeds.tsv"
DEFAULT_LEGACY_FEEDS_FILE = _WORKSPACE / "data" / "research" / "rss" / "feeds.txt"
DEFAULT_PROFILES_FILE = _WORKSPACE / "data" / "research" / "rss" / "profiles.json"
DEFAULT_STATE_FILE = _WORKSPACE / "data" / "research" / "rss" / "state.json"
DEFAULT_DIGEST_DIR = _WORKSPACE / "data" / "research" / "rss" / "digests"
DEFAULT_BACKUP_DIR = _WORKSPACE / "data" / "research" / "rss" / "backups"
DEFAULT_FEEDS_BOOTSTRAP = "enabled\ttag\tpriority\tkind\turl\tnotes\n1\tresearch\t10\tarxiv\thttps://export.arxiv.org/rss/cs.CC\tComputational complexity\n1\tresearch\t10\tarxiv\thttps://export.arxiv.org/rss/cs.DS\tData structures and algorithms\n1\tresearch\t10\tarxiv\thttps://export.arxiv.org/rss/cs.DM\tDiscrete mathematics\n1\tresearch\t10\tarxiv\thttps://export.arxiv.org/rss/math.CO\tCombinatorics\n1\tresearch\t9\tblog\thttps://11011110.github.io/blog/feed.xml\tTheory blog\n1\tresearch\t9\tblog\thttp://blog.computationalcomplexity.org/feeds/posts/default\tComputational Complexity Blog\n1\tevents\t8\tcfp\thttp://www.wikicfp.com/cfp/rss?cat=algorithms\tAlgorithms CFPs\n1\tjobs\t8\tjobs\thttps://www.mathjobs.org/jobs?joblist-0-0----rss--\tMath jobs\n1\tgeneral\t3\tnews\thttps://www.quantamagazine.org/feed/\tQuanta\n"
DEFAULT_PROFILES_BOOTSTRAP = "{\n  \"graph_theory\": [\n    \"graph\",\n    \"combinatorics\",\n    \"coloring\",\n    \"reconfiguration\",\n    \"token\",\n    \"planar\",\n    \"permutation graph\",\n    \"independent set\"\n  ],\n  \"complexity\": [\n    \"complexity\",\n    \"pspace\",\n    \"np-hard\",\n    \"reduction\",\n    \"hardness\",\n    \"lower bound\",\n    \"constraint logic\"\n  ],\n  \"algorithms\": [\n    \"algorithm\",\n    \"data structure\",\n    \"dynamic programming\",\n    \"approximation\",\n    \"streaming\",\n    \"online\",\n    \"randomized\"\n  ],\n  \"ai_research\": [\n    \"ai\",\n    \"artificial intelligence\",\n    \"chatgpt\",\n    \"llm\",\n    \"large language model\",\n    \"gpt\",\n    \"reproducibility\",\n    \"papers\",\n    \"research\"\n  ]\n}"
DEFAULT_MAX_ITEMS = 25
DEFAULT_PER_FEED_LIMIT = 12
STATE_LIMIT = 5000
MAX_TITLE_CHARS = 500
MAX_FEED_TITLE_CHARS = 300
MAX_LINK_CHARS = 2048
MAX_SUMMARY_CHARS = 2000
MAX_FEED_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_PARSED_FEED_ENTRIES = 500
MAX_FEED_XML_ELEMENTS = 20_000
MAX_FEED_XML_ATTRIBUTES = 20_000
MAX_FEED_XML_DEPTH = 128
FEED_TIMEOUT_SECONDS = 15
FEED_RESPONSE_DEADLINE_SECONDS = 60.0
MAX_FUTURE_CLOCK_SKEW_SECONDS = 24 * 60 * 60
_HTTP_WORKER_COMMAND = "__bounded-feed-http-worker"
_MAX_HTTP_WORKER_SPEC_BYTES = 16 * 1024
_MAX_HTTP_WORKER_ERROR_BYTES = 2_000
_FEED_WORKER_COUNT_BYTES = 8
MAX_CONFIG_BYTES = 4 * 1024 * 1024
MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_FEEDS = 1_000
MAX_FEED_KIND_CHARS = 80
MAX_FEED_NOTES_CHARS = 2_000
MAX_RUN_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_RUN_ITEMS = 5_000
MAX_PROFILE_COUNT = 100
MAX_PROFILE_TERMS = 1_000
MAX_PROFILE_TERM_CHARS = 200
MAX_PROFILE_TOTAL_CHARS = 50_000
MAX_BACKUPS = 50
MAX_BACKUP_DIRECTORY_ENTRIES = 10_000
MAX_BACKUP_INDEX_BYTES = 256 * 1024 * 1024
MAX_BACKUP_FUTURE_SKEW_SECONDS = 5 * 60
MAX_SCORE_TEXT_CHARS = MAX_TITLE_CHARS + (2 * MAX_SUMMARY_CHARS) + 2
MAX_DIGEST_ITEMS = 500
MAX_DIGEST_MARKDOWN_BYTES = 4 * 1024 * 1024
MAX_SIDECAR_BYTES = 2 * 1024 * 1024
MAX_ITEM_KEY_CHARS = 300
MAX_INGESTED_RECORDS = 50_000
MAX_INGESTED_LEDGER_BYTES = 4 * 1024 * 1024
INGESTED_RECORD_KEYS = frozenset({"source", "id", "processed_at"})
INGESTED_TIMESTAMP_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?\+00:00\Z"
)
KNOWN_TAGS = ["research", "events", "jobs", "general", "video"]
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
ARXIV_ITEM_PATH_RE = re.compile(
    r"^/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?/?$",
    re.IGNORECASE | re.ASCII,
)
YOUTUBE_ID_RE = re.compile(r"[A-Za-z0-9_-]{6,64}\Z")
YOUTUBE_VIDEO_PATH_RE = re.compile(r"^/videos/([A-Za-z0-9_-]{6,64})/?$")
STACKEXCHANGE_ITEM_PATH_RE = re.compile(
    r"^/questions/(\d+)(?:/[^/]*)?/?$",
    re.ASCII,
)
BIDI_CONTROLS = frozenset(
    chr(value)
    for value in (
        0x061C,
        0x200E,
        0x200F,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
    )
)
BACKUP_RE = re.compile(
    r"feeds-\d{8}T\d{6}(?:\d{6})?Z-[a-z0-9][a-z0-9._-]{0,159}(?:-[0-9a-f]{8})?\.tsv\Z"
)


class _PreparedProfileTerms(tuple):
    """Marker for profile terms normalized once before per-item scoring."""


def reject_duplicate_json_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object member: {key}")
        value[key] = item
    return value


def is_link_like_stat(info) -> bool:
    """Treat POSIX symlinks and Windows reparse points alike."""
    return bool(stat.S_ISLNK(info.st_mode)) or bool(
        int(getattr(info, "st_file_attributes", 0))
        & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    )


def now_ts() -> float:
    return time.time()


def ensure_feedparser():
    try:
        import feedparser  # type: ignore
    except ImportError:
        sys.stderr.write(
            "Missing dependency: feedparser. Install it with one of:\n"
            "  python3 -m pip install --user feedparser\n"
            "  or rerun the installer without --no-venv so it can create an isolated virtualenv.\n"
        )
        raise SystemExit(2)
    return feedparser


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


class _AggregateResponseBudget:
    """Share one hard response-byte budget across concurrent feed readers."""

    def __init__(self, limit: int):
        if limit <= 0:
            raise ValueError("aggregate response limit must be positive")
        self.limit = limit
        self.used = 0
        self.reserved = 0
        self._condition = threading.Condition()
        self._probe_lock = threading.Lock()

    def reserve(self, wanted: int) -> int:
        if wanted <= 0:
            raise ValueError("response read reservation must be positive")
        with self._condition:
            while self.used < self.limit:
                available = self.limit - self.used - self.reserved
                if available > 0:
                    amount = min(wanted, available)
                    self.reserved += amount
                    return amount
                self._condition.wait()
            raise RuntimeError(
                f"run feed responses exceed the {self.limit}-byte limit"
            )

    def probe_eof(self, read_one) -> bool:
        """Serialize the single unbudgeted byte needed to prove exact EOF.

        False means EOF. True means one overflow byte was consumed and recorded;
        once that happens all later reservations fail without another fetch.
        """
        with self._probe_lock:
            with self._condition:
                if self.used > self.limit:
                    return True
                if self.used < self.limit:
                    raise RuntimeError("aggregate EOF probe was requested too early")
            chunk = read_one()
            if not chunk:
                return False
            with self._condition:
                self.used += len(chunk)
                self._condition.notify_all()
            return True

    def settle(self, reserved: int, actual: int) -> None:
        if not 0 <= actual <= reserved:
            raise RuntimeError("feed reader violated its byte reservation")
        with self._condition:
            self.reserved -= reserved
            self.used += actual
            self._condition.notify_all()


class _FeedFetchError(RuntimeError):
    def __init__(self, message: str, *, bytes_read: int) -> None:
        super().__init__(message)
        self.bytes_read = max(0, bytes_read)


def _fetch_feed_bytes_in_process_impl(
    url: str,
    *,
    opener=None,
    max_bytes: int = MAX_FEED_RESPONSE_BYTES,
    aggregate_budget: _AggregateResponseBudget | None = None,
    progress=None,
) -> bytes:
    safe_url = normalize_external_url(url)
    if not safe_url:
        raise ValueError("feed URL must be an absolute HTTP(S) URL without whitespace")
    if max_bytes <= 0:
        raise ValueError("feed byte limit must be positive")
    request = Request(
        safe_url,
        headers={
            "User-Agent": "ai-agents-skills-rss-digest/1.0",
            "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.2",
        },
    )
    pending_reservation = 0
    if aggregate_budget is not None:
        pending_reservation = aggregate_budget.reserve(
            min(64 * 1024, max_bytes + 1)
        )
    try:
        client = opener or build_opener(_NoRedirectHandler())
        try:
            response = client.open(request, timeout=FEED_TIMEOUT_SECONDS)
        except HTTPError as exc:
            try:
                exc.close()
            except OSError:
                pass
            raise RuntimeError(f"feed fetch refused HTTP status {exc.code}") from exc
        with response:
            status_code = getattr(response, "status", None)
            if status_code is None:
                status_code = response.getcode()
            if 300 <= int(status_code) < 400:
                raise RuntimeError(f"feed fetch refused HTTP redirect status {status_code}")
            raw_length = response.headers.get("Content-Length")
            declared = None
            if raw_length is not None:
                try:
                    declared = int(raw_length)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("feed returned an invalid Content-Length") from exc
                if declared < 0 or declared > max_bytes:
                    raise RuntimeError(f"feed exceeds the {max_bytes}-byte limit")
            chunks = []
            total = 0
            while True:
                wanted = min(64 * 1024, max_bytes + 1 - total)
                if wanted <= 0:
                    raise RuntimeError(f"feed exceeds the {max_bytes}-byte limit")
                if aggregate_budget is not None:
                    if pending_reservation == 0:
                        try:
                            pending_reservation = aggregate_budget.reserve(wanted)
                        except RuntimeError:
                            read = response.read
                            def read_eof_probe():
                                chunk = read(1)
                                if progress is not None:
                                    progress(len(chunk))
                                return chunk

                            if aggregate_budget.probe_eof(read_eof_probe):
                                raise RuntimeError(
                                    f"run feed responses exceed the {aggregate_budget.limit}-byte limit"
                                )
                            break
                    read_size = pending_reservation
                else:
                    read_size = wanted
                try:
                    # ``HTTPResponse.read`` enforces declared framing and
                    # reports truncated bodies via ``IncompleteRead.partial``;
                    # ``read1`` can silently turn a short Content-Length body
                    # into ordinary EOF.
                    read = response.read
                    chunk = read(read_size)
                    if progress is not None:
                        progress(len(chunk))
                except BaseException as exc:
                    partial_size = 0
                    partial_exc = exc
                    seen_partial_exceptions = set()
                    while (
                        isinstance(partial_exc, http.client.HTTPException)
                        and id(partial_exc) not in seen_partial_exceptions
                        and partial_size < read_size
                    ):
                        seen_partial_exceptions.add(id(partial_exc))
                        partial = getattr(partial_exc, "partial", b"")
                        if isinstance(partial, (bytes, bytearray, memoryview)):
                            partial_size += min(
                                len(partial),
                                read_size - partial_size,
                            )
                        partial_exc = (
                            partial_exc.__cause__ or partial_exc.__context__
                        )
                    if progress is not None and partial_size:
                        progress(partial_size)
                    if aggregate_budget is not None:
                        aggregate_budget.settle(
                            pending_reservation,
                            min(pending_reservation, partial_size),
                        )
                        pending_reservation = 0
                    raise
                if aggregate_budget is not None:
                    aggregate_budget.settle(pending_reservation, len(chunk))
                    pending_reservation = 0
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError(f"feed exceeds the {max_bytes}-byte limit")
                chunks.append(chunk)
            if declared is not None and total != declared:
                raise RuntimeError(
                    "feed response length does not match its declared "
                    "Content-Length"
                )
            return b"".join(chunks)
    finally:
        if aggregate_budget is not None and pending_reservation:
            aggregate_budget.settle(pending_reservation, 0)


def _fetch_feed_bytes_in_process(
    url: str,
    *,
    opener=None,
    max_bytes: int = MAX_FEED_RESPONSE_BYTES,
    aggregate_budget: _AggregateResponseBudget | None = None,
) -> bytes:
    consumed = 0

    def record(amount: int) -> None:
        nonlocal consumed
        consumed += amount

    try:
        return _fetch_feed_bytes_in_process_impl(
            url,
            opener=opener,
            max_bytes=max_bytes,
            aggregate_budget=aggregate_budget,
            progress=record,
        )
    except _FeedFetchError:
        raise
    except Exception as exc:
        raise _FeedFetchError(str(exc), bytes_read=consumed) from exc


def _feed_worker_spec_bytes(url: str, max_bytes: int) -> bytes:
    try:
        payload = json.dumps(
            {"url": url, "max_bytes": max_bytes},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeError("feed worker request is not serializable") from exc
    if len(payload) > _MAX_HTTP_WORKER_SPEC_BYTES:
        raise RuntimeError("feed worker request is oversized")
    return payload


def _fetch_feed_bytes_via_worker(
    url: str,
    *,
    max_bytes: int,
    aggregate_budget: _AggregateResponseBudget | None,
    deadline_seconds: float,
) -> bytes:
    reserved = 0
    effective_max = max_bytes
    if aggregate_budget is not None:
        reserved = aggregate_budget.reserve(max_bytes)
        effective_max = reserved
    try:
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), _HTTP_WORKER_COMMAND],
            input=_feed_worker_spec_bytes(url, effective_max),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=deadline_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        if aggregate_budget is not None:
            aggregate_budget.settle(reserved, reserved)
            reserved = 0
        raise RuntimeError(
            f"feed exceeded the {deadline_seconds:g}-second response deadline"
        ) from exc
    except OSError:
        if aggregate_budget is not None:
            aggregate_budget.settle(reserved, 0)
            reserved = 0
        raise
    try:
        if len(result.stdout) > effective_max + _FEED_WORKER_COUNT_BYTES:
            raise RuntimeError(f"feed exceeds the {effective_max}-byte limit")
        if len(result.stdout) < _FEED_WORKER_COUNT_BYTES:
            raise RuntimeError("feed fetch worker returned an invalid byte count")
        consumed = int.from_bytes(
            result.stdout[:_FEED_WORKER_COUNT_BYTES],
            byteorder="big",
        )
        payload = result.stdout[_FEED_WORKER_COUNT_BYTES:]
        if consumed > effective_max + 1:
            raise RuntimeError("feed fetch worker returned an invalid byte count")
        if result.returncode != 0:
            if aggregate_budget is not None:
                aggregate_budget.settle(reserved, min(reserved, consumed))
                reserved = 0
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                error[-_MAX_HTTP_WORKER_ERROR_BYTES:] or "feed fetch worker failed"
            )
        if consumed != len(payload):
            raise RuntimeError("feed fetch worker returned an invalid byte count")
        if aggregate_budget is not None:
            aggregate_budget.settle(reserved, len(payload))
            reserved = 0
        return payload
    except BaseException:
        if aggregate_budget is not None and reserved:
            # A failed worker may have consumed its full admitted allowance
            # before reporting failure. Count that conservative worst case.
            aggregate_budget.settle(reserved, reserved)
            reserved = 0
        raise


def _feed_http_worker_main() -> int:
    try:
        raw = sys.stdin.buffer.read(_MAX_HTTP_WORKER_SPEC_BYTES + 1)
        if len(raw) > _MAX_HTTP_WORKER_SPEC_BYTES:
            raise RuntimeError("feed worker request is oversized")
        spec = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_json_keys,
            parse_constant=reject_json_constant,
        )
        if not isinstance(spec, dict):
            raise RuntimeError("feed worker request is invalid")
        url = spec.get("url")
        max_bytes = spec.get("max_bytes")
        if (
            not isinstance(url, str)
            or isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or not 0 < max_bytes <= MAX_FEED_RESPONSE_BYTES
        ):
            raise RuntimeError("feed worker request is invalid")
        payload = _fetch_feed_bytes_in_process(url, max_bytes=max_bytes)
        sys.stdout.buffer.write(len(payload).to_bytes(_FEED_WORKER_COUNT_BYTES, "big"))
        sys.stdout.buffer.write(payload)
        return 0
    except _FeedFetchError as exc:
        sys.stdout.buffer.write(
            exc.bytes_read.to_bytes(_FEED_WORKER_COUNT_BYTES, "big")
        )
        message = str(exc).replace("\r", " ").replace("\n", " ")
        sys.stderr.write(message[-_MAX_HTTP_WORKER_ERROR_BYTES:])
        return 2
    except (HTTPError, OSError, RuntimeError, TypeError, UnicodeError, ValueError) as exc:
        sys.stdout.buffer.write((0).to_bytes(_FEED_WORKER_COUNT_BYTES, "big"))
        message = str(exc).replace("\r", " ").replace("\n", " ")
        sys.stderr.write(message[-_MAX_HTTP_WORKER_ERROR_BYTES:])
        return 2


def fetch_feed_bytes(
    url: str,
    *,
    opener=None,
    max_bytes: int = MAX_FEED_RESPONSE_BYTES,
    aggregate_budget: _AggregateResponseBudget | None = None,
    deadline_seconds: float = FEED_RESPONSE_DEADLINE_SECONDS,
) -> bytes:
    safe_url = normalize_external_url(url)
    if not safe_url:
        raise ValueError("feed URL must be an absolute HTTP(S) URL without whitespace")
    if max_bytes <= 0:
        raise ValueError("feed byte limit must be positive")
    if deadline_seconds <= 0:
        raise ValueError("feed response deadline must be positive")
    if opener is not None:
        return _fetch_feed_bytes_in_process(
            safe_url,
            opener=opener,
            max_bytes=max_bytes,
            aggregate_budget=aggregate_budget,
        )
    return _fetch_feed_bytes_via_worker(
        safe_url,
        max_bytes=max_bytes,
        aggregate_budget=aggregate_budget,
        deadline_seconds=deadline_seconds,
    )


def reject_unsafe_feed_xml(payload: bytes) -> None:
    if not isinstance(payload, bytes):
        raise TypeError("feed payload must be bytes")
    # Removing NULs exposes the ASCII XML markup tokens in UTF-16/UTF-32 as
    # well as UTF-8. RSS/Atom does not require a custom DTD, and rejecting the
    # declaration prevents entity expansion before feedparser sees the bytes.
    markup_projection = payload.replace(b"\0", b"").upper()
    if b"<!DOCTYPE" in markup_projection or b"<!ENTITY" in markup_projection:
        raise RuntimeError("feed XML DTD/entity declarations are forbidden")


def enforce_feed_entry_limit(payload: bytes) -> int:
    """Count RSS/Atom entries without materializing remote XML objects."""
    if not isinstance(payload, bytes):
        raise TypeError("feed payload must be bytes")
    entry_count = 0
    element_count = 0
    attribute_count = 0
    depth = 0

    def count_entry(name: str, attributes) -> None:
        nonlocal attribute_count, depth, element_count, entry_count
        element_count += 1
        attribute_count += len(attributes)
        depth += 1
        if element_count > MAX_FEED_XML_ELEMENTS:
            raise RuntimeError(
                "feed XML exceeds the "
                f"{MAX_FEED_XML_ELEMENTS}-element parse limit"
            )
        if attribute_count > MAX_FEED_XML_ATTRIBUTES:
            raise RuntimeError(
                "feed XML exceeds the "
                f"{MAX_FEED_XML_ATTRIBUTES}-attribute parse limit"
            )
        if depth > MAX_FEED_XML_DEPTH:
            raise RuntimeError(
                "feed XML exceeds the "
                f"{MAX_FEED_XML_DEPTH}-level nesting limit"
            )
        local_name = name.rsplit("}", 1)[-1].rsplit(":", 1)[-1].casefold()
        if local_name not in {"item", "entry"}:
            return
        entry_count += 1
        if entry_count > MAX_PARSED_FEED_ENTRIES:
            raise RuntimeError(
                "feed XML exceeds the "
                f"{MAX_PARSED_FEED_ENTRIES}-entry parse limit"
            )

    def leave_entry(_name: str) -> None:
        nonlocal depth
        depth -= 1

    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = count_entry
    parser.EndElementHandler = leave_entry
    try:
        parser.Parse(payload, True)
    except RuntimeError:
        raise
    except expat.ExpatError as exc:
        raise RuntimeError("feed XML is not structurally well formed") from exc
    return entry_count


def clean_text(text: str, limit: int = 280) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(text or "")))
    text = TAG_RE.sub(" ", text)
    text = "".join(
        " "
        if char in BIDI_CONTROLS or unicodedata.category(char) in {"Cc", "Cf", "Cs"}
        else char
        for char in text
    )
    text = SPACE_RE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    if limit < 3:
        return text[:max(0, limit)]
    return text[: max(0, limit - 3)].rstrip() + "..."


def markdown_inline(value, limit: int) -> str:
    text = clean_text(value, limit)
    text = text.replace("\\", "\\\\")
    return re.sub(r"([`*_\[\]<>#!|])", r"\\\1", text)


def normalize_external_url(value) -> str:
    raw = unicodedata.normalize("NFKC", html.unescape(str(value or ""))).strip()
    if (
        not raw
        or len(raw) > MAX_LINK_CHARS
        or TAG_RE.search(raw)
        or any(
            char.isspace()
            or char in BIDI_CONTROLS
            or unicodedata.category(char) in {"Cc", "Cf", "Cs"}
            for char in raw
        )
    ):
        return ""
    try:
        parsed = urlparse(raw)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return ""
        parsed.port
    except ValueError:
        return ""
    normalized = urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc,
            parsed.path,
            "",
            parsed.query,
            "",
        )
    )
    return normalized if len(normalized) <= MAX_LINK_CHARS else ""


def yaml_scalar(value, limit: int) -> str:
    return json.dumps(clean_text(value, limit), ensure_ascii=False)


def bounded_cli_int(value: str, *, minimum: int, maximum: int, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"{label} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(
            f"{label} must be between {minimum} and {maximum}"
        )
    return parsed


def regular_file_entry_exists(path: Path, *, label: str) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise OSError(f"{label} metadata is unreadable: {path}") from exc
    if is_link_like_stat(info) or not stat.S_ISREG(info.st_mode):
        raise OSError(f"unsafe {label}: {path}")
    return True


def directory_entry_exists(path: Path, *, label: str) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise OSError(f"{label} metadata is unreadable: {path}") from exc
    if is_link_like_stat(info) or not stat.S_ISDIR(info.st_mode):
        raise OSError(f"unsafe {label}: {path}")
    return True


def admit_directory_entry(path: Path, *, label: str, create: bool) -> None:
    if directory_entry_exists(path, label=label):
        return
    if not create:
        raise OSError(f"missing {label}: {path}")
    try:
        path.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise OSError(f"{label} could not be created safely: {path}") from exc
    if not directory_entry_exists(path, label=label):
        raise OSError(f"{label} was not created safely: {path}")


def ensure_bootstrap_file(path: Path, content: str) -> None:
    if regular_file_entry_exists(path, label="bootstrap file"):
        return
    atomic_write_text(path, content)


def ensure_profiles(path: Path) -> None:
    ensure_bootstrap_file(path, DEFAULT_PROFILES_BOOTSTRAP)


def utc_timestamp_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def safe_backup_label(label: str) -> str:
    label = (label or "backup").strip().lower()
    label = re.sub(r"[^a-z0-9._-]+", "-", label)
    label = label.strip("-._")
    return label[:80] or "backup"


def atomic_write_bytes(path: Path, content: bytes) -> None:
    if not isinstance(content, bytes):
        raise TypeError("atomic byte content must be bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = -1
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        with os.fdopen(fd, "wb") as fh:
            fd = -1
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass


def atomic_write_text(path: Path, content: str) -> None:
    atomic_write_bytes(path, content.encode("utf-8"))


def publication_snapshot(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> bytes | None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OSError(f"{label} metadata is unreadable: {path}") from exc
    if (
        is_link_like_stat(info)
        or not stat.S_ISREG(info.st_mode)
        or info.st_size > max_bytes
    ):
        raise OSError(f"unsafe or oversized {label}: {path}")
    payload = path.read_bytes()
    if len(payload) > max_bytes:
        raise OSError(f"oversized {label}: {path}")
    return payload


def restore_publication_snapshot(
    path: Path,
    snapshot: bytes | None,
    *,
    label: str,
) -> None:
    if snapshot is not None:
        atomic_write_bytes(path, snapshot)
        return
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise OSError(f"{label} rollback metadata is unreadable: {path}") from exc
    if is_link_like_stat(info) or not stat.S_ISREG(info.st_mode):
        raise OSError(f"unsafe {label} rollback target: {path}")
    os.unlink(path)


def read_regular_text(path: Path, *, max_bytes: int = MAX_CONFIG_BYTES) -> str:
    info = os.lstat(path)
    if (
        is_link_like_stat(info)
        or not stat.S_ISREG(info.st_mode)
        or info.st_size > max_bytes
    ):
        raise OSError(f"unsafe or oversized text file: {path}")
    payload = path.read_bytes()
    if len(payload) > max_bytes:
        raise OSError(f"oversized text file: {path}")
    return payload.decode("utf-8")


def infer_kind(url: str) -> str:
    u = url.lower()
    if "youtube.com/feeds/videos.xml" in u:
        return "youtube"
    if "arxiv.org/rss/" in u:
        return "arxiv"
    if "wikicfp" in u or "cstheoryevents" in u:
        return "cfp"
    if "mathjobs" in u or "cstheory-jobs" in u:
        return "jobs"
    if any(x in u for x in ["bbc", "nytimes", "slashdot", "howtogeek", "ycombinator", "quanta"]):
        return "news"
    return "blog"


def infer_tag_priority(url: str):
    u = url.lower()
    kind = infer_kind(url)
    if kind == "youtube":
        return "video", 2, kind
    if kind == "cfp":
        return "events", 8, kind
    if kind == "jobs":
        return "jobs", 8, kind
    if kind == "news":
        return "general", 3, kind
    if "arxiv.org/rss/cs" in u and u.rstrip("/").endswith("/rss/cs"):
        return "research", 6, "arxiv"
    if "arxiv.org/rss/" in u:
        return "research", 10, "arxiv"
    if any(x in u for x in ["theory", "graph", "math", "combin", "complexity", "cstheory", "philtcs"]):
        return "research", 9, kind
    return "research", 7, kind


def migrate_legacy_feeds(legacy_file: Path, feeds_tsv: Path, force: bool = False):
    destination_exists = regular_file_entry_exists(
        feeds_tsv,
        label="feed configuration",
    )
    if destination_exists and not force:
        return False
    if not regular_file_entry_exists(legacy_file, label="legacy feed list"):
        return False
    rows = []
    for raw in read_regular_text(legacy_file).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if len(rows) >= MAX_FEEDS:
            raise SystemExit(f"Legacy feed list exceeds the {MAX_FEEDS}-feed limit.")
        tag, priority, kind = infer_tag_priority(line)
        rows.append({
            "enabled": True,
            "tag": tag,
            "priority": priority,
            "kind": kind,
            "url": line,
            "notes": "migrated from legacy feeds.txt",
        })
    save_feeds(feeds_tsv, rows)
    return True


def ensure_feeds_tsv(feeds_tsv: Path, legacy_file: Path) -> None:
    if regular_file_entry_exists(feeds_tsv, label="feed configuration"):
        return
    if migrate_legacy_feeds(legacy_file, feeds_tsv, force=False):
        return
    ensure_bootstrap_file(feeds_tsv, DEFAULT_FEEDS_BOOTSTRAP)


def load_profiles(path: Path):
    ensure_profiles(path)
    try:
        data = json.loads(
            read_regular_text(path),
            object_pairs_hook=reject_duplicate_json_keys,
            parse_constant=reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, OSError, RecursionError, ValueError) as exc:
        raise SystemExit("RSS profile configuration is unreadable or invalid") from exc
    out = {}
    if not isinstance(data, dict) or len(data) > MAX_PROFILE_COUNT:
        raise SystemExit("RSS profile configuration is unreadable or invalid")
    for raw_name, raw_terms in data.items():
        if (
            not isinstance(raw_name, str)
            or not raw_name
            or len(raw_name) > 100
            or clean_text(raw_name, 100) != raw_name
            or not isinstance(raw_terms, list)
            or len(raw_terms) > MAX_PROFILE_TERMS
        ):
            raise SystemExit("RSS profile configuration is unreadable or invalid")
        terms = []
        seen_terms = set()
        total_term_chars = 0
        for raw_term in raw_terms:
            if (
                not isinstance(raw_term, str)
                or not raw_term
                or len(raw_term) > MAX_PROFILE_TERM_CHARS
                or clean_text(raw_term, MAX_PROFILE_TERM_CHARS) != raw_term
            ):
                raise SystemExit("RSS profile configuration is unreadable or invalid")
            term = raw_term.casefold()
            if (
                len(term) > MAX_PROFILE_TERM_CHARS
                or term in seen_terms
            ):
                raise SystemExit("RSS profile configuration is unreadable or invalid")
            total_term_chars += len(term)
            if total_term_chars > MAX_PROFILE_TOTAL_CHARS:
                raise SystemExit("RSS profile configuration is unreadable or invalid")
            seen_terms.add(term)
            terms.append(term)
        out[raw_name] = _PreparedProfileTerms(terms)
    return out


def selected_profile_terms(profiles, requested: str, path: Path) -> list[str]:
    if not requested:
        return []
    if requested not in profiles:
        raise SystemExit(
            f"RSS profile '{requested}' is not defined in {path}"
        )
    return profiles[requested]


def load_feeds(feeds_tsv: Path):
    ensure_feeds_tsv(feeds_tsv, DEFAULT_LEGACY_FEEDS_FILE)
    return parse_feeds_tsv_text(read_regular_text(feeds_tsv))


def serialize_feed_rows(rows) -> str:
    rows = canonicalize_feed_rows(rows)
    from io import StringIO
    buf = StringIO()
    writer = csv.writer(buf, delimiter="\t")
    writer.writerow(["enabled", "tag", "priority", "kind", "url", "notes"])
    for row in rows:
        writer.writerow([
            1 if row.get("enabled", True) else 0,
            row.get("tag", "research"),
            row.get("priority", 5),
            row.get("kind", infer_kind(row.get("url", ""))),
            row.get("url", ""),
            row.get("notes", ""),
        ])
    content = buf.getvalue()
    if len(content.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise SystemExit(
            f"Feed configuration exceeds the {MAX_CONFIG_BYTES}-byte limit."
        )
    return content


def save_feeds(feeds_tsv: Path, rows) -> None:
    atomic_write_text(feeds_tsv, serialize_feed_rows(rows))


def bounded_feed_field(value, *, label: str, limit: int, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise SystemExit(f"Feed {label} must be text.")
    text = unicodedata.normalize("NFKC", value).strip()
    if (
        len(text) > limit
        or any(
            char in BIDI_CONTROLS
            or unicodedata.category(char) in {"Cc", "Cf", "Cs"}
            for char in text
        )
        or (not allow_empty and not text)
    ):
        raise SystemExit(f"Feed {label} must be bounded, single-line text.")
    return text


def canonicalize_feed_rows(rows):
    if len(rows) > MAX_FEEDS:
        raise SystemExit(f"Feed configuration exceeds the {MAX_FEEDS}-feed limit.")
    normalized = []
    seen_url_identities = set()
    for row in rows:
        if not isinstance(row, dict):
            raise SystemExit("Feed configuration rows must be objects.")
        url = normalize_external_url(row.get("url", ""))
        if not url:
            raise SystemExit("Feed URL must be an absolute, bounded HTTP(S) URL.")
        url_identity = normalize_url(url)
        if url_identity in seen_url_identities:
            raise SystemExit(
                "Feed configuration contains duplicate normalized URLs."
            )
        seen_url_identities.add(url_identity)
        tag = bounded_feed_field(
            row.get("tag", "research"),
            label="tag",
            limit=max(map(len, KNOWN_TAGS)),
            allow_empty=False,
        ).lower()
        if tag not in KNOWN_TAGS:
            raise SystemExit(f"Feed tag must be one of: {', '.join(KNOWN_TAGS)}.")
        kind = bounded_feed_field(
            row.get("kind") or infer_kind(url),
            label="kind",
            limit=MAX_FEED_KIND_CHARS,
            allow_empty=False,
        ).lower()
        notes = bounded_feed_field(
            row.get("notes", ""),
            label="notes",
            limit=MAX_FEED_NOTES_CHARS,
            allow_empty=True,
        )
        try:
            priority = int(row.get("priority", 5))
        except (TypeError, ValueError, OverflowError) as exc:
            raise SystemExit("Feed priority must be an integer from 0 to 10.") from exc
        if not 0 <= priority <= 10:
            raise SystemExit("Feed priority must be an integer from 0 to 10.")
        normalized.append({
            "enabled": bool(row.get("enabled", True)),
            "tag": tag,
            "priority": priority,
            "kind": kind,
            "url": url,
            "notes": notes,
        })
    return normalized


def ensure_backup_dir(path: Path) -> Path:
    admit_directory_entry(
        path,
        label="RSS backup directory",
        create=True,
    )
    return path


def rotate_backups(
    backup_dir: Path,
    keep: int = MAX_BACKUPS,
    *,
    required: Path | None = None,
) -> None:
    if not 0 <= keep <= MAX_BACKUPS:
        raise ValueError(f"backup retention must be between 0 and {MAX_BACKUPS}")
    backups, _entry_count = _scan_backups(backup_dir)
    if required is not None:
        if required not in backups or keep == 0:
            raise OSError("new RSS backup is missing from its admitted index")
        backups = [required, *(path for path in backups if path != required)]
    for path in backups[keep:]:
        path.unlink()


def write_backup_snapshot(backup_dir: Path, content: str, reason: str = "manual") -> Path:
    ensure_backup_dir(backup_dir)
    _backups, entry_count = _scan_backups(backup_dir)
    if entry_count >= MAX_BACKUP_DIRECTORY_ENTRIES:
        raise OSError(
            "RSS backup directory has no safely indexable capacity for a new backup"
        )
    name = (
        f"feeds-{utc_timestamp_label()}-{safe_backup_label(reason)}-"
        f"{secrets.token_hex(4)}.tsv"
    )
    path = backup_dir / name
    atomic_write_text(path, content)
    rotate_backups(backup_dir, required=path)
    return path


def backup_current_feeds(feeds_tsv: Path, backup_dir: Path, reason: str = "manual"):
    if not regular_file_entry_exists(feeds_tsv, label="feed configuration"):
        return None
    return write_backup_snapshot(
        backup_dir,
        read_regular_text(feeds_tsv),
        reason=reason,
    )


def _scan_backups(backup_dir: Path) -> tuple[list[Path], int]:
    if not directory_entry_exists(
        backup_dir,
        label="RSS backup directory",
    ):
        return [], 0
    try:
        entries = backup_dir.iterdir()
    except OSError as exc:
        raise OSError(
            f"RSS backup directory is unreadable: {backup_dir}"
        ) from exc
    backups = []
    entry_count = 0
    managed_bytes = 0
    while True:
        try:
            path = next(entries)
        except StopIteration:
            break
        except OSError as exc:
            raise OSError(
                f"RSS backup directory changed while reading: {backup_dir}"
            ) from exc
        entry_count += 1
        if entry_count > MAX_BACKUP_DIRECTORY_ENTRIES:
            raise OSError(
                "RSS backup directory exceeds the complete-index entry limit"
            )
        if BACKUP_RE.fullmatch(path.name) is None:
            continue
        try:
            info = os.lstat(path)
        except OSError as exc:
            raise OSError(f"RSS backup entry is unreadable: {path}") from exc
        if is_link_like_stat(info) or not stat.S_ISREG(info.st_mode):
            raise OSError(f"unsafe managed RSS backup entry: {path}")
        if info.st_size > MAX_CONFIG_BYTES:
            raise OSError(f"oversized managed RSS backup entry: {path}")
        managed_bytes += info.st_size
        if managed_bytes > MAX_BACKUP_INDEX_BYTES:
            raise OSError("managed RSS backup index exceeds its byte limit")
        stamp = path.name[len("feeds-"):].split("-", 1)[0]
        stamp_format = "%Y%m%dT%H%M%S%fZ" if len(stamp) == 22 else "%Y%m%dT%H%M%SZ"
        try:
            backup_time = datetime.strptime(stamp, stamp_format).replace(
                tzinfo=timezone.utc
            )
        except ValueError as exc:
            raise OSError(f"invalid managed RSS backup timestamp: {path}") from exc
        if backup_time.timestamp() > time.time() + MAX_BACKUP_FUTURE_SKEW_SECONDS:
            raise OSError(f"future-dated managed RSS backup entry: {path}")
        try:
            read_regular_text(path, max_bytes=MAX_CONFIG_BYTES)
        except (OSError, UnicodeError) as exc:
            raise OSError(f"unreadable managed RSS backup entry: {path}") from exc
        backups.append(path)
    return sorted(backups, reverse=True), entry_count


def list_backups(backup_dir: Path):
    backups, _entry_count = _scan_backups(backup_dir)
    return backups


def resolve_backup_path(backup_dir: Path, value: str) -> Path:
    backups, _entry_count = _scan_backups(backup_dir)
    raw = (value or "").strip()
    if not raw:
        if not backups:
            raise SystemExit(f"No backup files found under {backup_dir}")
        return backups[0]
    if Path(raw).name != raw or BACKUP_RE.fullmatch(raw) is None:
        raise SystemExit(f"Backup not found: {value}")
    for candidate in backups:
        if candidate.name == raw:
            return candidate
    raise SystemExit(f"Backup not found: {value}")


def save_feeds_with_backup(feeds_tsv: Path, rows, backup_dir: Path, reason: str):
    content = serialize_feed_rows(rows)
    backup_path = backup_current_feeds(feeds_tsv, backup_dir, reason=reason)
    atomic_write_text(feeds_tsv, content)
    return backup_path


def parse_feeds_tsv_text(text: str):
    if len(text.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise SystemExit(f"Input TSV exceeds the {MAX_CONFIG_BYTES}-byte limit.")
    rows = []
    from io import StringIO
    reader = csv.DictReader(StringIO(text), delimiter="\t")
    if not reader.fieldnames:
        raise SystemExit("Input TSV is empty or missing a header row.")
    allowed_fields = {"enabled", "tag", "priority", "kind", "url", "notes"}
    fieldnames = list(reader.fieldnames)
    if (
        any(
            not isinstance(name, str)
            or not name
            or name != name.strip()
            for name in fieldnames
        )
        or len(fieldnames) != len(set(fieldnames))
        or any(name not in allowed_fields for name in fieldnames)
    ):
        raise SystemExit("Input TSV contains invalid, ambiguous, or padded headers.")
    if "url" not in fieldnames:
        raise SystemExit("Input TSV must contain a 'url' column.")
    while True:
        try:
            raw = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            raise SystemExit(
                "Input TSV contains an oversized or malformed field."
            ) from exc
        if None in raw:
            raise SystemExit("Input TSV row has more columns than its header.")
        url = (raw.get("url") or "").strip()
        if not url:
            continue
        url = normalize_external_url(url)
        if not url:
            raise SystemExit("Input TSV contains an invalid or oversized feed URL.")
        if len(rows) >= MAX_FEEDS:
            raise SystemExit(f"Input TSV exceeds the {MAX_FEEDS}-feed limit.")
        enabled_raw = str(raw.get("enabled") or "").strip().lower()
        if not enabled_raw:
            enabled = True
        elif enabled_raw in {"1", "true", "yes", "y", "on"}:
            enabled = True
        elif enabled_raw in {"0", "false", "no", "n", "off"}:
            enabled = False
        else:
            raise SystemExit(
                "Input TSV enabled values must be true/false or 1/0."
            )
        tag = (raw.get("tag") or "").strip().lower()
        kind = (raw.get("kind") or "").strip().lower()
        notes = (raw.get("notes") or "").strip()
        priority_raw = str(raw.get("priority") or "").strip()
        if priority_raw:
            try:
                priority = int(priority_raw)
            except (ValueError, TypeError, OverflowError) as exc:
                raise SystemExit(
                    "Input TSV priority values must be integers from 0 to 10."
                ) from exc
            if not 0 <= priority <= 10:
                raise SystemExit(
                    "Input TSV priority values must be integers from 0 to 10."
                )
        else:
            priority = infer_tag_priority(url)[1]
        if not tag:
            tag = infer_tag_priority(url)[0]
        if tag not in KNOWN_TAGS:
            raise SystemExit(
                f"Input TSV tag values must be one of: {', '.join(KNOWN_TAGS)}."
            )
        if not kind:
            kind = infer_kind(url)
        rows.append({
            "enabled": enabled,
            "tag": tag,
            "priority": priority,
            "kind": kind,
            "url": url,
            "notes": notes,
        })
    return canonicalize_feed_rows(rows)


def merge_feed_rows(base_rows, incoming_rows):
    out = []
    seen = {}
    for row in base_rows:
        norm = normalize_url(row.get("url", ""))
        if not norm:
            continue
        out.append(row)
        seen[norm] = len(out) - 1
    for row in incoming_rows:
        norm = normalize_url(row.get("url", ""))
        if not norm:
            continue
        if norm in seen:
            out[seen[norm]] = row
        else:
            out.append(row)
            seen[norm] = len(out) - 1
    return out


def bounded_state_int(value, *, maximum: int) -> int:
    try:
        return max(0, min(int(value), maximum))
    except (TypeError, ValueError, OverflowError):
        return 0


def reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def canonicalize_state(state, *, strict_counts: bool = False) -> dict:
    if not isinstance(state, dict):
        raise ValueError("RSS state must be an object")
    raw_seen = state.get("seen_order", [])
    if not isinstance(raw_seen, list):
        raise ValueError("RSS state seen_order must be a list")
    if strict_counts and len(raw_seen) > STATE_LIMIT:
        raise ValueError(
            f"RSS state seen_order exceeds the {STATE_LIMIT}-record limit"
        )
    newest_seen = []
    seen_keys = set()
    for item in reversed(raw_seen):
        if not isinstance(item, str) or len(item) > MAX_ITEM_KEY_CHARS:
            continue
        key = clean_text(item, MAX_ITEM_KEY_CHARS)
        if key and key not in seen_keys:
            newest_seen.append(key)
            seen_keys.add(key)
            if len(newest_seen) >= STATE_LIMIT:
                break
    seen_order = list(reversed(newest_seen))
    raw_feeds = state.get("feeds", {})
    if not isinstance(raw_feeds, dict):
        raise ValueError("RSS state feeds must be an object")
    if strict_counts and len(raw_feeds) > MAX_FEEDS:
        raise ValueError(
            f"RSS state feeds exceeds the {MAX_FEEDS}-record limit"
        )
    safe_feeds = {}
    for raw_url, meta in list(raw_feeds.items())[-MAX_FEEDS:]:
        url = normalize_external_url(raw_url) if isinstance(raw_url, str) else ""
        if not url or not isinstance(meta, dict):
            continue
        safe_feeds[url] = {
            "last_fetch": clean_text(meta.get("last_fetch", ""), 100),
            "tag": clean_text(meta.get("tag", ""), 80),
            "kind": clean_text(meta.get("kind", ""), MAX_FEED_KIND_CHARS),
            "priority": bounded_state_int(meta.get("priority", 0), maximum=10),
            "failure_count": bounded_state_int(
                meta.get("failure_count", 0), maximum=1_000_000
            ),
            "last_error": clean_text(meta.get("last_error", ""), 500),
            "last_success": clean_text(meta.get("last_success", ""), 100),
            "last_feed_title": clean_text(
                meta.get("last_feed_title", ""), MAX_FEED_TITLE_CHARS
            ),
            "last_item_time": clean_text(meta.get("last_item_time", ""), 100),
            "last_entry_count": bounded_state_int(
                meta.get("last_entry_count", 0), maximum=1_000_000
            ),
            "last_new_items": bounded_state_int(
                meta.get("last_new_items", 0), maximum=1_000_000
            ),
        }
    return {"seen_order": seen_order, "feeds": safe_feeds}


def _encode_state(seen_order: list[str], feed_items: list[tuple[str, dict]]) -> str:
    return json.dumps(
        {"seen_order": seen_order, "feeds": dict(feed_items)},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def serialize_state(state) -> str:
    safe = canonicalize_state(state)
    seen_order = safe["seen_order"]
    feed_items = list(safe["feeds"].items())
    content = _encode_state(seen_order, feed_items)
    if len(content.encode("utf-8")) <= MAX_STATE_BYTES:
        return content

    # Dedup history is the primary state. Drop the oldest feed-health entries
    # first, then the oldest seen identifiers only if the history alone cannot
    # fit. Both collections are rolling bounded histories.
    low, high = 0, len(feed_items)
    best = None
    while low <= high:
        drop = (low + high) // 2
        candidate = _encode_state(seen_order, feed_items[drop:])
        if len(candidate.encode("utf-8")) <= MAX_STATE_BYTES:
            best = candidate
            high = drop - 1
        else:
            low = drop + 1
    if best is not None:
        return best

    low, high = 0, len(seen_order)
    kept_seen = []
    while low <= high:
        drop = (low + high) // 2
        candidate_seen = seen_order[drop:]
        candidate = _encode_state(candidate_seen, [])
        if len(candidate.encode("utf-8")) <= MAX_STATE_BYTES:
            kept_seen = candidate_seen
            high = drop - 1
        else:
            low = drop + 1

    low, high = 0, len(feed_items)
    best = _encode_state(kept_seen, [])
    while low <= high:
        drop = (low + high) // 2
        candidate = _encode_state(kept_seen, feed_items[drop:])
        if len(candidate.encode("utf-8")) <= MAX_STATE_BYTES:
            best = candidate
            high = drop - 1
        else:
            low = drop + 1
    return best


def load_state(path: Path):
    try:
        text = read_regular_text(path, max_bytes=MAX_STATE_BYTES)
    except FileNotFoundError:
        return {"seen_order": [], "feeds": {}}
    try:
        data = json.loads(
            text,
            parse_constant=reject_json_constant,
            object_pairs_hook=reject_duplicate_json_keys,
        )
        return canonicalize_state(data, strict_counts=True)
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise SystemExit("RSS state is unreadable or invalid") from exc
    except OSError as exc:
        raise SystemExit("RSS state is unsafe or unreadable") from exc


def save_state(path: Path, state):
    try:
        os.lstat(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise SystemExit("RSS state metadata is unreadable") from exc
    else:
        # Refuse to replace a state entry that has become unsafe or corrupt,
        # including between a command's initial load and its final publication.
        load_state(path)
    atomic_write_text(path, serialize_state(state))


def normalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    try:
        host = (parsed.hostname or "").casefold().rstrip(".")
    except ValueError:
        host = ""
    host_tracking_keys = (
        {"feature", "si"}
        if host in {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "youtube-nocookie.com",
            "www.youtube-nocookie.com",
            "youtu.be",
            "www.youtu.be",
        }
        else set()
    )
    filtered_query = []
    for segment in parsed.query.split("&"):
        raw_key, separator, raw_value = segment.partition("=")
        try:
            key = unquote_plus(raw_key, encoding="utf-8", errors="strict")
            if separator:
                unquote_plus(raw_value, encoding="utf-8", errors="strict")
        except UnicodeError:
            filtered_query = None
            break
        if key.lower().startswith("utm_"):
            continue
        if key.lower() in host_tracking_keys:
            continue
        filtered_query.append(segment)
    query_str = parsed.query if filtered_query is None else "&".join(filtered_query)
    return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path, "", query_str, ""))


def entry_timestamp(entry) -> float:
    def representable_utc_timestamp(value):
        timestamp = float(value)
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        if timestamp > now_ts() + MAX_FUTURE_CLOCK_SKEW_SECONDS:
            return 0.0
        return timestamp

    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct:
            try:
                return representable_utc_timestamp(calendar.timegm(struct))
            except (OSError, TypeError, ValueError, OverflowError):
                pass
    for key in ("published", "updated"):
        raw = entry.get(key)
        if raw:
            try:
                parsed = parsedate_to_datetime(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return representable_utc_timestamp(parsed.timestamp())
            except (OSError, TypeError, ValueError, OverflowError):
                pass
    return 0.0


def bounded_entry_text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return clean_text(value[:limit], limit)


def _parsed_item_url(value):
    if not isinstance(value, str) or not value or len(value) > MAX_LINK_CHARS:
        return None
    try:
        parsed = urlparse(value.strip())
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        parsed.port
        return parsed
    except ValueError:
        return None


def _canonical_special_item_key(kind: str, link: str, ident: str) -> str:
    def bounded_key(prefix: str, value: str) -> str:
        candidate = f"{prefix}:{value}"
        if len(candidate) <= MAX_ITEM_KEY_CHARS:
            return candidate
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        return f"{prefix}:sha256:{digest}"

    parsed_values = [
        parsed
        for parsed in (_parsed_item_url(link), _parsed_item_url(ident))
        if parsed is not None
    ]
    if kind == "arxiv":
        for parsed in parsed_values:
            host = (parsed.hostname or "").casefold().rstrip(".")
            if host not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
                continue
            match = ARXIV_ITEM_PATH_RE.fullmatch(parsed.path)
            if match:
                return bounded_key("arxiv", match.group(1))
    if kind == "youtube":
        for parsed in parsed_values:
            host = (parsed.hostname or "").casefold().rstrip(".")
            video_id = ""
            if host in {"youtu.be", "www.youtu.be"}:
                path = parsed.path.strip("/")
                if "/" not in path and YOUTUBE_ID_RE.fullmatch(path):
                    video_id = path
            elif host in {
                "youtube.com",
                "www.youtube.com",
                "m.youtube.com",
                "youtube-nocookie.com",
                "www.youtube-nocookie.com",
            }:
                if parsed.path.rstrip("/") == "/watch":
                    for name, value in parse_qsl(parsed.query, keep_blank_values=True):
                        if name == "v" and YOUTUBE_ID_RE.fullmatch(value):
                            video_id = value
                            break
                else:
                    match = YOUTUBE_VIDEO_PATH_RE.fullmatch(parsed.path)
                    if match:
                        video_id = match.group(1)
            if video_id:
                return bounded_key("yt", video_id)
    for parsed in parsed_values:
        host = (parsed.hostname or "").casefold().rstrip(".")
        if host != "stackexchange.com" and not host.endswith(".stackexchange.com"):
            continue
        match = STACKEXCHANGE_ITEM_PATH_RE.fullmatch(parsed.path)
        if match:
            return bounded_key("stackexchange", f"{host}:{match.group(1)}")
    return ""


def dedup_key(kind: str, entry) -> str:
    link = entry.get("link", "")
    ident = entry.get("id", "")
    title = entry.get("title", "")
    link = link if isinstance(link, str) else ""
    ident = ident if isinstance(ident, str) else ""
    title = title if isinstance(title, str) else ""
    identity = json.dumps(
        [
            "rss-item-v2",
            kind.casefold(),
            ident.strip(),
            normalize_url(link),
            title.strip().casefold(),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    special_key = _canonical_special_item_key(kind, link, ident)
    if special_key:
        return special_key
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def keyword_bonus(profile_terms, text: str) -> int:
    profile_terms = prepare_profile_terms(profile_terms)
    if not profile_terms:
        return 0
    hay = bounded_entry_text(text, MAX_SCORE_TEXT_CHARS).casefold()
    hits = 0
    for term in profile_terms:
        if term in hay:
            hits += 1
            if hits >= 5:
                return 40
    return min(40, hits * 8)


def prepare_profile_terms(profile_terms) -> _PreparedProfileTerms:
    if isinstance(profile_terms, _PreparedProfileTerms):
        return profile_terms
    if not profile_terms:
        return _PreparedProfileTerms()
    prepared = []
    seen = set()
    total_chars = 0
    for index, raw_term in enumerate(profile_terms):
        if index >= MAX_PROFILE_TERMS:
            raise ValueError(
                f"profile exceeds the {MAX_PROFILE_TERMS}-term limit"
            )
        term = bounded_entry_text(raw_term, MAX_PROFILE_TERM_CHARS).casefold()
        if not term or len(term) > MAX_PROFILE_TERM_CHARS or term in seen:
            continue
        total_chars += len(term)
        if total_chars > MAX_PROFILE_TOTAL_CHARS:
            raise ValueError(
                "profile exceeds the aggregate prepared-character limit"
            )
        seen.add(term)
        prepared.append(term)
    return _PreparedProfileTerms(prepared)


def freshness_bonus(ts: float, tag: str) -> int:
    if ts <= 0:
        return 0
    age_seconds = now_ts() - ts
    if age_seconds < -MAX_FUTURE_CLOCK_SKEW_SECONDS:
        return 0
    age_hours = max(0.0, age_seconds / 3600.0)
    if age_hours <= 12:
        bonus = 40
    elif age_hours <= 24:
        bonus = 30
    elif age_hours <= 72:
        bonus = 20
    elif age_hours <= 168:
        bonus = 10
    else:
        bonus = 0
    if tag == "video":
        bonus = min(bonus, 20)
    return bonus


def compute_score(feed_row, entry, profile_terms):
    text = " ".join([
        bounded_entry_text(entry.get("title", ""), MAX_TITLE_CHARS),
        bounded_entry_text(entry.get("summary", ""), MAX_SUMMARY_CHARS),
        bounded_entry_text(entry.get("description", ""), MAX_SUMMARY_CHARS),
    ])
    score = int(feed_row["priority"]) * 100
    score += freshness_bonus(entry_timestamp(entry), feed_row["tag"])
    score += keyword_bonus(profile_terms, text)
    if feed_row["tag"] == "video":
        score -= 15
    if feed_row["tag"] == "general":
        score -= 10
    return max(0, min(score, 1_000_000))


def iso_dt(ts: float):
    if ts <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    except (OSError, TypeError, ValueError, OverflowError):
        return ""


def build_digest(
    title: str,
    items,
    profile_name: str = "",
    *,
    discovery_failed: bool = False,
) -> str:
    lines = [
        "---",
        "artifact_role: raw_external_digest",
        "style_applied: false",
        "source_schema: digest-items.v1",
        "---",
        "",
        f"# {markdown_inline(title, 200)}",
        "",
        (
            "This raw artifact contains normalized, untrusted external source data; "
            "machine consumers must use the JSON sidecar."
        ),
        "",
    ]
    if profile_name:
        lines.append(f"Profile: {markdown_inline(profile_name, 100)}")
        lines.append("")
    if not items:
        lines.append(
            "Discovery failed for every attempted feed."
            if discovery_failed
            else "No new items found."
        )
        lines.append("")
        return "\n".join(lines)
    for idx, item in enumerate(items, 1):
        score = item.get("score", 0)
        if isinstance(score, bool) or not isinstance(score, int):
            score = 0
        score = max(0, min(score, 1_000_000))
        priority = item.get("priority", 0)
        if isinstance(priority, bool) or not isinstance(priority, int):
            priority = 0
        lines.append(f"## {idx}. {markdown_inline(item.get('title'), MAX_TITLE_CHARS)}")
        lines.append(f"- Feed: {markdown_inline(item.get('feed_title'), MAX_FEED_TITLE_CHARS)}")
        lines.append(f"- Tag: {markdown_inline(item.get('tag'), 80)}")
        lines.append(f"- Priority: {max(0, min(10, priority))}")
        lines.append(f"- Score: {score}")
        if item.get("published"):
            lines.append(f"- Published: {markdown_inline(item.get('published'), 100)}")
        link = normalize_external_url(item.get("link"))
        if link:
            lines.append(f"- Link: {markdown_inline(link, MAX_LINK_CHARS)}")
        lines.append("")
        summary = (
            markdown_inline(item.get("summary"), MAX_SUMMARY_CHARS)
            or "No summary available."
        )
        lines.append(f"> Untrusted source summary: {summary}")
        lines.append("")
    return "\n".join(lines)


def _compact_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_digest_sidecar(items, source: str, run_status: dict | None = None) -> dict:
    input_count = min(len(items), 1_000_000)
    sidecar = {
        "schema_version": "digest-items.v1",
        "artifact_role": "raw_external_digest",
        "style_applied": False,
        "source": clean_text(source, 200),
        "run_status": run_status or {},
        "input_item_count": input_count,
        "truncated": False,
        "items": [],
    }
    safe_items = []
    used_bytes = len(_compact_json(sidecar).encode("utf-8"))
    for item in items:
        if len(safe_items) >= MAX_DIGEST_ITEMS:
            sidecar["truncated"] = True
            break
        title = clean_text(item.get("title"), MAX_TITLE_CHARS)
        if not title:
            sidecar["truncated"] = True
            continue
        score = item.get("score", 0)
        if isinstance(score, bool) or not isinstance(score, int):
            score = 0
        safe_item = {
            "title": title,
            "link": normalize_external_url(item.get("link")),
            "score": max(0, min(score, 1_000_000)),
            "source": clean_text(item.get("feed_title"), MAX_FEED_TITLE_CHARS),
        }
        item_bytes = len(_compact_json(safe_item).encode("utf-8"))
        added_bytes = item_bytes + (1 if safe_items else 0)
        if used_bytes + added_bytes > MAX_SIDECAR_BYTES:
            sidecar["truncated"] = True
            break
        safe_items.append(safe_item)
        used_bytes += added_bytes
    sidecar["items"] = safe_items
    if len(safe_items) < input_count:
        sidecar["truncated"] = True
    if len(_compact_json(sidecar).encode("utf-8")) > MAX_SIDECAR_BYTES:
        raise ValueError("digest sidecar byte accounting exceeded its hard limit")
    return sidecar


def write_digest(path: Path, content: str) -> None:
    atomic_write_text(path, content)


def write_digest_pair(
    path: Path,
    title: str,
    items,
    profile_name: str = "",
    *,
    discovery_failed: bool = False,
    run_status: dict | None = None,
) -> Path:
    markdown_text, sidecar_text = build_digest_pair_texts(
        path,
        title,
        items,
        profile_name=profile_name,
        discovery_failed=discovery_failed,
        run_status=run_status,
    )
    write_digest(path, markdown_text)
    sidecar_path = path.with_suffix(".json")
    atomic_write_text(sidecar_path, sidecar_text)
    return sidecar_path


def build_digest_pair_texts(
    path: Path,
    title: str,
    items,
    profile_name: str = "",
    *,
    discovery_failed: bool = False,
    run_status: dict | None = None,
) -> tuple[str, str]:
    markdown_text = build_digest(
        title,
        items,
        profile_name=profile_name,
        discovery_failed=discovery_failed,
    )
    if len(markdown_text.encode("utf-8")) > MAX_DIGEST_MARKDOWN_BYTES:
        raise ValueError(
            "digest Markdown exceeds the "
            f"{MAX_DIGEST_MARKDOWN_BYTES}-byte limit"
        )
    sidecar_text = _compact_json(
        build_digest_sidecar(items, path.stem, run_status=run_status)
    )
    if len(sidecar_text.encode("utf-8")) > MAX_SIDECAR_BYTES:
        raise ValueError(
            f"digest sidecar exceeds the {MAX_SIDECAR_BYTES}-byte limit"
        )
    return markdown_text, sidecar_text


def validate_summary_run_status(run_status, *, path: Path) -> None:
    if not isinstance(run_status, dict):
        raise ValueError(f"invalid digest sidecar run status: {path}")
    ok = run_status.get("ok")
    degraded = run_status.get("degraded")
    if not isinstance(ok, bool) or not isinstance(degraded, bool):
        raise ValueError(f"invalid digest sidecar run status: {path}")
    counts = {}
    for field in (
        "attempted_feeds",
        "failed_feeds",
        "warning_feeds",
        "stub_failures",
    ):
        value = run_status.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= 1_000_000
        ):
            raise ValueError(f"invalid digest sidecar run status: {path}")
        counts[field] = value
    if (
        counts["failed_feeds"] + counts["warning_feeds"]
        > counts["attempted_feeds"]
        or (counts["stub_failures"] > 0 and ok)
        or (
            counts["attempted_feeds"] > 0
            and counts["failed_feeds"] == counts["attempted_feeds"]
            and ok
        )
    ):
        raise ValueError(f"inconsistent digest sidecar run status: {path}")
    if not ok:
        raise ValueError(f"digest sidecar reports failed publication: {path}")


def load_summary_sidecar(path: Path, *, expected_source: str) -> list[dict]:
    try:
        payload = json.loads(
            read_regular_text(path, max_bytes=MAX_SIDECAR_BYTES),
            object_pairs_hook=reject_duplicate_json_keys,
            parse_constant=reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, OSError, RecursionError, ValueError) as exc:
        raise ValueError(f"unreadable digest sidecar: {path}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "digest-items.v1"
        or payload.get("artifact_role") != "raw_external_digest"
        or payload.get("style_applied") is not False
        or payload.get("source") != expected_source
    ):
        raise ValueError(f"invalid digest sidecar metadata: {path}")
    validate_summary_run_status(payload.get("run_status"), path=path)
    items = payload.get("items")
    if not isinstance(items, list) or len(items) > MAX_DIGEST_ITEMS:
        raise ValueError(f"invalid digest sidecar items: {path}")
    safe_items = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"digest sidecar item {index} is not an object: {path}")
        raw_title = item.get("title")
        raw_link = item.get("link")
        if not isinstance(raw_title, str) or not isinstance(raw_link, str):
            raise ValueError(
                f"digest sidecar item {index} has invalid field types: {path}"
            )
        title = clean_text(raw_title, MAX_TITLE_CHARS)
        if not title:
            raise ValueError(f"digest sidecar item {index} has no title: {path}")
        link = normalize_external_url(raw_link)
        if raw_link and not link:
            raise ValueError(f"digest sidecar item {index} has an invalid link: {path}")
        safe_items.append({"title": title, "link": link})
    return safe_items


def build_raw_sidecar_summary(tagged_items: dict[str, list[dict]], *, created_at: str) -> str:
    lines = [
        "---",
        "artifact_role: raw_external_digest",
        "style_applied: false",
        "source_schema: digest-items.v1",
        "---",
        "",
        f"# RSS Digest Raw Summary - {markdown_inline(created_at, 100)}",
        "",
        (
            "This is a normalized view of untrusted external source data. "
            "It is not a final user-facing synthesis."
        ),
        "",
    ]
    wrote_item = False
    for tag in KNOWN_TAGS:
        items = tagged_items.get(tag, [])
        if not items:
            continue
        wrote_item = True
        lines.extend([f"## {markdown_inline(tag, 80)}", ""])
        for item in items:
            lines.append(f"- {markdown_inline(item.get('title'), MAX_TITLE_CHARS)}")
            link = normalize_external_url(item.get("link"))
            if link:
                lines.append(f"  - Link: {markdown_inline(link, MAX_LINK_CHARS)}")
        lines.append("")
    if not wrote_item:
        lines.extend(["No new per-tag items found.", ""])
    return "\n".join(lines)


def paths_alias(left: Path, right: Path) -> bool:
    try:
        left_real = os.path.normcase(os.path.realpath(os.path.abspath(left)))
        right_real = os.path.normcase(os.path.realpath(os.path.abspath(right)))
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("summary output path cannot be resolved safely") from exc
    if left_real == right_real:
        return True
    try:
        return os.path.samefile(left, right)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError("summary output path cannot be compared safely") from exc


def validate_summary_output_path(output: Path, args) -> None:
    try:
        output_absolute = Path(os.path.abspath(output))
        digest_absolute = Path(os.path.abspath(args.digest_dir))
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("summary output path cannot be resolved safely") from exc
    if output_absolute.parent != digest_absolute or not output_absolute.name:
        raise ValueError(
            "summary output must be a direct child of the RSS digest directory"
        )
    protected = [
        args.digest_dir / f"rss-{owner}.{suffix}"
        for owner in [*KNOWN_TAGS, "all"]
        for suffix in ("json", "md")
    ]
    for attribute in (
        "feeds_tsv",
        "legacy_feeds_file",
        "profiles_file",
        "state_file",
    ):
        value = getattr(args, attribute, None)
        if value is not None:
            protected.append(Path(value))
    protected.extend([
        _WORKSPACE / "data" / "research" / "rss" / "ingested.json",
        _WORKSPACE / "data" / "library" / "ingested.json",
    ])
    for path in protected:
        if paths_alias(output_absolute, path):
            raise ValueError(f"summary output collides with an owned artifact: {path}")


def cmd_summarize_sidecars(args):
    tagged_items = {}
    sidecars = []
    output = args.output or (args.digest_dir / "last-summary.md")
    try:
        validate_summary_output_path(output, args)
    except (OSError, ValueError) as exc:
        print(json.dumps({
            "status": "error",
            "error_code": "invalid_summary_output",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    try:
        admit_directory_entry(
            args.digest_dir,
            label="RSS digest directory",
            create=False,
        )
        for tag in KNOWN_TAGS:
            path = args.digest_dir / f"rss-{tag}.json"
            if not regular_file_entry_exists(path, label="digest sidecar"):
                continue
            items = load_summary_sidecar(
                path,
                expected_source=f"rss-{tag}",
            )
            tagged_items[tag] = items[: args.max_per_tag]
            sidecars.append(str(path))
        aggregate = args.digest_dir / "rss-all.json"
        if regular_file_entry_exists(aggregate, label="digest sidecar"):
            load_summary_sidecar(aggregate, expected_source="rss-all")
            sidecars.append(str(aggregate))
    except (OSError, ValueError) as exc:
        print(json.dumps({
            "status": "error",
            "error_code": "invalid_digest_sidecar",
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    if not sidecars:
        print(json.dumps({
            "status": "error",
            "error_code": "missing_digest_sidecar",
            "error": f"no RSS digest sidecars found under {args.digest_dir}",
        }, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    summary = build_raw_sidecar_summary(tagged_items, created_at=created_at)
    atomic_write_text(output, summary)
    history = None
    if not args.no_history:
        history = args.digest_dir / (
            f"summary-{utc_timestamp_label()}-{secrets.token_hex(4)}.md"
        )
        atomic_write_text(history, summary)
    print(json.dumps({
        "status": "ok",
        "output": str(output),
        "history": str(history) if history else None,
        "sidecars": sidecars,
        "artifact_role": "raw_external_digest",
        "style_applied": False,
    }, ensure_ascii=False, indent=2))


def fetch_items(feeds, state, per_feed_limit: int, summary_limit: int, selected_tag: str = "", profile_terms=None, include_disabled: bool = False, mark_seen: bool = True, parallel=0):
    feedparser = ensure_feedparser()
    seen_order = list(state.get("seen_order", []))
    seen_set = set(seen_order)
    run_seen = set()
    profile_terms = prepare_profile_terms(profile_terms)
    items = []
    health_rows = []
    feeds_state = state.setdefault("feeds", {})

    # Filter active feeds
    active_feeds = [f for f in feeds
                    if (include_disabled or f["enabled"])
                    and (not selected_tag or f["tag"] == selected_tag)]
    response_budget = _AggregateResponseBudget(MAX_RUN_RESPONSE_BYTES)

    # Parallel feed fetching (I/O-bound)
    def _fetch_one(url):
        try:
            raw = fetch_feed_bytes(url, aggregate_budget=response_budget)
            reject_unsafe_feed_xml(raw)
            enforce_feed_entry_limit(raw)
            parsed = feedparser.parse(raw)
            parsed_entries = getattr(parsed, "entries", None)
            if (
                isinstance(parsed_entries, (list, tuple))
                and len(parsed_entries) > MAX_PARSED_FEED_ENTRIES
            ):
                raise RuntimeError(
                    "parsed feed exceeds the "
                    f"{MAX_PARSED_FEED_ENTRIES}-entry limit"
                )
            return parsed
        except (
            http.client.HTTPException,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            return {
                "bozo": True,
                "bozo_exception": exc,
                "entries": [],
                "feed": {},
            }

    _parallel = parallel
    if _parallel == 0:
        _cpus = os.cpu_count() or 2
        _parallel = min(_cpus * 2, 16)
    def _iter_parsed_results():
        if _parallel <= 1 or len(active_feeds) <= 1:
            for feed_row in active_feeds:
                yield _fetch_one(feed_row["url"])
            return

        from concurrent.futures import ThreadPoolExecutor

        urls = [feed_row["url"] for feed_row in active_feeds]
        worker_count = min(_parallel, len(urls))
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            pending = [
                pool.submit(_fetch_one, url)
                for url in urls[:worker_count]
            ]
            next_index = worker_count
            while pending:
                future = pending.pop(0)
                yield future.result()
                if next_index < len(urls):
                    pending.append(pool.submit(_fetch_one, urls[next_index]))
                    next_index += 1

    parsed_results = _iter_parsed_results()

    # Process results sequentially (state mutation)
    for feed_row, parsed in zip(active_feeds, parsed_results):
        url = feed_row["url"]
        kind = feed_row["kind"]
        started = now_ts()
        status = "ok"
        last_error = ""
        entry_count = 0
        if not parsed.get("version") or (
            parsed.get("bozo") and not parsed.get("entries")
        ):
            status = "error"
            last_error = clean_text(
                parsed.get("bozo_exception") or "unrecognized or malformed feed",
                500,
            )

        # Assignment does not refresh an existing dict key's insertion order.
        # Move every feed touched by this run to the tail so bounded state
        # compaction retains current health rather than an equally sized stale
        # prefix.
        meta = feeds_state.pop(url, {})
        if not isinstance(meta, dict):
            meta = {}
        feeds_state[url] = meta
        meta["last_fetch"] = iso_dt(started)
        meta["tag"] = feed_row["tag"]
        meta["kind"] = kind
        meta["priority"] = feed_row["priority"]

        if status == "error" or parsed is None:
            meta["failure_count"] = int(meta.get("failure_count", 0)) + 1
            meta["last_error"] = last_error or "parse failed"
            health_rows.append({
                "tag": feed_row["tag"],
                "kind": kind,
                "url": url,
                "status": "error",
                "entries": 0,
                "new_items": 0,
                "last_success": meta.get("last_success", ""),
                "failure_count": meta.get("failure_count", 0),
                "last_error": meta.get("last_error", ""),
            })
            continue

        if getattr(parsed, "bozo", False):
            exc = getattr(parsed, "bozo_exception", None)
            if exc:
                status = "warning"
                last_error = clean_text(exc, 500)

        feed_title = clean_text(parsed.feed.get("title", url), MAX_FEED_TITLE_CHARS)
        recent_entries = list(
            itertools.islice(parsed.entries, max(0, per_feed_limit))
        )
        entry_count = len(recent_entries)
        new_items = 0
        last_ts = 0.0

        for entry in recent_entries:
            if len(items) >= MAX_RUN_ITEMS:
                status = "warning"
                last_error = f"run item limit reached ({MAX_RUN_ITEMS})"
                break
            ts = entry_timestamp(entry)
            last_ts = max(last_ts, ts)
            key = dedup_key(kind, entry)
            if key in seen_set or key in run_seen:
                continue
            score = compute_score(feed_row, entry, profile_terms)
            item = {
                "key": key,
                "tag": feed_row["tag"],
                "kind": kind,
                "priority": feed_row["priority"],
                "score": score,
                "source_url": normalize_external_url(url),
                "feed_title": feed_title,
                "title": clean_text(
                    entry.get("title") or "(untitled)",
                    MAX_TITLE_CHARS,
                ),
                "link": normalize_external_url(entry.get("link", "")),
                "summary": clean_text(
                    entry.get("summary", "") or entry.get("description", ""),
                    limit=min(max(0, summary_limit), MAX_SUMMARY_CHARS),
                ),
                # Display exactly the timestamp admitted for freshness and
                # ordering. Raw date strings may disagree with parsed fields
                # and must not reintroduce a rejected far-future value.
                "published": iso_dt(ts),
                "timestamp": ts,
            }
            items.append(item)
            run_seen.add(key)
            if mark_seen:
                seen_order.append(key)
                seen_set.add(key)
            new_items += 1

        if status == "ok":
            meta["last_success"] = iso_dt(now_ts())
            meta["failure_count"] = 0
            meta["last_error"] = ""
        else:
            meta["last_error"] = last_error
            meta["failure_count"] = int(meta.get("failure_count", 0))
        meta["last_feed_title"] = feed_title
        meta["last_item_time"] = iso_dt(last_ts)
        meta["last_entry_count"] = entry_count
        meta["last_new_items"] = new_items
        health_rows.append({
            "tag": feed_row["tag"],
            "kind": kind,
            "url": url,
            "status": status,
            "entries": entry_count,
            "new_items": new_items,
            "last_success": meta.get("last_success", ""),
            "failure_count": meta.get("failure_count", 0),
            "last_error": meta.get("last_error", ""),
        })

    state["seen_order"] = seen_order[-STATE_LIMIT:]
    return items, health_rows


def cmd_run(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    ensure_profiles(args.profiles_file)
    feeds = load_feeds(args.feeds_tsv)
    state = load_state(args.state_file)
    original_seen_order = list(state.get("seen_order", []))
    profiles = load_profiles(args.profiles_file)
    profile_terms = []
    active_profile = ""
    if args.profile:
        profile_terms = selected_profile_terms(
            profiles,
            args.profile,
            args.profiles_file,
        )
        active_profile = args.profile

    admit_directory_entry(
        args.digest_dir,
        label="RSS digest directory",
        create=True,
    )

    publication_stems = (
        [f"rss-{tag}" for tag in KNOWN_TAGS] + ["rss-all"]
        if args.all_tags
        else [f"rss-{args.tag}"]
    )
    sidecar_snapshots = {}
    for stem in publication_stems:
        markdown_path = args.digest_dir / f"{stem}.md"
        sidecar_path = args.digest_dir / f"{stem}.json"
        publication_snapshot(
            markdown_path,
            label=f"RSS Markdown for {stem}",
            max_bytes=MAX_DIGEST_MARKDOWN_BYTES,
        )
        sidecar_snapshots[sidecar_path] = publication_snapshot(
            sidecar_path,
            label=f"RSS sidecar for {stem}",
            max_bytes=MAX_SIDECAR_BYTES,
        )
    state_snapshot = publication_snapshot(
        args.state_file,
        label="RSS state",
        max_bytes=MAX_STATE_BYTES,
    )

    selected_tag = "" if args.all_tags else args.tag
    items, health_rows = fetch_items(
        feeds=feeds,
        state=state,
        per_feed_limit=args.per_feed_limit,
        summary_limit=args.summary_limit,
        selected_tag=selected_tag,
        profile_terms=profile_terms,
        include_disabled=args.include_disabled,
        mark_seen=not args.no_mark_seen,
        parallel=getattr(args, "parallel", 0),
    )

    items.sort(key=lambda item: (item["score"], item["timestamp"]), reverse=True)
    all_failed = bool(health_rows) and all(
        row.get("status") == "error" for row in health_rows
    )
    feed_degraded = any(
        row.get("status") in {"error", "warning"} for row in health_rows
    )
    failed_feeds = sum(row.get("status") == "error" for row in health_rows)
    warning_feeds = sum(row.get("status") == "warning" for row in health_rows)
    unwritten = _write_digest_stubs(items)
    for problem in unwritten:
        print(f"WARNING: digest stub not written -- {problem}", file=sys.stderr)
    stub_failed = bool(unwritten)
    if stub_failed:
        # The feed item must remain eligible for a retry. Some stubs may have
        # landed, but `_write_digest_stubs` can heal their ledger entries on
        # the retry once the failed boundary becomes writable again.
        state["seen_order"] = original_seen_order
    failed = all_failed or stub_failed
    degraded = feed_degraded or stub_failed
    run_status = {
        "ok": not failed,
        "degraded": degraded,
        "attempted_feeds": len(health_rows),
        "failed_feeds": failed_feeds,
        "warning_feeds": warning_feeds,
        "stub_failures": len(unwritten),
    }

    by_tag = {}
    for item in items:
        by_tag.setdefault(item["tag"], []).append(item)

    outputs = {}
    sidecars = {}
    publication_plan = []
    if args.all_tags:
        for tag in KNOWN_TAGS:
            path = args.digest_dir / f"rss-{tag}.md"
            selected_items = by_tag.get(tag, [])[: args.max_items]
            markdown_text, sidecar_text = build_digest_pair_texts(
                path,
                f"RSS Digest: {tag}",
                selected_items,
                profile_name=active_profile,
                discovery_failed=all_failed,
                run_status=run_status,
            )
            sidecar = path.with_suffix(".json")
            publication_plan.append((path, markdown_text, sidecar, sidecar_text))
            outputs[tag] = str(path)
            sidecars[tag] = str(sidecar)
        all_path = args.digest_dir / "rss-all.md"
        markdown_text, sidecar_text = build_digest_pair_texts(
            all_path,
            "RSS Digest: all",
            items[: args.max_items],
            profile_name=active_profile,
            discovery_failed=all_failed,
            run_status=run_status,
        )
        all_sidecar = all_path.with_suffix(".json")
        publication_plan.append(
            (all_path, markdown_text, all_sidecar, sidecar_text)
        )
        outputs["all"] = str(all_path)
        sidecars["all"] = str(all_sidecar)
    else:
        out_path = args.digest_dir / f"rss-{args.tag}.md"
        markdown_text, sidecar_text = build_digest_pair_texts(
            out_path,
            f"RSS Digest: {args.tag}",
            by_tag.get(args.tag, [])[: args.max_items],
            profile_name=active_profile,
            discovery_failed=all_failed,
            run_status=run_status,
        )
        sidecar = out_path.with_suffix(".json")
        publication_plan.append((out_path, markdown_text, sidecar, sidecar_text))
        outputs[args.tag] = str(out_path)
        sidecars[args.tag] = str(sidecar)

    # Force all bounded serialization before publishing the first digest file.
    serialize_state(state)
    for path, markdown_text, _sidecar, _sidecar_text in publication_plan:
        atomic_write_text(path, markdown_text)
    save_state(args.state_file, state)
    try:
        for _path, _markdown_text, sidecar, sidecar_text in publication_plan:
            atomic_write_text(sidecar, sidecar_text)
    except Exception:
        rollback_errors = []
        for sidecar, snapshot in sidecar_snapshots.items():
            try:
                restore_publication_snapshot(
                    sidecar,
                    snapshot,
                    label="RSS sidecar",
                )
            except Exception as rollback_exc:
                rollback_errors.append(rollback_exc)
        try:
            restore_publication_snapshot(
                args.state_file,
                state_snapshot,
                label="RSS state",
            )
        except Exception as rollback_exc:
            rollback_errors.append(rollback_exc)
        if rollback_errors:
            raise RuntimeError(
                "RSS sidecar publication failed and exact publication "
                "rollback also failed"
            ) from rollback_errors[0]
        raise
    print(json.dumps({
        "ok": not failed,
        "status": "error" if failed else "ok",
        "degraded": degraded,
        "tag": "all" if args.all_tags else args.tag,
        "profile": args.profile,
        "count": len(items),
        "outputs": outputs,
        "sidecars": sidecars,
        "artifact_role": "raw_external_digest",
        "style_applied": False,
        "source_status": health_rows,
        "stub_errors": [clean_text(problem, 500) for problem in unwritten],
    }, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)


def cmd_doctor(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    ensure_profiles(args.profiles_file)
    feeds = load_feeds(args.feeds_tsv)
    state = load_state(args.state_file)
    profiles = load_profiles(args.profiles_file)
    profile_terms = selected_profile_terms(
        profiles,
        args.profile,
        args.profiles_file,
    )
    _items, health_rows = fetch_items(
        feeds=feeds,
        state=state,
        per_feed_limit=max(1, args.per_feed_limit),
        summary_limit=120,
        selected_tag=args.tag,
        profile_terms=profile_terms,
        include_disabled=args.include_disabled,
        mark_seen=False,
    )
    if not args.no_save_state:
        save_state(args.state_file, state)
    if args.json:
        print(json.dumps(health_rows, ensure_ascii=False, indent=2))
        return
    headers = ["tag", "kind", "status", "entries", "new_items", "failures", "last_success", "url"]
    print("\t".join(headers))
    for row in health_rows:
        print("\t".join([
            str(row.get("tag", "")),
            str(row.get("kind", "")),
            str(row.get("status", "")),
            str(row.get("entries", 0)),
            str(row.get("new_items", 0)),
            str(row.get("failure_count", 0)),
            str(row.get("last_success", "")),
            str(row.get("url", "")),
        ]))


def cmd_list_feeds(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    rows = load_feeds(args.feeds_tsv)
    print("enabled\ttag\tpriority\tkind\turl\tnotes")
    for row in rows:
        if args.tag and row["tag"] != args.tag:
            continue
        print("\t".join([
            "1" if row["enabled"] else "0",
            row["tag"],
            str(row["priority"]),
            row["kind"],
            row["url"],
            row["notes"],
        ]))


def find_feed_index(rows, url: str) -> int:
    target = normalize_url(url)
    for idx, row in enumerate(rows):
        if normalize_url(row.get("url", "")) == target:
            return idx
    return -1


def require_feed_url(value) -> str:
    url = normalize_external_url(value)
    if not url:
        raise SystemExit("Feed URL must be an absolute, bounded HTTP(S) URL.")
    return url


def feed_matches_query(row, query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    hay = "\n".join([
        row.get("url", ""),
        row.get("tag", ""),
        row.get("kind", ""),
        row.get("notes", ""),
    ]).lower()
    return q in hay


def serialize_row(row):
    return {
        "enabled": bool(row.get("enabled", True)),
        "tag": row.get("tag", "research"),
        "priority": int(row.get("priority", 5)),
        "kind": row.get("kind", infer_kind(row.get("url", ""))),
        "url": row.get("url", ""),
        "notes": row.get("notes", ""),
    }


def cmd_backup_feeds(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    backup_path = backup_current_feeds(args.feeds_tsv, args.backup_dir, reason=args.reason or "manual")
    if backup_path is None:
        raise SystemExit(f"No feeds.tsv exists yet at {args.feeds_tsv}")
    print(json.dumps({
        "status": "backed_up",
        "backup": str(backup_path),
    }, ensure_ascii=False, indent=2))


def cmd_list_backups(args):
    backups = list_backups(args.backup_dir)
    if args.json:
        print(json.dumps([
            {"name": p.name, "path": str(p), "size": p.stat().st_size, "mtime": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()}
            for p in backups
        ], ensure_ascii=False, indent=2))
        return
    print("name	mtime_utc	size	path")
    for p in backups:
        stat = p.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        print("	".join([p.name, mtime, str(stat.st_size), str(p)]))


def cmd_restore_feeds_backup(args):
    ensure_backup_dir(args.backup_dir)
    backup_path = resolve_backup_path(args.backup_dir, args.backup)
    content = read_regular_text(backup_path)
    parse_feeds_tsv_text(content)
    pre_restore_backup = backup_current_feeds(
        args.feeds_tsv,
        args.backup_dir,
        reason="pre-restore",
    )
    atomic_write_text(args.feeds_tsv, content)
    print(json.dumps({
        "status": "restored",
        "restored_from": str(backup_path),
        "pre_restore_backup": str(pre_restore_backup) if pre_restore_backup else None,
        "feeds_tsv": str(args.feeds_tsv),
    }, ensure_ascii=False, indent=2))


def cmd_add_feed(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    rows = load_feeds(args.feeds_tsv)
    url = require_feed_url(args.url)
    idx = find_feed_index(rows, url)
    if idx >= 0:
        row = rows[idx]
        row["enabled"] = True
        row["url"] = url
        if args.tag:
            row["tag"] = args.tag
        if args.priority is not None:
            row["priority"] = args.priority
        if args.kind:
            row["kind"] = args.kind
        if args.notes is not None:
            row["notes"] = args.notes
        backup_path = save_feeds_with_backup(args.feeds_tsv, rows, args.backup_dir, reason="update-feed")
        print(json.dumps({"status": "updated", "url": url, "backup": str(backup_path) if backup_path else None}, ensure_ascii=False))
        return
    tag = args.tag
    kind = args.kind or ""
    priority = args.priority
    if not tag or not kind or priority is None:
        inferred_tag, inferred_priority, inferred_kind = infer_tag_priority(url)
        tag = tag or inferred_tag
        kind = kind or inferred_kind
        priority = priority if priority is not None else inferred_priority
    rows.append({
        "enabled": True,
        "tag": tag,
        "priority": int(priority),
        "kind": kind,
        "url": url,
        "notes": args.notes or "",
    })
    backup_path = save_feeds_with_backup(args.feeds_tsv, rows, args.backup_dir, reason="add-feed")
    print(json.dumps({"status": "added", "url": url, "tag": tag, "priority": priority, "kind": kind, "backup": str(backup_path) if backup_path else None}, ensure_ascii=False))


def set_feed_enabled(args, enabled: bool):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    rows = load_feeds(args.feeds_tsv)
    url = require_feed_url(args.url)
    idx = find_feed_index(rows, url)
    if idx < 0:
        raise SystemExit(f"Feed not found: {url}")
    rows[idx]["enabled"] = enabled
    backup_path = save_feeds_with_backup(args.feeds_tsv, rows, args.backup_dir, reason="enable-feed" if enabled else "disable-feed")
    print(json.dumps({"status": "ok", "enabled": enabled, "url": rows[idx]["url"], "backup": str(backup_path) if backup_path else None}, ensure_ascii=False))


def cmd_remove_feed(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    rows = load_feeds(args.feeds_tsv)
    url = require_feed_url(args.url)
    idx = find_feed_index(rows, url)
    if idx < 0:
        raise SystemExit(f"Feed not found: {url}")
    removed = rows.pop(idx)
    backup_path = save_feeds_with_backup(args.feeds_tsv, rows, args.backup_dir, reason="remove-feed")
    print(json.dumps({"status": "removed", "url": removed.get("url", url), "backup": str(backup_path) if backup_path else None}, ensure_ascii=False))


def cmd_search_feeds(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    rows = load_feeds(args.feeds_tsv)
    matches = []
    for row in rows:
        if args.tag and row["tag"] != args.tag:
            continue
        if args.enabled_only and not row["enabled"]:
            continue
        if args.disabled_only and row["enabled"]:
            continue
        if not feed_matches_query(row, args.query):
            continue
        matches.append(serialize_row(row))
    if args.json:
        print(json.dumps(matches, ensure_ascii=False, indent=2))
        return
    print("enabled	tag	priority	kind	url	notes")
    for row in matches:
        print("	".join([
            "1" if row["enabled"] else "0",
            row["tag"],
            str(row["priority"]),
            row["kind"],
            row["url"],
            row["notes"],
        ]))


def cmd_edit_feed(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    rows = load_feeds(args.feeds_tsv)
    url = require_feed_url(args.url)
    idx = find_feed_index(rows, url)
    if idx < 0:
        raise SystemExit(f"Feed not found: {url}")
    row = rows[idx]

    if args.set_url:
        new_url = require_feed_url(args.set_url)
        other_idx = find_feed_index(rows, new_url)
        if other_idx >= 0 and other_idx != idx:
            raise SystemExit(f"Another feed already exists with URL: {new_url}")
        row["url"] = new_url
    if args.tag:
        row["tag"] = args.tag
    if args.priority is not None:
        row["priority"] = args.priority
    if args.kind:
        row["kind"] = args.kind
    if args.notes is not None:
        row["notes"] = args.notes
    if args.enable:
        row["enabled"] = True
    if args.disable:
        row["enabled"] = False

    backup_path = save_feeds_with_backup(args.feeds_tsv, rows, args.backup_dir, reason="edit-feed")
    print(json.dumps({"status": "edited", "feed": serialize_row(row), "backup": str(backup_path) if backup_path else None}, ensure_ascii=False, indent=2))


def cmd_migrate_legacy_feeds(args):
    migrated = migrate_legacy_feeds(args.legacy_feeds_file, args.feeds_tsv, force=args.force)
    print(json.dumps({
        "status": "ok",
        "migrated": bool(migrated),
        "feeds_tsv": str(args.feeds_tsv),
        "legacy_file": str(args.legacy_feeds_file),
    }, ensure_ascii=False, indent=2))


def cmd_export_feeds_tsv(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    content = read_regular_text(args.feeds_tsv)
    if args.output == "-":
        sys.stdout.write(content)
    else:
        out_path = Path(args.output)
        atomic_write_text(out_path, content)
        print(json.dumps({
            "status": "exported",
            "source": str(args.feeds_tsv),
            "output": str(out_path),
        }, ensure_ascii=False, indent=2))


def cmd_import_feeds_tsv(args):
    ensure_feeds_tsv(args.feeds_tsv, args.legacy_feeds_file)
    if args.input == "-":
        content = sys.stdin.read(MAX_CONFIG_BYTES + 1)
    else:
        in_path = Path(args.input)
        content = read_regular_text(in_path)
    imported_rows = parse_feeds_tsv_text(content)
    existing_rows = [] if args.replace else load_feeds(args.feeds_tsv)
    merged_rows = imported_rows if args.replace else merge_feed_rows(existing_rows, imported_rows)
    backup_path = save_feeds_with_backup(args.feeds_tsv, merged_rows, args.backup_dir, reason="import-replace" if args.replace else "import-merge")
    print(json.dumps({
        "status": "imported",
        "mode": "replace" if args.replace else "merge",
        "imported_count": len(imported_rows),
        "final_count": len(merged_rows),
        "feeds_tsv": str(args.feeds_tsv),
        "backup": str(backup_path) if backup_path else None,
    }, ensure_ascii=False, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(description="Ranked RSS digest tool for OpenClaw and standalone use.")
    parser.add_argument("--feeds-tsv", type=Path, default=DEFAULT_FEEDS_TSV)
    parser.add_argument("--legacy-feeds-file", type=Path, default=DEFAULT_LEGACY_FEEDS_FILE)
    parser.add_argument("--profiles-file", type=Path, default=DEFAULT_PROFILES_FILE)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--digest-dir", type=Path, default=DEFAULT_DIGEST_DIR)
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR)

    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Fetch feeds and write ranked digests.")
    run.add_argument("--tag", choices=KNOWN_TAGS, default="research")
    run.add_argument("--all-tags", action="store_true")
    run.add_argument("--profile", default="")
    run.add_argument(
        "--max-items",
        type=lambda value: bounded_cli_int(
            value, minimum=0, maximum=MAX_DIGEST_ITEMS, label="max-items"
        ),
        default=DEFAULT_MAX_ITEMS,
    )
    run.add_argument(
        "--per-feed-limit",
        type=lambda value: bounded_cli_int(
            value, minimum=0, maximum=100, label="per-feed-limit"
        ),
        default=DEFAULT_PER_FEED_LIMIT,
    )
    run.add_argument(
        "--summary-limit",
        type=lambda value: bounded_cli_int(
            value, minimum=0, maximum=MAX_SUMMARY_CHARS, label="summary-limit"
        ),
        default=280,
    )
    run.add_argument("--include-disabled", action="store_true")
    run.add_argument("--no-mark-seen", action="store_true")
    run.add_argument(
        "--parallel",
        type=lambda value: bounded_cli_int(
            value, minimum=0, maximum=32, label="parallel"
        ),
        default=0,
        help="Parallel feed fetches (0=auto, 1=sequential)",
    )
    run.set_defaults(func=cmd_run)

    doctor = sub.add_parser("doctor", help="Check feed health and fetch status.")
    doctor.add_argument("--tag", choices=[""] + KNOWN_TAGS, default="")
    doctor.add_argument("--profile", default="")
    doctor.add_argument(
        "--per-feed-limit",
        type=lambda value: bounded_cli_int(
            value, minimum=0, maximum=100, label="per-feed-limit"
        ),
        default=1,
    )
    doctor.add_argument("--include-disabled", action="store_true")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--no-save-state", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    summarize = sub.add_parser(
        "summarize-sidecars",
        help="Write a raw top-item view from validated JSON sidecars.",
    )
    summarize.add_argument(
        "--max-per-tag",
        type=lambda value: bounded_cli_int(
            value, minimum=0, maximum=50, label="max-per-tag"
        ),
        default=5,
    )
    summarize.add_argument("--output", type=Path)
    summarize.add_argument("--no-history", action="store_true")
    summarize.set_defaults(func=cmd_summarize_sidecars)

    list_feeds = sub.add_parser("list-feeds", help="List configured feeds.")
    list_feeds.add_argument("--tag", choices=[""] + KNOWN_TAGS, default="")
    list_feeds.set_defaults(func=cmd_list_feeds)

    backup_cmd = sub.add_parser("backup-feeds", help="Create a manual backup of feeds.tsv.")
    backup_cmd.add_argument("--reason", default="manual")
    backup_cmd.set_defaults(func=cmd_backup_feeds)

    list_backups_cmd = sub.add_parser("list-backups", help="List available feeds.tsv backups.")
    list_backups_cmd.add_argument("--json", action="store_true")
    list_backups_cmd.set_defaults(func=cmd_list_backups)

    restore_cmd = sub.add_parser("restore-feeds-backup", help="Restore feeds.tsv from a backup file name or path.")
    restore_cmd.add_argument("backup", nargs="?", default="", help="Backup file name/path. Defaults to the newest backup.")
    restore_cmd.set_defaults(func=cmd_restore_feeds_backup)

    add = sub.add_parser("add-feed", help="Add a feed or update an existing one.")
    add.add_argument("url")
    add.add_argument("--tag", choices=KNOWN_TAGS)
    add.add_argument("--priority", type=int)
    add.add_argument("--kind", default="")
    add.add_argument("--notes", default="")
    add.set_defaults(func=cmd_add_feed)

    disable = sub.add_parser("disable-feed", help="Disable a feed by URL.")
    disable.add_argument("url")
    disable.set_defaults(func=lambda a: set_feed_enabled(a, False))

    enable = sub.add_parser("enable-feed", help="Enable a feed by URL.")
    enable.add_argument("url")
    enable.set_defaults(func=lambda a: set_feed_enabled(a, True))

    remove = sub.add_parser("remove-feed", help="Remove a feed by URL.")
    remove.add_argument("url")
    remove.set_defaults(func=cmd_remove_feed)

    search = sub.add_parser("search-feeds", help="Search configured feeds by substring match.")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--tag", choices=[""] + KNOWN_TAGS, default="")
    search.add_argument("--enabled-only", action="store_true")
    search.add_argument("--disabled-only", action="store_true")
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=cmd_search_feeds)

    edit = sub.add_parser("edit-feed", help="Edit an existing feed by URL.")
    edit.add_argument("url")
    edit.add_argument("--set-url", default="")
    edit.add_argument("--tag", choices=KNOWN_TAGS)
    edit.add_argument("--priority", type=int)
    edit.add_argument("--kind", default="")
    edit.add_argument("--notes")
    edit.add_argument("--enable", action="store_true")
    edit.add_argument("--disable", action="store_true")
    edit.set_defaults(func=cmd_edit_feed)

    migrate = sub.add_parser("migrate-legacy-feeds", help="Convert a flat feeds.txt to feeds.tsv.")
    migrate.add_argument("--force", action="store_true")
    migrate.set_defaults(func=cmd_migrate_legacy_feeds)

    export_cmd = sub.add_parser("export-feeds-tsv", help="Export the current feeds.tsv for bulk editing.")
    export_cmd.add_argument("--output", default="-", help="Output path, or '-' for stdout.")
    export_cmd.set_defaults(func=cmd_export_feeds_tsv)

    import_cmd = sub.add_parser("import-feeds-tsv", help="Import a feeds.tsv file and merge or replace the current config.")
    import_cmd.add_argument("input", help="Input TSV path, or '-' for stdin.")
    import_cmd.add_argument("--replace", action="store_true", help="Replace the current feeds.tsv instead of merging by normalized URL.")
    import_cmd.set_defaults(func=cmd_import_feeds_tsv)

    return parser


def digest_stub_path(papers_dir: Path, item_id: str) -> Path:
    slug = re.sub(r"[^\w]", "_", item_id.casefold()).strip("_")[:48] or "item"
    suffix = hashlib.sha256(item_id.encode("utf-8")).hexdigest()
    return papers_dir / f"digest_{slug}_{suffix}.md"


def canonicalize_ingested_records(
    records, *, allow_oldest_compaction: bool = False
) -> list[dict]:
    if not isinstance(records, list):
        raise ValueError("ledger must be a bounded list")
    if len(records) > MAX_INGESTED_RECORDS:
        if not allow_oldest_compaction:
            raise ValueError("ledger must be a bounded list")
        if len(records) > MAX_INGESTED_RECORDS + MAX_RUN_ITEMS:
            raise ValueError("ledger compaction input must be bounded")
    normalized = []
    seen_ids = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != INGESTED_RECORD_KEYS:
            raise ValueError("ledger records must have the exact owned schema")
        source = record.get("source")
        item_id = record.get("id")
        processed_at = record.get("processed_at")
        if (
            source != "digest"
            or not isinstance(item_id, str)
            or not 0 < len(item_id) <= 300
            or not isinstance(processed_at, str)
            or not 0 < len(processed_at) <= 100
        ):
            raise ValueError("ledger record fields are invalid or oversized")
        safe_id = clean_text(item_id, 300)
        safe_processed_at = clean_text(processed_at, 100)
        if safe_id != item_id or safe_processed_at != processed_at:
            raise ValueError("ledger record fields are not canonical control-clean text")
        if not INGESTED_TIMESTAMP_RE.fullmatch(processed_at):
            raise ValueError("ledger processed_at is not a producer UTC timestamp")
        try:
            parsed_timestamp = datetime.fromisoformat(processed_at)
        except ValueError as exc:
            raise ValueError("ledger processed_at is not a valid timestamp") from exc
        if parsed_timestamp.utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError("ledger processed_at is not a UTC timestamp")
        if item_id in seen_ids:
            raise ValueError("ledger record IDs must be unique")
        seen_ids.add(item_id)
        normalized.append({
            "source": "digest",
            "id": item_id,
            "processed_at": processed_at,
        })
    if allow_oldest_compaction and len(normalized) > MAX_INGESTED_RECORDS:
        normalized = normalized[-MAX_INGESTED_RECORDS:]
    return normalized


def serialize_ingested_records(records, *, required_tail: int = 0) -> str:
    if required_tail < 0:
        raise ValueError("required ledger tail must be nonnegative")
    normalized = canonicalize_ingested_records(
        records, allow_oldest_compaction=True
    )
    if required_tail > len(normalized) or required_tail > MAX_INGESTED_RECORDS:
        raise ValueError("required ledger records exceed the record limit")
    def encode(values):
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))

    content = encode(normalized)
    if len(content.encode("utf-8")) <= MAX_INGESTED_LEDGER_BYTES:
        return content
    max_drop = len(normalized) - required_tail
    low, high = 0, max_drop
    best = None
    while low <= high:
        drop = (low + high) // 2
        candidate = encode(normalized[drop:])
        if len(candidate.encode("utf-8")) <= MAX_INGESTED_LEDGER_BYTES:
            best = candidate
            high = drop - 1
        else:
            low = drop + 1
    if best is None:
        raise ValueError(
            f"required ledger tail exceeds the {MAX_INGESTED_LEDGER_BYTES}-byte limit"
        )
    return best


def load_ingested_records(path: Path) -> list[dict]:
    payload = json.loads(
        read_regular_text(path, max_bytes=MAX_INGESTED_LEDGER_BYTES),
        object_pairs_hook=reject_duplicate_json_keys,
        parse_constant=reject_json_constant,
    )
    return canonicalize_ingested_records(payload)


def load_legacy_digest_records(path: Path) -> list[dict]:
    payload = json.loads(
        read_regular_text(path, max_bytes=MAX_INGESTED_LEDGER_BYTES),
        object_pairs_hook=reject_duplicate_json_keys,
        parse_constant=reject_json_constant,
    )
    if not isinstance(payload, list) or len(payload) > MAX_INGESTED_RECORDS:
        raise ValueError("legacy shared ledger must be a bounded list")
    digest_records = []
    seen_ids = set()
    for record in payload:
        if not isinstance(record, dict) or record.get("source") != "digest":
            continue
        try:
            canonical = canonicalize_ingested_records([record])[0]
        except ValueError:
            continue
        if canonical["id"] in seen_ids:
            continue
        seen_ids.add(canonical["id"])
        digest_records.append(canonical)
    return digest_records


def _decode_digest_stub_scalar(line: str, prefix: str, limit: int) -> str:
    if not line.startswith(prefix):
        raise ValueError("stub scalar has the wrong field")
    value = json.loads(
        line[len(prefix):],
        object_pairs_hook=reject_duplicate_json_keys,
        parse_constant=reject_json_constant,
    )
    if (
        not isinstance(value, str)
        or len(value) > limit
        or clean_text(value, limit) != value
    ):
        raise ValueError("stub scalar is not canonical bounded text")
    return value


def _is_complete_owned_digest_stub(content: str, item_id: str) -> bool:
    """Recognize only the complete producer shape for an existing owned stub."""
    try:
        if not content.endswith("\n"):
            return False
        lines = content.splitlines()
        if len(lines) != 31:
            return False
        fixed_lines = {
            0: "---",
            1: "artifact_role: raw_external_digest",
            2: "style_applied: false",
            4: "authors: []",
            6: "type: paper",
            7: "sources:",
            8: "  zotero: null",
            9: "  calibre: null",
            10: f"  digest: {yaml_scalar(item_id, MAX_ITEM_KEY_CHARS)}",
            16: "full_text_available: false",
            18: "---",
            19: "",
            20: "## Summary",
            21: "",
            23: "",
            24: "## Key results / main ideas",
            25: "",
            26: "_To be filled on access._",
            27: "",
            28: "## Connections to current research",
            29: "",
            30: "_To be filled on access._",
        }
        if any(lines[index] != expected for index, expected in fixed_lines.items()):
            return False

        title = _decode_digest_stub_scalar(lines[3], "title: ", MAX_TITLE_CHARS)
        if not title:
            return False
        _decode_digest_stub_scalar(lines[5], "year: ", 4)
        tags = json.loads(
            lines[11].removeprefix("tags: "),
            object_pairs_hook=reject_duplicate_json_keys,
            parse_constant=reject_json_constant,
        ) if lines[11].startswith("tags: ") else None
        if (
            not isinstance(tags, list)
            or len(tags) != 1
            or not isinstance(tags[0], str)
            or len(tags[0]) > 80
            or clean_text(tags[0], 80) != tags[0]
        ):
            return False
        domain = _decode_digest_stub_scalar(lines[12], "domain: ", 80)
        if domain != tags[0]:
            return False
        url = _decode_digest_stub_scalar(lines[13], "url: ", MAX_LINK_CHARS)
        if normalize_external_url(url) != url:
            return False
        _decode_digest_stub_scalar(lines[14], "feed: ", MAX_FEED_TITLE_CHARS)
        score_match = re.fullmatch(r"digest_score: (-?[0-9]{1,10})", lines[15])
        if score_match is None or abs(int(score_match.group(1))) > 1_000_000_000:
            return False
        processed_at = _decode_digest_stub_scalar(
            lines[17],
            "processed_at: ",
            100,
        )
        if not INGESTED_TIMESTAMP_RE.fullmatch(processed_at):
            return False
        parsed_timestamp = datetime.fromisoformat(processed_at)
        if parsed_timestamp.utcoffset() != timezone.utc.utcoffset(None):
            return False
        summary_prefix = "Untrusted external source data: "
        if (
            not lines[22].startswith(summary_prefix)
            or len(lines[22]) > len(summary_prefix) + 1_000
            or any(
                unicodedata.category(char) in {"Cc", "Cf", "Cs"}
                for char in lines[22]
            )
        ):
            return False
    except (RecursionError, TypeError, ValueError):
        return False
    return True


def _write_digest_stubs(items):
    """Write minimal memory stubs for digest items not yet in memory/papers/.

    Returns the ids that could not be written. Both writes below used to
    swallow every exception, so an unwritable papers directory produced a
    digest that was silently short of items, and an unwritable ledger made the
    next run redo the work it had just reported as done.
    """
    unwritten = []
    workspace = os.environ.get("AAS_RUNTIME_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE") or os.path.join(os.path.expanduser("~"), ".codex", "runtime", "workspace")
    memory_dir = Path(workspace) / "memory"
    papers_dir = memory_dir / "papers"
    ingested_file = Path(workspace) / "data" / "research" / "rss" / "ingested.json"
    legacy_ingested_file = Path(workspace) / "data" / "library" / "ingested.json"
    try:
        admit_directory_entry(
            memory_dir,
            label="RSS memory directory",
            create=True,
        )
        admit_directory_entry(
            papers_dir,
            label="RSS papers directory",
            create=True,
        )
    except OSError as exc:
        return [f"papers directory: {exc}"]

    records = []
    try:
        os.lstat(ingested_file)
        ingested_entry_exists = True
    except FileNotFoundError:
        ingested_entry_exists = False
    except OSError as exc:
        return [f"ingested ledger: {exc}"]
    if ingested_entry_exists:
        try:
            records = load_ingested_records(ingested_file)
        except (UnicodeError, json.JSONDecodeError, OSError, RecursionError, ValueError) as exc:
            return [f"ingested ledger: {exc}"]
    elif legacy_ingested_file.exists():
        try:
            records = load_legacy_digest_records(legacy_ingested_file)
        except (UnicodeError, json.JSONDecodeError, OSError, RecursionError, ValueError) as exc:
            print(
                "WARNING: optional legacy digest-ledger migration was skipped "
                f"({type(exc).__name__})",
                file=sys.stderr,
            )
            records = []
    ingested_ids = {
        record["id"]
        for record in records
        if record.get("source") == "digest"
        and isinstance(record.get("id"), str)
        and 0 < len(record["id"]) <= 300
    }

    now = datetime.now(timezone.utc).isoformat()
    new_records = []

    for item in items:
        raw_item_id = item.get("key", "")
        item_id = (
            clean_text(raw_item_id, MAX_ITEM_KEY_CHARS)
            if isinstance(raw_item_id, str)
            and len(raw_item_id) <= MAX_ITEM_KEY_CHARS
            else ""
        )
        if not item_id:
            if raw_item_id:
                unwritten.append("invalid or oversized digest item key")
            continue
        was_ingested = item_id in ingested_ids
        out_file = digest_stub_path(papers_dir, item_id)
        try:
            os.lstat(out_file)
            occupant_exists = True
        except FileNotFoundError:
            occupant_exists = False
        except OSError as exc:
            unwritten.append(f"{item_id}: existing stub metadata is unsafe: {exc}")
            continue
        if occupant_exists:
            try:
                existing = read_regular_text(out_file, max_bytes=64 * 1024)
            except (OSError, UnicodeError) as exc:
                unwritten.append(f"{item_id}: existing stub is unsafe: {exc}")
                continue
            if not _is_complete_owned_digest_stub(existing, item_id):
                unwritten.append(f"{item_id}: existing stub is not owned by this digest item")
                continue
            if not was_ingested:
                new_records.append({"source": "digest", "id": item_id, "processed_at": now})
                ingested_ids.add(item_id)
            continue

        title = clean_text(item.get("title", "Unknown"), MAX_TITLE_CHARS)
        link = normalize_external_url(item.get("link", ""))
        summary = clean_text(item.get("summary", ""), 500)
        tag = clean_text(item.get("tag", ""), 80)
        score = item.get("score", 0)
        if isinstance(score, bool) or not isinstance(score, int):
            score = 0
        published = clean_text(item.get("published", ""), 100)
        feed_title = clean_text(item.get("feed_title", ""), MAX_FEED_TITLE_CHARS)

        content = (
            f"---\n"
            f"artifact_role: raw_external_digest\n"
            f"style_applied: false\n"
            f"title: {yaml_scalar(title, MAX_TITLE_CHARS)}\n"
            f"authors: []\n"
            f"year: {yaml_scalar(published[:4], 4)}\n"
            f"type: paper\n"
            f"sources:\n"
            f"  zotero: null\n"
            f"  calibre: null\n"
            f"  digest: {yaml_scalar(item_id, 300)}\n"
            f"tags: [{yaml_scalar(tag, 80)}]\n"
            f"domain: {yaml_scalar(tag, 80)}\n"
            f"url: {yaml_scalar(link, MAX_LINK_CHARS)}\n"
            f"feed: {yaml_scalar(feed_title, MAX_FEED_TITLE_CHARS)}\n"
            f"digest_score: {score}\n"
            f"full_text_available: false\n"
            f"processed_at: {yaml_scalar(now, 100)}\n"
            f"---\n\n"
            f"## Summary\n\nUntrusted external source data: {markdown_inline(summary, 500)}\n\n"
            f"## Key results / main ideas\n\n_To be filled on access._\n\n"
            f"## Connections to current research\n\n_To be filled on access._\n"
        )
        try:
            atomic_write_text(out_file, content)
            if not was_ingested:
                new_records.append({"source": "digest", "id": item_id, "processed_at": now})
                ingested_ids.add(item_id)
        except OSError as exc:
            unwritten.append(f"{item_id}: {exc}")

    if new_records:
        try:
            atomic_write_text(
                ingested_file,
                serialize_ingested_records(
                    records + new_records,
                    required_tail=len(new_records),
                ),
            )
        except (OSError, ValueError) as exc:
            unwritten.append(f"ingested ledger: {exc}")

    return unwritten


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    if sys.argv[1:] == [_HTTP_WORKER_COMMAND]:
        raise SystemExit(_feed_http_worker_main())
    main()
