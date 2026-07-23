from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .agents import skill_path_is_agent_visible, target_for
from .antigravity import public_check, redacted_cli
from .discovery import current_platform, discover_tool, split_command
from .state import load_state


KIMI_MODEL_PROBE_SCHEMA = "kimi-model-id.v1"
# Conservative model-alias grammar for argv/env validation (not network discovery).
KIMI_MODEL_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:+/@-]{0,127}"
KIMI_MODEL_ID_RE = re.compile(rf"^{KIMI_MODEL_ID_PATTERN}$")


KIMI_BARE_CLI_TOOL_SPEC: dict[str, Any] = {
    "candidates": {
        "linux": [
            "kimi",
            "~/.local/bin/kimi",
            "~/.kimi-code/bin/kimi",
        ],
        "macos": [
            "kimi",
            "~/.local/bin/kimi",
            "~/.kimi-code/bin/kimi",
            "/opt/homebrew/bin/kimi",
            "/usr/local/bin/kimi",
        ],
        "wsl": [
            "kimi",
            "~/.local/bin/kimi",
            "~/.kimi-code/bin/kimi",
        ],
        "windows": [
            "%USERPROFILE%\\.kimi-code\\bin\\kimi.exe",
            "kimi.exe",
            "kimi",
        ],
    }
}


KIMI_CLI_TOOL_SPEC: dict[str, Any] = {
    "candidates": {
        platform: list(candidates)
        for platform, candidates in KIMI_BARE_CLI_TOOL_SPEC["candidates"].items()
    }
}


KIMI_DIAGNOSTIC_CLI_TOOL_SPEC: dict[str, Any] = {
    "candidates": {
        "linux": ["${AAS_KIMI}", *KIMI_BARE_CLI_TOOL_SPEC["candidates"]["linux"]],
        "macos": ["${AAS_KIMI}", *KIMI_BARE_CLI_TOOL_SPEC["candidates"]["macos"]],
        "wsl": ["${AAS_KIMI}", *KIMI_BARE_CLI_TOOL_SPEC["candidates"]["wsl"]],
        "windows": ["%AAS_KIMI%", *KIMI_BARE_CLI_TOOL_SPEC["candidates"]["windows"]],
    }
}


def build_kimi_precheck(
    root: Path,
    platform: str,
    cli_result: dict[str, Any],
) -> dict[str, Any]:
    target = target_for(root, "kimi")
    cli_status = kimi_cli_status(cli_result)
    config_toml = target.home / "config.toml"
    credentials_dir = target.home / "credentials"
    return {
        "target": "kimi",
        "status": cli_status,
        "kimi_status": cli_status,
        "cli": redacted_cli(cli_result),
        "config_dir": {
            "path": str(target.home),
            "children": {
                "skills": str(target.skills_dir),
                "agents": str(target.home / "agents"),
                "agents_md": str(target.instructions_file),
                "config_toml": str(config_toml),
                "credentials": str(credentials_dir),
            },
            "file_contents_read": False,
        },
        "config_path_present": config_toml.is_file(),
        "credentials_dir_present": credentials_dir.is_dir(),
        "auth_ready": False,
        "auth_note": "auth readiness is not inferred from config presence; keys live in config.toml/credentials and are never opened by prechecks",
        "surfaces": {
            "global_skill_files": "~/.kimi-code/skills/<skill>/SKILL.md",
            "subagents": "~/.kimi-code/agents/<name>.md",
            "instructions": "~/.kimi-code/AGENTS.md",
        },
        "installation_policy": {
            "global_skill_layout": "directory",
            "native_cli": "kimi",
            "default_install_mode": "copy",
            "real_system_writes": "require existing installer --apply and --real-system gates",
            "kimi_code_home_relocated_installs": "unsupported",
            "official_docs_checked": [
                "data-locations",
                "skills",
                "agents",
                "hooks",
                "config-files",
            ],
        },
        "platform": platform,
    }


def kimi_cli_status(cli_result: dict[str, Any]) -> str:
    status = cli_result.get("status")
    if status == "ok":
        return "supported"
    if status == "degraded":
        return "offline-unverified"
    return "cli-missing"


def run_kimi_native_smoke(
    root: Path,
    *,
    agents: set[str] | None = None,
    platform: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    if agents is not None and "kimi" not in agents:
        return {"status": "skipped", "reason": "Kimi target not selected"}
    target = target_for(root, "kimi")
    state = load_state(root)
    kimi_artifacts = [
        item for item in state.get("artifacts", [])
        if item.get("agent") == "kimi"
    ]
    if not kimi_artifacts:
        return {"status": "skipped", "reason": "no managed Kimi artifacts"}
    if not target.home.exists():
        return {"status": "skipped", "reason": "Kimi target home is missing"}

    platform_name = current_platform(platform)
    cli = discover_tool("kimi-cli", KIMI_DIAGNOSTIC_CLI_TOOL_SPEC, platform_name, root)
    file_checks = validate_kimi_file_layout(kimi_artifacts)
    if cli.get("status") != "ok" or not cli.get("command"):
        status = "degraded" if any(not check["ok"] for check in file_checks) else "skipped"
        return {
            "status": status,
            "reason": "kimi CLI is unavailable or not host-executable",
            "cli": redacted_cli(cli),
            "target_home": str(target.home),
            "checked": len(file_checks),
            "checks": file_checks,
        }

    command = split_command(str(cli["command"]))
    env = isolated_kimi_env(root, target.home)
    doctor_check = run_kimi_command("doctor", [*command, "doctor"], env, timeout)
    checks = [*file_checks, doctor_check]
    checks = [public_check(check) for check in checks]
    # Doctor is advisory for offline layout; fail only when layout checks fail.
    if any(not check["ok"] for check in file_checks):
        status = "degraded"
    elif not doctor_check.get("ok"):
        # Network/mutating/broken doctor: keep layout-only ok when files are good.
        status = "ok"
        checks.append({
            "name": "doctor-nonfatal",
            "ok": True,
            "status": "ok",
            "reason": "doctor failed or unavailable; file-layout smoke accepted",
        })
    else:
        status = "ok"
    return {
        "status": status,
        "cli": redacted_cli(cli),
        "target_home": str(target.home),
        "checked": len(checks),
        "checks": checks,
    }


def isolated_kimi_env(root: Path, kimi_home_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(root),
            "USERPROFILE": str(root),
            "KIMI_CODE_HOME": str(kimi_home_path),
            "LOCALAPPDATA": str(root / "AppData" / "Local"),
            "APPDATA": str(root / "AppData" / "Roaming"),
            "XDG_CONFIG_HOME": str(root / ".config"),
            "XDG_DATA_HOME": str(root / ".local" / "share"),
            "XDG_CACHE_HOME": str(root / ".cache"),
            "XDG_STATE_HOME": str(root / ".local" / "state"),
        }
    )
    return env


def validate_kimi_file_layout(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in artifacts:
        artifact_type = item.get("artifact_type")
        path = Path(str(item.get("artifact", "")))
        if artifact_type == "skill-file":
            skill = str(item.get("skill"))
            ok = (
                path.exists()
                and path.is_file()
                and skill_path_is_agent_visible("kimi", path, skill)
            )
            checks.append({
                "name": f"kimi-skill-file:{skill}",
                "ok": ok,
                "status": "ok" if ok else "failed",
            })
        if artifact_type == "agent-persona":
            ok = path.exists() and path.is_file()
            checks.append({
                "name": f"kimi-persona-file:{item.get('artifact_name')}",
                "ok": ok,
                "status": "ok" if ok else "failed",
            })
    return checks


def run_kimi_command(
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
    # Never return raw doctor/config text into reports (may include paths only;
    # still redact body content).
    return {
        "name": name,
        "ok": result.returncode == 0,
        "status": "ok" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "stdout_preview": "<output-redacted>" if stdout else "",
        "stderr_preview": "<output-redacted>" if result.stderr.strip() else "",
    }
