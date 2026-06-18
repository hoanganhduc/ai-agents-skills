#!/usr/bin/env python3
"""slides-to-video runtime dispatcher.

Subcommands:
  setup      create the dedicated venv and install requirements (explicit, user-run)
  doctor     report environment readiness (ffmpeg, fonts, packages); installs nothing
  analyze    Phase 1: ingest slides -> deck.json + config.json + slides/
  draft      Phase 2: scaffold transcript.json/.md from the deck
  verbalize  Phase 2: rewrite math notation to speakable words (invalidates approval)
  status     show pipeline state
  approve    pin the current transcript SHA and unlock rendering
  render     Phase 3: synth -> measure -> per-slide clips -> concat -> captions (gated)
  selftest   offline smoke (no network/ffmpeg/venv)

Invoke via the managed runner, e.g.:
  bash ~/.codex/runtime/run_skill.sh skills/slides-to-video/run_slides_to_video.sh doctor
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
    env = os.environ.get("S2V_VENV")
    if env:
        return Path(env)
    return Path(os.path.expanduser("~")) / ".local" / "share" / "slides-to-video-venv"


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
           "note": "the run script auto-selects this venv on future commands"})
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    from s2v import doctor

    return doctor.main([])


def cmd_analyze(args: argparse.Namespace) -> int:
    from s2v.model import Config
    from s2v import orchestrator

    config = Config(language=args.language, role=args.role, engine_policy=args.engine_policy,
                    resolution=args.resolution, fps=args.fps, voice=args.voice,
                    burn_captions=args.burn_captions)
    _emit(orchestrator.analyze(Path(args.input), Path(args.work_dir), config))
    return 0


def cmd_draft(args: argparse.Namespace) -> int:
    from s2v import orchestrator

    _emit(orchestrator.draft(Path(args.work_dir)))
    return 0


def cmd_verbalize(args: argparse.Namespace) -> int:
    from s2v import orchestrator

    _emit(orchestrator.verbalize_transcripts(Path(args.work_dir)))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from s2v import orchestrator

    _emit(orchestrator.status(Path(args.work_dir)))
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    from s2v import orchestrator

    _emit(orchestrator.approve(Path(args.work_dir)))
    return 0


def cmd_add_interlude(args: argparse.Namespace) -> int:
    from s2v import orchestrator

    _emit(orchestrator.add_interlude(Path(args.work_dir), args.clip, args.after,
                                     transcript=args.transcript or "", language=args.language))
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    from s2v import orchestrator

    try:
        _emit(orchestrator.render(Path(args.work_dir)))
    except orchestrator.ApprovalRequired as exc:
        _emit({"error": "approval_required", "message": str(exc)})
        return 2
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    from s2v import selftest

    forwarded: list[str] = []
    if args.work_dir:
        forwarded += ["--work-dir", args.work_dir]
    if args.output:
        forwarded += ["--output", args.output]
    return selftest.main(forwarded)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slides-to-video")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup").set_defaults(func=cmd_setup)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)

    a = sub.add_parser("analyze")
    a.add_argument("--input", required=True, help="PNG directory, .pdf, or .pptx")
    a.add_argument("--work-dir", required=True)
    a.add_argument("--language", default="en-US")
    a.add_argument("--role", default="presenter")
    a.add_argument("--engine-policy", default="auto", dest="engine_policy")
    a.add_argument("--resolution", default="1920x1080")
    a.add_argument("--fps", type=int, default=30)
    a.add_argument("--voice", default=None)
    a.add_argument("--burn-captions", action="store_true", dest="burn_captions")
    a.set_defaults(func=cmd_analyze)

    for name, func in (("draft", cmd_draft), ("verbalize", cmd_verbalize),
                       ("status", cmd_status), ("approve", cmd_approve), ("render", cmd_render)):
        p = sub.add_parser(name)
        p.add_argument("--work-dir", required=True)
        p.set_defaults(func=func)

    ai = sub.add_parser("add-interlude", help="insert a pre-rendered (e.g. Manim) clip as a slide")
    ai.add_argument("--work-dir", required=True)
    ai.add_argument("--clip", required=True, help="path to the rendered video clip")
    ai.add_argument("--after", type=int, required=True, help="insert after this slide index (-1 = front)")
    ai.add_argument("--transcript", default=None, help="narration for the interlude (optional)")
    ai.add_argument("--language", default=None)
    ai.set_defaults(func=cmd_add_interlude)

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
