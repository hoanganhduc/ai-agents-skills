from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .agents import target_for
from .discovery import current_platform, discover_tool, split_command
from .state import load_state


OPENCODE_CLI_TOOL_SPEC: dict[str, Any] = {
    "candidates": {
        "linux": [
            "${AAS_OPENCODE}",
            "opencode",
            "~/.local/bin/opencode",
            "~/.npm-global/bin/opencode",
        ],
        "macos": [
            "${AAS_OPENCODE}",
            "opencode",
            "/opt/homebrew/bin/opencode",
            "/usr/local/bin/opencode",
        ],
        "wsl": [
            "${AAS_OPENCODE}",
            "opencode",
            "~/.local/bin/opencode",
            "~/.npm-global/bin/opencode",
        ],
        "windows": [
            "%AAS_OPENCODE%",
            "%APPDATA%\\npm\\opencode.cmd",
            "%LOCALAPPDATA%\\Programs\\opencode\\opencode.exe",
            "opencode.cmd",
            "opencode.exe",
            "opencode",
        ],
    }
}


def run_opencode_native_smoke(
    root: Path,
    *,
    agents: set[str] | None = None,
    platform: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    if agents is not None and "opencode" not in agents:
        return {"status": "skipped", "reason": "OpenCode target not selected"}
    target = target_for(root, "opencode")
    state = load_state(root)
    opencode_artifacts = [
        item for item in state.get("artifacts", [])
        if item.get("agent") == "opencode"
    ]
    if not opencode_artifacts:
        return {"status": "skipped", "reason": "no managed OpenCode artifacts"}
    if not target.home.exists():
        return {"status": "skipped", "reason": "OpenCode target home is missing"}

    platform_name = current_platform(platform)
    cli = discover_tool("opencode-cli", OPENCODE_CLI_TOOL_SPEC, platform_name, root)
    if cli.get("status") != "ok" or not cli.get("command"):
        return {
            "status": "skipped",
            "reason": "opencode CLI is unavailable or not host-executable",
            "cli": redacted_cli(cli),
        }

    command = split_command(str(cli["command"]))
    env = isolated_opencode_env(root, target)
    checks = [
        run_opencode_command("debug-paths", [*command, "--pure", "debug", "paths"], env, timeout),
        run_opencode_command("debug-skill", [*command, "--pure", "debug", "skill"], env, timeout),
        run_opencode_command("agent-list", [*command, "--pure", "agent", "list"], env, timeout),
    ]
    expected_skills = sorted(
        str(item.get("skill"))
        for item in opencode_artifacts
        if item.get("artifact_type") == "skill-file" and item.get("skill")
    )
    expected_agents = sorted(
        str(item.get("artifact_name") or Path(str(item.get("artifact", ""))).stem)
        for item in opencode_artifacts
        if item.get("artifact_type") == "agent-persona"
    )
    checks.extend(validate_skill_listing(checks[1], expected_skills, target.home))
    checks.extend(validate_agent_listing(checks[2], expected_agents))
    checks = [public_check(check) for check in checks]
    status = "ok" if all(check["ok"] for check in checks) else "degraded"
    return {
        "status": status,
        "cli": redacted_cli(cli),
        "target_home": str(target.home),
        "checked": len(checks),
        "checks": checks,
    }


def isolated_opencode_env(root: Path, target: Any) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "XDG_CONFIG_HOME": str(target.home.parent),
            "XDG_DATA_HOME": str(root / ".local" / "share"),
            "XDG_CACHE_HOME": str(root / ".cache"),
            "XDG_STATE_HOME": str(root / ".local" / "state"),
            "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
            "OPENCODE_PURE": "1",
        }
    )
    return env


def run_opencode_command(
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
    stdout_preview = "<output-redacted>" if name in {"debug-skill", "agent-list"} and stdout else stdout[:500]
    return {
        "name": name,
        "ok": result.returncode == 0,
        "status": "ok" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "stdout_preview": stdout_preview,
        "stdout": stdout,
        "stderr_preview": result.stderr.strip()[:500],
    }


def skill_files_present(home: Path, expected_skills: list[str]) -> list[dict[str, Any]]:
    """Filesystem fallback: newer OpenCode CLIs dropped `--pure debug skill`,
    so verify the managed SKILL.md files directly instead of failing the smoke
    on a removed debug command."""
    missing = [
        skill
        for skill in expected_skills
        if not (home / "skills" / skill / "SKILL.md").is_file()
    ]
    return [
        {
            "name": "opencode-skill-list-json",
            "ok": not missing,
            "status": "ok" if not missing else "failed",
            "mode": "filesystem-fallback",
            "missing": missing[:10],
        }
    ]


def validate_skill_listing(
    check: dict[str, Any], expected_skills: list[str], home: Path
) -> list[dict[str, Any]]:
    if not expected_skills:
        return []
    if not check.get("ok"):
        return skill_files_present(home, expected_skills)
    try:
        listed = json.loads(str(check.get("stdout", "")))
    except json.JSONDecodeError:
        return skill_files_present(home, expected_skills)
    names = {
        item.get("name")
        for item in listed
        if isinstance(item, dict)
    }
    return [
        {
            "name": f"opencode-skill-visible:{skill}",
            "ok": skill in names,
            "status": "ok" if skill in names else "failed",
        }
        for skill in expected_skills
    ]


def validate_agent_listing(check: dict[str, Any], expected_agents: list[str]) -> list[dict[str, Any]]:
    if not expected_agents or not check.get("ok"):
        return []
    output = str(check.get("stdout", ""))
    return [
        {
            "name": f"opencode-agent-visible:{agent}",
            "ok": agent in output,
            "status": "ok" if agent in output else "failed",
        }
        for agent in expected_agents
    ]


def redacted_cli(cli: dict[str, Any]) -> dict[str, Any]:
    allowed = {"logical_name", "status", "command", "version", "scope", "substrate", "reason"}
    result = {key: value for key, value in cli.items() if key in allowed}
    if "version" in result:
        result["version"] = "output-redacted"
    return result


def public_check(check: dict[str, Any]) -> dict[str, Any]:
    result = dict(check)
    result.pop("stdout", None)
    return result
