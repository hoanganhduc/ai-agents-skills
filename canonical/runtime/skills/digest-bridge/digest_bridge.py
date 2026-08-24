#!/usr/bin/env python3
"""Bridge between research/RSS digests and getscipapers paper retrieval."""
import argparse
from collections import deque
from contextlib import contextmanager
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

WORKSPACE_ROOT = Path(os.environ.get("AAS_RUNTIME_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE") or os.path.join(os.path.expanduser("~"), ".codex", "runtime", "workspace"))
RESEARCH_DIGEST = WORKSPACE_ROOT / "data" / "research" / "alerts" / "digests" / "latest-digest.md"
RESEARCH_SIDECAR = WORKSPACE_ROOT / "data" / "research" / "alerts" / "digests" / "latest-digest.json"
RSS_DIGEST_DIR = WORKSPACE_ROOT / "data" / "research" / "rss" / "digests"
BRIDGE_STATE_FILE = WORKSPACE_ROOT / "data" / "research" / "digest-bridge-state.json"
GSP_HELPER = Path(__file__).resolve().parent.parent / "getscipapers_requester" / "gsp_openclaw_helper.py"

MAX_SIDECAR_BYTES = 2 * 1024 * 1024
MAX_SIDECAR_ITEMS = 500
MAX_RSS_DIGEST_FILES = 1_000
RSS_DIGEST_TAGS = ("research", "events", "jobs", "general", "video")
# Five producer-owned RSS tag sidecars may each contain MAX_SIDECAR_ITEMS,
# plus the much smaller research sidecar. Keep the aggregate bounded while
# admitting the producer's documented maximum shape.
MAX_DISCOVERED_PAPERS = 3_000
MAX_TITLE_CHARS = 500
MAX_LINK_CHARS = 2048
MAX_REQUESTED_IDENTIFIERS = MAX_DISCOVERED_PAPERS
MAX_REQUEST_STATE_BYTES = 2 * 1024 * 1024
REQUEST_LOCK_TIMEOUT_SECONDS = 60.0
MAX_HELPER_JSON_BYTES = 4 * 1024 * 1024
MAX_HELPER_MANIFEST_JSON_BYTES = 16 * 1024 * 1024
MAX_HELPER_WATCH_JSON_BYTES = 32 * 1024 * 1024
MAX_HELPER_STDERR_BYTES = 64 * 1024
HELPER_READ_CHUNK_BYTES = 64 * 1024
HELPER_TERMINATE_GRACE_SECONDS = 1.0
MAX_HELPER_PROTOCOL_FRAMING_BYTES = 2
MAX_HELPER_PATH_CHARS = 4_096
MAX_HELPER_WATCH_ITEMS = 10_000
MAX_HELPER_WATCH_SERVICES = 20
MAX_HELPER_WATCH_SERVICE_CHARS = 100
MAX_HELPER_WATCH_NOTE_CHARS = 2_000
MAX_HELPER_WATCH_NOTES = 100
MAX_HELPER_WATCH_HASHES = 10_000
MAX_HELPER_WATCH_TIMESTAMP = 10_000_000_000
MAX_HELPER_WATCH_FUTURE_SKEW_SECONDS = 5 * 60
MAX_HELPER_WATCH_CHECK_COUNT = 1_000_000_000
MAX_PRODUCER_STATUS_DETAIL_CHARS = 1_000
RESEARCH_SOURCE_STATUS_KEYS = frozenset(
    {"arxiv", "s2_recommend", "s2_search"}
)
RESEARCH_SOURCE_ALLOWED_STATUSES = {
    "arxiv": frozenset({"success", "empty", "failed"}),
    "s2_recommend": frozenset({"success", "empty", "failed", "skipped"}),
    "s2_search": frozenset({"success", "empty", "partial", "failed", "skipped"}),
}
HELPER_WATCH_STATUSES = frozenset(
    {"active", "waiting", "posted", "found", "expired", "failed"}
)
LIVE_WATCH_STATUSES = frozenset({"active", "waiting", "posted"})
DURABLE_WATCH_SUCCESS_STATUSES = LIVE_WATCH_STATUSES | {"found"}
ARXIV_PATH_RE = re.compile(
    r"^/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?/?$",
    re.IGNORECASE | re.ASCII,
)
DOI_PATH_RE = re.compile(
    r"^/(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)$",
)
DATACITE_ARXIV_DOI_RE = re.compile(
    r"^10\.48550/arxiv\.(\d{4}\.\d{4,5})(?:v\d+)?$",
    re.IGNORECASE | re.ASCII,
)
ISBN_FULL_RE = re.compile(r"[0-9Xx]+(?:[- ][0-9Xx]+)*")


class DigestBridgeError(RuntimeError):
    """A digest sidecar failed the bridge's bounded machine contract."""


class BridgeStateError(DigestBridgeError):
    """The side-effect ledger cannot safely authorize another handoff."""


class BridgeLockError(DigestBridgeError):
    """The request critical section could not be acquired safely."""


def _reject_duplicate_json_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object member: {key}")
        value[key] = item
    return value


def _reject_json_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


def _is_link_like_stat(info) -> bool:
    """Treat POSIX symlinks and Windows reparse points alike."""
    return bool(stat.S_ISLNK(info.st_mode)) or bool(
        int(getattr(info, "st_file_attributes", 0))
        & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    )


def _safe_inline(value, *, label: str, limit: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise DigestBridgeError(f"{label} must be a bounded string")
    if any(
        char in "\r\n"
        or unicodedata.category(char) in {"Cc", "Cf", "Cs"}
        for char in value
    ):
        raise DigestBridgeError(f"{label} contains structural control characters")
    text = value.strip()
    if not text and not allow_empty:
        raise DigestBridgeError(f"{label} must not be empty")
    return text


def _safe_http_url(value, *, label: str, allow_empty: bool = False) -> str:
    raw = _safe_inline(
        value,
        label=label,
        limit=MAX_LINK_CHARS,
        allow_empty=allow_empty,
    )
    if not raw:
        return ""
    if any(char.isspace() for char in raw):
        raise DigestBridgeError(f"{label} must not contain whitespace")
    try:
        parsed = urlsplit(raw)
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise DigestBridgeError(f"{label} must be an absolute HTTP(S) URL")
        parsed.port
    except ValueError as exc:
        raise DigestBridgeError(f"{label} is not a valid URL") from exc
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc,
            parsed.path,
            parsed.query,
            "",
        )
    )


def _atomic_write_json(path: Path, value, *, max_bytes: int) -> None:
    try:
        payload = (
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as exc:
        raise BridgeStateError("request ledger update is invalid") from exc
    if len(payload) > max_bytes:
        raise BridgeStateError("request ledger update is oversized")
    path.parent.mkdir(parents=True, exist_ok=True)
    file_fd = -1
    stage_name = None
    try:
        file_fd, stage_name = tempfile.mkstemp(
            prefix=f"{path.name}.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(file_fd, "wb") as handle:
            file_fd = -1
            handle.write(payload)
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


def load_state() -> dict:
    try:
        info = os.lstat(BRIDGE_STATE_FILE)
    except FileNotFoundError:
        return {"requested": []}
    except OSError as exc:
        raise BridgeStateError("request ledger metadata is unreadable") from exc
    if (
        _is_link_like_stat(info)
        or not stat.S_ISREG(info.st_mode)
        or info.st_size > MAX_REQUEST_STATE_BYTES
    ):
        raise BridgeStateError("request ledger is unsafe or oversized")
    try:
        payload = BRIDGE_STATE_FILE.read_bytes()
        if len(payload) > MAX_REQUEST_STATE_BYTES:
            raise BridgeStateError("request ledger is oversized")
        data = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except BridgeStateError:
        raise
    except (UnicodeError, json.JSONDecodeError, OSError, RecursionError, ValueError) as exc:
        raise BridgeStateError("request ledger is unreadable") from exc
    requested = data.get("requested") if isinstance(data, dict) else None
    if (
        not isinstance(requested, list)
        or len(requested) > MAX_REQUESTED_IDENTIFIERS
        or any(
            not isinstance(item, str)
            or not 0 < len(item) <= 500
            or any(char in "\r\n" for char in item)
            for item in requested
        )
    ):
        raise BridgeStateError("request ledger has an invalid schema")
    canonical_requested = []
    seen = set()
    for item in requested:
        arxiv_match = re.fullmatch(
            r"\d{4}\.\d{4,5}(?:v\d+)?",
            item,
            re.IGNORECASE | re.ASCII,
        )
        if arxiv_match:
            canonical = _canonical_handoff_doi("arxiv", item)
        else:
            canonical = _canonical_handoff_doi("doi", item)
        if not canonical:
            raise BridgeStateError("request ledger contains an invalid identifier")
        key = canonical.casefold()
        if key not in seen:
            seen.add(key)
            canonical_requested.append(canonical)
    return {"requested": canonical_requested}


def save_state(state: dict) -> None:
    _atomic_write_json(
        BRIDGE_STATE_FILE,
        state,
        max_bytes=MAX_REQUEST_STATE_BYTES,
    )


@contextmanager
def _exclusive_request_lock(timeout: float = REQUEST_LOCK_TIMEOUT_SECONDS):
    """Serialize each bridge manifest/watch/ledger transition."""

    lock_path = BRIDGE_STATE_FILE.with_name(f"{BRIDGE_STATE_FILE.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = os.lstat(lock_path)
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise BridgeLockError("request lock metadata is unavailable") from exc
    if existing is not None and (
        _is_link_like_stat(existing) or not stat.S_ISREG(existing.st_mode)
    ):
        raise BridgeLockError("request lock path is unsafe")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise BridgeLockError("request lock file is unavailable") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise BridgeLockError("request lock path is not a regular file")
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
            os.fsync(fd)
        deadline = time.monotonic() + max(0.0, float(timeout))
        if os.name == "nt":
            import msvcrt

            def try_lock():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

            def unlock():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def try_lock():
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def unlock():
                fcntl.flock(fd, fcntl.LOCK_UN)

        while True:
            try:
                try_lock()
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise BridgeLockError("timed out waiting for the request lock") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            unlock()
    finally:
        os.close(fd)


def _identifier_from_validated_link(link: str) -> tuple[str, str] | None:
    parsed = urlsplit(link)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if host == "arxiv.org":
        match = ARXIV_PATH_RE.fullmatch(unquote(parsed.path))
        if match:
            return "arxiv", match.group(1)
    if host == "doi.org":
        match = DOI_PATH_RE.fullmatch(unquote(parsed.path))
        if match:
            identifier = _strict_doi_identifier(match.group(1))
            if identifier:
                return "doi", identifier
    return None


def _strict_doi_identifier(value: str) -> str:
    if not isinstance(value, str) or len(value) > 500:
        return ""
    candidate = value.strip()
    if (
        not candidate
        or DOI_PATH_RE.fullmatch(f"/{candidate}") is None
    ):
        return ""
    canonical = candidate.lower()
    if (
        len(canonical) > 500
        or DOI_PATH_RE.fullmatch(f"/{canonical}") is None
    ):
        return ""
    return canonical


def _canonical_handoff_doi(identifier_type: str, identifier: str) -> str:
    if identifier_type == "arxiv":
        match = re.fullmatch(
            r"(\d{4}\.\d{4,5})(?:v\d+)?",
            str(identifier or ""),
            re.IGNORECASE | re.ASCII,
        )
        return f"10.48550/arXiv.{match.group(1)}" if match else ""
    if identifier_type != "doi":
        return ""
    doi = _strict_doi_identifier(identifier)
    match = DATACITE_ARXIV_DOI_RE.fullmatch(doi)
    if match:
        return f"10.48550/arXiv.{match.group(1)}"
    return doi


def _canonical_watch_isbn(value: object) -> str:
    if not isinstance(value, str) or len(value) > 500:
        return ""
    if ISBN_FULL_RE.fullmatch(value) is None:
        return ""
    code = value.replace("-", "").replace(" ", "").upper()
    if len(code) == 10 and re.fullmatch(r"\d{9}[\dX]", code):
        total = sum(
            (10 - index) * (10 if char == "X" else int(char))
            for index, char in enumerate(code)
        )
        return code if total % 11 == 0 else ""
    if len(code) == 13 and re.fullmatch(r"\d{13}", code):
        total = sum(
            int(char) * (1 if index % 2 == 0 else 3)
            for index, char in enumerate(code[:12])
        )
        check = (10 - (total % 10)) % 10
        return code if check == int(code[-1]) else ""
    return ""


def _watch_identity(value: dict) -> tuple[str, str, str, tuple[str, ...]]:
    services = tuple(sorted(
        service.strip()
        for service in value.get("services", [])
        if isinstance(service, str) and service.strip()
    ))
    return (
        str(value.get("kind", "")),
        str(value.get("identifier_type", "")),
        str(value.get("identifier", "")),
        services,
    )


def _legacy_watch_key(value: dict) -> str:
    kind, identifier_type, identifier, services = _watch_identity(value)
    payload = f"{kind}|{identifier_type}|{identifier}|{','.join(services)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _structured_watch_key(value: dict) -> str:
    kind, identifier_type, identifier, services = _watch_identity(value)
    payload = json.dumps(
        [kind, identifier_type, identifier, list(services)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _validate_producer_outcome(data: dict, *, expected_owner: str, path: Path) -> None:
    if expected_owner == "research-digest-wrapper":
        source_status = data.get("source_status")
        if (
            not isinstance(source_status, dict)
            or set(source_status) != RESEARCH_SOURCE_STATUS_KEYS
        ):
            raise DigestBridgeError(f"research source status is invalid: {path}")
        attempted = 0
        failed = 0
        for source, outcome in source_status.items():
            _safe_inline(source, label="research source name", limit=100)
            if (
                not isinstance(outcome, dict)
                or set(outcome) != {"status", "detail"}
            ):
                raise DigestBridgeError(f"research source status is invalid: {path}")
            status_value = _safe_inline(
                outcome.get("status"),
                label="research source outcome",
                limit=20,
            )
            if status_value not in RESEARCH_SOURCE_ALLOWED_STATUSES[source]:
                raise DigestBridgeError(f"research source status is invalid: {path}")
            _safe_inline(
                outcome.get("detail", ""),
                label="research source detail",
                limit=MAX_PRODUCER_STATUS_DETAIL_CHARS,
                allow_empty=True,
            )
            if status_value != "skipped":
                attempted += 1
                failed += status_value == "failed"
        if attempted and failed == attempted:
            raise DigestBridgeError(
                f"research sidecar reports complete discovery failure: {path}"
            )
        return

    run_status = data.get("run_status")
    if not isinstance(run_status, dict):
        raise DigestBridgeError(f"RSS run status is invalid: {path}")
    ok = run_status.get("ok")
    degraded = run_status.get("degraded")
    if not isinstance(ok, bool) or not isinstance(degraded, bool):
        raise DigestBridgeError(f"RSS run status is invalid: {path}")
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
            raise DigestBridgeError(f"RSS run status is invalid: {path}")
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
        raise DigestBridgeError(f"RSS run status is inconsistent: {path}")
    if not ok:
        raise DigestBridgeError(f"RSS sidecar reports failed publication: {path}")


def load_digest_sidecar(
    path: Path, *, source: str, expected_owner: str
) -> list[dict]:
    try:
        info = os.lstat(path)
        if (
            _is_link_like_stat(info)
            or not stat.S_ISREG(info.st_mode)
            or info.st_size > MAX_SIDECAR_BYTES
        ):
            raise DigestBridgeError(f"digest sidecar is unsafe or oversized: {path}")
        payload = path.read_bytes()
        if len(payload) > MAX_SIDECAR_BYTES:
            raise DigestBridgeError(f"digest sidecar is oversized: {path}")
        data = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except FileNotFoundError as exc:
        raise DigestBridgeError(f"digest sidecar is missing: {path}") from exc
    except (UnicodeError, json.JSONDecodeError, OSError, RecursionError, ValueError) as exc:
        raise DigestBridgeError(f"digest sidecar is unreadable: {path}") from exc
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != "digest-items.v1"
        or data.get("artifact_role") != "raw_external_digest"
        or data.get("style_applied") is not False
        or data.get("source") != expected_owner
    ):
        raise DigestBridgeError(f"digest sidecar metadata is invalid: {path}")
    _validate_producer_outcome(data, expected_owner=expected_owner, path=path)
    items = data.get("items")
    if not isinstance(items, list) or len(items) > MAX_SIDECAR_ITEMS:
        raise DigestBridgeError(f"digest sidecar items are invalid: {path}")
    papers = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise DigestBridgeError(f"digest sidecar item {index} is not an object: {path}")
        title = _safe_inline(
            item.get("title"),
            label=f"digest item {index} title",
            limit=MAX_TITLE_CHARS,
        )
        link = _safe_http_url(
            item.get("link", ""),
            label=f"digest item {index} link",
            allow_empty=True,
        )
        score = item.get("score", 0)
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 1_000_000:
            raise DigestBridgeError(f"digest item {index} score is invalid: {path}")
        identifier = _identifier_from_validated_link(link) if link else None
        if identifier is None:
            continue
        identifier_type, identifier_value = identifier
        request_identifier = _canonical_handoff_doi(
            identifier_type, identifier_value
        )
        if not request_identifier:
            raise DigestBridgeError(
                f"digest item {index} identifier is not safely canonicalizable: {path}"
            )
        papers.append({
            "title": title,
            "link": link,
            "score": score,
            "identifier": identifier_value,
            "identifier_type": identifier_type,
            "request_identifier": request_identifier,
            "source": source,
        })
    return papers


def _bounded_digest_map(directory: Path, pattern: str) -> dict[str, Path]:
    paths = {}
    for index, path in enumerate(directory.glob(pattern)):
        if index >= MAX_RSS_DIGEST_FILES:
            raise DigestBridgeError(
                f"too many RSS digest files under {directory}"
            )
        paths[path.stem] = path
    return paths


def _extend_discovered(all_papers: list[dict], papers: list[dict]) -> None:
    if len(all_papers) + len(papers) > MAX_DISCOVERED_PAPERS:
        raise DigestBridgeError(
            f"digest sources exceed the {MAX_DISCOVERED_PAPERS}-paper limit"
        )
    all_papers.extend(papers)


def _entry_exists(path: Path, *, label: str) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DigestBridgeError(f"{label} metadata is unreadable: {path}") from exc
    return True


def _directory_entry_exists(path: Path, *, label: str) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DigestBridgeError(f"{label} metadata is unreadable: {path}") from exc
    if _is_link_like_stat(info) or not stat.S_ISDIR(info.st_mode):
        raise DigestBridgeError(f"{label} is unsafe: {path}")
    return True


def scan_digests(sources: list[str]) -> list[dict]:
    """Scan digest files and extract all paper identifiers."""
    all_papers = []

    if "research" in sources:
        if _entry_exists(RESEARCH_SIDECAR, label="research digest sidecar"):
            _extend_discovered(
                all_papers,
                load_digest_sidecar(
                    RESEARCH_SIDECAR,
                    source="research-digest",
                    expected_owner="research-digest-wrapper",
                ),
            )
        elif _entry_exists(RESEARCH_DIGEST, label="research digest Markdown"):
            raise DigestBridgeError(
                "research Markdown exists without its JSON sidecar; rerun the research digest"
            )

    if "rss" in sources and _directory_entry_exists(
        RSS_DIGEST_DIR,
        label="RSS digest directory",
    ):
        for tag in RSS_DIGEST_TAGS:
            stem = f"rss-{tag}"
            markdown = RSS_DIGEST_DIR / f"{stem}.md"
            sidecar = RSS_DIGEST_DIR / f"{stem}.json"
            markdown_present = _entry_exists(
                markdown,
                label=f"RSS Markdown for {stem}",
            )
            sidecar_present = _entry_exists(
                sidecar,
                label=f"RSS sidecar for {stem}",
            )
            if markdown_present and not sidecar_present:
                raise DigestBridgeError(
                    f"RSS Markdown exists without JSON sidecar for {stem}; rerun the RSS digest"
                )
            if not sidecar_present:
                continue
            _extend_discovered(
                all_papers,
                load_digest_sidecar(
                    sidecar,
                    source=stem,
                    expected_owner=stem,
                ),
            )

    # Deduplicate by the DOI that the helper will actually receive. An arXiv
    # URL and its 10.48550 DataCite URL are the same handoff.
    seen = {}
    unique = []
    for p in all_papers:
        key = p["request_identifier"].casefold()
        if key not in seen:
            seen[key] = len(unique)
            unique.append(p)
        elif p["score"] > unique[seen[key]]["score"]:
            unique[seen[key]] = p
    return unique


def filter_new(papers: list[dict], state: dict, min_score: int) -> list[dict]:
    """Filter out already-requested papers and those below min_score."""
    requested_set = {
        identifier.casefold() for identifier in state.get("requested", [])
    }
    return [
        p for p in papers
        if p["request_identifier"].casefold() not in requested_set
        and (p["score"] >= min_score or min_score == 0)
    ]


def scan_for_command(sources: list[str]) -> list[dict]:
    try:
        return scan_digests(sources)
    except DigestBridgeError as exc:
        print(json.dumps({
            "ok": False,
            "error_code": "invalid_digest_sidecar",
            "error": str(exc),
        }, indent=2))
        raise SystemExit(2)


def _paper_handoff_identifier(paper: dict) -> str:
    existing = paper.get("request_identifier")
    if isinstance(existing, str) and existing:
        return _canonical_handoff_doi("doi", existing)
    return _canonical_handoff_doi(
        str(paper.get("identifier_type") or ""),
        str(paper.get("identifier") or ""),
    )


def _manifest_covers_exactly(manifest: object, expected: list[str]) -> bool:
    if not isinstance(manifest, dict) or manifest.get("kind") != "paper":
        return False
    items = manifest.get("items")
    if not isinstance(items, list) or len(items) != len(expected):
        return False
    actual = []
    for item in items:
        if (
            not isinstance(item, dict)
            or item.get("identifier_type") != "doi"
            or not isinstance(item.get("identifier"), str)
        ):
            return False
        identifier = _canonical_handoff_doi("doi", item["identifier"])
        if not identifier:
            return False
        actual.append(identifier.casefold())
    wanted = [identifier.casefold() for identifier in expected]
    return len(set(actual)) == len(actual) and set(actual) == set(wanted)


def _parse_helper_json(
    text: str,
    *,
    label: str,
    max_bytes: int = MAX_HELPER_JSON_BYTES,
    allow_protocol_newline: bool = False,
):
    payload_text = text
    if allow_protocol_newline:
        if payload_text.endswith("\r\n"):
            payload_text = payload_text[:-2]
        elif payload_text.endswith("\n"):
            payload_text = payload_text[:-1]
    if len(payload_text.encode("utf-8", errors="replace")) > max_bytes:
        raise DigestBridgeError(
            f"{label} exceeds the {max_bytes}-byte limit"
        )
    try:
        return json.loads(
            payload_text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise DigestBridgeError(f"{label} is not valid JSON") from exc


def _run_helper_bounded(
    argv: list[str],
    *,
    input_text: str | None = None,
    max_output_bytes: int,
    timeout: float,
    label: str,
) -> subprocess.CompletedProcess:
    """Run a helper without allowing its pipes to allocate past the contract."""
    if max_output_bytes <= 0 or timeout <= 0:
        raise ValueError("helper output limit and timeout must be positive")
    input_stream = None
    try:
        if input_text is not None:
            input_stream = tempfile.TemporaryFile(mode="w+b")
            input_stream.write(input_text.encode("utf-8"))
            input_stream.seek(0)
        process = subprocess.Popen(
            argv,
            stdin=input_stream if input_stream is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except BaseException:
        if input_stream is not None:
            input_stream.close()
        raise

    assert process.stdout is not None and process.stderr is not None
    lock = threading.Lock()
    overflow = threading.Event()
    stdout_parts: list[bytes] = []
    stderr_parts: deque[bytes] = deque()
    totals = {"stdout": 0, "stderr_tail": 0, "combined": 0}
    failure: list[str] = []

    def retain_stderr(chunk: bytes) -> None:
        stderr_parts.append(chunk)
        totals["stderr_tail"] += len(chunk)
        excess = totals["stderr_tail"] - MAX_HELPER_STDERR_BYTES
        while excess > 0 and stderr_parts:
            first = stderr_parts[0]
            if len(first) <= excess:
                stderr_parts.popleft()
                totals["stderr_tail"] -= len(first)
                excess -= len(first)
            else:
                stderr_parts[0] = first[excess:]
                totals["stderr_tail"] -= excess
                excess = 0

    def drain(stream, *, is_stdout: bool) -> None:
        try:
            read = getattr(stream, "read1", stream.read)
            while True:
                chunk = read(HELPER_READ_CHUNK_BYTES)
                if not chunk:
                    break
                with lock:
                    if overflow.is_set():
                        continue
                    totals["combined"] += len(chunk)
                    if is_stdout:
                        remaining = max_output_bytes - totals["stdout"]
                        if remaining > 0:
                            stdout_parts.append(chunk[:remaining])
                        totals["stdout"] += len(chunk)
                    else:
                        retain_stderr(chunk)
                    if (
                        totals["stdout"] > max_output_bytes
                        or totals["combined"] > max_output_bytes
                    ):
                        failure.append(
                            f"{label} exceeds the {max_output_bytes}-byte output limit"
                        )
                        overflow.set()
        except (OSError, ValueError) as exc:
            with lock:
                if not overflow.is_set() and process.poll() is None:
                    failure.append(f"{label} output stream failed: {exc}")
                    overflow.set()

    readers = [
        threading.Thread(target=drain, args=(process.stdout,), kwargs={"is_stdout": True}, daemon=True),
        threading.Thread(target=drain, args=(process.stderr,), kwargs={"is_stdout": False}, daemon=True),
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        if overflow.wait(timeout=min(0.05, remaining)):
            break

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=HELPER_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    if input_stream is not None:
        input_stream.close()

    for reader in readers:
        reader.join(timeout=5)
    if any(reader.is_alive() for reader in readers):
        process.stdout.close()
        process.stderr.close()
        for reader in readers:
            reader.join(timeout=1)
        if not failure:
            failure.append(f"{label} output streams did not close")
    else:
        process.stdout.close()
        process.stderr.close()

    stdout_bytes = b"".join(stdout_parts)
    stderr_bytes = b"".join(stderr_parts)
    if timed_out:
        raise subprocess.TimeoutExpired(
            argv,
            timeout,
            output=stdout_bytes,
            stderr=stderr_bytes,
        )
    if failure:
        raise DigestBridgeError(failure[0])
    try:
        stdout_text = stdout_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise DigestBridgeError(f"{label} is not valid UTF-8") from exc
    stderr_text = stderr_bytes.decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(
        argv,
        process.returncode,
        stdout_text,
        stderr_text,
    )


def _read_regular_json_artifact(
    path_value: object,
    *,
    label: str,
    max_bytes: int = MAX_HELPER_JSON_BYTES,
):
    if (
        not isinstance(path_value, str)
        or not path_value
        or len(path_value) > MAX_HELPER_PATH_CHARS
        or any(
            char in "\r\n\0" or unicodedata.category(char) in {"Cc", "Cf", "Cs"}
            for char in path_value
        )
    ):
        raise DigestBridgeError(f"{label} path is invalid")
    path = Path(path_value)
    if not path.is_absolute():
        raise DigestBridgeError(f"{label} path must be absolute")
    try:
        initial = os.lstat(path)
        if (
            _is_link_like_stat(initial)
            or not stat.S_ISREG(initial.st_mode)
            or initial.st_size > max_bytes
        ):
            raise DigestBridgeError(f"{label} is unsafe or oversized")
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
                or opened.st_size > max_bytes
                or (initial.st_dev, initial.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise DigestBridgeError(f"{label} changed or is oversized")
            chunks = []
            total = 0
            while True:
                chunk = os.read(
                    file_fd,
                    min(64 * 1024, max_bytes + 1 - total),
                )
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise DigestBridgeError(f"{label} is oversized")
                chunks.append(chunk)
        finally:
            os.close(file_fd)
    except DigestBridgeError:
        raise
    except (OSError, ValueError) as exc:
        raise DigestBridgeError(f"{label} is unreadable") from exc
    try:
        return json.loads(
            b"".join(chunks).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise DigestBridgeError(f"{label} is not valid JSON") from exc


def _validate_watch_ack(
    value: object,
    expected_identifier: str,
    *,
    require_success_status: bool,
) -> dict:
    value = _validate_watch_ledger_item(value)
    if value.get("kind") != "paper" or value.get("identifier_type") != "doi":
        raise DigestBridgeError("watch helper response has the wrong kind")
    item_id = _safe_inline(
        value.get("id"), label="watch id", limit=300
    )
    watch_key = _safe_inline(
        value.get("watch_key"), label="watch key", limit=100
    )
    status_value = _safe_inline(
        value.get("status"), label="watch status", limit=50
    )
    if (
        require_success_status
        and status_value not in DURABLE_WATCH_SUCCESS_STATUSES
    ):
        raise DigestBridgeError("watch helper response is not a successful watch")
    if value.get("services") != ["all"]:
        raise DigestBridgeError("watch helper response did not persist services=all")
    identifier = _canonical_handoff_doi(
        "doi", str(value.get("identifier") or "")
    )
    if not identifier or identifier.casefold() != expected_identifier.casefold():
        raise DigestBridgeError("watch helper response identifier does not match")
    return {
        "id": item_id,
        "watch_key": watch_key,
        "status": status_value,
        "identifier": identifier,
    }


def _validate_watch_ledger_item(value: object) -> dict:
    if not isinstance(value, dict):
        raise DigestBridgeError("watch ledger contains a non-object item")
    _safe_inline(value.get("id"), label="watch id", limit=300)
    watch_key = _safe_inline(value.get("watch_key"), label="watch key", limit=100)
    if value.get("kind") not in {"paper", "book"}:
        raise DigestBridgeError("watch ledger item has an invalid kind")
    if value.get("identifier_type") not in {"doi", "isbn", "search"}:
        raise DigestBridgeError("watch ledger item has an invalid identifier type")
    identifier = _safe_inline(
        value.get("identifier"),
        label="watch identifier",
        limit=500,
    )
    identifier_type = value["identifier_type"]
    if identifier_type == "doi":
        canonical_identifier = _canonical_handoff_doi("doi", identifier)
    elif identifier_type == "isbn":
        canonical_identifier = _canonical_watch_isbn(identifier)
    else:
        canonical_identifier = identifier.strip()
    if not canonical_identifier or canonical_identifier != identifier:
        raise DigestBridgeError("watch ledger item identifier is not canonical")
    if value.get("status") not in HELPER_WATCH_STATUSES:
        raise DigestBridgeError("watch ledger item has an invalid status")
    services = value.get("services")
    if (
        not isinstance(services, list)
        or len(services) > MAX_HELPER_WATCH_SERVICES
    ):
        raise DigestBridgeError("watch ledger item has invalid services")
    for service in services:
        _safe_inline(
            service,
            label="watch service",
            limit=MAX_HELPER_WATCH_SERVICE_CHARS,
        )
    if watch_key not in {
        _structured_watch_key(value),
        _legacy_watch_key(value),
    }:
        raise DigestBridgeError("watch ledger item watch_key does not match identity")
    for field, maximum in (
        ("created_at", MAX_HELPER_WATCH_TIMESTAMP),
        ("updated_at", MAX_HELPER_WATCH_TIMESTAMP),
        ("check_count", MAX_HELPER_WATCH_CHECK_COUNT),
    ):
        field_value = value.get(field)
        if (
            isinstance(field_value, bool)
            or not isinstance(field_value, int)
            or not 0 <= field_value <= maximum
        ):
            raise DigestBridgeError(f"watch ledger item has invalid {field}")
    for field in ("deadline_ts", "last_checked_at"):
        field_value = value.get(field)
        if field_value is not None and (
            isinstance(field_value, bool)
            or not isinstance(field_value, int)
            or not 0 <= field_value <= MAX_HELPER_WATCH_TIMESTAMP
        ):
            raise DigestBridgeError(f"watch ledger item has invalid {field}")
    for field in ("label", "notes", "last_note"):
        field_value = value.get(field)
        if field_value is not None:
            _safe_inline(
                field_value,
                label=f"watch {field}",
                limit=(500 if field == "label" else MAX_HELPER_WATCH_NOTE_CHARS),
                allow_empty=True,
            )
    history = value.get("notes_history", [])
    if not isinstance(history, list) or len(history) > MAX_HELPER_WATCH_NOTES:
        raise DigestBridgeError("watch ledger item has invalid notes history")
    for note in history:
        if not isinstance(note, dict):
            raise DigestBridgeError("watch ledger item has invalid notes history")
        timestamp = note.get("ts")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, int)
            or not 0 <= timestamp <= MAX_HELPER_WATCH_TIMESTAMP
        ):
            raise DigestBridgeError("watch ledger item has invalid notes history")
        _safe_inline(
            note.get("note"),
            label="watch history note",
            limit=MAX_HELPER_WATCH_NOTE_CHARS,
            allow_empty=True,
        )
    hashes = value.get("sent_file_hashes")
    if not isinstance(hashes, list) or len(hashes) > MAX_HELPER_WATCH_HASHES:
        raise DigestBridgeError("watch ledger item has invalid sent-file hashes")
    for digest in hashes:
        _safe_inline(
            digest,
            label="watch sent-file hash",
            limit=300,
            allow_empty=True,
        )
    created_at = value["created_at"]
    updated_at = value["updated_at"]
    latest_event_timestamp = (
        int(time.time()) + MAX_HELPER_WATCH_FUTURE_SKEW_SECONDS
    )
    if created_at > latest_event_timestamp or updated_at > latest_event_timestamp:
        raise DigestBridgeError("watch ledger item timestamps are in the future")
    if updated_at < created_at:
        raise DigestBridgeError("watch ledger item timestamps are inconsistent")
    deadline = value.get("deadline_ts")
    if deadline is not None and deadline < created_at:
        raise DigestBridgeError("watch ledger item deadline is inconsistent")
    last_checked = value.get("last_checked_at")
    if last_checked is not None and last_checked > latest_event_timestamp:
        raise DigestBridgeError("watch ledger item check timestamp is in the future")
    if last_checked is not None and not created_at <= last_checked <= updated_at:
        raise DigestBridgeError("watch ledger item check timestamp is inconsistent")
    if any(note["ts"] > latest_event_timestamp for note in history):
        raise DigestBridgeError("watch ledger item notes timestamps are in the future")
    if any(
        not created_at <= note["ts"] <= updated_at
        for note in history
    ):
        raise DigestBridgeError("watch ledger item notes timestamps are inconsistent")
    return value


def _verify_watch_ledger(results: list[dict]) -> str | None:
    try:
        listed = _run_helper_bounded(
            [sys.executable, str(GSP_HELPER), "list-watches"],
            max_output_bytes=MAX_HELPER_WATCH_JSON_BYTES,
            timeout=30,
            label="watch ledger response",
        )
        if listed.returncode != 0:
            return (
                f"watch ledger verification failed (exit {listed.returncode}): "
                f"{listed.stderr.strip()[-2_000:]}"
            )
        payload = _parse_helper_json(
            listed.stdout,
            label="watch ledger response",
            max_bytes=MAX_HELPER_WATCH_JSON_BYTES,
        )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or len(items) > MAX_HELPER_WATCH_ITEMS:
            return "watch ledger response has invalid items"
        by_id = {}
        for item in items:
            item = _validate_watch_ledger_item(item)
            item_id = item.get("id")
            if item_id in by_id:
                return "watch ledger response contains missing or duplicate ids"
            by_id[item_id] = item
        for result in results:
            stored = by_id.get(result["id"])
            if stored is None:
                return f"watch {result['id']} is missing from the durable ledger"
            acknowledged = _validate_watch_ack(
                stored,
                result["identifier"],
                require_success_status=False,
            )
            if acknowledged["status"] not in DURABLE_WATCH_SUCCESS_STATUSES:
                return (
                    f"watch {result['id']} has non-success durable status "
                    f"{acknowledged['status']}"
                )
            if acknowledged["watch_key"] != result["watch_key"]:
                return f"watch {result['id']} changed identity in the durable ledger"
    except (DigestBridgeError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return f"watch ledger verification failed: {exc}"
    return None


def create_manifest(papers: list[dict]) -> dict | None:
    """Create a getscipapers manifest from paper identifiers.

    None means the manifest was not built, on every path: helper missing,
    non-zero exit, timeout, or unparseable stdout. `cmd_request` treats that as
    fatal, because the manifest is the handoff this command exists to perform.
    """
    if not GSP_HELPER.exists():
        print(f"ERROR: gsp_openclaw_helper.py not found at {GSP_HELPER}", file=sys.stderr)
        return None

    # Build a text block of identifiers for manifest creation
    lines = [_paper_handoff_identifier(p) for p in papers]
    if any(not identifier for identifier in lines) or len(set(map(str.casefold, lines))) != len(lines):
        print("ERROR: manifest input identifiers are invalid or duplicated", file=sys.stderr)
        return None
    identifier_text = "\n".join(lines)

    try:
        result = _run_helper_bounded(
            [sys.executable, str(GSP_HELPER), "make-manifest", "paper", "-"],
            input_text=identifier_text,
            max_output_bytes=(
                MAX_HELPER_MANIFEST_JSON_BYTES
                + MAX_HELPER_PROTOCOL_FRAMING_BYTES
            ),
            timeout=60,
            label="manifest helper response",
        )
        if result.returncode == 0:
            manifest = _parse_helper_json(
                result.stdout,
                label="manifest helper response",
                max_bytes=MAX_HELPER_MANIFEST_JSON_BYTES,
                allow_protocol_newline=True,
            )
            persisted = _read_regular_json_artifact(
                manifest.get("manifest_path") if isinstance(manifest, dict) else None,
                label="persisted manifest",
                max_bytes=MAX_HELPER_MANIFEST_JSON_BYTES,
            )
            if (
                _manifest_covers_exactly(manifest, lines)
                and _manifest_covers_exactly(persisted, lines)
                and persisted.get("manifest_path") == manifest.get("manifest_path")
            ):
                return persisted
            print(
                "ERROR: manifest output does not exactly cover requested identifiers",
                file=sys.stderr,
            )
            return None
        print(
            f"ERROR: manifest creation failed (exit {result.returncode}): "
            f"{result.stderr.strip()}",
            file=sys.stderr,
        )
    except (DigestBridgeError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        print(f"ERROR: manifest creation failed: {exc}", file=sys.stderr)
    return None


def create_watches(papers: list[dict]) -> list[dict]:
    """Create watches serially; the helper also locks each store transaction."""
    def _create_one(p):
        identifier = _paper_handoff_identifier(p)
        try:
            result = _run_helper_bounded(
                [sys.executable, str(GSP_HELPER), "create-watch",
                 "--kind", "paper",
                 f"--label={p['title']}",
                 "--identifier-type", "doi",
                 "--identifier", identifier,
                 "--services", "all"],
                max_output_bytes=MAX_HELPER_WATCH_JSON_BYTES,
                timeout=30,
                label="watch helper response",
            )
            if result.returncode == 0:
                value = _parse_helper_json(
                    result.stdout,
                    label="watch helper response",
                    max_bytes=MAX_HELPER_WATCH_JSON_BYTES,
                )
                acknowledged = _validate_watch_ack(
                    value,
                    identifier,
                    require_success_status=True,
                )
                return {
                    "identifier": identifier,
                    "status": "created",
                    "id": acknowledged["id"],
                    "watch_key": acknowledged["watch_key"],
                }
            return {
                "identifier": identifier,
                "status": "error",
                "error": result.stderr.strip()[-2_000:],
            }
        except (DigestBridgeError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
            return {"identifier": identifier, "status": "error", "error": str(exc)}

    results = []
    for paper in papers:
        result = _create_one(paper)
        results.append(result)
        if result.get("status") != "created":
            break
    if results and all(result.get("status") == "created" for result in results):
        verification_error = _verify_watch_ledger(results)
        if verification_error:
            results[-1]["status"] = "error"
            results[-1]["error"] = verification_error
    return results


def cmd_scan(args):
    sources = [args.source] if args.source != "all" else ["research", "rss"]
    papers = scan_for_command(sources)
    try:
        state = load_state()
    except BridgeStateError as exc:
        print(json.dumps({
            "ok": False,
            "error_code": "invalid_bridge_state",
            "error": str(exc),
        }, indent=2))
        raise SystemExit(2)
    new_papers = filter_new(papers, state, args.min_score)
    requested_set = {
        identifier.casefold() for identifier in state.get("requested", [])
    }
    already_requested = sum(
        paper["request_identifier"].casefold() in requested_set
        for paper in papers
    )
    below_min_score = sum(
        paper["request_identifier"].casefold() not in requested_set
        and args.min_score != 0
        and paper["score"] < args.min_score
        for paper in papers
    )

    print(json.dumps({
        "ok": True,
        "total_found": len(papers),
        "new_papers": len(new_papers),
        "already_requested": already_requested,
        "below_min_score": below_min_score,
        "min_score": args.min_score,
        "papers": new_papers,
    }, indent=2))


def _cmd_request_locked(args):
    sources = [args.source] if args.source != "all" else ["research", "rss"]
    papers = scan_for_command(sources)
    state = load_state()
    new_papers = filter_new(papers, state, args.min_score)

    if not new_papers:
        print(json.dumps({"ok": True, "message": "No new papers to request", "total_scanned": len(papers)}, indent=2))
        return

    manifest = create_manifest(new_papers)
    if manifest is None:
        # The state file is the ledger of what has already been handed off, so
        # banking a paper whose manifest was never built drops it for good: the
        # next `request` filters it out and reports "No new papers to request".
        # Nothing is recorded, and the operator can retry once getscipapers works.
        print(json.dumps({
            "ok": False,
            "error": "manifest creation failed; no paper was recorded as requested",
            "error_code": "manifest_failed",
            "total_scanned": len(papers),
            "papers": new_papers,
        }, indent=2))
        raise SystemExit(2)

    watch_results = []
    if args.watch:
        watch_results = create_watches(new_papers)
        failed_watches = [
            result for result in watch_results if result.get("status") != "created"
        ]
        if failed_watches:
            print(json.dumps({
                "ok": False,
                "error": "one or more requested watches failed; no paper was recorded as requested",
                "error_code": "watch_failed",
                "total_scanned": len(papers),
                "papers": new_papers,
                "manifest": manifest,
                "watches": watch_results,
            }, indent=2))
            raise SystemExit(2)

    # Mark as requested
    requested = state.get("requested", [])
    requested_keys = {value.casefold() for value in requested}
    for p in new_papers:
        identifier = p["request_identifier"]
        key = identifier.casefold()
        if key not in requested_keys:
            requested.append(identifier)
            requested_keys.add(key)
    # Keep every successfully handed-off identifier that is still present in
    # this bounded scan. Only the spare capacity is historical: otherwise a
    # saturated mixed ledger can evict current papers and reissue them on the
    # next identical request.
    current_requested = []
    current_keys = set()
    for paper in papers:
        identifier = paper["request_identifier"]
        key = identifier.casefold()
        if key in requested_keys and key not in current_keys:
            current_requested.append(identifier)
            current_keys.add(key)
    historical = [
        identifier
        for identifier in requested
        if identifier.casefold() not in current_keys
    ]
    spare = MAX_REQUESTED_IDENTIFIERS - len(current_requested)
    state["requested"] = current_requested + (historical[-spare:] if spare else [])
    save_state(state)

    print(json.dumps({
        "ok": True,
        "requested_count": len(new_papers),
        "papers": new_papers,
        "manifest": manifest,
        "watches": watch_results if watch_results else None,
    }, indent=2))


def cmd_request(args):
    try:
        with _exclusive_request_lock():
            return _cmd_request_locked(args)
    except BridgeStateError as exc:
        print(json.dumps({
            "ok": False,
            "error_code": "invalid_bridge_state",
            "error": str(exc),
        }, indent=2))
        raise SystemExit(2)
    except BridgeLockError as exc:
        print(json.dumps({
            "ok": False,
            "error_code": "request_lock_unavailable",
            "error": str(exc),
        }, indent=2))
        raise SystemExit(2)


def main():
    ap = argparse.ArgumentParser(description="Bridge digest outputs to getscipapers retrieval")
    sub = ap.add_subparsers(dest="command")

    scan_p = sub.add_parser("scan", help="Scan digests and show available papers")
    scan_p.add_argument("--source", choices=["all", "research", "rss"], default="all")
    scan_p.add_argument("--min-score", type=int, default=0)
    scan_p.set_defaults(func=cmd_scan)

    req_p = sub.add_parser("request", help="Create manifest and optionally watches")
    req_p.add_argument("--source", choices=["all", "research", "rss"], default="all")
    req_p.add_argument("--min-score", type=int, default=0)
    req_p.add_argument("--watch", action="store_true", help="Also create watches for monitoring")
    req_p.set_defaults(func=cmd_request)

    args = ap.parse_args()
    if args.command is None:
        ap.print_help()
        raise SystemExit(1)
    args.func(args)


if __name__ == "__main__":
    main()
