from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from glob import glob
from pathlib import Path
from typing import Any

from .manifest import REPO_ROOT


def discover_tool(
    name: str,
    spec: dict[str, Any],
    platform: str,
    root: Path | None = None,
) -> dict[str, Any]:
    root = root or Path.home()
    candidates = candidates_for_platform(spec.get("candidates", {}), platform)
    checked: list[dict[str, Any]] = []
    for raw in candidates:
        expanded = expand_candidate(raw, root, platform)
        if not expanded:
            continue
        if expanded.startswith("wsl:"):
            wsl_result = discover_wsl_candidate(name, expanded.removeprefix("wsl:"), raw)
            checked.append({key: value for key, value in wsl_result.items() if key != "checked"})
            if wsl_result["status"] == "ok" and wsl_result.get("command"):
                return {**wsl_result, "checked": checked}
            continue
        if expanded.startswith("wsl-rootfs:"):
            rootfs_result = discover_wsl_rootfs_candidate(
                name,
                expanded.removeprefix("wsl-rootfs:"),
                raw,
                root,
                platform,
            )
            checked.append({key: value for key, value in rootfs_result.items() if key != "checked"})
            if rootfs_result["status"] in {"ok", "degraded"}:
                return {**rootfs_result, "checked": checked}
            continue
        if expanded.startswith("wsl-local:"):
            local_wsl_result = discover_wsl_local_candidate(
                name,
                expanded.removeprefix("wsl-local:"),
                raw,
            )
            checked.append({key: value for key, value in local_wsl_result.items() if key != "checked"})
            if local_wsl_result["status"] in {"ok", "degraded"}:
                return {**local_wsl_result, "checked": checked}
            continue
        if expanded.startswith("wsl-vhdx:"):
            vhdx_result = discover_wsl_vhdx_candidate(
                name,
                expanded.removeprefix("wsl-vhdx:"),
                raw,
                root,
                platform,
            )
            checked.append({key: value for key, value in vhdx_result.items() if key != "checked"})
            if vhdx_result["status"] in {"ok", "degraded"}:
                return {**vhdx_result, "checked": checked}
            continue
        command = resolve_command(expanded, platform, root)
        if command is None:
            checked.append(unresolved_candidate(raw, platform))
            continue
        capabilities = check_capabilities(name, command, platform)
        selected = {
            "logical_name": name,
            "command": command,
            "version": detect_version(command, platform),
            "scope": infer_scope(command, root),
            "substrate": substrate_for(platform, command),
            "capabilities": capabilities,
            "status": "ok" if all(capabilities.values()) else "degraded",
            "checked": checked,
        }
        return selected
    if any(item.get("status") == "unverified" for item in checked):
        return {
            "logical_name": name,
            "status": "degraded",
            "checked": checked,
            "substrate": substrate_for(platform),
            "reason": "native Windows PATH cannot be inspected from this host",
        }
    degraded = next((item for item in checked if item.get("status") == "degraded"), None)
    if degraded:
        return {
            "logical_name": name,
            "status": "degraded",
            "checked": checked,
            "scope": degraded.get("scope"),
            "substrate": degraded.get("substrate", substrate_for(platform)),
            "reason": degraded.get("reason", "candidate is present but could not be fully verified"),
        }
    return {
        "logical_name": name,
        "status": "missing",
        "checked": checked,
        "substrate": substrate_for(platform),
    }


def expand_candidate(raw: str, root: Path | None = None, platform: str | None = None) -> str:
    root = root or Path.home()
    platform = current_platform(platform)
    value = raw.replace("<ROOT>", str(root))
    value = value.replace("<LINUX_HOME>", str(root))
    value = value.replace("<WINDOWS_HOME>", str(root))

    def expand_braced(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), "")

    def expand_percent(match: re.Match[str]) -> str:
        name = match.group(1)
        fallback = windows_env_fallback(name, root) if platform == "windows" else ""
        if not fallback:
            fallback = {
                "HOME": str(root),
                "USERPROFILE": str(root),
            }.get(name.upper(), "")
        return os.environ.get(name, fallback)

    value = re.sub(r"\$\{([^}]+)\}", expand_braced, value)
    value = re.sub(r"%([^%]+)%", expand_percent, value)
    if value.startswith("~/") or value == "~":
        value = str(root / value[2:]) if value != "~" else str(root)
    return os.path.expandvars(value)


def resolve_command(candidate: str, platform: str | None = None, root: Path | None = None) -> str | None:
    platform = current_platform(platform)
    root = root or Path.home()
    path_candidate = candidate.strip("\"'")
    if is_path_candidate(path_candidate):
        for path in candidate_path_options(path_candidate, root, platform):
            if path.exists():
                return render_command(str(path), [])
        return None
    parts = split_candidate(candidate, platform)
    if not parts:
        return None
    first = parts[0]
    for path in candidate_path_options(first, root, platform):
        if path.exists():
            return render_command(str(path), parts[1:])
    if is_path_candidate(first):
        return None
    if platform == "windows" and not host_is_windows():
        return None
    found = shutil.which(first)
    if not found:
        return None
    return render_command(found, parts[1:])


def split_candidate(candidate: str, platform: str) -> list[str]:
    try:
        return shlex.split(candidate, posix=platform != "windows")
    except ValueError:
        return []


def render_command(executable: str, args: list[str]) -> str:
    rendered = shlex.quote(executable)
    if not args:
        return rendered
    return " ".join([rendered, *args])


def candidate_path_options(first: str, root: Path, platform: str) -> list[Path]:
    token = first.strip("\"'")
    if platform == "windows" and not host_is_windows():
        token = token.replace("\\", "/")
    if token.startswith("~/") or token == "~":
        suffix = token[2:] if token != "~" else ""
        return expand_path_glob(root / suffix)
    if re.match(r"^[A-Za-z]:[\\/]", token):
        if host_is_windows():
            return expand_path_glob(Path(token))
        return expand_path_glob(windows_drive_path_to_mount(token, root))
    path = Path(token)
    if path.is_absolute():
        return expand_path_glob(path)
    if is_path_candidate(first):
        options = [root / path]
        if token.startswith("."):
            options.append(REPO_ROOT / path)
        expanded: list[Path] = []
        for option in options:
            expanded.extend(expand_path_glob(option))
        return expanded
    return []


def expand_path_glob(path: Path) -> list[Path]:
    text = str(path)
    if any(char in text for char in "*?["):
        return [Path(item) for item in sorted(glob(text), reverse=True)]
    return [path]


def existing_candidate_paths(expr: str, root: Path, platform: str) -> list[Path]:
    paths = candidate_path_options(expr, root, platform)
    return [path for path in paths if path.exists()]


def windows_env_fallback(name: str, root: Path) -> str:
    drive_root = windows_drive_root(root)
    mapping = {
        "APPDATA": root / "AppData" / "Roaming",
        "LOCALAPPDATA": root / "AppData" / "Local",
        "PROGRAMFILES": drive_root / "Program Files",
        "PROGRAMFILES(X86)": drive_root / "Program Files (x86)",
        "PROGRAMDATA": drive_root / "ProgramData",
        "SYSTEMROOT": drive_root / "Windows",
        "WINDIR": drive_root / "Windows",
        "USERPROFILE": root,
        "HOME": root,
    }
    if name.upper() == "SYSTEMDRIVE":
        return "C:"
    value = mapping.get(name.upper())
    return str(value) if value else ""


def windows_drive_root(root: Path) -> Path:
    parts = root.resolve().parts
    for index, part in enumerate(parts):
        if part.lower() == "users" and index > 0:
            return Path(*parts[:index])
    if len(parts) >= 2:
        return Path(*parts[:2])
    return root


def windows_drive_path_to_mount(token: str, root: Path) -> Path:
    rest = re.sub(r"^[A-Za-z]:[\\/]*", "", token)
    return windows_drive_root(root) / rest


def is_path_candidate(first: str) -> bool:
    return (
        first.startswith(".")
        or first.startswith("~")
        or "/" in first
        or "\\" in first
        or re.match(r"^[A-Za-z]:[\\/]", first) is not None
    )


def unresolved_candidate(raw: str, platform: str) -> dict[str, Any]:
    if platform == "windows" and not host_is_windows():
        parts = split_candidate(raw, platform)
        first = parts[0] if parts else raw
        if not is_path_candidate(first) or re.match(r"^[A-Za-z]:[\\/]", first):
            return {
                "candidate": raw,
                "status": "unverified",
                "reason": "native Windows PATH cannot be inspected from this host",
            }
    return {"candidate": raw, "status": "missing"}


def detect_version(command: str, platform: str | None = None) -> str:
    if not can_execute_host(command, platform):
        return "present-unverified"
    parts = split_command(command)
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


def check_capabilities(name: str, command: str, platform: str | None = None) -> dict[str, bool]:
    if not can_execute_host(command, platform):
        return {"executable": True, "host-executable": False}
    if name == "python-runtime":
        code = "import ssl, venv, pip; print('ok')"
        return {
            "ssl": run_python(command, code, platform),
            "venv": run_python(command, "import venv", platform),
            "pip": run_python(command, "import pip", platform),
        }
    if name == "powershell-runtime":
        return {"script-execution": True, "utf8-output": True}
    if name == "node-runtime":
        return {"npm": shutil.which("npm") is not None or shutil.which("npm.cmd") is not None}
    return {"executable": True}


def run_python(command: str, code: str, platform: str | None = None) -> bool:
    if not can_execute_host(command, platform):
        return False
    parts = split_command(command)
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


def infer_scope(command: str, root: Path | None = None) -> str:
    if "wsl" in command.lower():
        return "wsl"
    parts = split_command(command)
    if not parts:
        return "system"
    path = Path(parts[0])
    try:
        resolved = path.resolve()
    except OSError:
        return "system"
    home = root or Path.home()
    if root is not None and path_within(resolved, resolved_path(home)):
        return "user-local"
    if path_within(resolved, resolved_path(REPO_ROOT)):
        return "repo-local"
    if path_within(resolved, resolved_path(home)):
        return "user-local"
    return "system"


def resolved_path(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def substrate_for(platform: str, command: str | None = None) -> str:
    if command and "wsl" in command.lower():
        return "wsl"
    if platform == "windows" and command and is_posix_command(command):
        return "wsl"
    if platform == "windows":
        return "windows-native"
    return "linux-local"


def is_posix_command(command: str) -> bool:
    parts = split_command(command)
    if not parts:
        return False
    executable = parts[0]
    return executable.startswith("/") and not executable.lower().endswith(".exe")


def split_command(command: str, *, windows_host: bool | None = None) -> list[str]:
    posix = os.name != "nt" if windows_host is None else not windows_host
    try:
        parts = shlex.split(command, posix=posix)
    except ValueError:
        return []
    if parts:
        parts[0] = parts[0].strip("\"'")
    return parts


def host_is_windows() -> bool:
    return sys.platform.startswith("win")


def can_execute_host(command: str, platform: str | None = None) -> bool:
    platform = current_platform(platform)
    parts = split_command(command)
    if not parts:
        return False
    executable = parts[0].lower()
    if platform == "windows" and not host_is_windows() and executable.endswith(".exe"):
        return False
    return True


def current_platform(override: str | None = None) -> str:
    if override:
        return override
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def candidates_for_platform(candidates: dict[str, list[str]], platform: str) -> list[str]:
    if platform in candidates:
        return candidates[platform]
    if platform == "macos":
        return candidates.get("linux", [])
    return []


def discover_wsl_candidate(name: str, command_name: str, raw: str) -> dict[str, Any]:
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    if not wsl:
        if not host_is_windows():
            return {
                "logical_name": name,
                "candidate": raw,
                "status": "degraded",
                "reason": "WSL command cannot be verified from this host",
                "scope": "wsl",
                "substrate": "wsl",
            }
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


def discover_wsl_rootfs_candidate(
    name: str,
    path_expr: str,
    raw: str,
    root: Path,
    platform: str,
) -> dict[str, Any]:
    paths = existing_candidate_paths(path_expr, root, platform)
    if not paths:
        return {
            "logical_name": name,
            "candidate": raw,
            "status": "missing",
            "reason": "SageMath path not found in mounted WSL rootfs",
            "scope": "wsl-rootfs",
            "substrate": "wsl",
        }
    selected = paths[0]
    return {
        "logical_name": name,
        "candidate": raw,
        "command": str(selected),
        "version": "present-unverified",
        "scope": "wsl-rootfs",
        "substrate": "wsl",
        "capabilities": {
            "rootfs-present": True,
            "sage-path-present": True,
            "launch-via-wsl-unverified": True,
        },
        "status": "degraded",
        "reason": "SageMath path exists in a mounted WSL rootfs, but command execution still requires WSL",
    }


def discover_wsl_local_candidate(name: str, path_expr: str, raw: str) -> dict[str, Any]:
    if host_is_windows():
        return {
            "logical_name": name,
            "candidate": raw,
            "status": "missing",
            "reason": "local WSL filesystem is not directly inspectable from native Windows",
            "scope": "wsl-local",
            "substrate": "wsl",
        }
    paths: list[Path] = []
    if path_expr and not is_path_candidate(path_expr):
        found = shutil.which(path_expr)
        if found:
            paths.append(Path(found))
    if not paths:
        paths = existing_candidate_paths(path_expr, Path.home(), "linux")
    executable_paths = [path for path in paths if path.is_file() and os.access(path, os.X_OK)]
    if not executable_paths:
        return {
            "logical_name": name,
            "candidate": raw,
            "status": "missing",
            "reason": "SageMath path not found in the current WSL/Linux filesystem",
            "scope": "wsl-local",
            "substrate": "wsl",
        }
    selected = executable_paths[0]
    command = render_command(str(selected), [])
    return {
        "logical_name": name,
        "candidate": raw,
        "command": command,
        "version": detect_version(command, "linux"),
        "scope": "wsl-local",
        "substrate": "wsl",
        "capabilities": check_capabilities(name, command, "linux"),
        "status": "ok",
    }


def discover_wsl_vhdx_candidate(
    name: str,
    path_expr: str,
    raw: str,
    root: Path,
    platform: str,
) -> dict[str, Any]:
    paths = existing_candidate_paths(path_expr, root, platform)
    if not paths:
        return {
            "logical_name": name,
            "candidate": raw,
            "status": "missing",
            "reason": "WSL distro VHDX not found",
            "scope": "wsl-vhdx",
            "substrate": "wsl",
        }
    selected = paths[0]
    return {
        "logical_name": name,
        "candidate": raw,
        "path": str(selected),
        "version": "present-unverified",
        "scope": "wsl-vhdx",
        "substrate": "wsl",
        "capabilities": {
            "wsl-vhdx-present": True,
            "sage-inspection": False,
        },
        "status": "degraded",
        "reason": "WSL distro VHDX found, but SageMath inside cannot be inspected without WSL or a mounted rootfs",
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


def discover_python_package(
    package_name: str,
    module: str,
    python_command: str | None,
    *,
    platform: str | None = None,
    root: Path | None = None,
    python_candidates: list[str] | None = None,
    site_candidates: list[str] | None = None,
) -> dict[str, Any]:
    platform = current_platform(platform)
    root = root or Path.home()
    candidates = python_candidates or (["python-runtime"] if python_command else [])
    site_candidates = site_candidates or []
    checked: list[dict[str, Any]] = []
    if not candidates and not site_candidates:
        return {
            "logical_name": package_name,
            "status": "missing",
            "reason": "python runtime unavailable",
            "module": module,
            "checked": checked,
        }

    for raw in candidates:
        command = python_command if raw == "python-runtime" else None
        if raw != "python-runtime":
            expanded = expand_candidate(raw, root, platform)
            command = resolve_command(expanded, platform, root) if expanded else None
        if not command:
            checked.append({"candidate": raw, "status": "missing"})
            continue
        check = check_python_module(command, module, platform)
        check["candidate"] = raw
        checked.append(check)
        if check["status"] == "ok":
            return {
                "logical_name": package_name,
                "type": "python",
                "module": module,
                "status": "ok",
                "python": command,
                "detection": check.get("detection"),
                "checked": checked,
            }

    for raw in site_candidates:
        site_paths = resolve_site_candidate(raw, root, platform)
        if not site_paths:
            checked.append({"candidate": raw, "type": "site-packages", "status": "missing"})
            continue
        found_in_candidate = False
        for site_path in site_paths:
            hit = module_marker(site_path, module)
            if hit:
                checked.append(
                    {
                        "candidate": raw,
                        "type": "site-packages",
                        "status": "ok",
                        "site_package": str(hit),
                    }
                )
                return {
                    "logical_name": package_name,
                    "type": "python",
                    "module": module,
                    "status": "ok",
                    "detection": "site-packages",
                    "site_package": str(hit),
                    "checked": checked,
                }
            if site_path.exists():
                found_in_candidate = True
        checked.append(
            {
                "candidate": raw,
                "type": "site-packages",
                "status": "missing",
                "reason": "site-packages path exists but module marker was not found"
                if found_in_candidate
                else "site-packages path not found",
            }
        )

    return {
        "logical_name": package_name,
        "type": "python",
        "module": module,
        "status": "missing",
        "reason": "module not found in any checked Python environment",
        "checked": checked,
    }


def check_python_module(command: str, module: str, platform: str) -> dict[str, Any]:
    if can_execute_host(command, platform):
        parts = split_command(command)
        code = (
            "import importlib.util, sys; "
            f"spec = importlib.util.find_spec({module!r}); "
            "sys.exit(0 if spec else 1)"
        )
        try:
            result = subprocess.run(
                [parts[0], *parts[1:], "-c", code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
        except Exception as exc:
            site_hit = find_module_in_site_packages(command, module, platform)
            if site_hit:
                return {
                    "python": command,
                    "status": "ok",
                    "detection": "site-packages",
                    "site_package": site_hit,
                    "reason": f"import probe failed: {exc}",
                }
            return {"python": command, "status": "missing", "reason": f"import probe failed: {exc}"}
        return {
            "python": command,
            "status": "ok" if result.returncode == 0 else "missing",
            "detection": "import",
        }

    site_hit = find_module_in_site_packages(command, module, platform)
    if site_hit:
        return {
            "python": command,
            "status": "ok",
            "detection": "site-packages",
            "site_package": site_hit,
            "reason": "native executable is not runnable from this host",
        }
    return {
        "python": command,
        "status": "missing",
        "reason": "native executable is not runnable from this host and site-packages marker was not found",
    }


def find_module_in_site_packages(command: str, module: str, platform: str) -> str | None:
    parts = split_command(command)
    if not parts:
        return None
    executable = parts[0].replace("\\", "/") if platform == "windows" and not host_is_windows() else parts[0]
    exe_path = Path(executable)
    venv_roots = []
    if exe_path.parent.name.lower() in {"bin", "scripts"}:
        venv_roots.append(exe_path.parent.parent)
    site_dirs: list[Path] = []
    for venv_root in venv_roots:
        site_dirs.append(venv_root / "Lib" / "site-packages")
        site_dirs.append(venv_root / "lib" / "site-packages")
        site_dirs.extend(sorted((venv_root / "lib").glob("python*/site-packages")))
    for site_dir in site_dirs:
        hit = module_marker(site_dir, module)
        if hit:
            return str(hit)
    return None


def resolve_site_candidate(raw: str, root: Path, platform: str) -> list[Path]:
    expanded = expand_candidate(raw, root, platform)
    if not expanded:
        return []
    return existing_candidate_paths(expanded, root, platform)


def module_marker(site_dir: Path, module: str) -> Path | None:
    if not site_dir.exists():
        return None
    parts = module.split(".")
    module_path = site_dir.joinpath(*parts)
    if module_path.exists():
        return module_path
    module_file = module_path.with_suffix(".py")
    if module_file.exists():
        return module_file
    aliases = {
        "fitz": ["fitz", "PyMuPDF", "pymupdf"],
        "PyPDF2": ["PyPDF2", "pypdf2"],
        "google.oauth2": ["google/oauth2", "google_auth", "google-auth"],
    }
    names = aliases.get(module, [parts[0], parts[0].replace("_", "-")])
    for name in names:
        path_name = name.replace("/", os.sep)
        path = site_dir / path_name
        if path.exists():
            return path
        py_file = path.with_suffix(".py")
        if py_file.exists():
            return py_file
        for pattern in (f"{name}*.dist-info", f"{name}*.egg-info"):
            matches = list(site_dir.glob(pattern))
            if matches:
                return matches[0]
    return None
