#!/usr/bin/env python3
from __future__ import annotations

import os

# Capture and remove the credential at the first Python instruction boundary.
# The managed POSIX wrapper normally supplies it over a private inherited file
# descriptor; the environment form remains supported for direct managed MCP
# clients, but is consumed before discovery or any LeanExplore import.
_LEANEXPLORE_API_KEY = os.environ.pop("LEANEXPLORE_API_KEY", None)
_LEANEXPLORE_KEY_FD = os.environ.pop("AAS_LEANEXPLORE_KEY_FD", "")
_LEANEXPLORE_SITE_FD = os.environ.pop("AAS_LEANEXPLORE_SITE_FD", "")
_LEANEXPLORE_WRAPPER_PATH = os.environ.pop("AAS_LEANEXPLORE_WRAPPER_PATH", "")
_LEANEXPLORE_CAPTURE_ERROR = ""
if _LEANEXPLORE_API_KEY is None and _LEANEXPLORE_KEY_FD:
    try:
        _key_fd = int(_LEANEXPLORE_KEY_FD, 10)
        if _key_fd < 3:
            raise ValueError("credential descriptor must not be a standard stream")
        _key_bytes = bytearray()
        while len(_key_bytes) <= 4097:
            _chunk = os.read(_key_fd, 4098 - len(_key_bytes))
            if not _chunk:
                break
            _key_bytes.extend(_chunk)
        os.close(_key_fd)
        if len(_key_bytes) > 4097:
            raise ValueError("credential exceeds the supported length")
        if _key_bytes.endswith(b"\n"):
            del _key_bytes[-1:]
        if b"\n" in _key_bytes or b"\r" in _key_bytes:
            raise ValueError("credential contains an unsupported line break")
        _LEANEXPLORE_API_KEY = bytes(_key_bytes).decode("utf-8")
        for _index in range(len(_key_bytes)):
            _key_bytes[_index] = 0
    except (OSError, UnicodeError, ValueError) as exc:
        _LEANEXPLORE_CAPTURE_ERROR = str(exc)

import argparse
import importlib.util
import json
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


def _activate_attested_site_packages() -> None:
    if not _LEANEXPLORE_SITE_FD:
        return
    try:
        descriptor = int(_LEANEXPLORE_SITE_FD, 10)
    except ValueError as exc:
        raise RuntimeError("invalid LeanExplore site-packages descriptor") from exc
    if descriptor < 3 or not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        raise RuntimeError("invalid LeanExplore site-packages descriptor")
    for prefix in ("/proc/self/fd", "/dev/fd"):
        candidate = f"{prefix}/{descriptor}"
        if os.path.isdir(candidate):
            sys.path.insert(0, candidate)
            return
    raise RuntimeError("this POSIX host cannot bind LeanExplore site-packages")


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
        _activate_attested_site_packages()
        _require_supported_distribution()

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
