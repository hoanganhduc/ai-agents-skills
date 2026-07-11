from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .agents import skill_path_is_agent_visible, target_for
from .antigravity import public_check, redacted_cli
from .discovery import current_platform, discover_tool, split_command
from .state import load_state


# Delegation resolves this spec: on hosts where the region-correct ``grok-remote``
# proxy exists it wins (first-found), and elsewhere resolution falls through to a
# bare ``grok``. ``${AAS_GROK}`` / ``%AAS_GROK%`` forces either explicitly.
GROK_CLI_TOOL_SPEC: dict[str, Any] = {
    "candidates": {
        "linux": [
            "${AAS_GROK}",
            "grok-remote",
            "~/grok-proxy/grok-remote",
            "grok",
            "~/.local/bin/grok",
            "~/.grok/bin/grok",
        ],
        "macos": [
            "${AAS_GROK}",
            "grok-remote",
            "~/grok-proxy/grok-remote",
            "grok",
            "~/.local/bin/grok",
            "~/.grok/bin/grok",
            "/opt/homebrew/bin/grok",
            "/usr/local/bin/grok",
        ],
        "wsl": [
            "${AAS_GROK}",
            "grok-remote",
            "~/grok-proxy/grok-remote",
            "grok",
            "~/.local/bin/grok",
            "~/.grok/bin/grok",
        ],
        "windows": [
            "%AAS_GROK%",
            "grok-remote.cmd",
            "grok-remote",
            "%USERPROFILE%\\.grok\\bin\\grok.exe",
            "grok.exe",
            "grok",
        ],
    }
}


# The precheck and native smoke run local read-only subcommands (``grok inspect``)
# that must never bring up the ``grok-remote`` SOCKS tunnel, so they resolve BARE
# ``grok`` only and omit the proxy candidate.
GROK_DIAGNOSTIC_CLI_TOOL_SPEC: dict[str, Any] = {
    "candidates": {
        "linux": [
            "${AAS_GROK}",
            "grok",
            "~/.grok/bin/grok",
            "~/.local/bin/grok",
        ],
        "macos": [
            "${AAS_GROK}",
            "grok",
            "~/.grok/bin/grok",
            "~/.local/bin/grok",
            "/opt/homebrew/bin/grok",
            "/usr/local/bin/grok",
        ],
        "wsl": [
            "${AAS_GROK}",
            "grok",
            "~/.grok/bin/grok",
            "~/.local/bin/grok",
        ],
        "windows": [
            "%AAS_GROK%",
            "%USERPROFILE%\\.grok\\bin\\grok.exe",
            "grok.exe",
            "grok",
        ],
    }
}


def build_grok_precheck(
    root: Path,
    platform: str,
    cli_result: dict[str, Any],
) -> dict[str, Any]:
    target = target_for(root, "grok")
    cli_status = grok_cli_status(cli_result)
    return {
        "target": "grok",
        "status": cli_status,
        "grok_status": cli_status,
        "cli": redacted_cli(cli_result),
        "config_dir": {
            "path": str(target.home),
            "children": {
                "skills": str(target.skills_dir),
                "agents": str(target.home / "agents"),
                "commands": str(target.home / "commands"),
                "rules": str(target.home / "rules"),
                "hooks": str(target.home / "hooks"),
                "config_toml": str(target.home / "config.toml"),
                "agents_md": str(target.instructions_file),
            },
            "file_contents_read": False,
        },
        "surfaces": {
            "global_skill_files": "~/.grok/skills/<skill>/SKILL.md",
            "subagents": "~/.grok/agents/<name>.md",
            "commands": "~/.grok/commands/<name>.md",
            "rules": "~/.grok/rules/<name>.md",
            "autoloop_hook": "~/.grok/hooks/ai-agents-skills-autoloop.json",
            "compat_config": "~/.grok/config.toml",
            "instructions": "~/.grok/AGENTS.md",
        },
        "installation_policy": {
            "global_skill_layout": "directory",
            "native_cli": "grok",
            "hook_sink": "~/.grok/hooks/*.json",
            "settings_json_is_hook_sink": False,
            "real_system_writes": "require existing installer --apply and --real-system gates",
            "official_docs_checked": [
                "05-configuration",
                "08-skills",
                "10-hooks",
                "12-project-rules",
            ],
        },
        "platform": platform,
    }


def grok_cli_status(cli_result: dict[str, Any]) -> str:
    status = cli_result.get("status")
    if status == "ok":
        return "supported"
    if status == "degraded":
        return "offline-unverified"
    return "cli-missing"


def run_grok_native_smoke(
    root: Path,
    *,
    agents: set[str] | None = None,
    platform: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    if agents is not None and "grok" not in agents:
        return {"status": "skipped", "reason": "Grok target not selected"}
    target = target_for(root, "grok")
    state = load_state(root)
    grok_artifacts = [
        item for item in state.get("artifacts", [])
        if item.get("agent") == "grok"
    ]
    if not grok_artifacts:
        return {"status": "skipped", "reason": "no managed Grok artifacts"}
    if not target.home.exists():
        return {"status": "skipped", "reason": "Grok target home is missing"}

    platform_name = current_platform(platform)
    cli = discover_tool("grok-cli", GROK_DIAGNOSTIC_CLI_TOOL_SPEC, platform_name, root)
    file_checks = validate_grok_file_layout(grok_artifacts)
    if cli.get("status") != "ok" or not cli.get("command"):
        status = "degraded" if any(not check["ok"] for check in file_checks) else "skipped"
        return {
            "status": status,
            "reason": "grok CLI is unavailable or not host-executable",
            "cli": redacted_cli(cli),
            "target_home": str(target.home),
            "checked": len(file_checks),
            "checks": file_checks,
        }

    command = split_command(str(cli["command"]))
    env = isolated_grok_env(root)
    inspect_check = run_grok_command("inspect", [*command, "inspect", "--json"], env, timeout)
    expected_skills = sorted({
        str(item.get("skill"))
        for item in grok_artifacts
        if item.get("artifact_type") == "skill-file"
    })
    expected_personas = sorted({
        str(item.get("artifact_name"))
        for item in grok_artifacts
        if item.get("artifact_type") == "agent-persona"
    })
    checks = [
        *file_checks,
        inspect_check,
        *validate_grok_inspect_listing(inspect_check, expected_skills, expected_personas),
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


def isolated_grok_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(root),
            "USERPROFILE": str(root),
            "GROK_HOME": str(root / ".grok"),
            "LOCALAPPDATA": str(root / "AppData" / "Local"),
            "APPDATA": str(root / "AppData" / "Roaming"),
            "XDG_CONFIG_HOME": str(root / ".config"),
            "XDG_DATA_HOME": str(root / ".local" / "share"),
            "XDG_CACHE_HOME": str(root / ".cache"),
            "XDG_STATE_HOME": str(root / ".local" / "state"),
        }
    )
    return env


def validate_grok_file_layout(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in artifacts:
        artifact_type = item.get("artifact_type")
        path = Path(str(item.get("artifact", "")))
        if artifact_type == "skill-file":
            skill = str(item.get("skill"))
            ok = (
                path.exists()
                and path.is_file()
                and skill_path_is_agent_visible("grok", path, skill)
            )
            checks.append({
                "name": f"grok-skill-file:{skill}",
                "ok": ok,
                "status": "ok" if ok else "failed",
            })
        if artifact_type == "agent-persona":
            ok = path.exists() and path.is_file()
            checks.append({
                "name": f"grok-persona-file:{item.get('artifact_name')}",
                "ok": ok,
                "status": "ok" if ok else "failed",
            })
        if artifact_type == "native-hook-file":
            ok = path.exists() and path.is_file() and path.name == "ai-agents-skills-autoloop.json"
            checks.append({
                "name": "grok-autoloop-hook-file",
                "ok": ok,
                "status": "ok" if ok else "failed",
            })
    return checks


def run_grok_command(
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


def validate_grok_inspect_listing(
    check: dict[str, Any],
    expected_skills: list[str],
    expected_personas: list[str],
) -> list[dict[str, Any]]:
    output = str(check.get("stdout", ""))
    results: list[dict[str, Any]] = []
    for skill in expected_skills[:1]:
        ok = bool(check.get("ok")) and skill in output
        results.append({
            "name": f"grok-skill-visible:{skill}",
            "ok": ok,
            "status": "ok" if ok else "failed",
        })
    for persona in expected_personas[:1]:
        ok = bool(check.get("ok")) and persona in output
        results.append({
            "name": f"grok-persona-visible:{persona}",
            "ok": ok,
            "status": "ok" if ok else "failed",
        })
    return results
