"""Audio helpers around ffmpeg/ffprobe (the free system tool).

These shell out to ffmpeg/ffprobe at render time. ``subprocess`` is stdlib, but
the binaries may be absent (this host has none), so each call raises a clear
``FFmpegMissing`` instead of a cryptic OSError. Not exercised by the offline
selftest.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class FFmpegMissing(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG", "") or shutil.which("ffmpeg") or _missing("ffmpeg")


def ffprobe_bin() -> str:
    return os.environ.get("FFPROBE", "") or shutil.which("ffprobe") or _missing("ffprobe")


def _missing(name: str) -> str:
    raise FFmpegMissing(
        f"{name} not found on PATH. Install ffmpeg (LGPL build), e.g. "
        "`apt-get install ffmpeg` or a static build from johnvansickle.com/ffmpeg, "
        "then re-run. See references/pipeline-and-sync.md."
    )


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=True, capture_output=True, text=True)


def probe_duration(path: Path) -> float:
    """Authoritative per-segment duration (drives slide clip length AND captions)."""
    out = _run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)]
    )
    return float(out.stdout.strip())


def to_wav(src: Path, dst: Path, rate: int = 48000, channels: int = 2) -> Path:
    """Decode any synth output to a canonical PCM WAV so mixed engines concat cleanly."""
    _run([ffmpeg_bin(), "-y", "-i", str(src), "-ar", str(rate), "-ac", str(channels), str(dst)])
    return dst


def make_silence(dst: Path, duration: float, rate: int = 48000, channels: int = 2) -> Path:
    """Placeholder audio for an un-narrated slide (keeps the 1:1 pairing contract)."""
    layout = "stereo" if channels == 2 else "mono"
    _run([
        ffmpeg_bin(), "-y", "-f", "lavfi",
        "-i", f"anullsrc=r={rate}:cl={layout}",
        "-t", f"{max(0.05, duration):.3f}", str(dst),
    ])
    return dst


def pad_wav(src: Path, dst: Path, lead: float, tail: float, rate: int = 48000, channels: int = 2) -> Path:
    """Add lead/tail silence so each slide lingers briefly before/after speech."""
    base = probe_duration(src)
    total = lead + base + tail
    lead_ms = int(round(lead * 1000))
    af = f"adelay={lead_ms}:all=1,apad" if lead_ms > 0 else "apad"
    _run([
        ffmpeg_bin(), "-y", "-i", str(src), "-af", af,
        "-ar", str(rate), "-ac", str(channels), "-t", f"{total:.3f}", str(dst),
    ])
    return dst


def extend_to(src: Path, dst: Path, duration: float, rate: int = 48000, channels: int = 2) -> Path:
    """Pad audio with trailing silence to exactly ``duration`` seconds.

    Used for clip-backed (e.g. Manim) slides where the segment runs to
    max(clip_duration, narration_duration) and the narration must fill the gap.
    """
    _run([
        ffmpeg_bin(), "-y", "-i", str(src), "-af", "apad",
        "-ar", str(rate), "-ac", str(channels), "-t", f"{duration:.3f}", str(dst),
    ])
    return dst
