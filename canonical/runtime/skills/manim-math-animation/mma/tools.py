"""Cross-platform executable discovery for the Manim runtime."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def venv_executable(venv: Path, name: str, *, platform_name: str | None = None) -> Path:
    windows = (platform_name or os.name) == "nt"
    directory = "Scripts" if windows else "bin"
    filename = f"{name}.exe" if windows and not name.lower().endswith(".exe") else name
    return venv / directory / filename


def configured_venv() -> Path:
    value = os.environ.get("MMA_VENV")
    if value:
        return Path(value).expanduser()
    return Path.home() / ".local" / "share" / "manim-math-animation-venv"


def find_executable(
    name: str,
    *,
    env_var: str | None = None,
    prefer_configured_venv: bool = False,
) -> str | None:
    if env_var and os.environ.get(env_var):
        override = os.environ[env_var]
        resolved = shutil.which(override)
        if resolved:
            return resolved
        path = Path(override).expanduser()
        return str(path) if path.is_file() else None

    if prefer_configured_venv and os.environ.get("MMA_VENV"):
        configured = venv_executable(configured_venv(), name)
        return str(configured) if configured.is_file() else None

    python_dir = Path(sys.executable).parent
    if python_dir.name.lower() in {"bin", "scripts"}:
        active = venv_executable(python_dir.parent, name)
        if active.is_file():
            return str(active)

    if prefer_configured_venv:
        dedicated = venv_executable(configured_venv(), name)
        if dedicated.is_file():
            return str(dedicated)

    return shutil.which(name)
