from __future__ import annotations

import configparser
import glob
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from .agents import agent_home_statuses
from .discovery import current_platform, host_is_windows


SYSTEM_PROFILES = ("linux-local", "macos-local", "windows-mounted", "windows-native")
CLOUD_MARKERS = ("Google Drive", "OneDrive", "Dropbox", "MEGAsync")
CACHE_MARKERS = (
    ("runtime", "workspace", "data", "calibre", "cache"),
    (".claude", "data", "calibre", "cache"),
    ("codex-runtime-workspace", "data", "calibre", "cache"),
    ("openclaw-workspace", "workspace", "data", "calibre", "cache"),
)


@dataclass(frozen=True)
class SqliteCheck:
    status: str
    quick_check: str | None = None
    error: str | None = None


def audit_library_profiles(
    root: Path,
    *,
    platform: str | None = None,
    system_profile: str | None = None,
    run_integrity: bool = True,
) -> dict[str, Any]:
    """Read-only discovery for agent homes and local Zotero/Calibre libraries.

    Discovery is intentionally not authority. Returned candidates may be used to
    build a profile, but mutation-capable paths require explicit profile
    selection outside this function.
    """

    root = root.expanduser()
    platform_name = current_platform(platform)
    profile = system_profile or default_system_profile(root, platform_name)
    if profile not in SYSTEM_PROFILES:
        raise ValueError(f"unsupported library profile: {profile}")
    return {
        "status": "ok",
        "root": str(root),
        "platform": platform_name,
        "system_profile": profile,
        "path_dialect": path_dialect_for_profile(profile),
        "executor": executor_for_profile(profile),
        "authority_rule": "discovery-only; mutation requires explicit selected profile",
        "agent_homes": agent_home_statuses(root),
        "zotero": audit_zotero(root, profile, run_integrity=run_integrity),
        "calibre": audit_calibre(root, profile, run_integrity=run_integrity),
    }


def default_system_profile(root: Path, platform: str) -> str:
    if platform == "windows":
        return "windows-native"
    if platform == "macos":
        return "macos-local"
    if is_windows_mounted_path(root):
        return "windows-mounted"
    return "linux-local"


def path_dialect_for_profile(profile: str) -> str:
    return "windows" if profile == "windows-native" else "posix"


def executor_for_profile(profile: str) -> str:
    if profile == "windows-native":
        return "windows"
    return "linux"


def audit_zotero(root: Path, profile: str, *, run_integrity: bool) -> dict[str, Any]:
    candidates = [
        validate_zotero_candidate(path, profile, run_integrity=run_integrity)
        for path in discover_zotero_db_candidates(root)
    ]
    return {
        "status": "local-db-missing" if not candidates else "candidates-found",
        "candidates": candidates,
        "fallback_rule": (
            "missing local DB allows remote-only Zotero API/WebDAV mode if "
            "credentials are configured; local mutation stays blocked"
        ),
    }


def audit_calibre(root: Path, profile: str, *, run_integrity: bool) -> dict[str, Any]:
    candidates = [
        validate_calibre_candidate(path, profile, run_integrity=run_integrity)
        for path in discover_calibre_db_candidates(root)
    ]
    return {
        "status": "local-db-missing" if not candidates else "candidates-found",
        "candidates": candidates,
        "fallback_rule": (
            "missing authoritative library blocks Calibre mutation; cache-only "
            "search must be labeled degraded"
        ),
    }


def discover_zotero_db_candidates(root: Path) -> list[Path]:
    env_candidates: list[Path] = []
    for name in ("AAS_ZOTERO_DB", "ZOTERO_DB", "ZOTERO_SQLITE"):
        value = os.environ.get(name)
        if value:
            env_candidates.append(Path(value).expanduser())
    for name in ("AAS_ZOTERO_DATA_DIR", "ZOTERO_DATA_DIR"):
        value = os.environ.get(name)
        if value:
            env_candidates.append(Path(value).expanduser() / "zotero.sqlite")
    profile_candidates = zotero_profile_db_candidates(root)
    patterns = [
        "Zotero/zotero.sqlite",
        "ZoteroSync/zotero.sqlite",
        "Google Drive/DATA/Zotero/zotero.sqlite",
        "library/zotero/zotero.sqlite",
        ".zotero/zotero/zotero.sqlite",
        ".zotero/zotero/*.default/zotero.sqlite",
        ".var/app/org.zotero.Zotero/data/zotero/zotero/*.default/zotero.sqlite",
        "Library/Application Support/Zotero/Profiles/*.default/zotero.sqlite",
        "Library/Application Support/Zotero/zotero.sqlite",
        "AppData/Roaming/Zotero/Zotero/zotero.sqlite",
        "AppData/Roaming/Zotero/Zotero/Profiles/*.default/zotero.sqlite",
        "AppData/Roaming/Jurism/Zotero/zotero.sqlite",
    ]
    return existing_unique([*env_candidates, *profile_candidates, *(root / pattern for pattern in patterns)])


def discover_calibre_db_candidates(root: Path) -> list[Path]:
    env_candidates: list[Path] = []
    for name in ("AAS_CALIBRE_DB", "CALIBRE_DB", "CALIBRE_METADATA_DB"):
        value = os.environ.get(name)
        if value:
            env_candidates.append(Path(value).expanduser())
    for name in ("AAS_CALIBRE_LIBRARY", "CALIBRE_LIBRARY"):
        value = os.environ.get(name)
        if value:
            env_candidates.append(Path(value).expanduser() / "metadata.db")
    patterns = [
        "Calibre Library/metadata.db",
        "Google Drive/DATA/Calibre Library/metadata.db",
        "Documents/Calibre Library/metadata.db",
        "Library/Calibre Library/metadata.db",
        ".codex/runtime/workspace/data/calibre/cache/metadata.db",
        ".claude/data/calibre/cache/metadata.db",
        "codex-runtime-workspace/data/calibre/cache/metadata.db",
        "openclaw-workspace/workspace/data/calibre/cache/metadata.db",
        "library/calibre/metadata.db",
    ]
    return existing_unique([*env_candidates, *(root / pattern for pattern in patterns)])


def existing_unique(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        for expanded in expand_candidate(path):
            if not expanded.exists():
                continue
            key = str(expanded.resolve(strict=False))
            if key in seen:
                continue
            seen.add(key)
            result.append(expanded)
    return result


def expand_candidate(path: Path) -> list[Path]:
    text = str(path)
    if "*" not in text and "?" not in text and "[" not in text:
        return [path]
    return sorted(Path(match) for match in glob.glob(text))


def zotero_profile_db_candidates(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for profile_ini in (
        root / ".zotero" / "zotero" / "profiles.ini",
        root / "Library" / "Application Support" / "Zotero" / "profiles.ini",
        root / "AppData" / "Roaming" / "Zotero" / "Zotero" / "profiles.ini",
    ):
        candidates.extend(zotero_profile_dbs_from_ini(profile_ini))
    return candidates


def zotero_profile_dbs_from_ini(path: Path) -> list[Path]:
    if not path.is_file():
        return []
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error:
        return []
    base = path.parent
    candidates = []
    for section in parser.sections():
        if not section.lower().startswith("profile"):
            continue
        profile_path = parser.get(section, "Path", fallback="")
        if not profile_path:
            continue
        is_relative = parser.get(section, "IsRelative", fallback="1") != "0"
        resolved = (base / profile_path) if is_relative else Path(profile_path)
        candidates.append(resolved / "zotero.sqlite")
    return candidates


def validate_zotero_candidate(path: Path, profile: str, *, run_integrity: bool) -> dict[str, Any]:
    sqlite_check = sqlite_quick_check(path) if run_integrity else SqliteCheck("skipped")
    item_count = sqlite_count(path, "items")
    storage_dir = path.parent / "storage"
    better_bibtex = path.parent / "better-bibtex.sqlite"
    classification = classify_path(path, profile, cache=False)
    sidecars = sqlite_sidecar_status(path)
    allowed = allowed_operations(
        sqlite_check=sqlite_check,
        schema_valid=item_count is not None,
        classification=classification,
        cache=False,
        profile=profile,
    )
    return {
        "path": str(path),
        "kind": "zotero-db",
        "classification": classification,
        "size": file_size(path),
        "mtime": file_mtime(path),
        "sqlite": sqlite_check.__dict__,
        "readable": item_count is not None and sqlite_check.status in {"ok", "skipped"},
        "schema_valid": item_count is not None,
        "integrity_valid": sqlite_check.status == "ok",
        "sidecars": sidecars,
        "item_count": item_count,
        "storage_dir": {
            "path": str(storage_dir),
            "exists": storage_dir.is_dir(),
        },
        "better_bibtex_db": {
            "path": str(better_bibtex),
            "exists": better_bibtex.is_file(),
        },
        "authoritative": False,
        "allowed_operations": allowed,
        "authority_note": "candidate only; select in a profile before mutation",
    }


def validate_calibre_candidate(path: Path, profile: str, *, run_integrity: bool) -> dict[str, Any]:
    cache = is_cache_path(path)
    sqlite_check = sqlite_quick_check(path) if run_integrity else SqliteCheck("skipped")
    book_count = sqlite_count(path, "books")
    library_root = path.parent
    file_tree = calibre_file_tree_check(path, library_root)
    classification = classify_path(path, profile, cache=cache)
    sidecars = sqlite_sidecar_status(path)
    allowed = allowed_operations(
        sqlite_check=sqlite_check,
        schema_valid=book_count is not None,
        classification=classification,
        cache=cache,
        profile=profile,
    )
    if cache:
        allowed = [op for op in allowed if op != "mutate"]
    return {
        "path": str(path),
        "kind": "calibre-db",
        "classification": classification,
        "size": file_size(path),
        "mtime": file_mtime(path),
        "sqlite": sqlite_check.__dict__,
        "readable": book_count is not None and sqlite_check.status in {"ok", "skipped"},
        "schema_valid": book_count is not None,
        "integrity_valid": sqlite_check.status == "ok",
        "sidecars": sidecars,
        "book_count": book_count,
        "library_root": str(library_root),
        "file_tree": file_tree,
        "authoritative": False,
        "allowed_operations": allowed,
        "authority_note": "candidate only; select in a profile before mutation",
    }


def allowed_operations(
    *,
    sqlite_check: SqliteCheck,
    schema_valid: bool,
    classification: list[str],
    cache: bool,
    profile: str,
) -> list[str]:
    if sqlite_check.status in {"malformed", "unreadable"}:
        return []
    if not schema_valid:
        return []
    if cache:
        return ["read"]
    if "mounted-windows" in classification or "cloud-backed" in classification:
        return ["read", "dry-run"]
    if profile == "windows-native" and not host_is_windows():
        return ["read"]
    return ["read", "dry-run"]


def classify_path(path: Path, profile: str, *, cache: bool) -> list[str]:
    labels: list[str] = []
    if cache:
        labels.append("runtime-cache")
    if is_windows_mounted_path(path) or profile == "windows-mounted":
        labels.append("mounted-windows")
    if any(marker in path.parts for marker in CLOUD_MARKERS):
        labels.append("cloud-backed")
    labels.append(path_dialect_for_profile(profile) + "-dialect")
    return labels


def is_cache_path(path: Path) -> bool:
    parts = tuple(path.parts)
    return any(contains_subsequence(parts, marker) for marker in CACHE_MARKERS)


def contains_subsequence(parts: tuple[str, ...], marker: tuple[str, ...]) -> bool:
    if not marker:
        return False
    for index in range(len(parts) - len(marker) + 1):
        if parts[index:index + len(marker)] == marker:
            return True
    return False


def is_windows_mounted_path(path: Path) -> bool:
    parts = path.resolve(strict=False).parts
    lowered = [part.lower() for part in parts]
    return (
        len(lowered) >= 3
        and lowered[1] == "windows"
        and lowered[2] == "users"
    ) or (
        len(lowered) >= 4
        and lowered[1] == "mnt"
        and len(lowered[2]) == 1
        and lowered[3] == "users"
    )


def sqlite_uri(path: Path) -> str:
    return f"file:{quote(path.as_posix(), safe='/:')}?mode=ro"


def sqlite_sidecar_status(path: Path) -> dict[str, Any]:
    wal = Path(str(path) + "-wal")
    shm = Path(str(path) + "-shm")
    return {
        "wal_path": str(wal),
        "wal_exists": wal.is_file(),
        "shm_path": str(shm),
        "shm_exists": shm.is_file(),
        "snapshot_recommended": wal.is_file() or shm.is_file(),
        "classification": "live-wal" if wal.is_file() or shm.is_file() else "stable-db-file",
    }


def sqlite_quick_check(path: Path) -> SqliteCheck:
    try:
        with closing(sqlite3.connect(sqlite_uri(path), uri=True, timeout=2)) as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
        value = str(row[0]) if row else ""
        return SqliteCheck("ok" if value.lower() == "ok" else "malformed", quick_check=value)
    except sqlite3.DatabaseError as exc:
        return SqliteCheck("malformed", error=str(exc))
    except OSError as exc:
        return SqliteCheck("unreadable", error=str(exc))


def sqlite_count(path: Path, table: str) -> int | None:
    if table not in {"items", "books"}:
        raise ValueError(f"unsupported table count: {table}")
    try:
        with closing(sqlite3.connect(sqlite_uri(path), uri=True, timeout=2)) as conn:
            row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
        return int(row[0]) if row else None
    except (sqlite3.DatabaseError, OSError):
        return None


def calibre_file_tree_check(db_path: Path, library_root: Path) -> dict[str, Any]:
    checked: list[dict[str, Any]] = []
    try:
        with closing(sqlite3.connect(sqlite_uri(db_path), uri=True, timeout=2)) as conn:
            rows = conn.execute(
                "SELECT id, path FROM books WHERE path IS NOT NULL ORDER BY id LIMIT 5"
            ).fetchall()
    except (sqlite3.DatabaseError, OSError):
        return {"status": "unknown", "checked": checked}
    for book_id, rel_path in rows:
        book_dir = library_root / str(rel_path)
        checked.append({"id": book_id, "path": str(book_dir), "exists": book_dir.is_dir()})
    if not checked:
        return {"status": "unknown", "checked": checked}
    return {
        "status": "ok" if all(item["exists"] for item in checked) else "mismatch",
        "checked": checked,
    }


def file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def file_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None
