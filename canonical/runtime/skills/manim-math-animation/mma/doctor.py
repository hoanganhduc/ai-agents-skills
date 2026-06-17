"""Environment probe for manim-math-animation. Installs nothing."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _which(name: str) -> str | None:
    cand = shutil.which(name)
    if cand:
        return cand
    venv = Path(os.path.expanduser("~")) / ".local/share/manim-math-animation-venv/bin" / name
    return str(venv) if venv.exists() else None


def _tool_version(name: str) -> str | None:
    path = _which(name)
    if not path:
        return None
    for flag in ("--version", "-version"):
        try:
            out = subprocess.run([path, flag], capture_output=True, text=True, timeout=15)
            text = (out.stdout or out.stderr).strip()
            if text:
                return text.splitlines()[0]
        except Exception:
            continue
    return path


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
        out = subprocess.run([fc, ":family"], capture_output=True, text=True, timeout=10)
        return substr.lower() in out.stdout.lower()
    except Exception:
        return False


def collect() -> dict:
    report = {
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "system_tools": {
            "manim": _tool_version("manim"),
            "ffmpeg": _tool_version("ffmpeg"),
            "latex": _which("latex"),
            "xelatex": _which("xelatex"),
            "dvisvgm": _tool_version("dvisvgm"),
        },
        "python_packages": {
            "manim": _module("manim"),
            "manimpango": _module("manimpango"),
            "numpy": _module("numpy"),
        },
        "fonts": {"noto": _has_font("Noto"), "dejavu": _has_font("DejaVu")},
    }
    tools = report["system_tools"]
    report["ready_for_render"] = bool(
        report["python_packages"]["manim"] and tools["ffmpeg"] and tools["dvisvgm"]
        and (tools["latex"] or tools["xelatex"])
    )
    report["notes"] = []
    if not report["python_packages"]["manim"]:
        report["notes"].append("manim not importable -> run `setup` to create the venv.")
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
