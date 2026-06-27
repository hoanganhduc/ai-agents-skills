"""Environment probe: report capture readiness; installs nothing.

Emits a JSON report like ``s2v/doctor.py``. Fail-soft: a missing browser is
reported as ``status="missing"`` / ``BLOCKED_ENVIRONMENT`` rather than raising.
This is the ONLY surface that reports real capture readiness -- ``file-exists`` and
``offline-smoke`` verification passing does not imply a browser is present.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys

from . import detect

BLOCKED_ENVIRONMENT = "BLOCKED_ENVIRONMENT"


def _module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def collect() -> dict:
    browser = detect.detect_browser()
    report = {
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "os_name": os.name,
        "platform": sys.platform,
        "browser": browser.to_dict(),
        "optional_tools": {
            "imagemagick": shutil.which("magick") or shutil.which("convert"),
        },
        "optional_packages": {
            "pillow": _module("PIL"),
            "websocket-client": _module("websocket"),
        },
        "notes": [],
    }
    report["ready_for_capture"] = browser.status == "available"
    if not report["ready_for_capture"]:
        report["status"] = BLOCKED_ENVIRONMENT
        report["notes"].append(
            "No Chromium/Chrome/Edge found -> install one or set URL_TO_SCREENSHOT_BROWSER. "
            "Capture is unavailable until a browser is present."
        )
    else:
        report["status"] = "ok"
    if not report["optional_tools"]["imagemagick"]:
        report["notes"].append("ImageMagick not found -> --crop falls back to a CDP clip.")
    return report


def main(argv: list[str]) -> int:
    report = collect()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
