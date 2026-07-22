#!/usr/bin/env python3
"""opengauss: inert readiness helper for Math Inc. OpenGauss integration.

Offline doctor / config-snippet / smoke only. Never installs OpenGauss, never
starts gauss or backend agents, never reads secret values, never opens network
sockets. Live OpenGauss use is manual-native (Phase 1+) or adapter-gated (later).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from typing import Any


SCHEMA_VERSION = "opengauss.v1"
OPENGAUSS_GITHUB = "https://github.com/math-inc/OpenGauss"
OPENGAUSS_SITE = "https://www.math.inc/opengauss"
MORPH_TEMPLATE = "https://morph.new/opengauss-0-2-2"
PLACEHOLDER_TOKEN = "<GAUSS_BACKEND_TOKEN>"
PLACEHOLDER_API_KEY = "<ANTHROPIC_OR_OPENAI_API_KEY>"
SMOKE_CANARY = "OPENGAUSS-SMOKE-CANARY"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opengauss")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("config-snippet")
    sub.add_parser("smoke")
    sub.add_parser("selftest")
    args = parser.parse_args(argv)

    if args.command == "doctor":
        emit(doctor_payload())
        return 0
    if args.command == "config-snippet":
        emit(config_snippet_payload())
        return 0
    if args.command in {"smoke", "selftest"}:
        emit(smoke_payload())
        return 0
    raise AssertionError(args.command)


def base_payload(status: str = "ok") -> dict[str, Any]:
    return {
        "status": status,
        "schema_version": SCHEMA_VERSION,
        "skill": "opengauss",
        "no_auto_install": True,
        "installs_attempted": False,
        "network_required": False,
        "live_api_attempted": False,
        "config_written": False,
        "server_started": False,
        "package_install_attempted": False,
        "real_secrets_read": False,
        "gauss_launched": False,
    }


def doctor_payload() -> dict[str, Any]:
    plat = platform_status()
    payload = base_payload()
    payload.update(
        {
            "helper_python": python_status(),
            "platform": plat,
            "tool_status": {
                "gauss": tool_status("gauss", env_keys=("AAS_GAUSS", "GAUSS_CLI")),
                "uv": tool_status("uv"),
                "uvx": tool_status("uvx"),
                "rg": tool_status("rg"),
                "lake": tool_status("lake", env_keys=("AAS_LAKE",)),
                "lean": tool_status("lean", env_keys=("AAS_LEAN",)),
                "tmux": tool_status("tmux"),
            },
            "backend_auth_status": {
                "claude": auth_presence(("ANTHROPIC_API_KEY", "CLAUDE_API_KEY")),
                "codex_openai": auth_presence(("OPENAI_API_KEY",)),
                # Presence only; values never emitted.
                "note": "reports present|missing|empty only; never reads ~/.gauss/.env contents",
            },
            "live_execution_support": plat["live_execution_support"],
            "manual_live_use": manual_live_use(),
            "evidence_policy": evidence_policy(),
            "limitations": [
                "doctor is offline and never invokes gauss, lake, lean, or backend CLIs",
                "native Windows live OpenGauss is unsupported; use WSL2 or Morph",
                "missing OpenGauss is not failed theorem evidence",
                "live auto-launch requires a later headless_qualified feasibility spike",
            ],
        }
    )
    return payload


def config_snippet_payload() -> dict[str, Any]:
    payload = base_payload()
    payload.update(
        {
            "redaction_status": "placeholder-only",
            "local_install_snippet": {
                "posix": [
                    f"git clone {OPENGAUSS_GITHUB}.git",
                    "cd OpenGauss",
                    "./scripts/install.sh",
                    "# optional secrets (user-managed; never commit):",
                    f"# echo 'export ANTHROPIC_API_KEY={PLACEHOLDER_API_KEY}' >> ~/.gauss/.env",
                    "gauss doctor  # when installed",
                ],
                "windows_wsl": [
                    "wsl --install -d Ubuntu  # if needed",
                    "wsl",
                    f"git clone {OPENGAUSS_GITHUB}.git ~/OpenGauss",
                    "cd ~/OpenGauss && ./scripts/install.sh",
                ],
                "native_windows": {
                    "status": "unsupported",
                    "message": "Use WSL2 install path or Morph cloud template; do not expect native gauss.exe",
                },
            },
            "project_yaml_example": {
                "path": ".gauss/project.yaml",
                "content_placeholder": {
                    "name": "<project-name>",
                    "root": "<absolute-or-repo-relative-lean-root>",
                    "notes": "Register an existing Lake project; do not invent roots mid-discovery",
                },
            },
            "morph_cloud": {
                "url": MORPH_TEMPLATE,
                "setup": "manual-only",
                "credential": PLACEHOLDER_TOKEN,
            },
            "manual_live_use": manual_live_use(),
            "warnings": [
                "copy snippets manually; this helper never writes config or secrets",
                f"do not replace {PLACEHOLDER_API_KEY} or {PLACEHOLDER_TOKEN} in the repo",
                "OpenGauss workflow success is opengauss_run evidence, never formal_check alone",
            ],
            "snippet_contains_placeholder": True,
        }
    )
    return payload


def smoke_payload() -> dict[str, Any]:
    snippet = config_snippet_payload()
    doctor = doctor_payload()
    serialized = json.dumps(snippet, sort_keys=True)
    payload = base_payload()
    payload.update(
        {
            "smoke_mode": "offline",
            "command": "smoke",
            "ok": True,
            "tool_status": doctor["tool_status"],
            "platform": doctor["platform"],
            "snippet_contains_placeholder": PLACEHOLDER_API_KEY in serialized
            or PLACEHOLDER_TOKEN in serialized,
            "snippet_has_install_pointer": OPENGAUSS_GITHUB in serialized,
            # Policy is always refuse native Windows live Gauss (use WSL/Morph).
            "native_windows_refused": (
                snippet.get("local_install_snippet", {})
                .get("native_windows", {})
                .get("status")
                == "unsupported"
            ),
            "evidence_policy": evidence_policy(),
            "manual_live_use": manual_live_use(),
            "checks": [
                "offline_only",
                "no_auto_install",
                "placeholder_redaction",
                "windows_live_policy",
                "evidence_policy_present",
            ],
        }
    )
    # Ensure canary env cannot leak as a secret value in payload.
    if SMOKE_CANARY in json.dumps(payload, sort_keys=True):
        payload["status"] = "error"
        payload["ok"] = False
        payload["error"] = "smoke canary leaked into payload"
    return payload


def platform_status() -> dict[str, Any]:
    system = platform.system().lower()
    # Presence-only WSL hint on Windows (no execution).
    in_wsl = False
    if system == "linux":
        try:
            with open("/proc/version", encoding="utf-8", errors="ignore") as fh:
                in_wsl = "microsoft" in fh.read().lower()
        except OSError:
            in_wsl = False

    if system == "windows":
        live = "unsupported"
        note = "native Windows live OpenGauss unsupported; use WSL2"
    elif in_wsl:
        live = "wsl_supported_path"
        note = "WSL path is the supported Windows host strategy when AAS+Lean+Gauss share the distro"
    elif system == "darwin":
        live = "experimental"
        note = "macOS live execution is experimental until dated evidence exists"
    elif system == "linux":
        live = "primary"
        note = "linux is the primary live target"
    else:
        live = "unknown"
        note = f"unrecognized platform {system}"

    return {
        "system": system,
        "in_wsl": in_wsl,
        "native_windows_live": "unsupported" if system == "windows" else "n/a",
        "live_execution_support": live,
        "note": note,
    }


def python_status() -> dict[str, Any]:
    return {
        "status": "available",
        "version": ".".join(str(part) for part in sys.version_info[:3]),
        "executable": sys.executable,
    }


def tool_status(name: str, env_keys: tuple[str, ...] = ()) -> dict[str, Any]:
    path = ""
    for key in env_keys:
        candidate = os.environ.get(key, "").strip()
        if candidate and os.path.isfile(candidate):
            path = candidate
            break
    if not path:
        found = shutil.which(name)
        path = found or ""
    return {
        "status": "available" if path else "tool_unavailable",
        "path": path,
        "checked_by": "env_or_shutil.which",
        "executed": False,
    }


def auth_presence(env_keys: tuple[str, ...]) -> str:
    seen_empty = False
    for key in env_keys:
        if key not in os.environ:
            continue
        if os.environ.get(key) == "":
            seen_empty = True
            continue
        return "present"
    if seen_empty:
        return "empty"
    return "missing"


def manual_live_use() -> dict[str, Any]:
    return {
        "product": "OpenGauss",
        "site": OPENGAUSS_SITE,
        "source_repository": OPENGAUSS_GITHUB,
        "morph_template": MORPH_TEMPLATE,
        "install": "manual-native (clone + ./scripts/install.sh; Windows via WSL2)",
        "workflows_mvp": ["/prove", "/draft"],
        "workflows_gated": ["/swarm", "/autoprove", "/autoformalize"],
        "backends": ["claude-code", "codex"],
        "phase": "inert-helper-only; live invoke is Phase 1+",
        "auto_launch": "blocked until headless_qualified spike",
    }


def evidence_policy() -> dict[str, Any]:
    return {
        "opengauss_run": "provenance only; never formal_check or claim promotion by itself",
        "formal_check": "requires lean-strict-verification-gate local scan/typecheck evidence",
        "claim_support": "uses deep-research CLAIM_SUPPORT_STATUSES; lead/human for supports_claim_after_equivalence_review",
        "missing_gauss": "tool_unavailable / defer — not failed theorem evidence",
    }


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
