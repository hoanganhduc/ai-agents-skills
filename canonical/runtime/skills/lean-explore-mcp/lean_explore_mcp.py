#!/usr/bin/env python3
from __future__ import annotations

import os

# Capture and remove the credential at the first Python instruction boundary.
# The managed POSIX wrapper normally supplies it over a private inherited file
# descriptor; the environment form remains supported for direct managed MCP
# clients, but is consumed before discovery or any LeanExplore import.
_LEANEXPLORE_API_KEY = os.environ.pop("LEANEXPLORE_API_KEY", None)
_LEANEXPLORE_KEY_FD = os.environ.pop("AAS_LEANEXPLORE_KEY_FD", "")
_LEANEXPLORE_CLOSURE_FD = os.environ.pop("AAS_LEANEXPLORE_CLOSURE_FD", "")
_LEANEXPLORE_SITE_RELATIVE = os.environ.pop(
    "AAS_LEANEXPLORE_SITE_RELATIVE", ""
)
os.environ.pop("AAS_LEANEXPLORE_SITE_FD", None)
_LEANEXPLORE_WRAPPER_PATH = os.environ.pop("AAS_LEANEXPLORE_WRAPPER_PATH", "")
_LEANEXPLORE_CAPTURE_ERROR = ""

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import stat
import sys
from pathlib import Path
from typing import Any


LEAN_EXPLORE_PACKAGE = "lean-explore"
LEAN_EXPLORE_MODULE = "lean_explore"
LEAN_EXPLORE_COMMAND = "lean-explore"
LEAN_EXPLORE_DOCS = "https://www.leanexplore.com/docs/mcp"
LEAN_EXPLORE_API_KEYS_URL = "https://www.leanexplore.com/api-keys"
LEAN_EXPLORE_CACHE = Path.home() / ".lean_explore" / "cache"
SUPPORTED_LEAN_EXPLORE_VERSION = "1.2.1"
BACKENDS = {"api", "local"}
CLOSURE_MARKER = ".coding-system-python-closure.json"
CLOSURE_MARKER_SCHEMA = "coding-system.python-closure-install/v3"
CLOSURE_CONTENT_SCHEMA = "coding-system.python-closure-content/v2"
_SITE_RELATIVE_RE = re.compile(r"^lib/python3\.[0-9]{1,3}/site-packages$")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lean-explore-mcp")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    config = sub.add_parser("config-snippet")
    config.add_argument("--backend", choices=sorted(BACKENDS), default="api")
    sub.add_parser("smoke")
    serve = sub.add_parser("serve")
    serve.add_argument("--backend", choices=sorted(BACKENDS), default="api")
    args = parser.parse_args(argv)

    if args.command == "doctor":
        emit(doctor_payload())
        return 0
    if args.command == "config-snippet":
        emit(config_snippet_payload(args.backend))
        return 0
    if args.command == "smoke":
        emit(smoke_payload())
        return 0
    if args.command == "serve":
        return serve_adapter(args.backend)
    raise AssertionError(args.command)


def base_payload(status: str = "ok") -> dict[str, Any]:
    return {
        "status": status,
        "schema_version": "lean-explore-mcp.v1",
        "no_auto_install": True,
        "installs_attempted": False,
        "network_required": False,
        "live_api_attempted": False,
        "config_written": False,
        "server_started": False,
        "downloads_attempted": False,
    }


def doctor_payload() -> dict[str, Any]:
    payload = base_payload()
    payload.update({
        "helper_python": python_status(),
        "tool_status": {
            LEAN_EXPLORE_COMMAND: tool_status(LEAN_EXPLORE_COMMAND),
        },
        "module_status": module_status(LEAN_EXPLORE_MODULE),
        "auth_status": auth_status(),
        "local_cache_status": local_cache_status(),
        "manual_live_use": manual_live_use(),
        "limitations": [
            "doctor is offline and never invokes lean-explore or the MCP server",
            "LeanExplore API key presence is reported without exposing the value",
            "local cache status is presence-only and does not prove data freshness",
            "live LeanExplore use is manual and outside installer/runtime smoke",
        ],
    })
    return payload


def config_snippet_payload(backend: str) -> dict[str, Any]:
    payload = base_payload()
    local_command = local_stdio_command(backend)
    payload.update({
        "redaction_status": "placeholder-only",
        "backend": backend,
        "local_stdio_mcp_config": {
            "mcpServers": {
                "lean-explore": local_command,
            },
        },
        "manual_live_use": manual_live_use(),
        "warnings": [
            "copy snippets manually into an MCP client config only after reviewing the target client",
            "do not replace LEANEXPLORE_API_KEY placeholders in this repo or in generated artifacts",
            "local backend requires user-managed LeanExplore data prepared outside this repo",
        ],
    })
    return payload


def smoke_payload() -> dict[str, Any]:
    api_snippet = config_snippet_payload("api")
    local_snippet = config_snippet_payload("local")
    serialized = json.dumps([api_snippet, local_snippet], sort_keys=True)
    local_stdio = json.dumps(local_snippet["local_stdio_mcp_config"], sort_keys=True)
    payload = base_payload()
    payload.update({
        "smoke_mode": "offline",
        "auth_status": "not_inspected",
        "tool_status": {
            LEAN_EXPLORE_COMMAND: tool_status(LEAN_EXPLORE_COMMAND),
        },
        "expected_commands": {
            "api": local_stdio_command("api"),
            "local": local_stdio_command("local"),
        },
        "api_snippet_contains_placeholder": "LEANEXPLORE_API_KEY" in serialized,
        "local_snippet_omits_api_key": "LEANEXPLORE_API_KEY" not in local_stdio,
        "manual_live_use": manual_live_use(),
    })
    return payload


def python_status() -> dict[str, Any]:
    return {
        "status": "available",
        "version": ".".join(str(part) for part in sys.version_info[:3]),
        "executable": sys.executable,
    }


def tool_status(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    return {
        "status": "available" if path else "tool_unavailable",
        "path": path or "",
        "checked_by": "shutil.which",
        "executed": False,
    }


def module_status(name: str) -> dict[str, Any]:
    spec = importlib.util.find_spec(name)
    return {
        "status": "available" if spec else "module_unavailable",
        "module": name,
        "origin": getattr(spec, "origin", "") if spec else "",
        "imported": False,
    }


def auth_status() -> str:
    if _LEANEXPLORE_API_KEY is None:
        return "missing"
    if _LEANEXPLORE_API_KEY == "":
        return "empty"
    return "present"


def local_cache_status() -> dict[str, Any]:
    cache = LEAN_EXPLORE_CACHE.expanduser()
    status: dict[str, Any] = {
        "path": str(cache),
        "exists": cache.is_dir(),
        "data_observed": False,
        "checked": "presence-only",
    }
    if not cache.is_dir():
        return status
    try:
        status["data_observed"] = any(cache.iterdir())
    except OSError as exc:
        status["error"] = str(exc)
    return status


def local_stdio_command(backend: str) -> dict[str, Any]:
    wrapper = managed_wrapper_path()
    command: dict[str, Any] = {
        "command": wrapper,
        "args": ["serve", "--backend", backend],
        "env": {
            "AAS_LEANEXPLORE_SITE_PACKAGES": "<ABSOLUTE_LEANEXPLORE_1_2_1_SITE_PACKAGES>",
        },
    }
    if backend == "api":
        command["env"]["LEANEXPLORE_API_KEY"] = "<LEANEXPLORE_API_KEY>"
    return command


def managed_wrapper_path() -> str:
    candidate = Path(_LEANEXPLORE_WRAPPER_PATH or Path(__file__).with_name("run_lean_explore_mcp.sh"))
    if not candidate.is_absolute():
        candidate = candidate.resolve()
    return str(candidate)


def _stable_file_digest(
    name: str, expected_owner: int, *, dir_fd: int | None = None
) -> tuple[str, int]:
    descriptor = os.open(
        name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != expected_owner
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or before.st_nlink != 1
        ):
            raise RuntimeError("LeanExplore closure file has unsafe ownership or type")
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise RuntimeError("LeanExplore closure file changed during attestation")
        return digest.hexdigest(), int(before.st_size)
    finally:
        os.close(descriptor)


def _installed_content_manifest(
    root: Path, *, expected_owner: int
) -> dict[str, object]:
    """Reproduce CSR's complete v2 installed-content manifest."""

    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        return _held_content_manifest(root_fd, expected_owner=expected_owner)
    finally:
        os.close(root_fd)


def _held_content_manifest(
    root_fd: int, *, expected_owner: int
) -> dict[str, object]:
    """Attest the held closure through descriptor-relative addressing only.

    Every open, stat, and readlink below is dir_fd-relative (openat
    semantics), so the walk never depends on /proc/self/fd or /dev/fd path
    traversal and always attests the directory the held descriptor pins.
    """

    root_info = os.fstat(root_fd)
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != expected_owner
        or root_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise RuntimeError("LeanExplore closure root is not immutable authority")
    digest = hashlib.sha256()
    digest.update((CLOSURE_CONTENT_SCHEMA + "\n").encode("utf-8"))
    entry_count = 0

    def add(record: dict[str, object]) -> None:
        nonlocal entry_count
        payload = json.dumps(
            record, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        entry_count += 1

    def walk(directory_fd: int, relative: Path) -> None:
        try:
            entries = sorted(os.scandir(directory_fd), key=lambda item: item.name)
        except OSError as exc:
            raise RuntimeError("LeanExplore closure cannot be enumerated") from exc
        for entry in entries:
            item_relative = relative / entry.name
            relative_text = item_relative.as_posix()
            if relative_text == CLOSURE_MARKER:
                continue
            info = entry.stat(follow_symlinks=False)
            if info.st_uid != expected_owner or info.st_mode & (
                stat.S_IWGRP | stat.S_IWOTH
            ):
                raise RuntimeError(
                    "LeanExplore closure content has unsafe ownership or mode"
                )
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISDIR(info.st_mode):
                add({"mode": mode, "path": relative_text, "type": "directory"})
                child_fd = os.open(
                    entry.name,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
                try:
                    walk(child_fd, item_relative)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(info.st_mode):
                file_digest, size = _stable_file_digest(
                    entry.name, expected_owner, dir_fd=directory_fd
                )
                add(
                    {
                        "mode": mode,
                        "path": relative_text,
                        "sha256": file_digest,
                        "size": size,
                        "type": "file",
                    }
                )
            elif stat.S_ISLNK(info.st_mode):
                target = os.readlink(entry.name, dir_fd=directory_fd)
                escapes = os.path.isabs(target)
                if not escapes:
                    joined = os.path.normpath(
                        (item_relative.parent / target).as_posix()
                    )
                    escapes = joined == ".." or joined.startswith("../")
                if escapes:
                    raise RuntimeError(
                        "LeanExplore closure symlink escapes the held generation"
                    )
                os.stat(entry.name, dir_fd=directory_fd)
                add(
                    {
                        "mode": mode,
                        "path": relative_text,
                        "target": target,
                        "type": "symlink",
                    }
                )
            else:
                raise RuntimeError("LeanExplore closure has an unsupported entry type")

    add(
        {
            "mode": stat.S_IMODE(root_info.st_mode),
            "path": ".",
            "type": "directory",
        }
    )
    # A fresh "." open yields an independent directory-stream offset for the
    # same pinned inode, so repeated attestation passes over one held
    # descriptor never observe a shared readdir position.
    walk_fd = os.open(
        ".", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0), dir_fd=root_fd
    )
    try:
        walk(walk_fd, Path())
    finally:
        os.close(walk_fd)
    return {
        "schema": CLOSURE_CONTENT_SCHEMA,
        "entryCount": entry_count,
        "sha256": digest.hexdigest(),
    }


def verify_lean_explore_closure(
    descriptor: int,
    site_relative: str,
    *,
    expected_owner: int = 0,
    between_passes: Any | None = None,
) -> str:
    """Validate the complete held CSR closure twice before credential read."""

    if descriptor < 3 or not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        raise RuntimeError("invalid LeanExplore closure descriptor")
    if _SITE_RELATIVE_RE.fullmatch(site_relative) is None:
        raise RuntimeError("invalid LeanExplore site-packages relative path")
    root_text = next(
        (
            f"{prefix}/{descriptor}"
            for prefix in ("/proc/self/fd", "/dev/fd")
            if os.path.isdir(f"{prefix}/{descriptor}")
        ),
        "",
    )
    if not root_text:
        raise RuntimeError("this POSIX host cannot hold the LeanExplore closure")
    # Validation below is descriptor-relative (openat semantics) throughout;
    # root_text only names the returned interpreter-visible site path.  Path
    # traversal beneath /dev/fd entries is unsupported on macOS, so the marker
    # opens through dir_fd instead of a descriptor-rooted string path.
    marker_fd = os.open(
        CLOSURE_MARKER,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=descriptor,
    )
    try:
        marker_info = os.fstat(marker_fd)
        if (
            not stat.S_ISREG(marker_info.st_mode)
            or marker_info.st_uid != expected_owner
            or marker_info.st_mode & 0o222
            or marker_info.st_nlink != 1
            or marker_info.st_size > 1024 * 1024
        ):
            raise RuntimeError("LeanExplore closure marker is unsafe")
        marker_payload = os.read(marker_fd, 1024 * 1024 + 1)
        marker_after = os.fstat(marker_fd)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(marker_payload) > 1024 * 1024 or any(
            getattr(marker_info, field) != getattr(marker_after, field)
            for field in fields
        ):
            raise RuntimeError("LeanExplore closure marker changed during read")
    finally:
        os.close(marker_fd)
    try:
        marker = json.loads(marker_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("LeanExplore closure marker is invalid") from exc
    if not isinstance(marker, dict) or marker.get("schema") != CLOSURE_MARKER_SCHEMA:
        raise RuntimeError("LeanExplore closure marker schema is invalid")
    if marker.get("environment") != "lean-explore":
        raise RuntimeError("LeanExplore closure marker names the wrong environment")
    distributions = marker.get("distributions")
    if not isinstance(distributions, list) or sum(
        1
        for item in distributions
        if isinstance(item, dict)
        and item.get("name") == LEAN_EXPLORE_PACKAGE
        and item.get("version") == SUPPORTED_LEAN_EXPLORE_VERSION
    ) != 1:
        raise RuntimeError("LeanExplore closure lacks the exact supported distribution")
    expected_content = marker.get("installedContent")
    if (
        not isinstance(expected_content, dict)
        or set(expected_content) != {"schema", "entryCount", "sha256"}
        or expected_content.get("schema") != CLOSURE_CONTENT_SCHEMA
        or not isinstance(expected_content.get("entryCount"), int)
        or isinstance(expected_content.get("entryCount"), bool)
        or int(expected_content.get("entryCount") or 0) < 1
        or re.fullmatch(r"[0-9a-f]{64}", str(expected_content.get("sha256") or ""))
        is None
    ):
        raise RuntimeError("LeanExplore closure content claim is invalid")
    first = _held_content_manifest(descriptor, expected_owner=expected_owner)
    if first != expected_content:
        raise RuntimeError("LeanExplore closure content differs from its CSR marker")
    if between_passes is not None:
        between_passes()
    second = _held_content_manifest(descriptor, expected_owner=expected_owner)
    if second != first:
        raise RuntimeError("LeanExplore closure changed after validation")
    component_fds: list[int] = []
    try:
        current_fd = descriptor
        for component in site_relative.split("/"):
            current_fd = os.open(
                component,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=current_fd,
            )
            component_fds.append(current_fd)
    except OSError as exc:
        raise RuntimeError(
            "LeanExplore site-packages is unavailable in held closure"
        ) from exc
    finally:
        for component_fd in component_fds:
            os.close(component_fd)
    return f"{root_text}/{site_relative}"


def _consume_private_key_descriptor() -> None:
    global _LEANEXPLORE_API_KEY, _LEANEXPLORE_CAPTURE_ERROR
    if _LEANEXPLORE_API_KEY is not None or not _LEANEXPLORE_KEY_FD:
        return
    key_bytes = bytearray()
    try:
        key_fd = int(_LEANEXPLORE_KEY_FD, 10)
        if key_fd < 3:
            raise ValueError("credential descriptor must not be a standard stream")
        while len(key_bytes) <= 4097:
            chunk = os.read(key_fd, 4098 - len(key_bytes))
            if not chunk:
                break
            key_bytes.extend(chunk)
        os.close(key_fd)
        if len(key_bytes) > 4097:
            raise ValueError("credential exceeds the supported length")
        if key_bytes.endswith(b"\n"):
            del key_bytes[-1:]
        if b"\n" in key_bytes or b"\r" in key_bytes:
            raise ValueError("credential contains an unsupported line break")
        _LEANEXPLORE_API_KEY = bytes(key_bytes).decode("utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        _LEANEXPLORE_CAPTURE_ERROR = str(exc)
    finally:
        for index in range(len(key_bytes)):
            key_bytes[index] = 0


def _require_supported_distribution() -> None:
    from importlib import metadata

    try:
        version = metadata.version(LEAN_EXPLORE_PACKAGE)
    except metadata.PackageNotFoundError as exc:
        raise RuntimeError("lean-explore 1.2.1 is not available in the managed closure") from exc
    if version != SUPPORTED_LEAN_EXPLORE_VERSION:
        raise RuntimeError(
            f"unsupported lean-explore version {version!r}; exact 1.2.1 is required"
        )


def serve_adapter(backend: str) -> int:
    """Run the reviewed 1.2.1 FastMCP app without the upstream CLI bridge."""

    global _LEANEXPLORE_API_KEY
    if os.name != "posix":
        print(
            "LeanExplore MCP serve is disabled on native Windows until private-FD credential transport is available.",
            file=sys.stderr,
        )
        return 78
    if _LEANEXPLORE_CAPTURE_ERROR:
        print("LeanExplore credential capture failed.", file=sys.stderr)
        return 78
    try:
        if not _LEANEXPLORE_CLOSURE_FD:
            raise RuntimeError("held CSR LeanExplore closure is required")
        try:
            closure_descriptor = int(_LEANEXPLORE_CLOSURE_FD, 10)
        except ValueError as exc:
            raise RuntimeError("invalid LeanExplore closure descriptor") from exc
        site_packages = verify_lean_explore_closure(
            closure_descriptor,
            _LEANEXPLORE_SITE_RELATIVE,
        )
        sys.path.insert(0, site_packages)
        _require_supported_distribution()
        _consume_private_key_descriptor()
        if _LEANEXPLORE_CAPTURE_ERROR:
            raise RuntimeError("LeanExplore credential capture failed")

        # Importing tools registers the exact 1.2.1 tool surface on mcp_app.
        from lean_explore.mcp import tools as _registered_tools  # noqa: F401
        from lean_explore.mcp.app import mcp_app

        if backend == "api":
            api_key = _LEANEXPLORE_API_KEY
            _LEANEXPLORE_API_KEY = None
            if not api_key:
                raise RuntimeError("LEANEXPLORE_API_KEY is required for the api backend")
            from lean_explore.api import ApiClient

            backend_service = ApiClient(api_key=api_key)
            api_key = None
        else:
            from lean_explore.config import Config

            if not Config.DATABASE_PATH.is_file():
                raise RuntimeError("LeanExplore local database is unavailable")
            from lean_explore.search import SearchEngine, Service

            backend_service = Service(engine=SearchEngine(use_local_data=False))
        mcp_app._lean_explore_backend_service = backend_service
        mcp_app.run(transport="stdio")
        return 0
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        print(f"LeanExplore MCP adapter refused to start: {exc}", file=sys.stderr)
        return 78


def manual_live_use() -> dict[str, Any]:
    return {
        "package": LEAN_EXPLORE_PACKAGE,
        "module": LEAN_EXPLORE_MODULE,
        "package_source": "https://pypi.org/project/lean-explore/",
        "documentation": LEAN_EXPLORE_DOCS,
        "api_keys_url": LEAN_EXPLORE_API_KEYS_URL,
        "local_cache": str(LEAN_EXPLORE_CACHE),
        "local_stdio_commands": {
            "api": local_stdio_command("api"),
            "local": local_stdio_command("local"),
        },
        "mcp_tools": ["search", "search_summary", "get_source_code"],
    }


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
