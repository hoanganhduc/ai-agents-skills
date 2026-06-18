"""Host font discovery for captions.

The render path only needs a font family name for ffmpeg/libass. Linux and
macOS commonly expose fontconfig; Windows often does not, so inspect the Windows
font registry and font directories directly.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

VIETNAMESE_CAPTION_FONTS = (
    "Noto Sans",
    "Be Vietnam Pro",
    "Arial",
    "Calibri",
    "Segoe UI",
    "Tahoma",
    "Verdana",
    "Times New Roman",
    "DejaVu Sans",
)


def _squash(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _fontconfig_has_font(family: str) -> bool | None:
    fc = shutil.which("fc-match") or shutil.which("fc-list")
    if not fc:
        return None
    try:
        if Path(fc).name.startswith("fc-match"):
            out = subprocess.run([fc, family], capture_output=True, text=True, timeout=10)
        else:
            out = subprocess.run([fc, ":family"], capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    return _squash(family) in _squash(out.stdout)


def _strip_registry_suffix(name: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()


@lru_cache(maxsize=1)
def _windows_font_names() -> tuple[str, ...]:
    if os.name != "nt":
        return ()
    names: set[str] = set()
    try:
        import winreg

        keys = (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        )
        for root, subkey in keys:
            try:
                with winreg.OpenKey(root, subkey) as key:
                    count = winreg.QueryInfoKey(key)[1]
                    for index in range(count):
                        value_name, value_data, _value_type = winreg.EnumValue(key, index)
                        names.add(_strip_registry_suffix(value_name))
                        names.add(Path(str(value_data)).stem)
            except OSError:
                continue
    except Exception:
        pass

    font_dirs = []
    windir = os.environ.get("WINDIR")
    if windir:
        font_dirs.append(Path(windir) / "Fonts")
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        font_dirs.append(Path(localappdata) / "Microsoft" / "Windows" / "Fonts")
    for font_dir in font_dirs:
        if not font_dir.exists():
            continue
        for path in font_dir.glob("*"):
            if path.suffix.lower() in {".ttf", ".ttc", ".otf"}:
                names.add(path.stem)
    return tuple(sorted(name for name in names if name))


def font_available(family: str) -> bool:
    wanted = _squash(family)
    if os.name == "nt":
        if any(wanted in _squash(name) for name in _windows_font_names()):
            return True
    fc_result = _fontconfig_has_font(family)
    if fc_result is not None:
        return fc_result
    return False


def available_vietnamese_fonts() -> list[str]:
    return [family for family in VIETNAMESE_CAPTION_FONTS if font_available(family)]


def best_caption_font(preferred: str | None = None) -> str:
    if preferred and font_available(preferred):
        return preferred
    for family in VIETNAMESE_CAPTION_FONTS:
        if font_available(family):
            return family
    return preferred or "Noto Sans"
