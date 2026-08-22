"""Every environment variable a shipped document names must be one the code reads.

A document that tells an operator to set a variable is making a claim about the
runtime: set this, and behaviour changes.  When no code ever reads the name the
claim is false in the worst way -- it reads as a working control, so a reader
who follows it believes a setting is in force that is not.  `send-email/SKILL.md`
shipped exactly that: it told the reader not to leave a send-email path in a
permanent `AAS_ALLOW_EXTERNAL_SECRETS_FILE` setting, a name no runtime file has
ever read, apparently a copy of the real `AAS_ALLOW_EXTERNAL_RUNTIME_WORKSPACE`
with the tail swapped.

The scan is deliberately blunt: collect every `AAS_*` / `OPENCLAW_*` / `ARL_*`
token out of the shipped Markdown, collect the same out of everything that is
not Markdown, and require the first set to sit inside the second.  Two escapes
are allowed, and each one has to earn its place:

* a dynamic family, where the code builds the name from a provider or agent key
  rather than writing it out -- every family below cites the line that builds
  it, and that citation is itself asserted;
* a literal allowance, for an operator-side shell variable that the snippet
  naming it also defines.

`tests/` is not code for this purpose.  A name that appears only in a test is
still a name no runtime reads, and the defect above carried precisely such a
companion: a `env.pop("AAS_ALLOW_EXTERNAL_SECRETS_FILE", None)` that looked like
coverage while the assertion under it held for an unrelated reason.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

NAME = re.compile(r"\b((?:AAS|OPENCLAW|ARL)_[A-Z0-9_]*[A-Z0-9])\b")

DOC_GLOBS = ("canonical/**/*.md", "docs/**/*.md", "targets/**/*.md", "tools/**/*.md")
DOC_FILES = ("README.md", "SPEC.md")

# Not code: version control and caches carry no claims, `docs/` is generated
# Markdown, and `tests/` is checked as documentation-adjacent, never as a reader.
NON_CODE_TOP_LEVEL = {".git", ".pytest_cache", "__pycache__", "docs", "tests"}

# name pattern -> (file that builds the name, the exact expression that builds it)
DYNAMIC_FAMILIES: tuple[tuple[str, str, str], ...] = (
    (
        r"^AAS_[A-Z0-9]+_DISPATCH_COMMAND$",
        "installer/ai_agents_skills/delegation_dispatch.py",
        'f"AAS_{provider.upper()}_DISPATCH_COMMAND"',
    ),
    (
        r"^AAS_[A-Z0-9]+_HIGHEST_THINKING$",
        "installer/ai_agents_skills/delegation_dispatch.py",
        'env.get(f"AAS_{provider.upper()}_HIGHEST_THINKING")',
    ),
    (
        r"^AAS_AUTOLOOP_ATTESTED_[A-Z0-9]+_[A-Z0-9]+$",
        "canonical/runtime/skills/autonomous-research-loop-runtime/panel_parent.py",
        'env.get(f"AAS_AUTOLOOP_ATTESTED_BIN_{key}")',
    ),
    (
        r"^AAS_AUTOLOOP_ARGS_[A-Z0-9]+$",
        "canonical/runtime/skills/autonomous-research-loop-runtime"
        "/autonomous_research_loop_runtime.py",
        'args_raw = env.get(f"AAS_AUTOLOOP_ARGS_{key}")',
    ),
    (
        r"^AAS_AUTOLOOP_CMD_[A-Z0-9]+$",
        "canonical/runtime/skills/autonomous-research-loop-runtime"
        "/autonomous_research_loop_runtime.py",
        'full = env.get(f"AAS_AUTOLOOP_CMD_{key}")',
    ),
)

# name -> the shipped line that both defines the variable and states its default
ALLOWED_WITHOUT_A_READER = {
    "AAS_FORMAL_ENV_INC": (
        "canonical/templates/sample-arl-headless-driver-with-formal/README.md",
        'FORMAL_ENV="${AAS_FORMAL_ENV_INC:-',
    ),
}


def _doc_paths() -> list[Path]:
    found = {p for glob in DOC_GLOBS for p in ROOT.glob(glob)}
    found.update(ROOT / name for name in DOC_FILES)
    return sorted(p for p in found if p.is_file())


def _code_paths() -> list[Path]:
    return sorted(
        p
        for p in ROOT.rglob("*")
        if p.is_file()
        and p.suffix != ".md"
        and p.relative_to(ROOT).parts[0] not in NON_CODE_TOP_LEVEL
        and "__pycache__" not in p.parts
    )


def _tracked_files() -> list[Path]:
    """Every readable text file in the working tree, caches and history aside."""

    skipped = {".git", ".pytest_cache", "__pycache__"}
    return sorted(
        p
        for p in ROOT.rglob("*")
        if p.is_file() and not skipped.intersection(p.relative_to(ROOT).parts)
    )


def _names_by_file(paths: list[Path]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in NAME.finditer(text):
            found.setdefault(match.group(1), set()).add(
                path.relative_to(ROOT).as_posix()
            )
    return found


def _matches_a_family(name: str) -> bool:
    return any(re.match(pattern, name) for pattern, _, _ in DYNAMIC_FAMILIES)


def _unread(documented: dict[str, set[str]], in_code: set[str]) -> dict[str, set[str]]:
    return {
        name: sites
        for name, sites in documented.items()
        if name not in in_code
        and not _matches_a_family(name)
        and name not in ALLOWED_WITHOUT_A_READER
    }


class DocumentedEnvVarsAreReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documented = _names_by_file(_doc_paths())
        self.in_code = set(_names_by_file(_code_paths()))

    def test_no_shipped_document_names_a_variable_nothing_reads(self) -> None:
        unread = _unread(self.documented, self.in_code)
        self.assertEqual(
            {},
            unread,
            "documents name variables no non-Markdown file reads; either the "
            "code lost the reader or the document invented the name: "
            + "; ".join(
                f"{name} in {sorted(sites)[0]}" for name, sites in sorted(unread.items())
            ),
        )

    def test_the_scan_is_not_vacuous(self) -> None:
        """A pass has to mean something: both sides must be populated, and a
        variable known to be real must be found on both."""

        self.assertGreater(len(self.documented), 50, sorted(self.documented))
        self.assertGreater(len(self.in_code), 100, len(self.in_code))
        for real in ("AAS_RUNTIME_ROOT", "AAS_ALLOW_RAW_NOTIFY_CMD"):
            self.assertIn(real, self.documented, f"{real} left the documents")
            self.assertIn(real, self.in_code, f"{real} lost its reader")

    def test_the_shipped_defect_would_be_caught(self) -> None:
        """The sentence removed from send-email/SKILL.md, replayed."""

        shipped = (
            "Do not put a send-email-only path in global `AAS_SECRETS_FILE` or "
            "permanent\n`AAS_ALLOW_EXTERNAL_SECRETS_FILE` settings."
        )
        names = {match.group(1) for match in NAME.finditer(shipped)}
        self.assertIn("AAS_ALLOW_EXTERNAL_SECRETS_FILE", names)
        replayed = dict(self.documented)
        replayed["AAS_ALLOW_EXTERNAL_SECRETS_FILE"] = {
            "canonical/skills/send-email/SKILL.md"
        }
        self.assertIn(
            "AAS_ALLOW_EXTERNAL_SECRETS_FILE", _unread(replayed, self.in_code)
        )

    def test_the_removed_name_is_gone_from_the_whole_tree(self) -> None:
        """Wider than the scan above on purpose.

        The phantom shipped in two places, and the second was a test that
        `env.pop`-ed the name -- coverage-shaped, but the assertion under it held
        because the launcher unsets every ambient pointer regardless.  `tests/`
        is outside the doc-versus-code comparison, so this sweep reads every
        tracked file instead, excluding only this module, which has to name the
        string to test for it.
        """

        me = Path(__file__).resolve()
        surviving = [
            path.relative_to(ROOT).as_posix()
            for path in _tracked_files()
            if path.resolve() != me
            and "AAS_ALLOW_EXTERNAL_SECRETS_FILE"
            in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual([], surviving)

    def test_the_whole_tree_sweep_reaches_the_two_places_it_shipped(self) -> None:
        """Non-vacuity for the sweep above: both former sites are still read."""

        reached = {path.relative_to(ROOT).as_posix() for path in _tracked_files()}
        self.assertIn("canonical/skills/send-email/SKILL.md", reached)
        self.assertIn("tests/test_runtime_integration.py", reached)

    def test_every_dynamic_family_cites_a_line_that_still_builds_it(self) -> None:
        for pattern, path, expression in DYNAMIC_FAMILIES:
            with self.subTest(pattern=pattern):
                source = (ROOT / path).read_text(encoding="utf-8")
                self.assertIn(expression, source, f"{path} no longer builds {pattern}")

    def test_every_escape_is_load_bearing(self) -> None:
        """An escape that covers nothing is an escape that should be deleted."""

        for pattern, _, _ in DYNAMIC_FAMILIES:
            with self.subTest(pattern=pattern):
                covered = [
                    name
                    for name in self.documented
                    if re.match(pattern, name) and name not in self.in_code
                ]
                self.assertTrue(covered, f"no documented name needs {pattern}")
        for name, (path, needle) in ALLOWED_WITHOUT_A_READER.items():
            with self.subTest(name=name):
                self.assertIn(name, self.documented, f"{name} is documented nowhere")
                self.assertNotIn(name, self.in_code, f"{name} has a reader; drop it")
                self.assertIn(
                    needle,
                    (ROOT / path).read_text(encoding="utf-8"),
                    f"{path} no longer defines {name}",
                )

    def test_a_family_does_not_swallow_an_unrelated_name(self) -> None:
        """The patterns are anchored, so a near-miss is still reported."""

        for near_miss in (
            "AAS_ALLOW_EXTERNAL_SECRETS_FILE",
            "AAS_AUTOLOOP_ATTESTED_BIN",
            "AAS_DISPATCH_COMMAND",
            "AAS_AUTOLOOP_CMD",
        ):
            with self.subTest(name=near_miss):
                self.assertFalse(_matches_a_family(near_miss), near_miss)


if __name__ == "__main__":
    unittest.main()
