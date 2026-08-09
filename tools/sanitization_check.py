from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from installer.ai_agents_skills.sanitize import has_sensitive_material


SKIP_DIRS = {".git", ".learnings", ".venv", "__pycache__", "_build"}
SKIP_PREFIXES = {
    Path(".codex") / "runs",
}
ALLOWLIST = {
    Path("installer/ai_agents_skills/sanitize.py"),
    Path("tests/test_sanitization.py"),
}


def git_ignored_prefixes(root: Path) -> frozenset[Path]:
    """Repo-relative paths git is told to ignore, empty when git cannot answer.

    The check guards what the repository ships, and `.gitignore` already draws
    that line: a local agent settings file or an editor scratch directory is
    never committed, so flagging it only teaches the operator to ignore the
    check. Untracked files stay in scope — they are candidates for the next
    commit. `--directory` collapses a wholly ignored tree into one entry, so
    the result is a set of prefixes rather than of files.
    """
    try:
        completed = subprocess.run(
            [
                "git",
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
                "--directory",
                "-z",
            ],
            cwd=root,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return frozenset()
    return frozenset(
        Path(entry) for entry in completed.stdout.decode("utf-8", "replace").split("\0") if entry
    )


def should_skip_path(path: Path, ignored: frozenset[Path] = frozenset()) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    prefixes = SKIP_PREFIXES | set(ignored)
    return any(path == prefix or prefix in path.parents for prefix in prefixes)


def main() -> int:
    bad: list[str] = []
    ignored = git_ignored_prefixes(Path("."))
    for path in Path(".").rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(".")
        if should_skip_path(rel, ignored):
            continue
        if rel in ALLOWLIST:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if has_sensitive_material(text):
            bad.append(str(rel))

    if bad:
        print("Sensitive material patterns detected:", file=sys.stderr)
        for item in bad:
            print(f"- {item}", file=sys.stderr)
        return 1
    print("sanitization-check=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
