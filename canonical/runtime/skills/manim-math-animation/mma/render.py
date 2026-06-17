"""Render a SceneSpec to a normalized, silent interlude clip.

Writes the generated Manim script to a temp file, renders it with the ``manim``
CLI, then normalizes the output with ffmpeg to the canonical slides-to-video
profile (target resolution, fps, yuv420p) and adds a silent 48 kHz stereo AAC
track so the clip concatenates cleanly into a slides-to-video deck.

``build_manim_args`` / ``build_normalize_args`` are pure (argv builders, unit
tested offline); ``render`` runs them. manim + ffmpeg are required at run time.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import scenegen
from .model import SceneSpec


class ToolMissing(RuntimeError):
    pass


def manim_bin() -> str:
    cand = os.environ.get("MANIM") or shutil.which("manim")
    venv = Path(os.path.expanduser("~")) / ".local/share/manim-math-animation-venv/bin/manim"
    if not cand and venv.exists():
        cand = str(venv)
    if not cand:
        raise ToolMissing("manim not found. Run `setup` to create the venv, or install manim.")
    return cand


def ffmpeg_bin() -> str:
    cand = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not cand:
        raise ToolMissing("ffmpeg not found on PATH (LGPL build).")
    return cand


def build_manim_args(script: str, scene: str, media_dir: str,
                     quality: str = "-qh", manim: str = "manim") -> list[str]:
    return [manim, "render", quality, "--format=mp4", "--media_dir", media_dir, script, scene]


def build_normalize_args(src: str, dst: str, width: int, height: int, fps: int,
                         ffmpeg: str = "ffmpeg") -> list[str]:
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p"
    )
    return [
        ffmpeg, "-y", "-i", src,
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-shortest", "-movflags", "+faststart", dst,
    ]


def render(spec: SceneSpec, out_path: str, quality: str = "-qh",
           scene: str = "GeneratedScene") -> Path:
    spec.validate()
    work = Path(tempfile.mkdtemp(prefix="mma_render_"))
    script = work / "scene.py"
    script.write_text(scenegen.generate_scene(spec, scene), encoding="utf-8")
    media_dir = work / "media"
    subprocess.run(build_manim_args(str(script), scene, str(media_dir), quality, manim_bin()),
                   check=True, capture_output=True, text=True)
    produced = sorted(media_dir.glob(f"videos/**/{scene}.mp4"))
    if not produced:
        raise RuntimeError("manim did not produce an output mp4")
    subprocess.run(build_normalize_args(str(produced[-1]), out_path, spec.width, spec.height,
                                        spec.fps, ffmpeg_bin()),
                   check=True, capture_output=True, text=True)
    return Path(out_path)
