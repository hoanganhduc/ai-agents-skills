#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


LEAN_EXPLORE_PACKAGE = "lean-explore"
LEAN_EXPLORE_MODULE = "lean_explore"
LEAN_EXPLORE_COMMAND = "lean-explore"
LEAN_EXPLORE_DOCS = "https://www.leanexplore.com/docs/mcp"
LEAN_EXPLORE_API_KEYS_URL = "https://www.leanexplore.com/api-keys"
LEAN_EXPLORE_CACHE = Path.home() / ".lean_explore" / "cache"
BACKENDS = {"api", "local"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lean-explore-mcp")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    config = sub.add_parser("config-snippet")
    config.add_argument("--backend", choices=sorted(BACKENDS), default="api")
    sub.add_parser("smoke")
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
    if "LEANEXPLORE_API_KEY" not in os.environ:
        return "missing"
    if os.environ.get("LEANEXPLORE_API_KEY") == "":
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
    command: dict[str, Any] = {
        "command": LEAN_EXPLORE_COMMAND,
        "args": ["mcp", "serve", "--backend", backend],
    }
    if backend == "api":
        command["env"] = {"LEANEXPLORE_API_KEY": "<LEANEXPLORE_API_KEY>"}
    return command


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
        "mcp_tools": ["search", "get_by_id"],
    }


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
