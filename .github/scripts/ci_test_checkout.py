"""Run POSIX CI checks from the same commit at an account-neutral source path.

Reference adapters legitimately name sources outside a fake target home. A
GitHub checkout under /home or /Users would embed the runner account in those
adapters. A real temporary checkout keeps references usable without changing
the renderer or relaxing the leak checks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.name != "posix":
        print("ci_test_checkout requires a POSIX GitHub Actions job", file=sys.stderr)
        return 2
    command = list(sys.argv[1:] if argv is None else argv)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        print("ci_test_checkout requires -- <command> [args...]", file=sys.stderr)
        return 2

    source = Path(__file__).resolve().parents[2]
    try:
        # Never silently discard a CI step's tracked source edits by cloning HEAD.
        git_output(source, "diff", "--exit-code", "HEAD", "--")
        expected_sha = git_output(source, "rev-parse", "HEAD")
        # Resolve /tmp first: on macOS it is an alias for /private/tmp.
        with tempfile.TemporaryDirectory(prefix="aas-ci-", dir=Path("/tmp").resolve()) as tmp:
            checkout = Path(tmp) / "repo"
            git_output(source, "clone", "--no-hardlinks", "--no-checkout", "--", str(source), str(checkout))
            git_output(checkout, "checkout", "--detach", expected_sha)
            if git_output(checkout, "rev-parse", "HEAD") != expected_sha:
                raise ValueError("CI test checkout does not match the source commit")
            env = dict(os.environ)
            env["AAS_PYTHON"] = sys.executable
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            print(json.dumps({
                "ci_test_sha": expected_sha,
                "ci_test_checkout": str(checkout),
                "python": sys.executable,
                "command": command,
            }), flush=True)
            code = subprocess.run(command, cwd=checkout, env=env, check=False).returncode
            return code if code >= 0 else 128 - code
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"CI test checkout failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
