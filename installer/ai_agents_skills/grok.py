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


GROK_MODEL_PROBE_SCHEMA = "grok-model-membership.v1"
GROK_MODEL_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:+/@-]{0,127}"
GROK_MODEL_ID_RE = re.compile(rf"^{GROK_MODEL_ID_PATTERN}$")
GROK_AVAILABLE_MODEL_LINE_RE = re.compile(
    rf"^\s*\*\s+(?P<model>{GROK_MODEL_ID_PATTERN})(?:\s+\(default\))?\s*$"
)


# Bare Grok and the managed region proxy are separate discovery tiers. Generic
# discovery and prechecks use only the bare tier. Automatic dispatch may consult
# the remote tier only inside the exact-model fallback resolver after an already
# resolved, valid model is not confirmed by ``grok models``. ``AAS_GROK`` is
# handled explicitly by the dispatch resolver, outside generic discovery, and
# is never silently replaced by fallback.
GROK_BARE_CLI_TOOL_SPEC: dict[str, Any] = {
    "candidates": {
        "linux": [
            "grok",
            "~/.local/bin/grok",
            "~/.grok/bin/grok",
        ],
        "macos": [
            "grok",
            "~/.local/bin/grok",
            "~/.grok/bin/grok",
            "/opt/homebrew/bin/grok",
            "/usr/local/bin/grok",
        ],
        "wsl": [
            "grok",
            "~/.local/bin/grok",
            "~/.grok/bin/grok",
        ],
        "windows": [
            "%USERPROFILE%\\.grok\\bin\\grok.exe",
            "grok.exe",
            "grok",
        ],
    }
}


GROK_REMOTE_CLI_TOOL_SPEC: dict[str, Any] = {
    "candidates": {
        "linux": [
            "grok-remote",
            "~/grok-proxy/grok-remote",
        ],
        "macos": [
            "grok-remote",
            "~/grok-proxy/grok-remote",
        ],
        "wsl": [
            "grok-remote",
            "~/grok-proxy/grok-remote",
        ],
        "windows": [
            "grok-remote.cmd",
            "grok-remote",
        ],
    }
}


GROK_CLI_TOOL_SPEC: dict[str, Any] = {
    "candidates": {
        platform: list(candidates)
        for platform, candidates in GROK_BARE_CLI_TOOL_SPEC["candidates"].items()
    }
}


# Native smoke runs the local read-only ``grok inspect`` subcommand. Keep the
# explicit override for operator control, then use only the bare automatic tier.
# Prechecks do not use this override-bearing spec; they use the bare tier above.
GROK_DIAGNOSTIC_CLI_TOOL_SPEC: dict[str, Any] = {
    "candidates": {
        "linux": ["${AAS_GROK}", *GROK_BARE_CLI_TOOL_SPEC["candidates"]["linux"]],
        "macos": ["${AAS_GROK}", *GROK_BARE_CLI_TOOL_SPEC["candidates"]["macos"]],
        "wsl": ["${AAS_GROK}", *GROK_BARE_CLI_TOOL_SPEC["candidates"]["wsl"]],
        "windows": ["%AAS_GROK%", *GROK_BARE_CLI_TOOL_SPEC["candidates"]["windows"]],
    }
}


def parse_grok_available_models(output: str) -> list[str]:
    """Parse only exact ``grok models`` available-model rows.

    The anchored grammar intentionally ignores prose such as ``Default model:``
    and rejects substring matches. A CLI output-format change therefore fails
    closed instead of accidentally confirming the requested model.
    """
    models: list[str] = []
    for line in output.splitlines():
        match = GROK_AVAILABLE_MODEL_LINE_RE.fullmatch(line)
        if match is not None and match.group("model") not in models:
            models.append(match.group("model"))
    return models


def probe_grok_model_membership(
    command: str,
    resolved_model: str,
    env: dict[str, str],
    *,
    timeout: int = 10,
) -> dict[str, Any]:
    """Confirm an exact resolved-model membership row from bare ``grok models``."""
    result: dict[str, Any] = {
        "schema_version": GROK_MODEL_PROBE_SCHEMA,
        "status": "not-confirmed",
        "resolved_model": resolved_model,
        "available_models": [],
        "reason_code": "probe_failed",
    }
    if GROK_MODEL_ID_RE.fullmatch(resolved_model) is None:
        result["reason_code"] = "resolved_model_invalid"
        return result
    try:
        parts = split_command(command)
    except ValueError:
        result["reason_code"] = "command_invalid"
        return result
    if not parts:
        result["reason_code"] = "command_empty"
        return result
    probe_env = dict(env)
    probe_env.setdefault("NO_COLOR", "1")
    probe_env.setdefault("TERM", "dumb")
    private_umask = {"umask": 0o077} if os.name == "posix" else {}
    try:
        completed = subprocess.run(
            [parts[0], "models"],
            text=True,
            capture_output=True,
            timeout=timeout,
            env=probe_env,
            check=False,
            **private_umask,
        )
    except subprocess.TimeoutExpired:
        result["reason_code"] = "probe_timed_out"
        return result
    except OSError:
        result["reason_code"] = "probe_could_not_execute"
        return result
    models = parse_grok_available_models(completed.stdout)
    result["available_models"] = models
    if completed.returncode != 0:
        result["reason_code"] = "probe_exit_nonzero"
        return result
    if not models:
        result["reason_code"] = "available_model_rows_missing"
        return result
    if resolved_model not in models:
        result["reason_code"] = "resolved_model_not_listed"
        return result
    result["status"] = "confirmed"
    result["reason_code"] = "resolved_model_listed"
    return result


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
