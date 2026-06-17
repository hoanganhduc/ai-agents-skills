"""Offline self-test (CI smoke).

Validates scene-spec round-trips, the generated Manim source, the emphasis
validation, and the manim/ffmpeg argv builders -- with the standard library
only (no manim, no LaTeX, no ffmpeg, no render). Exit 0 = pass, 1 = fail.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from . import render, scenegen
from .model import Emphasis, SceneSpec


class _Checks:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def ok(self, name: str, cond: bool, detail: str = "") -> None:
        self.results.append((name, bool(cond), detail))

    def raises(self, name: str, fn, exc: type) -> None:
        try:
            fn()
            self.results.append((name, False, "expected exception"))
        except exc:
            self.results.append((name, True, ""))
        except Exception as other:
            self.results.append((name, False, f"raised {type(other).__name__}"))

    @property
    def passed(self) -> bool:
        return all(r[1] for r in self.results)


def run_checks() -> _Checks:
    c = _Checks()
    spec = SceneSpec(
        equations=[r"f(x) = x^2 + 2x + 1", r"f(x) = (x + 1)^2"],
        title="Hàm số bậc hai",
        emphases=[Emphasis(at=1, type="circumscribe")],
    )
    c.ok("spec_roundtrip", SceneSpec.from_dict(spec.to_dict()).equations == spec.equations)

    src = scenegen.generate_scene(spec)
    c.ok("gen_import", "from manim import *" in src)
    c.ok("gen_config_fps", "config.frame_rate = 30" in src)
    c.ok("gen_mathtex", "MathTex(" in src)
    c.ok("gen_write_first", "self.play(Write(eq_0)" in src)
    c.ok("gen_transform", "TransformMatchingTex(eq_0, eq_1)" in src)
    c.ok("gen_emphasis", "Circumscribe(eq_1)" in src)
    c.ok("gen_title_pango", "Text(" in src and "font=" in src)
    c.ok("gen_latex_backslash_preserved", "x^2" in src)

    c.raises("bad_emphasis_type", lambda: SceneSpec(equations=["a"], emphases=[Emphasis(0, "nope")]).validate(), ValueError)
    c.raises("emphasis_out_of_range", lambda: SceneSpec(equations=["a"], emphases=[Emphasis(3)]).validate(), ValueError)
    c.raises("empty_equations", lambda: SceneSpec(equations=[]).validate(), ValueError)

    margs = render.build_manim_args("scene.py", "GeneratedScene", "/tmp/m", "-qh", "manim")
    c.ok("manim_args_render", "render" in margs and "GeneratedScene" in margs and "--format=mp4" in margs)
    nargs = render.build_normalize_args("a.mp4", "b.mp4", 1920, 1080, 30, "ffmpeg")
    c.ok("normalize_libx264", "libx264" in nargs)
    c.ok("normalize_silent_audio", "anullsrc=r=48000:cl=stereo" in nargs)
    c.ok("normalize_yuv420p", any("yuv420p" in a for a in nargs))
    return c


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="manim-math-animation selftest")
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)
    if args.work_dir:
        Path(args.work_dir).mkdir(parents=True, exist_ok=True)
    else:
        tempfile.mkdtemp(prefix="mma_selftest_")

    c = run_checks()
    report = {
        "ok": c.passed,
        "passed": sum(1 for _, ok, _ in c.results if ok),
        "total": len(c.results),
        "failures": [{"check": n, "detail": d} for n, ok, d in c.results if not ok],
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    return 0 if c.passed else 1
