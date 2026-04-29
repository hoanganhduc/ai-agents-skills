from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .manifest import REPO_ROOT


def discover_tool(name: str, spec: dict[str, Any], platform: str) -> dict[str, Any]:
    candidates = spec.get("candidates", {}).get(platform, [])
    checked: list[dict[str, Any]] = []
    for raw in candidates:
        expanded = expand_candidate(raw)
        if not expanded:
            continue
        if expanded.startswith("wsl:"):
            checked.append(
                {
                    "candidate": raw,
                    "status": "degraded",
                    "reason": "WSL candidates require native Windows verification",
                    "scope": "wsl",
                    "substrate": "wsl",
                }
            )
            continue
        command = resolve_command(expanded)
        if command is None:
            checked.append({"candidate": raw, "status": "missing"})
            continue
        capabilities = check_capabilities(name, command)
        selected = {
            "logical_name": name,
            "command": command,
            "version": detect_version(command),
            "scope": infer_scope(command),
            "substrate": substrate_for(platform, command),
            "capabilities": capabilities,
            "status": "ok" if all(capabilities.values()) else "degraded",
            "checked": checked,
        }
        return selected
    return {
        "logical_name": name,
        "status": "missing",
        "checked": checked,
        "substrate": substrate_for(platform),
    }


def expand_candidate(raw: str) -> str:
    if raw.startswith("${") and raw.endswith("}"):
        return os.environ.get(raw[2:-1], "")
    if raw.startswith("%") and raw.endswith("%"):
        return os.environ.get(raw[1:-1], "")
    return raw


def resolve_command(candidate: str) -> str | None:
    parts = candidate.split()
    first = parts[0]
    path = Path(first)
    if path.is_absolute() or first.startswith("."):
        resolved = (REPO_ROOT / path).resolve() if first.startswith(".") else path
        return str(resolved) if resolved.exists() else None
    found = shutil.which(first)
    if not found:
        return None
    if len(parts) > 1:
        return " ".join([found, *parts[1:]])
    return found


def detect_version(command: str) -> str:
    parts = command.split()
    for args in (["--version"], ["-V"]):
        try:
            result = subprocess.run(
                [parts[0], *parts[1:], *args],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=5,
                check=False,
            )
        except Exception:
            continue
        line = result.stdout.strip().splitlines()
        if line:
            return line[0]
    return "unknown"


def check_capabilities(name: str, command: str) -> dict[str, bool]:
    if name == "python-runtime":
        code = "import ssl, venv, pip; print('ok')"
        return {"ssl": run_python(command, code), "venv": run_python(command, "import venv"), "pip": run_python(command, "import pip")}
    if name == "powershell-runtime":
        return {"script-execution": True, "utf8-output": True}
    if name == "node-runtime":
        return {"npm": shutil.which("npm") is not None or shutil.which("npm.cmd") is not None}
    return {"executable": True}


def run_python(command: str, code: str) -> bool:
    parts = command.split()
    try:
        result = subprocess.run(
            [parts[0], *parts[1:], "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0


def infer_scope(command: str) -> str:
    path = Path(command.split()[0])
    try:
        resolved = path.resolve()
    except OSError:
        return "system"
    if str(resolved).startswith(str(REPO_ROOT)):
        return "repo-local"
    home = Path.home()
    if str(resolved).startswith(str(home)):
        return "user-local"
    if "wsl" in command.lower():
        return "wsl"
    return "system"


def substrate_for(platform: str, command: str | None = None) -> str:
    if command and "wsl" in command.lower():
        return "wsl"
    if platform == "windows" and command and is_posix_command(command):
        return "wsl"
    if platform == "windows":
        return "windows-native"
    return "linux-local"


def is_posix_command(command: str) -> bool:
    executable = command.split()[0]
    return executable.startswith("/") and not executable.lower().endswith(".exe")


def current_platform(override: str | None = None) -> str:
    if override:
        return override
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"
