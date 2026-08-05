#!/usr/bin/env python3
"""Offline strict-loader probe for skill, compute, and provider child projections."""
from __future__ import annotations

import argparse
import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path


# The probe imports the fixed strict loader by path. Installed runtime source trees are
# immutable candidates, so that import must never synthesize __pycache__ beside them.
sys.dont_write_bytecode = True


LANE_POINTERS = {
    "skill": "AAS_SKILL_SECRETS_FILE",
    "compute": "AAS_COMPUTE_SECRETS_FILE",
    "provider": "AAS_PROVIDER_SECRETS_FILE",
}
SKILL_KEYS = frozenset({
    "AXLE_API_KEY", "LEANEXPLORE_API_KEY", "OCR_SPACE_API_KEY", "OCR_SPACE_KEY",
    "OCRSPACE_API_KEY", "OCRSPACE_KEY", "OPENCLAW_S2_API_KEY",
    "SEMANTIC_SCHOLAR_API_KEY", "UNPAYWALL_EMAIL", "ZENODO_TOKEN",
})
COMPUTE_KEYS = frozenset({
    "HCLOUD_TOKEN", "HCLOUD_SSH_KEYS", "KAGGLE_API_TOKEN", "KAGGLE_CONFIG_DIR",
})
PROVIDER_ARL_KEYS = frozenset({
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN", "COPILOT_GITHUB_TOKEN", "COPILOT_PROVIDER_API_KEY",
    "COPILOT_PROVIDER_BEARER_TOKEN", "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "GH_TOKEN",
    "GITHUB_TOKEN", "GOOGLE_API_KEY", "GROK_API_KEY", "KIMI_API_KEY",
    "MOONSHOT_API_KEY", "OPENAI_API_KEY", "OPENCODE_API_KEY", "XAI_API_KEY",
})
PROVIDER_COPILOT_KEYS = frozenset({
    "COPILOT_GITHUB_TOKEN", "COPILOT_PROVIDER_API_KEY",
    "COPILOT_PROVIDER_BEARER_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
})
ALL_KEYS = SKILL_KEYS | COMPUTE_KEYS | PROVIDER_ARL_KEYS


class _QuietParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _fail(lane: str | None, reason: str, *, code: int = 1) -> int:
    prefix = f" lane={lane}" if lane else ""
    print(f"FAIL{prefix} reason={reason}")
    return code


def _trusted_adjacent_file(path: Path, expected: Path) -> bool:
    try:
        supplied = path.lstat()
        canonical = expected.lstat()
    except OSError:
        return False
    if (
        not path.is_absolute()
        or path != expected
        or not stat.S_ISREG(supplied.st_mode)
        or stat.S_ISLNK(supplied.st_mode)
        or int(supplied.st_nlink) != 1
        or (supplied.st_dev, supplied.st_ino) != (canonical.st_dev, canonical.st_ino)
    ):
        return False
    if os.name == "posix":
        return int(supplied.st_uid) in {0, os.geteuid()} and not (
            stat.S_IMODE(supplied.st_mode) & 0o022
        )
    return True


def _load_strict_loader(path: Path):
    spec = importlib.util.spec_from_file_location("aas_projection_strict_loader", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("loader-unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_parser() -> argparse.ArgumentParser:
    parser = _QuietParser(description=__doc__)
    parser.add_argument("--lane", choices=tuple(LANE_POINTERS), required=True)
    parser.add_argument(
        "--provider-profile", choices=("arl", "copilot"), default=None,
        help="required for provider lane; selects the exact authority schema",
    )
    parser.add_argument("--checker", required=True)
    parser.add_argument("--expect-key", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        # argparse uses SystemExit(0) for the normal --help control path.  Keep
        # that successful path distinct from malformed arguments so automation
        # never sees both a help banner and a contradictory failure record.
        if exc.code == 0:
            return 0
        return _fail(None, "invalid-arguments", code=2)
    except ValueError:
        return _fail(None, "invalid-arguments", code=2)
    lane = str(args.lane)
    if os.name == "nt":
        # Native Windows authorities require the handle-bound owner/DACL implementation in
        # load_secret_env.ps1. This Python probe must not silently downgrade that boundary.
        return _fail(lane, "native-windows-requires-powershell-loader", code=3)
    if (lane == "provider") != bool(args.provider_profile):
        return _fail(lane, "provider-profile-mismatch", code=2)
    allowed = (
        PROVIDER_COPILOT_KEYS if args.provider_profile == "copilot"
        else PROVIDER_ARL_KEYS if args.provider_profile == "arl"
        else COMPUTE_KEYS if lane == "compute"
        else SKILL_KEYS
    )
    expected_keys = frozenset(str(key) for key in args.expect_key)
    if not expected_keys or not expected_keys.issubset(allowed):
        return _fail(lane, "invalid-expected-keys", code=2)
    runtime_dir = Path(__file__).resolve().parent
    checker = Path(str(args.checker))
    fixed_checker = runtime_dir / "credential_projection_check.py"
    adjacent_loader = runtime_dir / "load_secret_env.py"
    installed_loader = runtime_dir.parent / "load_secret_env.py"
    fixed_loader = installed_loader if installed_loader.is_file() else adjacent_loader
    if not _trusted_adjacent_file(checker, fixed_checker):
        return _fail(lane, "untrusted-checker", code=2)
    if not _trusted_adjacent_file(fixed_loader, fixed_loader):
        return _fail(lane, "untrusted-loader", code=2)
    pointer = LANE_POINTERS[lane]
    try:
        loader = _load_strict_loader(fixed_loader)
        loaded = loader.load_pointer_secret_env(pointer, allowed_keys=allowed)
    except Exception:  # The strict loader's detailed error can contain metadata; keep output stable.
        return _fail(lane, "authority-rejected")
    if not loaded or not any(loaded.get(key) for key in expected_keys):
        return _fail(lane, "expected-key-missing")
    child_env = {
        key: value for key, value in os.environ.items()
        if key in {"LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP", "TZ", "WINDIR"}
        or key.startswith("LC_")
    }
    child_env.update({key: value for key, value in loaded.items() if key in allowed})
    command = [
        sys.executable, "-I", str(fixed_checker), "--lane", lane,
    ]
    for key in sorted(expected_keys):
        command.extend(("--expect-any-key", key))
    for key in sorted(ALL_KEYS - allowed):
        command.extend(("--forbid-key", key))
    try:
        result = subprocess.run(
            command, env=child_env, capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return _fail(lane, "checker-launch-failed")
    output = (result.stdout or "").strip()
    expected_output = f"PASS lane={lane}"
    if result.returncode == 0 and output == expected_output and not result.stderr:
        print(expected_output)
        return 0
    reason = "checker-rejected"
    if output.startswith(f"FAIL lane={lane} reason="):
        candidate = output.rsplit("reason=", 1)[-1]
        if candidate in {"pointer-leak", "unrelated-key-leak", "expected-key-missing"}:
            reason = candidate
    return _fail(lane, reason)


if __name__ == "__main__":
    raise SystemExit(main())
