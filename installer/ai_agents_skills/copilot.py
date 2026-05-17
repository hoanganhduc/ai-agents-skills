from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

from .capabilities import normalized_path_within, resolved_path_within


COPILOT_CLI_TOOL_SPEC: dict[str, Any] = {
    "candidates": {
        "linux": [
            "${AAS_COPILOT}",
            "copilot",
            "~/.local/bin/copilot",
            "~/.npm-global/bin/copilot",
        ],
        "macos": [
            "${AAS_COPILOT}",
            "copilot",
            "/opt/homebrew/bin/copilot",
            "/usr/local/bin/copilot",
        ],
        "wsl": [
            "${AAS_COPILOT}",
            "copilot",
            "~/.local/bin/copilot",
            "~/.npm-global/bin/copilot",
        ],
        "windows": [
            "%AAS_COPILOT%",
            "%APPDATA%\\npm\\copilot.cmd",
            "%LOCALAPPDATA%\\Programs\\GitHub Copilot\\copilot.exe",
            "copilot.cmd",
            "copilot.exe",
            "copilot",
        ],
    }
}

COPILOT_PRECHECK_STATUSES = {
    "supported",
    "unsupported-model",
    "model-unavailable-for-account",
    "provider-unavailable",
    "unknown-entitlement",
    "offline-unverified",
    "cli-missing",
    "probe-disabled",
    "partial-auth",
    "rate-limited",
    "probe-timeout",
}
COPILOT_STATUS_PRIORITY = [
    "cli-missing",
    "unsupported-model",
    "model-unavailable-for-account",
    "provider-unavailable",
    "partial-auth",
    "rate-limited",
    "probe-timeout",
    "offline-unverified",
    "unknown-entitlement",
    "probe-disabled",
    "supported",
]
COPILOT_AUTH_ENV_NAMES = (
    "COPILOT_GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
)


def reduce_copilot_status(*statuses: str | None) -> str:
    normalized = []
    for status in statuses:
        if status in {None, "", "ok"}:
            normalized.append("supported")
        elif status in COPILOT_PRECHECK_STATUSES:
            normalized.append(str(status))
        else:
            normalized.append("unknown-entitlement")
    if not normalized:
        return "probe-disabled"
    status_set = set(normalized)
    for status in COPILOT_STATUS_PRIORITY:
        if status in status_set:
            return status
    return "unknown-entitlement"


def build_copilot_precheck(
    root: Path,
    platform: str,
    cli_result: dict[str, Any],
    *,
    account_status: str = "probe-disabled",
    provider_status: str = "probe-disabled",
    model_status: str = "probe-disabled",
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = env if env is not None else dict(os.environ)
    cli_status = _copilot_cli_status(cli_result)
    status = reduce_copilot_status(cli_status, account_status, provider_status, model_status)
    return {
        "target": "copilot",
        "status": status,
        "cli": _redacted_cli_result(cli_result),
        "config_dir": summarize_copilot_home(root),
        "surfaces": copilot_surfaces(root),
        "auth": {
            "status": account_status,
            "credential_sources": credential_sources(env),
            "secret_values_read": False,
        },
        "provider_registry": {
            "status": provider_status,
            "providers": [],
            "source": "account-specific provider/model listing is not probed by this installer",
            "update_policy": (
                "rerun precheck after installing or authenticating Copilot CLI; "
                "do not hardcode account entitlements"
            ),
        },
        "models": {
            "status": model_status,
            "selected_model_env_present": "COPILOT_MODEL" in env,
            "source": "not-probed",
        },
        "delegation_policy": {
            "instruction_precedence": [
                "delegation-packet-authority",
                "repository-copilot-surfaces",
                "personal-copilot-surfaces",
            ],
            "authority_envelope_fields": [
                "allowed_read_roots",
                "allowed_write_roots",
                "tool_policy",
                "network_policy",
                "secret_classes",
                "output_limits",
                "approvals",
                "instruction_precedence",
                "redaction_policy",
                "authority_hash",
            ],
            "redaction_required_before_persistence": True,
            "hostile_output_handling": "strict-schema-output-or-quarantine",
        },
        "status_reduction": {
            "inputs": {
                "cli": cli_status,
                "account": account_status,
                "provider": provider_status,
                "model": model_status,
            },
            "priority": COPILOT_STATUS_PRIORITY,
        },
        "platform": platform,
    }


def _copilot_cli_status(cli_result: dict[str, Any]) -> str:
    status = cli_result.get("status")
    if status == "ok":
        return "supported"
    if status == "degraded":
        return "offline-unverified"
    return "cli-missing"


def _redacted_cli_result(cli_result: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "logical_name",
        "status",
        "command",
        "version",
        "scope",
        "substrate",
        "capabilities",
        "checked",
        "reason",
    }
    return {key: value for key, value in cli_result.items() if key in allowed}


def summarize_copilot_home(root: Path) -> dict[str, Any]:
    home = root / ".copilot"
    status = path_status(root, home)
    children = {
        "skills": path_status(root, home / "skills"),
        "agents": path_status(root, home / "agents"),
        "settings_json": path_status(root, home / "settings.json"),
        "legacy_config_json": path_status(root, home / "config.json"),
        "mcp_config_json": path_status(root, home / "mcp-config.json"),
    }
    return {
        "path": str(home),
        "status": status["status"],
        "reason": status.get("reason"),
        "children": children,
        "file_contents_read": False,
    }


def path_status(root: Path, path: Path) -> dict[str, Any]:
    if not normalized_path_within(root, path) or not resolved_path_within(root, path.parent):
        return {"path": str(path), "status": "blocked", "reason": "path resolves outside selected root"}
    if path.is_symlink():
        return {"path": str(path), "status": "blocked", "reason": "path is a symlink"}
    if not path.exists():
        return {"path": str(path), "status": "missing"}
    if path.is_dir():
        return {"path": str(path), "status": "directory"}
    if path.is_file():
        return {"path": str(path), "status": "file"}
    return {"path": str(path), "status": "blocked", "reason": "path is neither regular file nor directory"}


def credential_sources(env: dict[str, str]) -> list[dict[str, Any]]:
    sources = []
    for name in COPILOT_AUTH_ENV_NAMES:
        sources.append({"kind": "env", "name": name, "present": bool(env.get(name))})
    sources.append({"kind": "github-cli", "name": "gh-auth-fallback", "present": "not-probed"})
    return sources


def copilot_surfaces(root: Path) -> list[dict[str, Any]]:
    return [
        _surface("personal-skill", root / ".copilot" / "skills" / "<skill>" / "SKILL.md", True, "user"),
        _surface("personal-agent", root / ".copilot" / "agents" / "*.agent.md", True, "user"),
        _surface("repository-skill", root / ".github" / "skills" / "<skill>" / "SKILL.md", False, "repository"),
        _surface("repository-agent", root / ".github" / "agents" / "*.agent.md", False, "repository"),
        _surface("repository-instructions", root / ".github" / "copilot-instructions.md", False, "repository"),
        _surface("path-instructions", root / ".github" / "instructions" / "**" / "*.instructions.md", False, "repository"),
    ]


def _surface(name: str, path: Path, writable: bool, scope: str) -> dict[str, Any]:
    return {
        "name": name,
        "path": str(path),
        "scope": scope,
        "installer_writes": writable,
    }


def validate_copilot_statuses(statuses: Iterable[str]) -> list[str]:
    return sorted({status for status in statuses if status not in COPILOT_PRECHECK_STATUSES})
