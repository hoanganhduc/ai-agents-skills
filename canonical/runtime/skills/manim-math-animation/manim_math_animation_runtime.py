#!/usr/bin/env python3
"""manim-math-animation runtime dispatcher.

Subcommands:
  setup     create the dedicated venv and install manim (explicit, user-run)
  doctor    report environment readiness (manim, LaTeX, dvisvgm, ffmpeg); installs nothing
  gen       scene-spec JSON -> generated Manim .py (no render)
  render    scene-spec JSON -> silent, normalized interlude clip (needs manim + ffmpeg)
  selftest  offline smoke (no network/manim/LaTeX/ffmpeg)

Invoke via the managed runner, e.g.:
  bash ~/.local/share/ai-agents-skills/runtime/run_skill.sh \
    skills/manim-math-animation/run_manim_math_animation.sh doctor
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _venv_dir() -> Path:
    env = os.environ.get("MMA_VENV")
    if env:
        return Path(env)
    return Path(os.path.expanduser("~")) / ".local" / "share" / "manim-math-animation-venv"


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")


def cmd_setup(_args: argparse.Namespace) -> int:
    venv = _venv_dir()
    req = HERE / "requirements.txt"
    print(f"[setup] creating venv at {venv}", file=sys.stderr)
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    py = _venv_python(venv)
    subprocess.run([str(py), "-m", "ensurepip", "--upgrade"], check=True)
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([str(py), "-m", "pip", "install", "-r", str(req)], check=True)
    _emit({"venv": str(venv), "python": str(py),
           "note": "Manim also needs LaTeX (with dvisvgm + standalone/preview), cairo/pango, and ffmpeg as system tools."})
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    from mma import doctor

    return doctor.main([])


def cmd_gen(args: argparse.Namespace) -> int:
    from mma import scenegen
    from mma.model import SceneSpec

    spec = SceneSpec.from_dict(json.loads(Path(args.spec).read_text(encoding="utf-8")))
    source = scenegen.generate_scene(spec)
    Path(args.output).write_text(source, encoding="utf-8")
    _emit({"script": args.output, "equations": len(spec.equations)})
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    from mma import render
    from mma.model import SceneSpec

    spec = SceneSpec.from_dict(json.loads(Path(args.spec).read_text(encoding="utf-8")))
    out = render.render(spec, args.output, quality=args.quality)
    _emit({"clip": str(out), "equations": len(spec.equations), "quality": args.quality})
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    from mma import selftest

    forwarded: list[str] = []
    if args.work_dir:
        forwarded += ["--work-dir", args.work_dir]
    if args.output:
        forwarded += ["--output", args.output]
    return selftest.main(forwarded)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="manim-math-animation")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup").set_defaults(func=cmd_setup)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)

    g = sub.add_parser("gen")
    g.add_argument("--spec", required=True)
    g.add_argument("--output", required=True)
    g.set_defaults(func=cmd_gen)

    r = sub.add_parser("render")
    r.add_argument("--spec", required=True)
    r.add_argument("--output", required=True)
    r.add_argument("--quality", default="-qh")
    r.set_defaults(func=cmd_render)

    st = sub.add_parser("selftest")
    st.add_argument("--work-dir", default=None)
    st.add_argument("--output", default=None)
    st.set_defaults(func=cmd_selftest)
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
