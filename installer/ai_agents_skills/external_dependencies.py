"""Guarded host provisioning for allowlisted external Python projects.

This module deliberately does not use the ordinary skill installer machinery:
the two projects are executable application dependencies, not managed skill
artifacts.  It keeps their source and virtual environments under a private
host-owned tree, then points the documented stable venv names at a verified
generation only after offline installation succeeds.
"""

from __future__ import annotations

import base64
import binascii
import copy
from contextlib import contextmanager
import csv
import hashlib
import io
import json
import os
import platform as host_platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator

from .capabilities import normalized_path_within
from .manifest import MANIFEST_DIR
from .state import (
    preflight_state_path,
    prepare_state_directory,
    sha256_file,
    state_dir,
    write_text_atomic,
)
from .windows_security import require_handle_bound_mutation


EXTERNAL_STATE_SCHEMA_VERSION = 1
EXTERNAL_PLAN_SCHEMA_VERSION = 1
EXTERNAL_TRANSACTION_SCHEMA_VERSION = 1
EXTERNAL_BUILD_INPUT_SCHEMA_VERSION = 1
EXTERNAL_PROVISION_CONFIRMATION_ENV = "AAS_EXTERNAL_PROVISION_CONFIRM"
EXTERNAL_PROVISION_CONFIRMATION_PREFIX = "I approve external dependency plan "
EXTERNAL_EXECUTION_RISK_CONFIRMATION_ENV = "AAS_EXTERNAL_EXECUTION_RISK_CONFIRM"
EXTERNAL_EXECUTION_RISK_CONFIRMATION_PREFIX = "I understand pinned external build code is not sandboxed for plan "
MAX_STATE_BYTES = 512 * 1024
MAX_GIT_TEXT_BYTES = 8 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
MAX_WHEEL_METADATA_BYTES = 256 * 1024
MAX_RUNTIME_RECORD_BYTES = 16 * 1024 * 1024
MAX_RUNTIME_RECORD_ENTRIES = 200_000
MAX_WHEEL_LOCK_BYTES = 256 * 1024
MAX_WHEEL_LOCK_ENTRIES = 512
SOURCE_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
WHEEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
WHEEL_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.!+:-]{0,127}$")
WHEEL_LOCK_LINE_RE = re.compile(
    r"^(?P<distribution>[A-Za-z0-9][A-Za-z0-9_.-]{0,127})"
    r"==(?P<version>[A-Za-z0-9][A-Za-z0-9_.!+:-]{0,127})"
    r" --hash=(?P<sha256>sha256:[0-9a-f]{64})$"
)
REQUIREMENT_DISTRIBUTION_RE = re.compile(r"^(?P<distribution>[A-Za-z0-9][A-Za-z0-9_.-]{0,127})")
BUILD_TOOL_DISTRIBUTIONS = frozenset({"setuptools", "wheel"})
PIP_INDEX_URL = "https://pypi.org/simple"
EXTERNAL_WHEEL_LOCKS_ROOT = MANIFEST_DIR / "external-dependency-locks"
NATIVE_HOST_TARGETS = (
    "antigravity",
    "chatgpt-local-coder",
    "claude",
    "codex",
    "copilot",
    "deepseek",
    "grok",
    "kimi",
    "opencode",
)


def external_confirmation_phrase(plan_digest: str) -> str:
    return EXTERNAL_PROVISION_CONFIRMATION_PREFIX + plan_digest


def external_execution_risk_confirmation_phrase(plan_digest: str) -> str:
    return EXTERNAL_EXECUTION_RISK_CONFIRMATION_PREFIX + plan_digest


def verify_external_provision_confirmation(plan_digest: str) -> None:
    expected = external_confirmation_phrase(plan_digest)
    answer = os.environ.get(EXTERNAL_PROVISION_CONFIRMATION_ENV)
    if answer is None:
        answer = sys.stdin.readline()
    if not answer:
        raise ValueError("external dependency provisioning confirmation is required")
    if answer.strip() != expected:
        raise ValueError("external dependency provisioning aborted: confirmation did not bind the plan digest")
    risk_expected = external_execution_risk_confirmation_phrase(plan_digest)
    risk_answer = os.environ.get(EXTERNAL_EXECUTION_RISK_CONFIRMATION_ENV)
    if risk_answer is None:
        risk_answer = sys.stdin.readline()
    if not risk_answer:
        raise ValueError("external dependency execution-risk confirmation is required")
    if risk_answer.strip() != risk_expected:
        raise ValueError("external dependency provisioning aborted: execution-risk confirmation did not bind the plan digest")


def select_external_bundles(manifests: dict[str, Any], requested: Iterable[str] | None) -> list[str]:
    bundles = manifests["external_dependencies"]["bundles"]
    selected = sorted(bundles) if requested is None else sorted(dict.fromkeys(requested))
    unknown = [name for name in selected if name not in bundles]
    if unknown:
        raise ValueError(f"unknown external dependency bundle(s): {', '.join(unknown)}")
    if not selected:
        raise ValueError("at least one external dependency bundle is required")
    return selected


def external_storage_root(root: Path, platform: str) -> Path:
    if platform == "windows":
        return root / "AppData" / "Local" / "ai-agents-skills" / "external-dependencies"
    return root / ".local" / "share" / "ai-agents-skills" / "external-dependencies"


def external_state_path(root: Path) -> Path:
    return state_dir(root) / "external-dependencies.json"


def external_transaction_path(root: Path) -> Path:
    return state_dir(root) / "external-dependencies.transaction.json"


def bundle_paths(root: Path, platform: str, name: str, spec: dict[str, Any]) -> dict[str, Path]:
    storage = external_storage_root(root, platform)
    return {
        "storage_root": storage,
        "source": storage / "sources" / spec["source_directory"] / spec["revision"],
        "source_parent": storage / "sources" / spec["source_directory"],
        "generation": storage / "venvs" / name / spec["revision"],
        "generation_parent": storage / "venvs" / name,
        "pointer": root / spec["venv_pointer"],
        "state": external_state_path(root),
    }


def _entry_state(path: Path) -> dict[str, Any]:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return {"kind": "missing"}
    result: dict[str, Any] = {
        "kind": "other",
        "mode": stat.S_IMODE(info.st_mode),
        "size": int(info.st_size),
        "uid": int(getattr(info, "st_uid", 0)),
        "inode": int(getattr(info, "st_ino", 0)),
    }
    if stat.S_ISLNK(info.st_mode):
        target = os.readlink(path)
        if len(target.encode("utf-8", "surrogateescape")) > 1024:
            target = "<overlong>"
        result.update({"kind": "symlink", "target": target})
    elif stat.S_ISDIR(info.st_mode):
        result["kind"] = "directory"
    elif stat.S_ISREG(info.st_mode):
        result["kind"] = "file"
    return result


def _receipt_entry_state(path: Path) -> dict[str, Any]:
    """Return bounded receipt metadata suitable for a no-write plan."""
    result = _entry_state(path)
    if result["kind"] == "file":
        if int(result["size"]) > MAX_STATE_BYTES:
            result["sha256"] = "<over-limit>"
        else:
            digest = sha256_file(path)
            if digest is None:
                raise ValueError("external dependency receipt could not be hashed")
            result["sha256"] = digest
    return result


def _path_within(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _canonical_sha256(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _wheel_lock_target(platform: str) -> str | None:
    """Return the reviewed wheel-lock target for the running host, if any."""
    if platform not in {"linux", "wsl"} or not sys.platform.startswith("linux"):
        return None
    machine = host_platform.machine().strip().lower()
    aliases = {
        "aarch64": "aarch64",
        "arm64": "aarch64",
        "x86_64": "x86_64",
        "amd64": "x86_64",
    }
    architecture = aliases.get(machine)
    return f"linux-{architecture}" if architecture is not None else None


def _requirement_distribution(requirement: str) -> str:
    match = REQUIREMENT_DISTRIBUTION_RE.match(requirement)
    if match is None:
        raise ValueError("external dependency requirement has no distribution name")
    return _normalized_distribution(match.group("distribution"))


def _wheel_lock_path(bundle: str, target: str | None) -> Path | None:
    if target is None:
        return None
    root = EXTERNAL_WHEEL_LOCKS_ROOT.resolve(strict=False)
    candidate = (root / target / f"{bundle}.txt").resolve(strict=False)
    if not _path_within(root, candidate):  # Defensive even though bundle names are manifest-validated.
        raise ValueError("external dependency wheel lock path escapes the manifest directory")
    return candidate


def _load_reviewed_wheel_lock(name: str, spec: dict[str, Any], platform: str) -> dict[str, Any]:
    """Load one fixed, hash-pinned third-party wheel lock without mutating state."""
    target = _wheel_lock_target(platform)
    path = _wheel_lock_path(name, target)
    if path is None or _entry_state(path)["kind"] == "missing":
        return {
            "status": "unavailable",
            "platform": target,
            "reason": "no reviewed hash-pinned wheel lock is committed for this host platform",
        }
    state = _entry_state(path)
    if state["kind"] != "file" or int(state["size"]) > MAX_WHEEL_LOCK_BYTES:
        raise ValueError("reviewed external dependency wheel lock is unsafe or overlong")
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8", "strict")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("reviewed external dependency wheel lock is not readable UTF-8") from exc
    if not text or not text.endswith("\n"):
        raise ValueError("reviewed external dependency wheel lock must be non-empty and newline-terminated")
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        match = WHEEL_LOCK_LINE_RE.fullmatch(line)
        if match is None:
            raise ValueError("reviewed external dependency wheel lock has an invalid line")
        distribution = match.group("distribution")
        normalized = _normalized_distribution(distribution)
        if normalized in seen:
            raise ValueError("reviewed external dependency wheel lock has duplicate distributions")
        seen.add(normalized)
        records.append(
            {
                "distribution": distribution,
                "version": match.group("version"),
                "sha256": match.group("sha256"),
            }
        )
    if not records or len(records) > MAX_WHEEL_LOCK_ENTRIES:
        raise ValueError("reviewed external dependency wheel lock has an invalid number of entries")
    missing_requirements = {
        _requirement_distribution(requirement) for requirement in spec["requirements"]
    } - seen
    if missing_requirements:
        raise ValueError("reviewed external dependency wheel lock omits a declared requirement")
    if not BUILD_TOOL_DISTRIBUTIONS.issubset(seen):
        raise ValueError("reviewed external dependency wheel lock omits required build tools")
    if _normalized_distribution(spec["distribution"]) in seen:
        raise ValueError("reviewed external dependency wheel lock must not contain the local project wheel")
    return {
        "status": "ready",
        "platform": target,
        "path": path.relative_to(MANIFEST_DIR.parent).as_posix(),
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "records": sorted(records, key=lambda record: _normalized_distribution(record["distribution"])),
    }


def _build_input(name: str, spec: dict[str, Any], platform: str) -> dict[str, Any]:
    payload = {
        "schema_version": EXTERNAL_BUILD_INPUT_SCHEMA_VERSION,
        "bundle": name,
        "distribution": spec["distribution"],
        "version": spec["version"],
        "requirements": list(spec["requirements"]),
        "build_tools": sorted(BUILD_TOOL_DISTRIBUTIONS),
        "resolver": {
            "index_url": PIP_INDEX_URL,
            "download": ["--only-binary=:all:", "--no-deps", "--require-hashes"],
            "build": ["--no-index", "--no-deps", "--no-build-isolation"],
        },
        "wheel_lock": _load_reviewed_wheel_lock(name, spec, platform),
    }
    return {**payload, "digest": _canonical_sha256(payload)}


def _require_ready_build_input(build_input: dict[str, Any]) -> dict[str, Any]:
    wheel_lock = build_input.get("wheel_lock")
    if not isinstance(wheel_lock, dict) or wheel_lock.get("status") != "ready":
        raise ValueError("no reviewed hash-pinned wheel lock is available for this host platform")
    if not isinstance(build_input.get("digest"), str) or SHA256_DIGEST_RE.fullmatch(build_input["digest"]) is None:
        raise ValueError("external dependency build input has an invalid digest")
    return wheel_lock


def _assert_current_build_input(
    name: str,
    spec: dict[str, Any],
    platform: str,
    approved_build_input: dict[str, Any],
) -> dict[str, Any]:
    current = _build_input(name, spec, platform)
    if current.get("digest") != approved_build_input.get("digest"):
        raise ValueError("external dependency build inputs changed after plan approval; run a fresh dry run")
    _require_ready_build_input(current)
    return current


def _plan_payload(plan: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(plan)
    payload.pop("plan_digest", None)
    payload.pop("status", None)
    return payload


def plan_digest(plan: dict[str, Any]) -> str:
    return _canonical_sha256(_plan_payload(plan))


def build_external_dependency_plan(
    root: Path,
    manifests: dict[str, Any],
    *,
    platform: str,
    requested_bundles: Iterable[str] | None = None,
) -> dict[str, Any]:
    root = Path(os.path.abspath(root))
    selected = select_external_bundles(manifests, requested_bundles)
    specs = manifests["external_dependencies"]["bundles"]
    bundles = []
    for name in selected:
        spec = specs[name]
        paths = bundle_paths(root, platform, name, spec)
        build_input = _build_input(name, spec, platform)
        bundles.append(
            {
                "name": name,
                "repository": spec["repository"],
                "revision": spec["revision"],
                "distribution": spec["distribution"],
                "version": spec["version"],
                "modules": list(spec["modules"]),
                "help_modules": list(spec["help_modules"]),
                "build_input": build_input,
                "paths": {key: str(value) for key, value in paths.items()},
                "pre_state": {
                    "source": _entry_state(paths["source"]),
                    "generation": _entry_state(paths["generation"]),
                    "pointer": _entry_state(paths["pointer"]),
                    "receipt": _receipt_entry_state(paths["state"]),
                },
            }
        )
    plan = {
        "schema_version": EXTERNAL_PLAN_SCHEMA_VERSION,
        "root": str(root),
        "platform": platform,
        "bundles": bundles,
        "native_host_targets": list(NATIVE_HOST_TARGETS),
        "excluded_targets": {
            "openclaw": "uses an image-local environment and cannot reuse host virtual environments"
        },
        "mutation_platform_status": (
            "blocked-native-windows" if platform == "windows" else "supported-posix-or-wsl"
        ),
    }
    plan["plan_digest"] = plan_digest(plan)
    return plan


def _validate_external_state(loaded: Any) -> dict[str, Any]:
    if not isinstance(loaded, dict) or loaded.get("schema_version") != EXTERNAL_STATE_SCHEMA_VERSION:
        raise ValueError("external dependency receipt has an unsupported schema")
    bundles = loaded.get("bundles")
    if not isinstance(bundles, dict):
        raise ValueError("external dependency receipt bundles must be an object")
    return loaded


def load_external_state(root: Path) -> dict[str, Any]:
    path = external_state_path(root)
    preflight_state_path(root, path)
    state = _entry_state(path)
    if state["kind"] == "missing":
        return {"schema_version": EXTERNAL_STATE_SCHEMA_VERSION, "bundles": {}}
    if state["kind"] != "file":
        raise ValueError("external dependency receipt must be a regular file")
    if int(state["size"]) > MAX_STATE_BYTES:
        raise ValueError("external dependency receipt exceeds the size limit")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("external dependency receipt is not valid UTF-8 JSON") from exc
    return _validate_external_state(loaded)


def save_external_state(root: Path, state: dict[str, Any]) -> None:
    if state.get("schema_version") != EXTERNAL_STATE_SCHEMA_VERSION or not isinstance(state.get("bundles"), dict):
        raise ValueError("refusing to write an invalid external dependency receipt")
    path = external_state_path(root)
    preflight_state_path(root, path)
    prepare_state_directory(root, path.parent)
    write_text_atomic(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def _require_private_directory_chain(root: Path, target: Path) -> None:
    root = Path(os.path.abspath(root))
    target = Path(os.path.abspath(target))
    if not _path_within(root, target):
        raise ValueError(f"external dependency path escapes selected root: {target}")
    root_state = _entry_state(root)
    if root_state["kind"] != "directory":
        raise ValueError("external dependency root must be an existing regular directory")
    expected_uid = int(root_state.get("uid", 0)) if os.name == "posix" else None
    current = root
    for part in target.relative_to(root).parts:
        current = current / part
        state = _entry_state(current)
        if state["kind"] == "missing":
            current.mkdir(mode=0o700)
            state = _entry_state(current)
        if state["kind"] != "directory":
            raise ValueError(f"external dependency path has an unsafe non-directory component: {current}")
        if os.name == "posix":
            if state.get("uid") != expected_uid:
                raise ValueError(f"external dependency path owner differs from selected root: {current}")
            if int(state.get("mode", 0)) & 0o022:
                raise ValueError(f"external dependency path is group/world writable: {current}")


def _require_private_root(root: Path) -> None:
    """Refuse to provision beneath a root another local user can replace.

    The ordinary installer can manage a nominated sandbox root, while external
    dependency provisioning executes fetched build code and switches stable
    interpreter pointers.  That operation needs the selected home itself to
    be an existing, non-symlinked directory owned by the invoking user.
    """
    state = _entry_state(root)
    if state["kind"] != "directory":
        raise ValueError("external dependency root must be an existing regular directory")
    if os.name == "posix":
        if state.get("uid") != os.getuid():
            raise ValueError("external dependency root must be owned by the invoking user")
        if int(state.get("mode", 0)) & 0o022:
            raise ValueError("external dependency root must not be group/world writable")


@contextmanager
def external_provision_lock(root: Path) -> Iterator[None]:
    """Serialize a provision run below the private installer state directory.

    Native Windows provisioning is fail-closed before this is called.  POSIX
    ``flock`` therefore provides a small, process-scoped interlock that keeps a
    second invocation from changing a pointer or receipt between plan checks
    and activation.
    """
    _require_private_root(root)
    lock_dir = state_dir(root)
    _require_private_directory_chain(root, lock_dir)
    lock_path = lock_dir / "external-dependencies.lock"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        state = _entry_state(lock_path)
        if state["kind"] != "file":
            raise ValueError("external dependency provision lock is not a regular file")
        if os.name == "posix":
            if state.get("uid") != os.getuid() or int(state.get("mode", 0)) & 0o077:
                raise ValueError("external dependency provision lock is not private to the invoking user")
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _require_absent_or_directory(path: Path, *, label: str) -> None:
    state = _entry_state(path)
    if state["kind"] not in {"missing", "directory"}:
        raise ValueError(f"{label} must be absent or a regular directory")


def _safe_symlink_target(pointer: Path, target: Path) -> str:
    return os.path.relpath(target, pointer.parent)


def _resolved_pointer_target(pointer: Path) -> Path | None:
    state = _entry_state(pointer)
    if state["kind"] == "missing":
        return None
    if state["kind"] != "symlink":
        raise ValueError(f"external dependency pointer is not a managed symlink: {pointer}")
    return (pointer.parent / str(state["target"])).resolve(strict=False)


def _record_matches_paths(
    record: Any,
    paths: dict[str, Path],
    spec: dict[str, Any],
    *,
    build_input: dict[str, Any] | None = None,
) -> bool:
    if not isinstance(record, dict):
        return False
    expected = {
        "repository": spec["repository"],
        "revision": spec["revision"],
        "source_path": str(paths["source"]),
        "generation_path": str(paths["generation"]),
        "pointer_path": str(paths["pointer"]),
        "distribution": spec["distribution"],
        "version": spec["version"],
    }
    if not all(record.get(key) == value for key, value in expected.items()):
        return False
    if build_input is None:
        return True
    try:
        wheel_lock = _require_ready_build_input(build_input)
    except ValueError:
        return False
    expected_build = {
        "build_input_digest": build_input["digest"],
        "wheel_lock_sha256": wheel_lock["sha256"],
        "wheel_lock_path": wheel_lock["path"],
        "wheel_lock_platform": wheel_lock["platform"],
    }
    return all(record.get(key) == value for key, value in expected_build.items())


def _legacy_record_matches_build_input(
    record: Any,
    paths: dict[str, Path],
    spec: dict[str, Any],
    build_input: dict[str, Any],
) -> bool:
    """Allow a confirmed receipt-only upgrade from the earlier hash-record format."""
    if not _record_matches_paths(record, paths, spec):
        return False
    if not isinstance(record, dict) or any(
        key in record
        for key in (
            "build_input_digest",
            "wheel_lock_sha256",
            "wheel_lock_path",
            "wheel_lock_platform",
        )
    ):
        return False
    return _record_wheels_match_build_input(record, spec, build_input)


def _stamp_record_build_input(record: dict[str, Any], build_input: dict[str, Any]) -> dict[str, Any]:
    wheel_lock = _require_ready_build_input(build_input)
    stamped = copy.deepcopy(record)
    stamped.update(
        {
            "build_input_digest": build_input["digest"],
            "wheel_lock_sha256": wheel_lock["sha256"],
            "wheel_lock_path": wheel_lock["path"],
            "wheel_lock_platform": wheel_lock["platform"],
        }
    )
    return stamped


def _assert_pointer_admissible(
    pointer: Path,
    generation: Path,
    record: Any,
    paths: dict[str, Path],
    spec: dict[str, Any],
) -> str | None:
    previous = _resolved_pointer_target(pointer)
    if previous is None:
        return None
    if not _record_matches_paths(record, paths, spec):
        raise ValueError("refusing to replace an unmanaged external dependency pointer")
    if previous != generation.resolve(strict=False):
        raise ValueError("external dependency pointer target does not match its managed receipt")
    return _entry_state(pointer)["target"]


def _load_external_transaction(root: Path) -> dict[str, Any] | None:
    path = external_transaction_path(root)
    preflight_state_path(root, path)
    entry = _entry_state(path)
    if entry["kind"] == "missing":
        return None
    if entry["kind"] != "file":
        raise ValueError("external dependency transaction journal must be a regular file")
    if int(entry["size"]) > MAX_STATE_BYTES:
        raise ValueError("external dependency transaction journal exceeds the size limit")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("external dependency transaction journal is not valid UTF-8 JSON") from exc
    if not isinstance(loaded, dict):
        raise ValueError("external dependency transaction journal must be an object")
    return loaded


def _save_external_transaction(root: Path, transaction: dict[str, Any]) -> None:
    path = external_transaction_path(root)
    preflight_state_path(root, path)
    prepare_state_directory(root, path.parent)
    write_text_atomic(path, json.dumps(transaction, indent=2, sort_keys=True) + "\n")


def _clear_external_transaction(root: Path) -> None:
    path = external_transaction_path(root)
    preflight_state_path(root, path)
    entry = _entry_state(path)
    if entry["kind"] == "missing":
        return
    if entry["kind"] != "file":
        raise ValueError("external dependency transaction journal changed during cleanup")
    path.unlink()


def _transaction_entries(
    root: Path,
    platform: str,
    manifests: dict[str, Any],
    transaction: dict[str, Any],
) -> list[tuple[str, dict[str, Any], dict[str, Path], str | None]]:
    expected_keys = {
        "schema_version",
        "root",
        "platform",
        "plan_digest",
        "receipt_before_kind",
        "state_before",
        "state_after",
        "bundles",
    }
    if set(transaction) != expected_keys or transaction.get("schema_version") != EXTERNAL_TRANSACTION_SCHEMA_VERSION:
        raise ValueError("external dependency transaction journal has an unsupported schema")
    if transaction.get("root") != str(Path(os.path.abspath(root))) or transaction.get("platform") != platform:
        raise ValueError("external dependency transaction journal belongs to another target")
    if not isinstance(transaction.get("plan_digest"), str) or SHA256_DIGEST_RE.fullmatch(transaction["plan_digest"]) is None:
        raise ValueError("external dependency transaction journal has an invalid plan digest")
    if transaction.get("receipt_before_kind") not in {"missing", "file"}:
        raise ValueError("external dependency transaction journal has an invalid receipt pre-state")
    _validate_external_state(transaction.get("state_before"))
    state_after = transaction.get("state_after")
    if state_after is not None:
        _validate_external_state(state_after)
    raw_bundles = transaction.get("bundles")
    if not isinstance(raw_bundles, list) or not raw_bundles:
        raise ValueError("external dependency transaction journal has no bundles")
    specs = manifests["external_dependencies"]["bundles"]
    entries: list[tuple[str, dict[str, Any], dict[str, Path], str | None]] = []
    seen: set[str] = set()
    for raw in raw_bundles:
        if not isinstance(raw, dict) or set(raw) != {
            "name",
            "generation_path",
            "pointer_path",
            "previous_pointer",
            "generation_was_missing",
        }:
            raise ValueError("external dependency transaction journal has an invalid bundle entry")
        name = raw.get("name")
        if not isinstance(name, str) or name not in specs or name in seen:
            raise ValueError("external dependency transaction journal has an unknown or duplicate bundle")
        paths = bundle_paths(root, platform, name, specs[name])
        if raw.get("generation_path") != str(paths["generation"]) or raw.get("pointer_path") != str(paths["pointer"]):
            raise ValueError("external dependency transaction journal path does not match its bundle")
        if raw.get("previous_pointer") is not None or raw.get("generation_was_missing") is not True:
            raise ValueError("external dependency transaction journal is not safe to recover")
        seen.add(name)
        entries.append((name, specs[name], paths, None))
    return entries


def _new_external_transaction(
    root: Path,
    platform: str,
    plan_digest_value: str,
    state_before: dict[str, Any],
    pending: list[tuple[str, dict[str, Any], dict[str, Path], dict[str, Any], str | None]],
) -> dict[str, Any]:
    receipt_entry = _entry_state(external_state_path(root))
    if receipt_entry["kind"] not in {"missing", "file"}:
        raise ValueError("external dependency receipt changed before transaction creation")
    bundles = []
    for name, _spec, paths, _record, previous in pending:
        if previous is not None or _entry_state(paths["generation"])["kind"] != "missing":
            raise ValueError("external dependency generation changed before transaction creation")
        bundles.append(
            {
                "name": name,
                "generation_path": str(paths["generation"]),
                "pointer_path": str(paths["pointer"]),
                "previous_pointer": None,
                "generation_was_missing": True,
            }
        )
    return {
        "schema_version": EXTERNAL_TRANSACTION_SCHEMA_VERSION,
        "root": str(Path(os.path.abspath(root))),
        "platform": platform,
        "plan_digest": plan_digest_value,
        "receipt_before_kind": receipt_entry["kind"],
        "state_before": copy.deepcopy(state_before),
        "state_after": None,
        "bundles": bundles,
    }


def _transaction_is_committed(
    root: Path,
    transaction: dict[str, Any],
    entries: list[tuple[str, dict[str, Any], dict[str, Path], str | None]],
) -> bool:
    state_after = transaction.get("state_after")
    if state_after is None or load_external_state(root) != state_after:
        return False
    for _name, _spec, paths, _previous in entries:
        if _entry_state(paths["generation"])["kind"] != "directory":
            return False
        try:
            target = _resolved_pointer_target(paths["pointer"])
        except ValueError:
            return False
        if target != paths["generation"].resolve(strict=False):
            return False
    return True


def _rollback_external_transaction(
    root: Path,
    transaction: dict[str, Any],
    entries: list[tuple[str, dict[str, Any], dict[str, Path], str | None]],
) -> None:
    state_before = transaction["state_before"]
    state_after = transaction["state_after"]
    current_state = load_external_state(root)
    if current_state != state_before and current_state != state_after:
        raise ValueError("external dependency receipt changed during interrupted transaction recovery")
    for _name, _spec, paths, previous in entries:
        pointer = _entry_state(paths["pointer"])
        expected_target = _safe_symlink_target(paths["pointer"], paths["generation"])
        if pointer["kind"] not in {"missing", "symlink"}:
            raise ValueError("external dependency pointer changed during interrupted transaction recovery")
        if pointer["kind"] == "symlink" and pointer.get("target") not in {expected_target, previous}:
            raise ValueError("external dependency pointer changed during interrupted transaction recovery")
        generation = _entry_state(paths["generation"])
        if generation["kind"] not in {"missing", "directory"}:
            raise ValueError("external dependency generation changed during interrupted transaction recovery")
    for _name, _spec, paths, previous in reversed(entries):
        pointer = _entry_state(paths["pointer"])
        expected_target = _safe_symlink_target(paths["pointer"], paths["generation"])
        if pointer["kind"] == "symlink" and pointer.get("target") == expected_target:
            _restore_pointer(paths["pointer"], paths["generation"], previous)
    for _name, _spec, paths, _previous in entries:
        if _entry_state(paths["generation"])["kind"] == "directory":
            _require_private_directory_chain(root, paths["generation_parent"])
            shutil.rmtree(paths["generation"])
    if transaction["receipt_before_kind"] == "missing":
        receipt = external_state_path(root)
        entry = _entry_state(receipt)
        if entry["kind"] == "file":
            receipt.unlink()
        elif entry["kind"] != "missing":
            raise ValueError("external dependency receipt changed during interrupted transaction recovery")
    else:
        save_external_state(root, state_before)
    _clear_external_transaction(root)


def _recover_external_transaction(root: Path, platform: str, manifests: dict[str, Any]) -> bool:
    transaction = _load_external_transaction(root)
    if transaction is None:
        return False
    entries = _transaction_entries(root, platform, manifests, transaction)
    if _transaction_is_committed(root, transaction, entries):
        _clear_external_transaction(root)
        return True
    _rollback_external_transaction(root, transaction, entries)
    return True


def _child_environment(home: Path) -> dict[str, str]:
    home.mkdir(mode=0o700, parents=True, exist_ok=False)
    empty_hooks = home / "git-hooks"
    empty_hooks.mkdir(mode=0o700)
    cwd = home / "cwd"
    cwd.mkdir(mode=0o700)
    env = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        # All command entry points are passed as absolute paths.  Keep package
        # build hooks off the invoking account's PATH, which commonly includes
        # user-writable language managers and tool shims.
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "PIP_NO_COLOR": "1",
        "PIP_NO_CACHE_DIR": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": os.devnull,
        "GIT_ALLOW_PROTOCOL": "https",
        "GIT_LFS_SKIP_SMUDGE": "1",
    }
    env["AAS_EXTERNAL_EMPTY_GIT_HOOKS"] = str(empty_hooks)
    env["AAS_EXTERNAL_CHILD_CWD"] = str(cwd)
    return env


def _isolated_child_cwd(env: dict[str, str]) -> Path:
    raw = env.get("AAS_EXTERNAL_CHILD_CWD")
    home_raw = env.get("HOME")
    if not isinstance(raw, str) or not isinstance(home_raw, str):
        raise ValueError("external dependency child environment has no isolated working directory")
    home = Path(home_raw).resolve(strict=False)
    cwd = Path(raw).resolve(strict=False)
    state = _entry_state(cwd)
    if state["kind"] != "directory" or not _path_within(home, cwd):
        raise ValueError("external dependency child working directory is unsafe")
    if os.name == "posix" and (state.get("uid") != os.getuid() or int(state.get("mode", 0)) & 0o077):
        raise ValueError("external dependency child working directory is not private")
    return cwd


def _tool_path(name: str) -> str:
    located = shutil.which(name, path=os.defpath)
    if not located:
        raise ValueError(f"required executable is unavailable: {name}")
    return str(Path(located).resolve())


def _run_quiet(argv: list[str], *, env: dict[str, str], timeout: int = 600, label: str) -> None:
    try:
        completed = subprocess.run(
            argv,
            cwd=str(_isolated_child_cwd(env)),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"{label} timed out") from exc
    if completed.returncode != 0:
        raise ValueError(f"{label} failed with exit status {completed.returncode}")


def _run_text(argv: list[str], *, env: dict[str, str], timeout: int = 60, label: str) -> str:
    try:
        completed = subprocess.run(
            argv,
            cwd=str(_isolated_child_cwd(env)),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"{label} timed out") from exc
    if completed.returncode != 0:
        raise ValueError(f"{label} failed with exit status {completed.returncode}")
    output = completed.stdout
    if len(output) > MAX_GIT_TEXT_BYTES:
        raise ValueError(f"{label} produced overlong output")
    try:
        return output.decode("utf-8", "strict").strip()
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} did not produce UTF-8 output") from exc


def _git_argv(git: str, env: dict[str, str], *args: str) -> list[str]:
    return [
        git,
        "-c",
        f"core.hooksPath={env['AAS_EXTERNAL_EMPTY_GIT_HOOKS']}",
        "-c",
        "protocol.file.allow=never",
        "-c",
        "submodule.recurse=false",
        *args,
    ]


def _verify_source_checkout(source: Path, spec: dict[str, Any], *, git: str, env: dict[str, str]) -> str:
    state = _entry_state(source)
    if state["kind"] != "directory":
        raise ValueError("external source generation is not a regular directory")
    remote = _run_text(
        _git_argv(git, env, "-C", str(source), "remote", "get-url", "origin"),
        env=env,
        label="source remote verification",
    )
    if remote != spec["repository"]:
        raise ValueError("external source generation remote does not match the allowlisted repository")
    status = _run_text(
        _git_argv(git, env, "-C", str(source), "status", "--porcelain=v1", "--untracked-files=all"),
        env=env,
        label="source cleanliness verification",
    )
    if status:
        raise ValueError("external source generation is dirty or contains untracked files")
    head = _run_text(
        _git_argv(git, env, "-C", str(source), "rev-parse", "HEAD"),
        env=env,
        label="source revision verification",
    )
    if head != spec["revision"]:
        raise ValueError("external source generation revision does not match the pinned commit")
    tree = _run_text(
        _git_argv(git, env, "-C", str(source), "rev-parse", f"{spec['revision']}^{{tree}}"),
        env=env,
        label="source tree verification",
    )
    if SOURCE_REVISION_RE.fullmatch(tree) is None:
        raise ValueError("external source generation reported an invalid tree identifier")
    return tree


def _ensure_source_checkout(paths: dict[str, Path], spec: dict[str, Any], *, git: str, env: dict[str, str]) -> tuple[Path, str]:
    source = paths["source"]
    source_state = _entry_state(source)
    if source_state["kind"] != "missing":
        return source, _verify_source_checkout(source, spec, git=git, env=env)
    _require_private_directory_chain(paths["pointer"].parent, paths["source_parent"])
    source_parent = paths["source_parent"]
    stage = source_parent / f".{source.name}.{uuid.uuid4().hex}.stage"
    try:
        _run_quiet(
            _git_argv(
                git,
                env,
                "clone",
                "--no-checkout",
                "--no-tags",
                "--",
                spec["repository"],
                str(stage),
            ),
            env=env,
            timeout=300,
            label="pinned source clone",
        )
        _run_quiet(
            _git_argv(git, env, "-C", str(stage), "checkout", "--detach", spec["revision"]),
            env=env,
            timeout=120,
            label="pinned source checkout",
        )
        tree = _verify_source_checkout(stage, spec, git=git, env=env)
        if _entry_state(source)["kind"] != "missing":
            raise ValueError("external source generation appeared while cloning")
        os.replace(stage, source)
        return source, tree
    except Exception:
        if _entry_state(stage)["kind"] == "directory":
            shutil.rmtree(stage)
        raise


def _archive_source(source: Path, revision: str, archive: Path, *, git: str, env: dict[str, str]) -> str:
    try:
        with archive.open("xb") as output:
            completed = subprocess.run(
                _git_argv(git, env, "-C", str(source), "archive", "--format=tar", "--prefix=source/", revision),
                cwd=str(_isolated_child_cwd(env)),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=120,
            )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("source archive creation timed out") from exc
    if completed.returncode != 0:
        raise ValueError(f"source archive creation failed with exit status {completed.returncode}")
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("source archive exceeds the size limit")
    digest = sha256_file(archive)
    if digest is None:
        raise ValueError("source archive digest could not be calculated")
    return digest


def _safe_extract_source(archive: Path, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    with tarfile.open(archive, mode="r:", encoding="utf-8") as bundle:
        members = bundle.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("source archive has too many entries")
        for member in members:
            relative = PurePosixPath(member.name)
            if (
                relative.is_absolute()
                or not relative.parts
                or relative.parts[0] != "source"
                or ".." in relative.parts
                or member.issym()
                or member.islnk()
                or member.isdev()
                or member.isfifo()
            ):
                raise ValueError("source archive contains an unsafe entry")
            target = destination.joinpath(*relative.parts[1:])
            if not _path_within(destination, target):
                raise ValueError("source archive entry escapes its staging directory")
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError("source archive contains an unsupported entry type")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            raw = bundle.extractfile(member)
            if raw is None:
                raise ValueError("source archive file entry could not be read")
            copied = 0
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(target, flags, 0o600)
            try:
                while True:
                    chunk = raw.read(65536)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > member.size:
                        raise ValueError("source archive file length is inconsistent")
                    offset = 0
                    while offset < len(chunk):
                        written = os.write(descriptor, chunk[offset:])
                        if written <= 0:
                            raise ValueError("source archive file could not be written")
                        offset += written
            finally:
                os.close(descriptor)
                raw.close()
            if copied != member.size:
                raise ValueError("source archive file is truncated")


def _venv_python(venv: Path, platform: str) -> Path:
    return venv / ("Scripts/python.exe" if platform == "windows" else "bin/python")


def _create_venv(base_python: str, destination: Path, *, platform: str, env: dict[str, str], label: str) -> Path:
    if _entry_state(destination)["kind"] != "missing":
        raise ValueError(f"{label} target already exists")
    _run_quiet(
        [base_python, "-I", "-m", "venv", str(destination)],
        env=env,
        timeout=120,
        label=label,
    )
    python = _venv_python(destination, platform)
    state = _entry_state(python)
    if state["kind"] not in {"file", "symlink"}:
        raise ValueError(f"{label} did not create a usable Python interpreter")
    return python


def _wheel_metadata(path: Path) -> tuple[str, str]:
    if path.suffix != ".whl":
        raise ValueError("wheelhouse contains a non-wheel artifact")
    try:
        with zipfile.ZipFile(path) as wheel:
            candidates = [
                name
                for name in wheel.namelist()
                if (parts := PurePosixPath(name).parts)
                and len(parts) == 2
                and parts[0].endswith(".dist-info")
                and parts[1] == "METADATA"
            ]
            if len(candidates) != 1:
                raise ValueError("wheel must contain exactly one top-level distribution metadata file")
            info = wheel.getinfo(candidates[0])
            if info.file_size > MAX_WHEEL_METADATA_BYTES:
                raise ValueError("wheel metadata exceeds the size limit")
            data = wheel.read(candidates[0]).decode("utf-8", "strict")
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
        raise ValueError("wheel metadata could not be read") from exc
    values: dict[str, str] = {}
    for line in data.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in {"Name", "Version"} and key not in values:
            values[key] = value.strip()
    name = values.get("Name", "")
    version = values.get("Version", "")
    if WHEEL_NAME_RE.fullmatch(name) is None or WHEEL_VERSION_RE.fullmatch(version) is None:
        raise ValueError("wheel metadata has an invalid name or version")
    return name, version


def _normalized_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def wheel_records(wheelhouse: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in sorted(wheelhouse.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.is_symlink():
            raise ValueError("wheelhouse contains an unsafe entry")
        name, version = _wheel_metadata(path)
        normalized = _normalized_distribution(name)
        if normalized in seen:
            raise ValueError("wheelhouse contains multiple wheels for one distribution")
        digest = sha256_file(path)
        if digest is None:
            raise ValueError("wheelhouse artifact digest could not be calculated")
        seen.add(normalized)
        records.append(
            {
                "distribution": name,
                "version": version,
                "filename": path.name,
                "sha256": digest,
            }
        )
    if not records:
        raise ValueError("wheelhouse is empty")
    return sorted(records, key=lambda item: _normalized_distribution(item["distribution"]))


def write_hash_lock(path: Path, records: list[dict[str, str]], *, only: set[str] | None = None) -> str:
    selected = [
        record
        for record in records
        if only is None or _normalized_distribution(record["distribution"]) in only
    ]
    if not selected:
        raise ValueError("hash lock would be empty")
    lines = [
        f"{record['distribution']}=={record['version']} --hash={record['sha256']}"
        for record in selected
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    digest = sha256_file(path)
    if digest is None:
        raise ValueError("hash lock digest could not be calculated")
    return digest


def _wheel_record_index(records: Any, *, label: str) -> dict[str, tuple[str, str]]:
    if not isinstance(records, list) or not records:
        raise ValueError(f"{label} must contain a non-empty wheel record list")
    index: dict[str, tuple[str, str]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(f"{label} has an invalid wheel record")
        distribution = record.get("distribution")
        version = record.get("version")
        digest = record.get("sha256")
        if (
            not isinstance(distribution, str)
            or WHEEL_NAME_RE.fullmatch(distribution) is None
            or not isinstance(version, str)
            or WHEEL_VERSION_RE.fullmatch(version) is None
            or not isinstance(digest, str)
            or SHA256_DIGEST_RE.fullmatch(digest) is None
        ):
            raise ValueError(f"{label} has an invalid wheel record")
        normalized = _normalized_distribution(distribution)
        if normalized in index:
            raise ValueError(f"{label} has duplicate wheel distributions")
        index[normalized] = (version, digest)
    return index


def _reviewed_lock_records(build_input: dict[str, Any]) -> list[dict[str, str]]:
    wheel_lock = _require_ready_build_input(build_input)
    records = wheel_lock.get("records")
    index = _wheel_record_index(records, label="reviewed external dependency wheel lock")
    if not BUILD_TOOL_DISTRIBUTIONS.issubset(index):
        raise ValueError("reviewed external dependency wheel lock omits required build tools")
    return sorted(
        [
            {
                "distribution": record["distribution"],
                "version": record["version"],
                "sha256": record["sha256"],
            }
            for record in records
        ],
        key=lambda record: _normalized_distribution(record["distribution"]),
    )


def _assert_wheel_records_match_lock(
    records: Any,
    build_input: dict[str, Any],
    *,
    spec: dict[str, Any] | None = None,
    label: str,
) -> None:
    expected = _wheel_record_index(
        _reviewed_lock_records(build_input),
        label="reviewed external dependency wheel lock",
    )
    actual = _wheel_record_index(records, label=label)
    if spec is not None:
        local_name = _normalized_distribution(spec["distribution"])
        local = actual.pop(local_name, None)
        if local is None or local[0] != spec["version"]:
            raise ValueError(f"{label} does not contain the expected local project wheel")
    if actual != expected:
        raise ValueError(f"{label} does not exactly match the reviewed hash-pinned wheel lock")


def _record_wheels_match_build_input(
    record: dict[str, Any],
    spec: dict[str, Any],
    build_input: dict[str, Any],
) -> bool:
    try:
        _assert_wheel_records_match_lock(
            record.get("wheels"),
            build_input,
            spec=spec,
            label="managed external dependency receipt",
        )
    except ValueError:
        return False
    return True


def _record_hash_matches(path: Path, encoded: str, size_text: str) -> bool:
    if not encoded.startswith("sha256=") or not size_text.isdigit():
        return False
    digest = sha256_file(path)
    if digest is None:
        return False
    try:
        payload = encoded.split("=", 1)[1]
        expected = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    except (ValueError, binascii.Error):
        return False
    try:
        actual = bytes.fromhex(digest.removeprefix("sha256:"))
    except ValueError:
        return False
    try:
        return actual == expected and path.stat().st_size == int(size_text)
    except OSError:
        return False


def _runtime_record_integrity(venv: Path) -> str:
    """Verify wheel-installed files against their RECORD hashes.

    The receipt retains a digest of the RECORD files.  Rechecking them before
    accepting an already-active generation detects a changed install tree while
    allowing normal unrecorded artifacts such as ``__pycache__`` files.
    """
    root = venv.resolve(strict=False)
    if _entry_state(venv)["kind"] != "directory" or root != venv.absolute():
        raise ValueError("runtime integrity root is not a regular non-symlinked directory")
    site_packages_paths = [
        *venv.glob("lib*/python*/site-packages"),
        venv / "Lib" / "site-packages",
    ]
    site_packages: list[Path] = []
    seen_site_packages: set[Path] = set()
    for path in site_packages_paths:
        if _entry_state(path)["kind"] != "directory":
            continue
        resolved = path.resolve(strict=False)
        if not _path_within(root, resolved):
            raise ValueError("runtime site-packages path escapes the virtual environment")
        if resolved not in seen_site_packages:
            seen_site_packages.add(resolved)
            site_packages.append(resolved)
    record_paths = sorted(
        (
            (site_path, record)
            for site_path in site_packages
            for record in site_path.glob("*.dist-info/RECORD")
        ),
        key=lambda item: item[1].relative_to(venv).as_posix(),
    )
    if not record_paths:
        raise ValueError("runtime environment has no installed distribution RECORD files")
    digest = hashlib.sha256()
    expected_files: dict[Path, set[tuple[str, str]]] = {}
    checked_entries = 0
    for site_packages, record in record_paths:
        entry = _entry_state(record)
        if entry["kind"] != "file" or int(entry["size"]) > MAX_RUNTIME_RECORD_BYTES:
            raise ValueError("runtime distribution RECORD is unsafe or overlong")
        raw = record.read_bytes()
        try:
            text = raw.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise ValueError("runtime distribution RECORD is not UTF-8") from exc
        relative_record = record.relative_to(venv).as_posix()
        digest.update(relative_record.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(raw).digest())
        digest.update(b"\n")
        for row in csv.reader(io.StringIO(text)):
            checked_entries += 1
            if checked_entries > MAX_RUNTIME_RECORD_ENTRIES or len(row) != 3:
                raise ValueError("runtime distribution RECORD has an invalid entry")
            raw_path, encoded, size_text = row
            relative = PurePosixPath(raw_path)
            if not raw_path or relative.is_absolute() or "\\" in raw_path:
                raise ValueError("runtime distribution RECORD has an unsafe path")
            candidate = (site_packages.joinpath(*relative.parts)).resolve(strict=False)
            if not _path_within(root, candidate):
                raise ValueError("runtime distribution RECORD path escapes the virtual environment")
            if encoded:
                if not encoded.startswith("sha256=") or not size_text.isdigit():
                    raise ValueError("runtime distribution RECORD has an invalid hashed entry")
                expected_files.setdefault(candidate, set()).add((encoded, size_text))
            if not encoded and size_text:
                raise ValueError("runtime distribution RECORD has an inconsistent unhashed entry")
    for candidate, expected_hashes in expected_files.items():
        if not any(_record_hash_matches(candidate, encoded, size_text) for encoded, size_text in expected_hashes):
            raise ValueError("runtime distribution file does not match any installed RECORD hash")
    return "sha256:" + digest.hexdigest()


def _verify_runtime_venv(python: Path, spec: dict[str, Any], *, env: dict[str, str]) -> None:
    _run_quiet([str(python), "-I", "-m", "pip", "check"], env=env, timeout=120, label="offline dependency verification")
    modules_literal = json.dumps(list(spec["modules"]))
    distribution_literal = json.dumps(spec["distribution"])
    version_literal = json.dumps(spec["version"])
    probe = (
        "import importlib, importlib.metadata, os, pathlib, sys; "
        f"modules={modules_literal}; distribution={distribution_literal}; expected={version_literal}; "
        "root=pathlib.Path(sys.prefix).resolve(); "
        "assert importlib.metadata.version(distribution) == expected; "
        "[(lambda module: (module, getattr(module, '__file__', None)))(importlib.import_module(name)) for name in modules]; "
        "[(lambda origin: (_ for _ in ()).throw(AssertionError('module origin escapes selected venv')) if origin is None or os.path.commonpath([str(root), str(pathlib.Path(origin).resolve())]) != str(root) else None)(getattr(importlib.import_module(name), '__file__', None)) for name in modules]"
    )
    _run_quiet([str(python), "-I", "-c", probe], env=env, timeout=120, label="runtime import verification")
    for module in spec["help_modules"]:
        _run_quiet(
            [str(python), "-I", "-m", module, "--help"],
            env=env,
            timeout=120,
            label=f"agent-safe help verification for {module}",
        )


def _build_generation(
    root: Path,
    platform: str,
    name: str,
    spec: dict[str, Any],
    paths: dict[str, Path],
    *,
    source: Path,
    tree: str,
    git: str,
    base_python: str,
    build_input: dict[str, Any],
) -> dict[str, Any]:
    build_input = _assert_current_build_input(name, spec, platform, build_input)
    reviewed_wheels = _reviewed_lock_records(build_input)
    reviewed_lock = _require_ready_build_input(build_input)
    _require_private_directory_chain(root, paths["storage_root"])
    _require_private_directory_chain(root, paths["generation_parent"])
    if _entry_state(paths["generation"])["kind"] != "missing":
        raise ValueError("external venv generation already exists without an active managed receipt")
    staging_root = paths["storage_root"] / "staging"
    _require_private_directory_chain(root, staging_root)
    stage = Path(tempfile.mkdtemp(prefix=f".{name}.", suffix=".stage", dir=staging_root))
    try:
        home = stage / "home"
        env = _child_environment(home)
        archive = stage / "source.tar"
        archive_sha256 = _archive_source(source, spec["revision"], archive, git=git, env=env)
        extracted = stage / "source"
        _safe_extract_source(archive, extracted)
        builder_venv = stage / "builder-venv"
        builder_python = _create_venv(
            base_python,
            builder_venv,
            platform=platform,
            env=env,
            label="isolated wheel-builder environment creation",
        )
        wheelhouse = stage / "wheelhouse"
        wheelhouse.mkdir(mode=0o700)
        third_party_lock = stage / "third-party.lock"
        if write_hash_lock(third_party_lock, reviewed_wheels) != reviewed_lock["sha256"]:
            raise ValueError("reviewed external dependency wheel lock did not render canonically")
        _run_quiet(
            [
                str(builder_python),
                "-I",
                "-m",
                "pip",
                "download",
                "--index-url",
                PIP_INDEX_URL,
                "--only-binary=:all:",
                "--no-deps",
                "--require-hashes",
                "--dest",
                str(wheelhouse),
                "-r",
                str(third_party_lock),
            ],
            env=env,
            timeout=900,
            label="reviewed hash-locked wheel acquisition",
        )
        downloaded = wheel_records(wheelhouse)
        _assert_wheel_records_match_lock(
            downloaded,
            build_input,
            label="downloaded external dependency wheels",
        )
        bootstrap_lock = stage / "build-tools.lock"
        write_hash_lock(bootstrap_lock, downloaded, only=set(BUILD_TOOL_DISTRIBUTIONS))
        _run_quiet(
            [
                str(builder_python),
                "-I",
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                "--require-hashes",
                "--no-deps",
                "-r",
                str(bootstrap_lock),
            ],
            env=env,
            timeout=300,
            label="offline build-tool installation",
        )
        _run_quiet(
            [
                str(builder_python),
                "-I",
                "-m",
                "pip",
                "wheel",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(wheelhouse),
                str(extracted),
            ],
            env=env,
            timeout=600,
            label="pinned project wheel build",
        )
        records = wheel_records(wheelhouse)
        _assert_wheel_records_match_lock(
            records,
            build_input,
            spec=spec,
            label="built external dependency wheelhouse",
        )
        lock = stage / "requirements.lock"
        lock_sha256 = write_hash_lock(lock, records)
        # Create the runtime venv at its final generation path.  Python console
        # scripts embed the venv path, so relocating a verified staging venv
        # would invalidate those scripts.  The enclosing transaction journal
        # records this newly absent path before creation and removes it on any
        # failure before receipt commit.
        runtime_venv = paths["generation"]
        if _entry_state(runtime_venv)["kind"] != "missing":
            raise ValueError("external venv generation appeared before creation")
        runtime_python = _create_venv(
            base_python,
            runtime_venv,
            platform=platform,
            env=env,
            label="isolated runtime environment creation",
        )
        _run_quiet(
            [
                str(runtime_python),
                "-I",
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                "--require-hashes",
                "--no-deps",
                "-r",
                str(lock),
            ],
            env=env,
            timeout=900,
            label="offline hash-locked runtime installation",
        )
        _verify_runtime_venv(runtime_python, spec, env=env)
        runtime_record_sha256 = _runtime_record_integrity(runtime_venv)
        return {
            "repository": spec["repository"],
            "revision": spec["revision"],
            "tree": tree,
            "archive_sha256": archive_sha256,
            "source_path": str(paths["source"]),
            "generation_path": str(paths["generation"]),
            "pointer_path": str(paths["pointer"]),
            "distribution": spec["distribution"],
            "version": spec["version"],
            "modules": list(spec["modules"]),
            "build_input_digest": build_input["digest"],
            "wheel_lock_sha256": reviewed_lock["sha256"],
            "wheel_lock_path": reviewed_lock["path"],
            "wheel_lock_platform": reviewed_lock["platform"],
            "lock_sha256": lock_sha256,
            "runtime_record_sha256": runtime_record_sha256,
            "wheels": records,
            "installed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
    finally:
        if _entry_state(stage)["kind"] == "directory":
            shutil.rmtree(stage)


def _activate_pointer(pointer: Path, target: Path, previous: str | None) -> None:
    if previous is not None:
        current = _entry_state(pointer)
        if current.get("kind") != "symlink" or current.get("target") != previous:
            raise ValueError("external dependency pointer changed after plan approval")
    elif _entry_state(pointer)["kind"] != "missing":
        raise ValueError("external dependency pointer appeared after plan approval")
    stage = pointer.with_name(f".{pointer.name}.{uuid.uuid4().hex}.new")
    os.symlink(_safe_symlink_target(pointer, target), stage)
    os.replace(stage, pointer)


def _restore_pointer(pointer: Path, target: Path, previous: str | None) -> None:
    current = _entry_state(pointer)
    expected_target = _safe_symlink_target(pointer, target)
    if current.get("kind") != "symlink" or current.get("target") != expected_target:
        raise ValueError("external dependency pointer changed while attempting rollback")
    if previous is None:
        pointer.unlink()
        return
    stage = pointer.with_name(f".{pointer.name}.{uuid.uuid4().hex}.restore")
    os.symlink(previous, stage)
    os.replace(stage, pointer)


def _verify_active_runtime(root: Path, paths: dict[str, Path], spec: dict[str, Any], record: dict[str, Any], *, platform: str) -> None:
    staging_root = paths["storage_root"] / "staging"
    _require_private_directory_chain(root, staging_root)
    stage_home = Path(tempfile.mkdtemp(prefix=".active-check.", suffix=".home", dir=staging_root))
    try:
        env = _child_environment(stage_home / "home")
        actual_integrity = _runtime_record_integrity(paths["generation"])
        recorded_integrity = record.get("runtime_record_sha256")
        if recorded_integrity is not None:
            if not isinstance(recorded_integrity, str) or SHA256_DIGEST_RE.fullmatch(recorded_integrity) is None:
                raise ValueError("managed runtime receipt has an invalid integrity digest")
            if actual_integrity != recorded_integrity:
                raise ValueError("managed runtime files no longer match their recorded wheel installation")
        _verify_runtime_venv(_venv_python(paths["generation"], platform), spec, env=env)
    finally:
        if _entry_state(stage_home)["kind"] == "directory":
            shutil.rmtree(stage_home)


def _runtime_is_active(
    root: Path,
    paths: dict[str, Path],
    spec: dict[str, Any],
    record: Any,
    *,
    platform: str,
    build_input: dict[str, Any],
) -> bool:
    if not _record_matches_paths(record, paths, spec, build_input=build_input):
        if not _legacy_record_matches_build_input(record, paths, spec, build_input):
            return False
    if not isinstance(record, dict) or not _record_wheels_match_build_input(record, spec, build_input):
        raise ValueError("managed external dependency receipt wheels do not match the reviewed hash-pinned lock")
    if not _record_matches_paths(record, paths, spec):
        return False
    try:
        target = _resolved_pointer_target(paths["pointer"])
    except ValueError:
        return False
    if target != paths["generation"].resolve(strict=False):
        return False
    interpreter = _venv_python(paths["generation"], platform)
    if _entry_state(interpreter)["kind"] not in {"file", "symlink"}:
        raise ValueError("managed external dependency runtime interpreter is missing")
    _verify_active_runtime(root, paths, spec, record, platform=platform)
    return True


def apply_external_dependency_plan(
    plan: dict[str, Any],
    manifests: dict[str, Any],
    *,
    expected_plan_digest: str,
) -> dict[str, Any]:
    if expected_plan_digest != plan.get("plan_digest") or plan_digest(plan) != expected_plan_digest:
        raise ValueError("provided plan digest does not match the current external dependency plan")
    root = Path(plan["root"])
    platform = str(plan["platform"])
    raw_bundles = plan.get("bundles")
    if not isinstance(raw_bundles, list) or not raw_bundles:
        raise ValueError("external dependency plan has no bundles")
    requested_bundles = []
    for item in raw_bundles:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("external dependency plan has an invalid bundle entry")
        requested_bundles.append(item["name"])
    current_plan = build_external_dependency_plan(
        root,
        manifests,
        platform=platform,
        requested_bundles=requested_bundles,
    )
    if current_plan["plan_digest"] != expected_plan_digest:
        raise ValueError("external dependency pre-state changed after plan approval; run a fresh dry run")
    for item in current_plan["bundles"]:
        _require_ready_build_input(item["build_input"])
    if platform == "windows":
        require_handle_bound_mutation("external dependency provisioning")
    if sys.version_info < (3, 10):
        raise ValueError("external dependency provisioning requires Python 3.10+")
    _require_private_root(root)
    with external_provision_lock(root):
        _recover_external_transaction(root, platform, manifests)
        locked_plan = build_external_dependency_plan(
            root,
            manifests,
            platform=platform,
            requested_bundles=requested_bundles,
        )
        if locked_plan["plan_digest"] != expected_plan_digest:
            raise ValueError("external dependency pre-state changed after plan approval; run a fresh dry run")
        for item in locked_plan["bundles"]:
            _require_ready_build_input(item["build_input"])
        specs = manifests["external_dependencies"]["bundles"]
        state = load_external_state(root)
        state_after_active = copy.deepcopy(state)
        git = _tool_path("git")
        base_python = str(Path(sys.executable).resolve())
        pending: list[tuple[str, dict[str, Any], dict[str, Path], dict[str, Any], str | None]] = []
        results: list[dict[str, Any]] = []
        for item in locked_plan["bundles"]:
            name = item["name"]
            spec = specs[name]
            build_input = item["build_input"]
            paths = bundle_paths(root, platform, name, spec)
            record = state["bundles"].get(name)
            previous = _assert_pointer_admissible(paths["pointer"], paths["generation"], record, paths, spec)
            if _runtime_is_active(
                root,
                paths,
                spec,
                record,
                platform=platform,
                build_input=build_input,
            ):
                if not _record_matches_paths(record, paths, spec, build_input=build_input):
                    if not isinstance(record, dict):  # Defensive: active records are dictionaries above.
                        raise ValueError("managed external dependency receipt is invalid")
                    state_after_active["bundles"][name] = _stamp_record_build_input(record, build_input)
                    status = "already-active-revalidated"
                else:
                    status = "already-active"
                results.append({"name": name, "status": status, "generation": str(paths["generation"])})
                continue
            if _entry_state(paths["generation"])["kind"] != "missing":
                raise ValueError("managed external dependency generation failed verification; refusing in-place replacement")
            if previous is not None:
                raise ValueError("managed external dependency pointer is dangling; refusing replacement")
            pending.append((name, spec, paths, record if isinstance(record, dict) else {}, previous))
        build_inputs = {item["name"]: item["build_input"] for item in locked_plan["bundles"]}
        prepared: list[tuple[str, dict[str, Any], dict[str, Path], str, str, str | None, dict[str, Any]]] = []
        for name, spec, paths, _record, previous in pending:
            staging_root = paths["storage_root"] / "staging"
            _require_private_directory_chain(root, staging_root)
            stage_home = Path(tempfile.mkdtemp(prefix=f".{name}.", suffix=".git-home", dir=staging_root))
            try:
                env = _child_environment(stage_home / "home")
                source, tree = _ensure_source_checkout(paths, spec, git=git, env=env)
            finally:
                if _entry_state(stage_home)["kind"] == "directory":
                    shutil.rmtree(stage_home)
            prepared.append((name, spec, paths, str(source), tree, previous, build_inputs[name]))
        if not prepared:
            if state_after_active != state:
                save_external_state(root, state_after_active)
            return {
                "status": "ok",
                "plan_digest": expected_plan_digest,
                "results": results,
                "receipt": str(external_state_path(root)),
                "excluded_targets": locked_plan["excluded_targets"],
            }
        transaction = _new_external_transaction(root, platform, expected_plan_digest, state, pending)
        _save_external_transaction(root, transaction)
        try:
            built: list[tuple[str, dict[str, Any], dict[str, Path], dict[str, Any], str | None]] = []
            for name, spec, paths, source_text, tree, previous, build_input in prepared:
                receipt = _build_generation(
                    root,
                    platform,
                    name,
                    spec,
                    paths,
                    source=Path(source_text),
                    tree=tree,
                    git=git,
                    base_python=base_python,
                    build_input=build_input,
                )
                built.append((name, spec, paths, receipt, previous))
            state_after = copy.deepcopy(state_after_active)
            for name, _spec, _paths, receipt, _previous in built:
                state_after["bundles"][name] = receipt
            transaction["state_after"] = state_after
            _save_external_transaction(root, transaction)
            for name, _spec, paths, _receipt, previous in built:
                _activate_pointer(paths["pointer"], paths["generation"], previous)
                results.append({"name": name, "status": "activated", "generation": str(paths["generation"])})
            save_external_state(root, state_after)
        except Exception:
            try:
                transaction_entries = _transaction_entries(root, platform, manifests, transaction)
                _rollback_external_transaction(root, transaction, transaction_entries)
            except Exception as rollback_error:  # pragma: no cover - hostile or I/O failure during rollback
                raise ValueError("external dependency provisioning failed and transaction rollback also failed") from rollback_error
            raise
        _clear_external_transaction(root)
        return {
            "status": "ok",
            "plan_digest": expected_plan_digest,
            "results": results,
            "receipt": str(external_state_path(root)),
            "excluded_targets": locked_plan["excluded_targets"],
        }
