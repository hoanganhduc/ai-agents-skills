"""Environment probe: report what the skill needs and what is present.

Never installs anything; prints a JSON report and a human summary. Safe to run
anywhere (degrades to "missing" rather than raising).
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import fonts, pptx_render


def _which(name: str) -> str | None:
    return shutil.which(name)


def _existing_path(path: str | Path | None) -> str | None:
    if not path:
        return None
    candidate = Path(os.path.expandvars(str(path)))
    return str(candidate) if candidate.exists() else None


def _venv_script(name: str) -> str | None:
    suffix = ".exe" if os.name == "nt" else ""
    return _existing_path(Path(sys.executable).resolve().parent / f"{name}{suffix}")


def _espeak_ng() -> str | None:
    return (
        _which("espeak-ng")
        or _existing_path(r"%PROGRAMFILES%\eSpeak NG\espeak-ng.exe")
        or _existing_path(r"%PROGRAMFILES(X86)%\eSpeak NG\espeak-ng.exe")
    )


def _piper_cli() -> str | None:
    return _which("piper") or _venv_script("piper")


def _tool_version(name: str) -> str | None:
    path = _which(name)
    if not path:
        return None
    try:
        out = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).splitlines()[0] if (out.stdout or out.stderr) else path
    except Exception:
        return path


def _module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def collect() -> dict:
    available_vietnamese_fonts = fonts.available_vietnamese_fonts()
    report = {
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "system_tools": {
            "ffmpeg": _tool_version("ffmpeg"),
            "ffprobe": _tool_version("ffprobe"),
            "espeak-ng": _espeak_ng(),
            "soffice": _which("soffice") or _which("libreoffice"),
            "powerpoint": pptx_render.powerpoint_status(),
            "piper": _piper_cli(),
        },
        "python_packages": {
            name: _module(mod)
            for name, mod in (
                ("edge-tts", "edge_tts"),
                ("kokoro", "kokoro"),
                ("piper-tts", "piper"),
                ("python-pptx", "pptx"),
                ("pymupdf", "fitz"),
                ("pillow", "PIL"),
                ("numpy", "numpy"),
                ("soundfile", "soundfile"),
                ("pydub", "pydub"),
            )
        },
        "fonts": {
            "noto": fonts.font_available("Noto Sans"),
            "be_vietnam_pro": fonts.font_available("Be Vietnam Pro"),
            "dejavu": fonts.font_available("DejaVu Sans"),
            "vietnamese_covering": bool(available_vietnamese_fonts),
            "caption_font": fonts.best_caption_font("Noto Sans"),
            "available_vietnamese_candidates": available_vietnamese_fonts,
        },
    }
    tools = report["system_tools"]
    report["ready_for_render"] = bool(tools["ffmpeg"] and tools["ffprobe"])
    report["ready_for_pptx"] = bool(tools["soffice"] or tools["powerpoint"])
    report["notes"] = []
    if not report["ready_for_render"]:
        report["notes"].append("ffmpeg/ffprobe missing -> install before `render` (LGPL build).")
    if not report["ready_for_pptx"]:
        report["notes"].append("PPTX input needs Microsoft PowerPoint on Windows or LibreOffice (soffice) on PATH.")
    if not report["fonts"]["vietnamese_covering"]:
        report["notes"].append("No Vietnamese-covering caption font found -> captions may show tofu.")
    return report


def main(argv: list[str]) -> int:
    report = collect()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
