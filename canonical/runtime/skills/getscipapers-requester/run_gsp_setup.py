#!/usr/bin/env python3
"""getscipapers-requester runtime setup dispatcher.

Subcommands:
  setup    create the dedicated venv and install the getscipapers fork
           (explicit, user-run; tracks the main branch of the fork)
  doctor   report whether the getscipapers binary is resolvable; installs nothing

The venv is owned by this skill's runtime, not the installer. It is created at
``~/.getscipapers_venv`` (override with the ``GETSCIPAPERS_VENV`` environment
variable). The console script is then resolvable at
``<venv>/bin/getscipapers`` on Linux/macOS or
``<venv>\\Scripts\\getscipapers.exe`` on Windows, which the run scripts export
as ``GETSCIPAPERS_BIN`` and ``gsp_openclaw_helper.find_getscipapers`` discovers.

Invoke via the managed runner, e.g.:
  bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/getscipapers_requester/run_gsp_setup.py setup
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _venv_dir() -> Path:
    env = os.environ.get("GETSCIPAPERS_VENV")
    if env:
        return Path(env)
    return Path(os.path.expanduser("~")) / ".getscipapers_venv"


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")


def _venv_getscipapers(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin") / ("getscipapers.exe" if os.name == "nt" else "getscipapers")


def cmd_setup(_args: argparse.Namespace) -> int:
    venv = _venv_dir()
    req = HERE / "requirements.txt"
    print(f"[setup] creating venv at {venv}", file=sys.stderr)
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    py = _venv_python(venv)
    subprocess.run([str(py), "-m", "ensurepip", "--upgrade"], check=True)
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([str(py), "-m", "pip", "install", "-r", str(req)], check=True)
    binary = _venv_getscipapers(venv)
    _emit({
        "venv": str(venv),
        "python": str(py),
        "getscipapers": str(binary),
        "note": "the run scripts export GETSCIPAPERS_BIN to this binary on future commands",
    })
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    venv = _venv_dir()
    binary = _venv_getscipapers(venv)
    env_bin = os.environ.get("GETSCIPAPERS_BIN")
    resolved = None
    for candidate in (env_bin, str(binary)):
        if candidate and Path(candidate).is_file():
            resolved = candidate
            break
    _emit({
        "venv": str(venv),
        "venv_exists": venv.exists(),
        "expected_binary": str(binary),
        "getscipapers_bin_env": env_bin,
        "resolved": resolved,
        "ready": bool(resolved),
    })
    return 0 if resolved else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="getscipapers-requester-setup")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup").set_defaults(func=cmd_setup)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
