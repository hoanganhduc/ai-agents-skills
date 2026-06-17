"""Environment probe: report what the skill needs and what is present.

Never installs anything; prints a JSON report and a human summary. Safe to run
anywhere (degrades to "missing" rather than raising).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys


def _which(name: str) -> str | None:
    return shutil.which(name)


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


def _has_font(substr: str) -> bool:
    fc = _which("fc-list")
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
            "ffmpeg": _tool_version("ffmpeg"),
            "ffprobe": _tool_version("ffprobe"),
            "espeak-ng": _which("espeak-ng"),
            "soffice": _which("soffice") or _which("libreoffice"),
            "piper": _which("piper"),
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
            "noto": _has_font("Noto"),
            "be_vietnam_pro": _has_font("Be Vietnam"),
            "dejavu": _has_font("DejaVu"),
        },
    }
    tools = report["system_tools"]
    report["ready_for_render"] = bool(tools["ffmpeg"] and tools["ffprobe"])
    report["ready_for_pptx"] = bool(tools["soffice"])
    report["notes"] = []
    if not report["ready_for_render"]:
        report["notes"].append("ffmpeg/ffprobe missing -> install before `render` (LGPL build).")
    if not (report["fonts"]["noto"] or report["fonts"]["be_vietnam_pro"]):
        report["notes"].append("No Vietnamese-covering font (Noto/Be Vietnam Pro) -> captions may show tofu.")
    return report


def main(argv: list[str]) -> int:
    report = collect()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
