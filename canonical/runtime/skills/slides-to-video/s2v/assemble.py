"""Assemble per-slide clips and concatenate them losslessly.

Per-slide-segment strategy: each slide becomes a self-contained clip whose video
duration equals its measured audio duration, so A/V correctness is local and the
final concat is a stream copy with ~zero drift. ``build_clip_args`` /
``build_concat_args`` return argv lists (pure, unit-testable); ``render_clip`` /
``concat_clips`` run them. ffmpeg is required at run time only.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import effects
from .audio import ffmpeg_bin
from .model import EffectSpec


def build_clip_args(image: str, audio: str, duration: float, width: int, height: int,
                    fps: int, effs: list[EffectSpec], out: str, ffmpeg: str = "ffmpeg") -> list[str]:
    """Argv to encode one normalized clip (still image + audio) at exact duration."""
    vf = effects.build_filtergraph(width, height, fps, duration, effs)
    return [
        ffmpeg, "-y",
        "-loop", "1", "-framerate", str(fps), "-i", image,
        "-i", audio,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-tune", "stillimage",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", f"{duration:.3f}", "-movflags", "+faststart", out,
    ]


def build_clip_segment_args(clip: str, audio: str, duration: float, width: int, height: int,
                            fps: int, out: str, ffmpeg: str = "ffmpeg") -> list[str]:
    """Argv to normalize a pre-rendered (e.g. Manim) video clip into one segment.

    The clip is scaled/padded to the canonical frame and its last frame is frozen
    (``tpad``) so the segment runs to exactly ``duration`` = max(clip, narration);
    the narration audio (already padded to ``duration``) is muxed in. Output uses
    the same codec/profile as image segments so the final concat stays a stream copy.
    """
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},"
        f"format=yuv420p,tpad=stop_mode=clone:stop_duration=3600"
    )
    return [
        ffmpeg, "-y", "-i", clip, "-i", audio,
        "-filter_complex", f"[0:v]{vf}[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", f"{duration:.3f}", "-movflags", "+faststart", out,
    ]


def build_concat_args(list_file: str, out: str, ffmpeg: str = "ffmpeg") -> list[str]:
    """Argv for the lossless concat-demuxer join (all clips share one profile)."""
    return [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_file,
            "-c", "copy", "-movflags", "+faststart", out]


def build_burn_args(video: str, subs: str, out: str, font: str, fonts_dir: str | None = None,
                    fps: int = 30, ffmpeg: str = "ffmpeg") -> list[str]:
    """Argv to burn captions with libass, forcing a Vietnamese-covering font."""
    style = f"subtitles={subs}:force_style='FontName={font},Fontsize=24'"
    if fonts_dir:
        style += f":fontsdir={fonts_dir}"
    return [ffmpeg, "-y", "-i", video, "-vf", style,
            "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart", out]


def write_concat_list(clips: list[str], list_file: Path) -> Path:
    lines = [f"file '{Path(c).as_posix()}'" for c in clips]
    Path(list_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Path(list_file)


def render_clip(image: str, audio: str, duration: float, width: int, height: int,
                fps: int, effs: list[EffectSpec], out: str) -> Path:
    argv = build_clip_args(image, audio, duration, width, height, fps, effs, out, ffmpeg_bin())
    subprocess.run(argv, check=True, capture_output=True, text=True)
    return Path(out)


def render_clip_segment(clip: str, audio: str, duration: float, width: int, height: int,
                        fps: int, out: str) -> Path:
    argv = build_clip_segment_args(clip, audio, duration, width, height, fps, out, ffmpeg_bin())
    subprocess.run(argv, check=True, capture_output=True, text=True)
    return Path(out)


def concat_clips(clips: list[str], out: str, work_dir: Path) -> Path:
    list_file = write_concat_list(clips, Path(work_dir) / "clips.txt")
    subprocess.run(build_concat_args(str(list_file), out, ffmpeg_bin()),
                   check=True, capture_output=True, text=True)
    return Path(out)
