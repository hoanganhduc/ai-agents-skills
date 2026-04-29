from __future__ import annotations

import os
import shlex
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
            wsl_result = discover_wsl_candidate(name, expanded.removeprefix("wsl:"), raw)
            checked.append(wsl_result)
            if wsl_result["status"] in {"ok", "degraded"} and wsl_result.get("command"):
                wsl_result["checked"] = checked
                return wsl_result
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
    return os.path.expanduser(os.path.expandvars(raw))


def resolve_command(candidate: str) -> str | None:
    parts = shlex.split(candidate, posix=os.name != "nt")
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
    parts = shlex.split(command, posix=os.name != "nt")
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
    parts = shlex.split(command, posix=os.name != "nt")
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
    path = Path(shlex.split(command, posix=os.name != "nt")[0])
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
    executable = shlex.split(command, posix=os.name != "nt")[0]
    return executable.startswith("/") and not executable.lower().endswith(".exe")


def current_platform(override: str | None = None) -> str:
    if override:
        return override
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def discover_wsl_candidate(name: str, command_name: str, raw: str) -> dict[str, Any]:
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    if not wsl:
        return {
            "logical_name": name,
            "candidate": raw,
            "status": "missing",
            "reason": "wsl executable not found from this substrate",
            "scope": "wsl",
            "substrate": "wsl",
        }
    resolve_script = r'''
cmd=$1
case "$cmd" in
  "~/"*) cmd="$HOME/${cmd#~/}" ;;
esac
if [ -x "$cmd" ]; then
  printf '%s\n' "$cmd"
elif command -v "$cmd" >/dev/null 2>&1; then
  command -v "$cmd"
else
  exit 1
fi
'''
    probe = subprocess.run(
        [wsl, "sh", "-lc", resolve_script, "sh", command_name],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    if probe.returncode != 0 or not probe.stdout.strip():
        return {
            "logical_name": name,
            "candidate": raw,
            "status": "missing",
            "reason": f"{command_name} not found inside default WSL distro",
            "scope": "wsl",
            "substrate": "wsl",
        }
    resolved = probe.stdout.strip().splitlines()[0]
    command = f"{wsl} sh -lc {shlex.quote(resolved)}"
    return {
        "logical_name": name,
        "candidate": raw,
        "command": command,
        "version": detect_wsl_version(wsl, resolved),
        "scope": "wsl",
        "substrate": "wsl",
        "capabilities": {"executable": True, "wsl-command-exec": True},
        "status": "ok",
    }


def detect_wsl_version(wsl: str, command_name: str) -> str:
    version_script = r'''
cmd=$1
"$cmd" --version 2>&1 | head -n 1
'''
    result = subprocess.run(
        [wsl, "sh", "-lc", version_script, "sh", command_name],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=10,
        check=False,
    )
    return result.stdout.strip() or "unknown"


def discover_python_package(package_name: str, module: str, python_command: str | None) -> dict[str, Any]:
    if not python_command:
        return {
            "logical_name": package_name,
            "status": "missing",
            "reason": "python runtime unavailable",
            "module": module,
        }
    parts = shlex.split(python_command, posix=os.name != "nt")
    code = (
        "import importlib.util, sys; "
        f"spec = importlib.util.find_spec({module!r}); "
        "sys.exit(0 if spec else 1)"
    )
    result = subprocess.run(
        [parts[0], *parts[1:], "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    return {
        "logical_name": package_name,
        "type": "python",
        "module": module,
        "status": "ok" if result.returncode == 0 else "missing",
        "python": python_command,
    }
