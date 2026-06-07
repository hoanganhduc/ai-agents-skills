from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .agents import skill_path_is_agent_visible, target_for
from .discovery import current_platform, discover_tool, render_command, split_command
from .state import load_state


ANTIGRAVITY_CLI_TOOL_SPEC: dict[str, Any] = {
    "candidates": {
        "linux": [
            "${AAS_ANTIGRAVITY}",
            "agy",
            "~/.local/bin/agy",
            "~/.npm-global/bin/agy",
        ],
        "macos": [
            "${AAS_ANTIGRAVITY}",
            "agy",
            "~/.local/bin/agy",
            "/opt/homebrew/bin/agy",
            "/usr/local/bin/agy",
        ],
        "wsl": [
            "${AAS_ANTIGRAVITY}",
            "agy",
            "~/.local/bin/agy",
            "~/.npm-global/bin/agy",
        ],
        "windows": [
            "%AAS_ANTIGRAVITY%",
            "%LOCALAPPDATA%\\agy\\bin\\agy.cmd",
            "%LOCALAPPDATA%\\agy\\bin\\agy.exe",
            "agy.cmd",
            "agy.exe",
            "agy",
        ],
    }
}


def build_antigravity_precheck(
    root: Path,
    platform: str,
    cli_result: dict[str, Any],
) -> dict[str, Any]:
    target = target_for(root, "antigravity")
    cli_status = antigravity_cli_status(cli_result)
    return {
        "target": "antigravity",
        "status": cli_status,
        "antigravity_status": cli_status,
        "cli": redacted_cli(cli_result),
        "config_dir": {
            "path": str(target.home),
            "children": {
                "global_skills": str(target.skills_dir),
                "plugins": str(target.home / "plugins"),
                "managed_plugin": str(target.home / "plugins" / "ai-agents-skills"),
                "settings_json": str(target.home / "settings.json"),
                "global_mcp_config": str(root / ".gemini" / "config" / "mcp_config.json"),
                "global_context": str(root / ".gemini" / "GEMINI.md"),
            },
            "file_contents_read": False,
        },
        "surfaces": {
            "global_skill_files": "~/.gemini/antigravity-cli/skills/<skill>.md",
            "plugin_manifest": "~/.gemini/antigravity-cli/plugins/ai-agents-skills/plugin.json",
            "plugin_skills": "~/.gemini/antigravity-cli/plugins/ai-agents-skills/skills/",
            "plugin_agents": "~/.gemini/antigravity-cli/plugins/ai-agents-skills/agents/",
            "plugin_rules": "~/.gemini/antigravity-cli/plugins/ai-agents-skills/rules/",
            "plugin_hooks": "~/.gemini/antigravity-cli/plugins/ai-agents-skills/hooks.json",
            "plugin_mcp": "~/.gemini/antigravity-cli/plugins/ai-agents-skills/mcp_config.json",
            "workspace_skills": ".agents/skills/",
            "workspace_mcp": ".agents/mcp_config.json",
        },
        "installation_policy": {
            "global_skill_layout": "flat-markdown",
            "native_cli": "agy",
            "real_system_writes": "require existing installer --apply and --real-system gates",
            "official_docs_checked": [
                "cli-getting-started",
                "cli-plugins",
                "cli-settings",
                "gcli-migration",
            ],
        },
        "platform": platform,
    }


def antigravity_cli_status(cli_result: dict[str, Any]) -> str:
    status = cli_result.get("status")
    if status == "ok":
        return "supported"
    if status == "degraded":
        return "offline-unverified"
    return "cli-missing"


def run_antigravity_native_smoke(
    root: Path,
    *,
    agents: set[str] | None = None,
    platform: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    if agents is not None and "antigravity" not in agents:
        return {"status": "skipped", "reason": "Antigravity target not selected"}
    target = target_for(root, "antigravity")
    state = load_state(root)
    antigravity_artifacts = [
        item for item in state.get("artifacts", [])
        if item.get("agent") == "antigravity"
    ]
    if not antigravity_artifacts:
        return {"status": "skipped", "reason": "no managed Antigravity artifacts"}
    if not target.home.exists():
        return {"status": "skipped", "reason": "Antigravity target home is missing"}

    platform_name = current_platform(platform)
    cli = discover_tool("antigravity-cli", ANTIGRAVITY_CLI_TOOL_SPEC, platform_name, root)
    file_checks = validate_antigravity_file_layout(antigravity_artifacts)
    if cli.get("status") != "ok" or not cli.get("command"):
        status = "degraded" if any(not check["ok"] for check in file_checks) else "skipped"
        return {
            "status": status,
            "reason": "agy CLI is unavailable or not host-executable",
            "cli": redacted_cli(cli),
            "target_home": str(target.home),
            "checked": len(file_checks),
            "checks": file_checks,
        }

    command = split_command(str(cli["command"]))
    env = isolated_antigravity_env(root)
    command_checks = [
        run_antigravity_command("help", [*command, "--help"], env, timeout),
        run_antigravity_command("plugin-list", [*command, "plugin", "list"], env, timeout),
    ]
    expected_plugin = any(item.get("artifact_type") == "plugin" for item in antigravity_artifacts)
    checks = [
        *file_checks,
        *command_checks,
        *validate_plugin_listing(command_checks[1], expected_plugin),
    ]
    checks = [public_check(check) for check in checks]
    status = "ok" if all(check["ok"] for check in checks) else "degraded"
    return {
        "status": status,
        "cli": redacted_cli(cli),
        "target_home": str(target.home),
        "checked": len(checks),
        "checks": checks,
    }


def isolated_antigravity_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(root),
            "USERPROFILE": str(root),
            "LOCALAPPDATA": str(root / "AppData" / "Local"),
            "APPDATA": str(root / "AppData" / "Roaming"),
            "XDG_CONFIG_HOME": str(root / ".config"),
            "XDG_DATA_HOME": str(root / ".local" / "share"),
            "XDG_CACHE_HOME": str(root / ".cache"),
            "XDG_STATE_HOME": str(root / ".local" / "state"),
        }
    )
    return env


def validate_antigravity_file_layout(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in artifacts:
        artifact_type = item.get("artifact_type")
        path = Path(str(item.get("artifact", "")))
        if artifact_type == "skill-file":
            skill = str(item.get("skill"))
            ok = (
                path.exists()
                and path.is_file()
                and skill_path_is_agent_visible("antigravity", path, skill)
            )
            checks.append({
                "name": f"antigravity-global-skill-file:{skill}",
                "ok": ok,
                "status": "ok" if ok else "failed",
            })
        if artifact_type == "plugin":
            ok = path.exists() and path.is_file() and path.name == "plugin.json"
            checks.append({
                "name": "antigravity-plugin-manifest",
                "ok": ok,
                "status": "ok" if ok else "failed",
            })
        if artifact_type in {"mcp-config", "hook-config", "settings-file"}:
            ok = path.exists() and path.is_file()
            checks.append({
                "name": f"antigravity-{artifact_type}",
                "ok": ok,
                "status": "ok" if ok else "failed",
            })
    return checks


def run_antigravity_command(
    name: str,
    command: list[str],
    env: dict[str, str],
    timeout: int,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env=env,
        )
    except Exception as exc:
        return {"name": name, "ok": False, "status": "error", "error": str(exc)}
    stdout = result.stdout.strip()
    return {
        "name": name,
        "ok": result.returncode == 0,
        "status": "ok" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "stdout_preview": "<output-redacted>" if stdout else "",
        "stdout": stdout,
        "stderr_preview": result.stderr.strip()[:500],
    }


def validate_plugin_listing(check: dict[str, Any], expected_plugin: bool) -> list[dict[str, Any]]:
    if not expected_plugin:
        return []
    output = str(check.get("stdout", ""))
    ok = bool(check.get("ok")) and "ai-agents-skills" in output
    return [
        {
            "name": "antigravity-plugin-visible:ai-agents-skills",
            "ok": ok,
            "status": "ok" if ok else "failed",
        }
    ]


def redacted_cli(cli: dict[str, Any]) -> dict[str, Any]:
    allowed = {"logical_name", "status", "command", "version", "scope", "substrate", "reason"}
    result = {key: value for key, value in cli.items() if key in allowed}
    if "command" in result:
        result["command"] = redacted_command(result["command"])
    if "version" in result:
        result["version"] = "output-redacted"
    return result


def redacted_command(command: Any) -> str:
    parts = split_command(str(command))
    if not parts:
        return "<empty-command>"
    if len(parts) == 1:
        return render_command(parts[0], [])
    return f"{render_command(parts[0], [])} <args-redacted>"


def public_check(check: dict[str, Any]) -> dict[str, Any]:
    result = dict(check)
    result.pop("stdout", None)
    return result
