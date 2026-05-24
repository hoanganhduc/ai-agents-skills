from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .capabilities import existing_parents, normalized_path_within, resolved_path_within
from .copilot import COPILOT_CLI_TOOL_SPEC
from .discovery import discover_tool, render_command, split_command


PROVIDER_CLI_SPECS: dict[str, dict[str, Any]] = {
    "claude": {
        "candidates": {
            "linux": ["${AAS_CLAUDE}", "claude", "~/.local/bin/claude", "~/.claude/local/claude"],
            "macos": ["${AAS_CLAUDE}", "claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"],
            "wsl": ["${AAS_CLAUDE}", "claude", "~/.local/bin/claude", "~/.claude/local/claude"],
            "windows": ["%AAS_CLAUDE%", "claude.cmd", "claude.exe", "claude"],
        }
    },
    "deepseek": {
        "candidates": {
            "linux": [
                "${AAS_DEEPSEEK}",
                "codewhale",
                "codewhale-tui",
                "deepseek",
                "deepseek-cli",
                "~/.local/bin/codewhale",
                "~/.local/bin/codewhale-tui",
                "~/.local/bin/deepseek",
            ],
            "macos": [
                "${AAS_DEEPSEEK}",
                "codewhale",
                "codewhale-tui",
                "deepseek",
                "deepseek-cli",
                "/opt/homebrew/bin/codewhale",
                "/opt/homebrew/bin/codewhale-tui",
                "/opt/homebrew/bin/deepseek",
            ],
            "wsl": [
                "${AAS_DEEPSEEK}",
                "codewhale",
                "codewhale-tui",
                "deepseek",
                "deepseek-cli",
                "~/.local/bin/codewhale",
                "~/.local/bin/codewhale-tui",
                "~/.local/bin/deepseek",
            ],
            "windows": [
                "%AAS_DEEPSEEK%",
                "%APPDATA%\\npm\\codewhale.cmd",
                "%APPDATA%\\npm\\codewhale-tui.cmd",
                "codewhale.cmd",
                "codewhale-tui.cmd",
                "codewhale.exe",
                "codewhale",
                "deepseek.cmd",
                "deepseek.exe",
                "deepseek",
            ],
        }
    },
    "copilot": COPILOT_CLI_TOOL_SPEC,
}

PROVIDER_AUTH_ENV_NAMES: dict[str, tuple[str, ...]] = {
    "claude": ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "copilot": ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
}

PROVIDER_CONFIG_PATHS: dict[str, tuple[str, ...]] = {
    "codex": (".codex",),
    "claude": (".claude",),
    "deepseek": (".deepseek",),
    "copilot": (".copilot",),
}


def build_external_agent_prechecks(
    root: Path,
    platform: str,
    delegation: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = env if env is not None else dict(os.environ)
    policy = delegation["policy"]
    nested = delegation["nested_delegation"]
    provider_specs = delegation["providers"]
    providers = [
        build_provider_precheck(root, platform, provider, provider_specs[provider], policy, nested, env)
        for provider in [*policy["active_providers"], *policy["reference_only_providers"]]
    ]
    active = [provider for provider in providers if provider["configured_status"] == "active"]
    candidates = [
        provider["provider"]
        for provider in active
        if provider["research_model_policy"]["status"] == "runtime-probe-required"
    ]
    if policy["mode"] == "off":
        status = "disabled"
    elif len(candidates) >= policy["min_distinct_providers"]:
        status = "runtime-probes-required"
    elif policy.get("fallback_to_codex_only", False):
        status = "codex-fallback-allowed"
    else:
        status = "insufficient-providers"
    return {
        "schema_version": "external-agent-precheck.v1",
        "policy": {
            "mode": policy["mode"],
            "min_distinct_providers": policy["min_distinct_providers"],
            "active_providers": list(policy["active_providers"]),
            "reference_only_providers": list(policy["reference_only_providers"]),
            "fallback_to_codex_only": bool(policy["fallback_to_codex_only"]),
            "require_parent_confirmation_for_external_cli": bool(policy["require_parent_confirmation_for_external_cli"]),
            "stale_profile_after_hours": policy["stale_profile_after_hours"],
            "template_policy": policy["template_policy"],
            "research_model_policy": policy["research_model_policy"],
        },
        "nested_delegation": {
            "enabled": bool(nested["enabled"]),
            "max_depth": nested["max_depth"],
            "max_child_workers_per_manager": nested["max_child_workers_per_manager"],
            "require_same_model_as_manager": bool(nested["require_same_model_as_manager"]),
        },
        "summary": {
            "status": status,
            "candidate_provider_count": len(candidates),
            "candidate_providers": candidates,
            "required_runtime_probes": [
                "smoke_prompt",
                "output_contract",
                "final_marker",
                "timeout_behavior",
                "latest_model",
                "highest_thinking",
                "file_read_fidelity_when_needed",
                "nested_subagent_same_model_when_needed",
            ],
        },
        "providers": providers,
    }


def build_provider_precheck(
    root: Path,
    platform: str,
    provider: str,
    spec: dict[str, Any],
    policy: dict[str, Any],
    nested: dict[str, Any],
    env: dict[str, str],
) -> dict[str, Any]:
    configured_status = spec["status"]
    if provider == "codex":
        return build_codex_provider_precheck(root, spec, policy, nested)
    if configured_status == "reference_only":
        return {
            "provider": provider,
            "configured_status": configured_status,
            "recipient_profile": spec["recipient_profile"],
            "default_role_family": spec["default_role_family"],
            "status": "reference-only",
            "cli": {"logical_name": f"{provider}-cli", "status": "not-probed"},
            "config": {"status": "not-probed", "paths": []},
            "auth": {"status": "not-probed", "credential_sources": [], "secret_values_read": False},
            "research_model_policy": {
                "status": "blocked",
                "policy": policy["research_model_policy"],
                "reason": "provider is reference-only",
            },
            "nested_delegation": {
                "status": "blocked",
                "reason": "provider is reference-only",
            },
            "read_policy": {"file_contents_read": False, "secret_values_read": False},
        }

    cli = discover_tool(f"{provider}-cli", PROVIDER_CLI_SPECS[provider], platform, root)
    cli_status = cli.get("status", "missing")
    if cli_status == "missing":
        status = "missing"
        research_status = "blocked"
        nested_status = "blocked"
        reason = "cli missing"
    elif cli_status == "degraded":
        status = "degraded-runtime-probe-required"
        research_status = "runtime-probe-required"
        nested_status = "runtime-probe-required" if nested["enabled"] else "disabled"
        reason = "cli present but degraded; live model and transport probes required"
    else:
        status = "runtime-probe-required"
        research_status = "runtime-probe-required"
        nested_status = "runtime-probe-required" if nested["enabled"] else "disabled"
        reason = "live model, thinking, transport, and output-contract probes required"
    return {
        "provider": provider,
        "configured_status": configured_status,
        "recipient_profile": spec["recipient_profile"],
        "default_role_family": spec["default_role_family"],
        "status": status,
        "cli": redacted_cli_result(cli),
        "config": config_summary(root, provider),
        "auth": auth_summary(provider, env),
        "research_model_policy": {
            "status": research_status,
            "policy": policy["research_model_policy"],
            "latest_model": "runtime-probe-required" if research_status == "runtime-probe-required" else "unavailable",
            "highest_thinking": "runtime-probe-required" if research_status == "runtime-probe-required" else "unavailable",
            "reason": reason,
        },
        "nested_delegation": {
            "status": nested_status,
            "max_depth": nested["max_depth"] if nested_status == "runtime-probe-required" else 0,
            "max_child_workers_per_manager": (
                nested["max_child_workers_per_manager"] if nested_status == "runtime-probe-required" else 0
            ),
            "require_same_model_as_manager": bool(nested["require_same_model_as_manager"]),
            "reason": (
                "same-provider same-model nested worker probe required"
                if nested_status == "runtime-probe-required"
                else reason
            ),
        },
        "read_policy": {"file_contents_read": False, "secret_values_read": False},
    }


def build_codex_provider_precheck(
    root: Path,
    spec: dict[str, Any],
    policy: dict[str, Any],
    nested: dict[str, Any],
) -> dict[str, Any]:
    return {
        "provider": "codex",
        "configured_status": spec["status"],
        "recipient_profile": spec["recipient_profile"],
        "default_role_family": spec["default_role_family"],
        "status": "parent-runtime-probe-required",
        "cli": {"logical_name": "codex-runtime", "status": "parent-runtime"},
        "config": config_summary(root, "codex"),
        "auth": {"status": "not-needed", "credential_sources": [], "secret_values_read": False},
        "research_model_policy": {
            "status": "runtime-probe-required",
            "policy": policy["research_model_policy"],
            "latest_model": "active-runtime-model-list-required",
            "highest_thinking": "active-runtime-reasoning-list-required",
            "reason": "AGD must inspect the active Codex runtime model list before spawning",
        },
        "nested_delegation": {
            "status": "runtime-probe-required" if nested["enabled"] else "disabled",
            "max_depth": nested["max_depth"] if nested["enabled"] else 0,
            "max_child_workers_per_manager": nested["max_child_workers_per_manager"] if nested["enabled"] else 0,
            "require_same_model_as_manager": bool(nested["require_same_model_as_manager"]),
            "reason": "spawn_agent child workers must inherit or explicitly use the manager's resolved model",
        },
        "read_policy": {"file_contents_read": False, "secret_values_read": False},
    }


def redacted_cli_result(cli: dict[str, Any]) -> dict[str, Any]:
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
    redacted = {key: value for key, value in cli.items() if key in allowed}
    if "command" in redacted:
        redacted["command"] = redacted_command(redacted["command"])
    if "version" in redacted:
        redacted["version"] = "output-redacted"
    if "checked" in redacted:
        redacted["checked"] = [redacted_checked_item(item) for item in redacted["checked"]]
    return redacted


def redacted_checked_item(item: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(item)
    if "command" in redacted:
        redacted["command"] = redacted_command(redacted["command"])
    if "version" in redacted:
        redacted["version"] = "output-redacted"
    return redacted


def redacted_command(command: Any) -> str:
    parts = split_command(str(command))
    if not parts:
        return "<empty-command>"
    executable = Path(parts[0]).name or parts[0]
    if len(parts) == 1:
        return executable
    return f"{render_command(executable, [])} <args-redacted>"


def auth_summary(provider: str, env: dict[str, str]) -> dict[str, Any]:
    sources = [
        {"kind": "env", "name": name, "present": bool(env.get(name))}
        for name in PROVIDER_AUTH_ENV_NAMES.get(provider, ())
    ]
    return {
        "status": "env-present" if any(source["present"] for source in sources) else "not-detected",
        "credential_sources": sources,
        "secret_values_read": False,
    }


def config_summary(root: Path, provider: str) -> dict[str, Any]:
    paths = [
        provider_path_status(root, root / relative)
        for relative in PROVIDER_CONFIG_PATHS.get(provider, ())
    ]
    status = "missing"
    if any(path["status"] == "blocked" for path in paths):
        status = "blocked"
    elif any(path["status"] in {"directory", "file"} for path in paths):
        status = "present"
    return {
        "status": status,
        "paths": paths,
        "file_contents_read": False,
    }


def provider_path_status(root: Path, path: Path) -> dict[str, Any]:
    if not normalized_path_within(root, path) or not resolved_path_within(root, path.parent):
        return {"path": str(path), "status": "blocked", "reason": "path resolves outside selected root"}
    for parent in existing_parents(path.parent, root):
        if parent.is_symlink():
            return {"path": str(path), "status": "blocked", "reason": f"path has symlinked parent: {parent}"}
        if not parent.is_dir():
            return {"path": str(path), "status": "blocked", "reason": f"path has non-directory parent: {parent}"}
    if path.is_symlink():
        return {"path": str(path), "status": "blocked", "reason": "path is a symlink"}
    if not path.exists():
        return {"path": str(path), "status": "missing"}
    if path.is_dir():
        return {"path": str(path), "status": "directory"}
    if path.is_file():
        return {"path": str(path), "status": "file"}
    return {"path": str(path), "status": "blocked", "reason": "path is neither regular file nor directory"}
