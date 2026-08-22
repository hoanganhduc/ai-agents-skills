"""Regression for dependency records that under-report who needs the package.

`manifest/system-dependencies.yaml` is rendered verbatim into the last column of
the table in `docs/dependencies.md`, so `used_by` is what an operator reads to
decide whether a package is their concern. Three records named fewer consumers
than the code has: `tomli` was described as a Docling concern while the ARL
broker and every research-compute lane also parse TOML, and `PyMuPDF` and
`numpy` omitted slides-to-video, which pins both in its own venv requirements
and imports them at `s2v/ingest.py` and `s2v/tts.py`.

Scope and its limit: attribution is automatic only where a skill directory owns
the import. `canonical/runtime/runners/` and `canonical/runtime/workspace/` are
shared by several lanes and belong to no single skill, so what they import is
reported here for a human to attribute rather than asserted against a slug.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SKILLS = ROOT / "canonical" / "runtime" / "skills"
SHARED_TREES = ("canonical/runtime/runners", "canonical/runtime/workspace")

# A top-level `import x` / `from x import ...`, at any indentation: several of
# these imports are deliberately deferred into the function that needs them.
IMPORT = re.compile(r"^[ \t]*(?:from[ \t]+([A-Za-z_][\w.]*)|import[ \t]+([A-Za-z_][\w.]*))", re.M)

# Packages whose importing skill is a re-export or vendored copy rather than a
# consumer. Empty today; an entry needs a reason on the line above it.
EXEMPT: dict[str, set[str]] = {}


def top_level_imports(tree: Path) -> set[str]:
    modules: set[str] = set()
    for source in tree.rglob("*.py"):
        text = source.read_text(encoding="utf-8", errors="replace")
        for match in IMPORT.finditer(text):
            modules.add((match.group(1) or match.group(2)).split(".")[0])
    return modules


def declared_packages() -> dict[str, dict]:
    raw = (ROOT / "manifest" / "system-dependencies.yaml").read_text(encoding="utf-8")
    return json.loads(raw)["python_packages"]


def import_name(package: str, record: dict) -> str:
    return record.get("import_name") or package.replace("-", "_")


class DeclaredDependencyConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.packages = declared_packages()
        cls.by_skill = {
            d.name: top_level_imports(d) for d in sorted(RUNTIME_SKILLS.iterdir()) if d.is_dir()
        }

    def _unnamed(self) -> dict[str, list[str]]:
        gaps: dict[str, list[str]] = {}
        for package, record in self.packages.items():
            module = import_name(package, record)
            declared = " ".join(str(entry) for entry in record.get("used_by", []))
            exempt = EXEMPT.get(package, set())
            missing = [
                skill
                for skill, modules in self.by_skill.items()
                if module in modules and skill not in exempt and skill not in declared
            ]
            if missing:
                gaps[package] = sorted(missing)
        return gaps

    def test_every_importing_skill_is_named_in_used_by(self) -> None:
        self.assertEqual({}, self._unnamed(), self._unnamed())

    def test_the_scan_reaches_the_skills_and_packages_it_claims_to(self) -> None:
        """A zero result is only evidence if the scan is not vacuous."""

        self.assertGreater(len(self.packages), 30, len(self.packages))
        self.assertGreater(len(self.by_skill), 25, len(self.by_skill))
        with_imports = [s for s, m in self.by_skill.items() if m]
        self.assertGreater(len(with_imports), 20, with_imports)
        # The three records this test was written for must be reachable by it.
        self.assertIn("fitz", self.by_skill["slides-to-video"])
        self.assertIn("numpy", self.by_skill["slides-to-video"])
        self.assertIn("tomli", self.by_skill["docling"])

    def test_the_rule_fires_when_a_consumer_is_dropped(self) -> None:
        """The predicate itself, against the record shape that shipped."""

        original = self.packages["PyMuPDF"]["used_by"]
        self.packages["PyMuPDF"]["used_by"] = ["annotated-review", "tikz-draw"]
        try:
            self.assertEqual({"PyMuPDF": ["slides-to-video"]}, self._unnamed())
        finally:
            self.packages["PyMuPDF"]["used_by"] = original
        self.assertEqual({}, self._unnamed())

    def test_deferred_imports_are_seen_as_well_as_module_scope_ones(self) -> None:
        """slides-to-video imports both of its packages inside a function."""

        for name, module in (("ingest.py", "fitz"), ("tts.py", "numpy")):
            source = RUNTIME_SKILLS / "slides-to-video" / "s2v" / name
            text = source.read_text(encoding="utf-8")
            self.assertIn(module, top_level_imports(source.parent), name)
            self.assertNotIn(f"\nimport {module}", text, f"{name} is not indented")

    def test_shared_runtime_imports_are_reported_for_human_attribution(self) -> None:
        """No slug owns these trees; the test records what they pull in."""

        shared = set()
        for relative in SHARED_TREES:
            shared |= top_level_imports(ROOT / relative)
        declared = {import_name(p, r) for p, r in self.packages.items()}
        self.assertTrue(shared & declared, sorted(shared)[:20])
        self.assertIn("tomli", shared)


if __name__ == "__main__":
    unittest.main()
