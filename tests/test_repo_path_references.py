"""Regression for repo-relative paths named in documentation that do not exist.

A backticked path in a doc reads as an instruction: open this file. When the
file is not there the reader has no way to tell a typo from a path they lack
permission to see, so they go looking. Two such references shipped — one baked
into the generated docs by `installer/ai_agents_skills/docs.py`, one hand-written
in `targets/openclaw/README.md` — and both pointed at the same non-existent
`canonical/runtime/skills/remote-bridge/openclaw-adapter/README.md`.

The scan runs over two surfaces because the defect appeared once in each: the
in-memory `render_docs` output, so a bad edit to `docs.py` fails before `make
docs` writes it out, and the markdown checked into the tree, which `make docs`
never touches.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from installer.ai_agents_skills.docs import render_docs
from installer.ai_agents_skills.manifest import load_manifests


ROOT = Path(__file__).resolve().parents[1]

# Version-control internals and build residue: never referenced as documentation.
TRANSIENT_TOP_LEVEL = {".git", ".pytest_cache", "__pycache__"}

# First segments that name somebody else's tree, not this one. `.github/*` is
# discussed as a layout that exists in a user's own repositories, and
# `.learnings/*` is created at runtime by self-improving-agent from templates
# (canonical/skills/self-improving-agent/SKILL.md step 2 says so explicitly).
EXTERNAL_FIRST_SEGMENTS = {".github", ".learnings"}

# Paths that are named on purpose while absent. Each entry carries the reason it
# is not a broken reference; an entry that stops being justified should be
# deleted, not left to rot.
ALLOWED_ABSENT = {
    # docs/course-management.md attributes this to the upstream toolkit on the
    # line above: "README and `docs/usage.rst` in that repository".
    "docs/usage.rst",
    # docs/submission-venue-selector-plan.md offers this "for example" under a
    # required OpenClaw enablement phase that is not implemented yet.
    "manifest/openclaw/target-support-files/submission-venue-selector.json",
}

# Markdown whose path claims are load-bearing. tasks/ is deliberately out of
# scope: its unchecked entries list the files a future task will create.
DISK_GLOBS = ("docs/**/*.md", "canonical/**/*.md", "targets/**/*.md", "tools/**/*.md")
DISK_FILES = ("README.md", "SPEC.md")

# A backticked span, or the target of a markdown link.
TOKEN = re.compile(r"`([^`\n]+)`|\]\(([^)\s]+)\)")

# Shell globs, placeholders and prose punctuation: not literal paths.
REJECTED_CHARS = set(" \t*<>{}|\\?\"'")
REJECTED_PREFIXES = ("/", "~", "$", "%", "-", "http", "#")


def top_level_names() -> set[str]:
    return {p.name for p in ROOT.iterdir() if p.name not in TRANSIENT_TOP_LEVEL}


def looks_like_repo_path(token: str, top: set[str]) -> bool:
    """Whether a token claims a path inside this repository.

    Anchoring on a real top-level name is what keeps the scan precise: `pip/foo`
    and `owner/repo` are not path claims. The cost is that a typo in the first
    segment itself reads as somebody else's path and is not checked.
    """
    if not token or any(c in REJECTED_CHARS for c in token):
        return False
    if token.startswith(REJECTED_PREFIXES):
        return False
    if "/" not in token:
        return False
    parts = token.split("/")
    if "..." in parts or "." in parts or ".." in parts:
        return False
    head = parts[0]
    return head in top and head not in EXTERNAL_FIRST_SEGMENTS


def path_claims(text: str, top: set[str]) -> set[str]:
    found = set()
    for match in TOKEN.finditer(text):
        token = (match.group(1) or match.group(2)).strip().rstrip(".,;:")
        if looks_like_repo_path(token, top):
            found.add(token)
    return found


def absent(claims: set[str]) -> set[str]:
    return {c for c in claims if c not in ALLOWED_ABSENT and not (ROOT / c).exists()}


class RepoPathReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.top = top_level_names()

    def _scan_disk(self) -> dict[str, set[str]]:
        by_file: dict[str, set[str]] = {}
        paths = [p for glob in DISK_GLOBS for p in ROOT.glob(glob)]
        paths += [ROOT / name for name in DISK_FILES]
        for path in sorted(set(paths)):
            if not path.is_file():
                continue
            claims = path_claims(path.read_text(encoding="utf-8"), self.top)
            if claims:
                by_file[path.relative_to(ROOT).as_posix()] = claims
        return by_file

    def _scan_rendered(self) -> dict[str, set[str]]:
        by_file: dict[str, set[str]] = {}
        for path, text in render_docs(load_manifests()).items():
            claims = path_claims(text, self.top)
            if claims:
                by_file[path.relative_to(ROOT).as_posix()] = claims
        return by_file

    def test_generated_docs_name_only_paths_that_exist(self) -> None:
        broken = {f: sorted(absent(c)) for f, c in self._scan_rendered().items() if absent(c)}
        self.assertEqual({}, broken, broken)

    def test_checked_in_markdown_names_only_paths_that_exist(self) -> None:
        broken = {f: sorted(absent(c)) for f, c in self._scan_disk().items() if absent(c)}
        self.assertEqual({}, broken, broken)

    def test_the_scan_reaches_both_surfaces(self) -> None:
        """A zero result only means something if the scan found claims to check."""

        rendered = self._scan_rendered()
        disk = self._scan_disk()
        self.assertGreater(len(rendered), 5, sorted(rendered))
        self.assertGreater(len({c for cs in rendered.values() for c in cs}), 40)
        self.assertGreater(len(disk), 30, sorted(disk))
        self.assertGreater(len({c for cs in disk.values() for c in cs}), 80)
        self.assertIn("targets/openclaw/README.md", disk)
        self.assertIn("README.md", rendered)

    def test_the_two_shipped_defects_would_be_caught(self) -> None:
        adapter = "canonical/runtime/skills/remote-bridge/openclaw-adapter/README.md"
        self.assertTrue(looks_like_repo_path(adapter, self.top))
        self.assertEqual({adapter}, absent(path_claims(f"See `{adapter}`.", self.top)))
        self.assertEqual(set(), absent(path_claims("See `targets/openclaw/README.md`.", self.top)))

    def test_the_allowlist_stays_honest(self) -> None:
        """An allowlisted path that starts existing is a stale exemption."""

        for entry in ALLOWED_ABSENT:
            self.assertTrue(looks_like_repo_path(entry, self.top), entry)
            self.assertFalse((ROOT / entry).exists(), entry)

    def test_prose_and_commands_are_not_read_as_path_claims(self) -> None:
        for token in (
            "pip/install",
            "hoanganhduc/ai-agents-skills",
            "/usr/local/libexec",
            "~/.claude/skills",
            "$HOME/.local/share",
            "canonical/runtime/*/run_skill.sh",
            "canonical/runtime/.../autoloop_driver.sh",
            "make docs",
            "docs.py",
        ):
            self.assertFalse(looks_like_repo_path(token, self.top), token)


if __name__ == "__main__":
    unittest.main()
