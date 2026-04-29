from __future__ import annotations

import sys
from pathlib import Path

from installer.ai_agents_skills.sanitize import has_sensitive_material


SKIP_DIRS = {".git", ".learnings", "__pycache__", "_build"}
ALLOWLIST = {
    Path("installer/ai_agents_skills/sanitize.py"),
    Path("tests/test_sanitization.py"),
}


def main() -> int:
    bad: list[str] = []
    for path in Path(".").rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(".")
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
