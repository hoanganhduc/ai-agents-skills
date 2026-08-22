"""Environment probe for manim-math-animation. Installs nothing."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys

from . import tools


def _which(name: str) -> str | None:
    env_var = {"manim": "MANIM", "ffmpeg": "FFMPEG"}.get(name)
    return tools.find_executable(name, env_var=env_var, prefer_configured_venv=name == "manim")


def _tool_version_from_path(path: str | None) -> str | None:
    if not path:
        return None
    for flag in ("--version", "-version"):
        try:
            out = subprocess.run([path, flag], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        except Exception:
            continue
        if out.returncode != 0:
            continue
        text = (out.stdout or out.stderr).strip()
        return text.splitlines()[0] if text else path
    return None


def _tool_version(name: str) -> str | None:
    return _tool_version_from_path(_which(name))


def _module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _has_font(substr: str) -> bool:
    fc = shutil.which("fc-list")
    if not fc:
        return False
    try:
        out = subprocess.run([fc, ":family"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        return substr.lower() in out.stdout.lower()
    except Exception:
        return False


def collect() -> dict:
    tool_paths = {name: _which(name) for name in ("manim", "ffmpeg", "latex", "xelatex", "dvisvgm")}
    report = {
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "system_tools": {
            "manim": _tool_version_from_path(tool_paths["manim"]),
            "ffmpeg": _tool_version_from_path(tool_paths["ffmpeg"]),
            "latex": tool_paths["latex"],
            "xelatex": tool_paths["xelatex"],
            "dvisvgm": _tool_version_from_path(tool_paths["dvisvgm"]),
        },
        "tool_paths": tool_paths,
        "python_packages": {
            "manim": _module("manim"),
            "manimpango": _module("manimpango"),
            "numpy": _module("numpy"),
        },
        "fonts": {"noto": _has_font("Noto"), "dejavu": _has_font("DejaVu")},
    }
    tools = report["system_tools"]
    report["ready_for_render"] = bool(
        report["python_packages"]["manim"] and tools["manim"] and tools["ffmpeg"] and tools["dvisvgm"]
        and (tools["latex"] or tools["xelatex"])
    )
    report["notes"] = []
    if not report["python_packages"]["manim"]:
        report["notes"].append("manim not importable -> run `setup` to create the venv.")
    if not tools["manim"]:
        report["notes"].append("manim CLI not found -> run `setup` to create the venv, or install manim.")
    if not tools["dvisvgm"]:
        report["notes"].append("dvisvgm missing -> needed by Manim MathTex (install texlive + dvisvgm).")
    if not tools["ffmpeg"]:
        report["notes"].append("ffmpeg missing -> needed for normalization (LGPL build).")
    if not (tools["latex"] or tools["xelatex"]):
        report["notes"].append("no LaTeX engine -> install texlive (with standalone/preview, cm-super).")
    return report


def main(argv: list[str]) -> int:
    print(json.dumps(collect(), ensure_ascii=False, indent=2))
    return 0
