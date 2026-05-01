from __future__ import annotations

import hashlib
import json
import os
import stat
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sanitize import sanitize_text


SCHEMA_VERSION = "openclaw.inventory.v1"
DENYLIST_VERSION = "openclaw.denylist.v1"
REDACTION_VERSION = "openclaw.redaction.v1"
EVIDENCE_CLASSES = (
    "fixture-only",
    "ci-container",
    "native-linux",
    "native-macos",
    "native-windows",
    "wsl-native",
    "wsl-mounted-windows",
)

MAX_SAFE_LABEL_LEN = 80
DEFAULT_MAX_ENTRIES = 1000

DENIED_NAME_KEYWORDS = (
    "api_key",
    "apikey",
    "auth",
    "cache",
    "credential",
    "history",
    "keychain",
    "login",
    "memory",
    "provider",
    "secret",
    "session",
    "snapshot",
    "token",
)
DENIED_TOP_LEVELS = {
    ".env",
    ".git",
    "auth",
    "browser",
    "cache",
    "caches",
    "credentials",
    "downloads",
    "gateway",
    "gateways",
    "history",
    "logs",
    "memory",
    "memories",
    "private",
    "provider",
    "providers",
    "secrets",
    "sessions",
    "snapshots",
    "state",
    "tokens",
    "workspace",
    "workspaces",
}
DENIED_SUFFIXES = {
    ".db",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def build_inventory(
    source_root: Path,
    *,
    evidence_class: str = "fixture-only",
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> dict[str, Any]:
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(f"unsupported OpenClaw evidence class: {evidence_class}")
    if max_entries < 1:
        raise ValueError("max_entries must be greater than zero")

    root = source_root.expanduser()
    denied: Counter[str] = Counter()
    items: list[dict[str, Any]] = []

    if has_symlink_prefix(root):
        denied["source-root-symlink-prefix-denied"] += 1
    elif not root.exists():
        denied["source-root-absent"] += 1
    elif not root.is_dir():
        denied["source-root-not-directory"] += 1
    else:
        items, denied = scan_root(root, max_entries=max_entries)

    inventory = {
        "schema_version": SCHEMA_VERSION,
        "inventory_id": "inv_pending",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_root": {
            "root_id": "root_explicit",
            "explicit_input": True,
            "canonicalization_policy": (
                "fixture-only"
                if evidence_class == "fixture-only"
                else "platform-native-realpath-no-symlink-prefix"
            ),
            "display_label": "<FAKE_OPENCLAW_ROOT>" if evidence_class == "fixture-only" else "<OPENCLAW_ROOT>",
        },
        "denylist_version": DENYLIST_VERSION,
        "redaction_version": REDACTION_VERSION,
        "evidence_class": evidence_class,
        "content_read_policy": "deny-by-default",
        "contains_raw_paths": False,
        "items": sorted(items, key=lambda item: item["relative_path_token"]),
        "denied_categories": denied_categories(denied),
    }
    inventory["inventory_id"] = f"inv_{stable_digest(stable_inventory_payload(inventory))}"
    return inventory


def scan_root(root: Path, *, max_entries: int) -> tuple[list[dict[str, Any]], Counter[str]]:
    items: list[dict[str, Any]] = []
    denied: Counter[str] = Counter()
    seen_keys: set[str] = set()

    try:
        walker = os.walk(root, topdown=True, followlinks=False)
        for current, dir_names, file_names in walker:
            current_path = Path(current)
            entries = sorted([*dir_names, *file_names])
            dir_names[:] = sorted(dir_names)
            for entry_name in entries:
                entry = current_path / entry_name
                try:
                    relative = entry.relative_to(root)
                except ValueError:
                    denied["outside-root-path-denied"] += 1
                    continue

                reason = denied_path_reason(relative)
                collision_key = normalized_collision_key(relative)
                if collision_key in seen_keys:
                    reason = reason or "case-or-unicode-collision-denied"
                else:
                    seen_keys.add(collision_key)
                if reason:
                    denied[reason] += 1
                    if entry_name in dir_names:
                        dir_names.remove(entry_name)
                    continue

                try:
                    metadata = entry.lstat()
                except OSError:
                    denied["lstat-failed"] += 1
                    continue

                if stat.S_ISDIR(metadata.st_mode):
                    kind = "directory"
                elif stat.S_ISLNK(metadata.st_mode):
                    kind = "symlink-metadata-only"
                elif stat.S_ISREG(metadata.st_mode):
                    if metadata.st_nlink > 1:
                        denied["hardlink-denied"] += 1
                        continue
                    kind = "regular-file"
                else:
                    kind = "special-file-denied"
                    denied["special-file-denied"] += 1

                items.append(inventory_item(relative, kind, metadata))
                if len(items) >= max_entries:
                    denied["max-entries-exceeded"] += 1
                    dir_names[:] = []
                    return items, denied
    except OSError:
        denied["walk-failed"] += 1
    return items, denied


def inventory_item(relative: Path, kind: str, metadata: os.stat_result) -> dict[str, Any]:
    token = relative_path_token(relative)
    reason = "symlink-target-not-read" if kind == "symlink-metadata-only" else "content-not-opened"
    return {
        "item_id": f"item_{stable_digest(token)}",
        "category": category_for_path(relative),
        "kind": kind,
        "safe_label": safe_label(relative.name),
        "relative_path_token": token,
        "metadata": {
            "size_class": size_class(metadata.st_size, kind),
            "read_policy": "lstat-only",
            "reason_code": reason,
        },
    }


def denied_categories(denied: Counter[str]) -> list[dict[str, Any]]:
    return [
        {
            "category_id": reason,
            "reason_code": reason,
            "count": count,
            "read_policy": "lstat-only",
        }
        for reason, count in sorted(denied.items())
    ]


def denied_path_reason(relative: Path) -> str | None:
    parts = list(relative.parts)
    lowered = [part.lower() for part in parts]
    if any(part in {"", ".", ".."} for part in parts):
        return "traversal-path-denied"
    if any(reserved_windows_name(part) for part in parts):
        return "reserved-name-denied"
    if lowered and lowered[0] in DENIED_TOP_LEVELS:
        return "private-category-denied"
    for part in lowered:
        stem = Path(part).stem
        if part in DENIED_TOP_LEVELS or any(keyword in part for keyword in DENIED_NAME_KEYWORDS):
            return "private-category-denied"
        if stem in DENIED_TOP_LEVELS or any(keyword in stem for keyword in DENIED_NAME_KEYWORDS):
            return "private-category-denied"
    if relative.suffix.lower() in DENIED_SUFFIXES:
        return "private-category-denied"
    return None


def category_for_path(relative: Path) -> str:
    parts = [part.lower() for part in relative.parts]
    if any(part in {"hook", "hooks", "scheduler", "schedulers", "cron", "launchd", "systemd"} for part in parts):
        return "hook-metadata-detected-only"
    if any(part in {"alias", "aliases", "commands", "entrypoints"} for part in parts):
        return "alias-metadata"
    if any(part in {"template", "templates"} for part in parts):
        return "template-metadata"
    if any(part in {"instruction", "instructions", "agents"} for part in parts):
        return "instruction-metadata"
    if any(part in {"skill", "skills"} for part in parts) or relative.name.lower() == "skill.md":
        return "skill-metadata"
    return "unknown-count-only"


def relative_path_token(relative: Path) -> str:
    safe_parts = [safe_label(part) for part in relative.parts]
    return "<OPENCLAW_ROOT>/" + "/".join(safe_parts)


def safe_label(value: str) -> str:
    sanitized = sanitize_text(value)
    sanitized = sanitized.replace("\\", "_").replace("\r", "_").replace("\n", "_").replace("\t", "_")
    sanitized = "".join(char if char.isprintable() else "_" for char in sanitized)
    if sanitized != value or len(sanitized) > MAX_SAFE_LABEL_LEN:
        return f"redacted_{stable_digest(value)}"
    return sanitized or "unnamed"


def size_class(size: int, kind: str) -> str:
    if kind not in {"regular-file", "symlink-metadata-only"}:
        return "unknown"
    if size == 0:
        return "empty"
    if size <= 64 * 1024:
        return "small"
    if size <= 1024 * 1024:
        return "medium"
    if size <= 10 * 1024 * 1024:
        return "large"
    return "oversized"


def reserved_windows_name(part: str) -> bool:
    return Path(part).stem.upper() in WINDOWS_RESERVED_NAMES


def normalized_collision_key(relative: Path) -> str:
    return unicodedata.normalize("NFKC", relative.as_posix()).casefold()


def has_symlink_prefix(path: Path) -> bool:
    absolute = path.absolute()
    probe = Path(absolute.anchor) if absolute.anchor else Path("/")
    for part in absolute.parts[1 if absolute.anchor else 0:]:
        probe = probe / part
        try:
            mode = probe.lstat().st_mode
        except FileNotFoundError:
            return False
        except OSError:
            return True
        if stat.S_ISLNK(mode):
            return True
    return False


def stable_inventory_payload(inventory: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in inventory.items()
        if key not in {"generated_at", "inventory_id"}
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def stable_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
