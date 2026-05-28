#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from typing import Any


AXLE_PACKAGE = "axiom-axle-mcp"
AXLE_PACKAGE_VERSION = "0.3.3"
AXLE_PACKAGE_SPEC = f"{AXLE_PACKAGE}=={AXLE_PACKAGE_VERSION}"
AXLE_SERVER_COMMAND = "axle-mcp-server"
HOSTED_MCP_URL = "https://mcp.axiommath.ai/mcp"
LOCAL_COMMAND = {
    "command": "uvx",
    "args": ["--from", AXLE_PACKAGE_SPEC, AXLE_SERVER_COMMAND],
    "env": {"AXLE_API_KEY": "<AXLE_API_KEY>"},
}
PYPI_SOURCE = "https://pypi.org/project/axiom-axle-mcp/"
GITHUB_SOURCE = "https://github.com/AxiomMath/axle-mcp-server"
WHEEL_SHA256 = "0474ac6ebc6c4d78ba925f64694d4c642fa36e5a4963465dc29eb9780cb0336f"
SDIST_SHA256 = "132c7924f2746a09a039bdeb8ada6ccd8275567441de638a8225b280e1455881"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="axiom-axle-mcp")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("config-snippet")
    sub.add_parser("smoke")
    args = parser.parse_args(argv)

    if args.command == "doctor":
        emit(doctor_payload())
        return 0
    if args.command == "config-snippet":
        emit(config_snippet_payload())
        return 0
    if args.command == "smoke":
        emit(smoke_payload())
        return 0
    raise AssertionError(args.command)


def base_payload(status: str = "ok") -> dict[str, Any]:
    return {
        "status": status,
        "schema_version": "axiom-axle-mcp.v1",
        "no_auto_install": True,
        "installs_attempted": False,
        "network_required": False,
        "live_api_attempted": False,
        "config_written": False,
        "server_started": False,
    }


def doctor_payload() -> dict[str, Any]:
    payload = base_payload()
    payload.update({
        "helper_python": python_status(),
        "live_server_python_requirement": ">=3.11",
        "live_server_python_requirement_satisfied": sys.version_info >= (3, 11),
        "tool_status": {
            "uvx": tool_status("uvx"),
        },
        "auth_status": auth_status(),
        "manual_live_use": manual_live_use(),
        "limitations": [
            "doctor is offline and never invokes uvx or the AXLE MCP server",
            "AXLE API key presence is reported without exposing the value",
            "live AXLE use is manual and outside installer/runtime smoke",
        ],
    })
    return payload


def config_snippet_payload() -> dict[str, Any]:
    payload = base_payload()
    payload.update({
        "redaction_status": "placeholder-only",
        "local_stdio_mcp_config": {
            "mcpServers": {
                "axle": LOCAL_COMMAND,
            },
        },
        "hosted_remote_mcp": {
            "url": HOSTED_MCP_URL,
            "setup": "manual-only",
            "credential": "<AXLE_API_KEY>",
        },
        "manual_live_use": manual_live_use(),
        "warnings": [
            "copy snippets manually into an MCP client config only after reviewing the target client",
            "do not replace <AXLE_API_KEY> in this repo or in generated artifacts",
        ],
    })
    return payload


def smoke_payload() -> dict[str, Any]:
    snippet = config_snippet_payload()
    payload = base_payload()
    payload.update({
        "smoke_mode": "offline",
        "auth_status": "not_inspected",
        "tool_status": {
            "uvx": tool_status("uvx"),
        },
        "expected_command": LOCAL_COMMAND,
        "snippet_contains_placeholder": "<AXLE_API_KEY>" in json.dumps(snippet, sort_keys=True),
        "snippet_package_pinned": AXLE_PACKAGE_SPEC in json.dumps(snippet, sort_keys=True),
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


def auth_status() -> str:
    if "AXLE_API_KEY" not in os.environ:
        return "missing"
    if os.environ.get("AXLE_API_KEY") == "":
        return "empty"
    return "present"


def manual_live_use() -> dict[str, Any]:
    return {
        "package": AXLE_PACKAGE_SPEC,
        "package_source": PYPI_SOURCE,
        "source_repository": GITHUB_SOURCE,
        "release_observed": "2026-05-07",
        "python_requirement": ">=3.11",
        "wheel_sha256": WHEEL_SHA256,
        "sdist_sha256": SDIST_SHA256,
        "local_stdio_command": LOCAL_COMMAND,
        "hosted_remote_mcp_url": HOSTED_MCP_URL,
    }


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
