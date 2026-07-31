"""Contract tests for the core research gates plus the seeded-defect corpus.

Two offline guarantees:

1. Each core research gate still declares its output contract (verdict
   vocabulary, required sections, style-profile fields) and ships its
   referenced checklist file.
2. The seeded-defect corpus in tests/gates/ stays well-formed: every fixture
   carries exactly one labelled defect, the declared defect is actually
   present in the artifact (deterministic per-class checks), verdicts are
   coherent, and the clean control trips none of the poison checks.

The gates are prompt-convention skills; these tests pin their contracts and
the corpus, not live gate behavior. See tests/gates/README.md.
"""

from __future__ import annotations

import json
import unittest

from installer.ai_agents_skills.manifest import REPO_ROOT

SKILLS_DIR = REPO_ROOT / "canonical" / "skills"
GATES_DIR = REPO_ROOT / "tests" / "gates"

CORE_GATES = {
    "research-briefing": {
        "markers": [
            "Research Brief",
            "Goal",
            "Scope",
            "Evidence plan",
            "Workflow",
            "Risks",
            "style_profile_ref",
        ],
        "references": ["references/brief-template.md"],
    },
    "decision-doubt-loop": {
        "markers": [
            "Doubt verdict",
            "STANDS",
            "REVISED",
            "BLOCKED",
            "BLOCKED-FRESH-CONTEXT-UNAVAILABLE",
            "Load-bearing assumption",
        ],
        "references": [],
    },
    "research-report-reviewer": {
        "markers": [
            "Review Findings",
            "BLOCK",
            "FLAG",
            "PASS",
            "Findings",
            "Repairs",
            "style_profile_ref",
        ],
        "references": ["references/reviewer-prompt.md"],
    },
    "research-verification-gate": {
        "markers": [
            "Delivery Check",
            "Gate version",
            "READY",
            "NOT READY",
            "incomplete analysis",
            "style_profile_ref",
            "active_overlays",
            "active_requirement_ids",
            "style_applied",
        ],
        "references": ["references/checklist.md"],
    },
}

# Defect class -> gates expected to catch it -> contract vocabulary that gate
# must contain for the mapping to be meaningful. An empty gate set is an
# explicitly recorded coverage gap (see tests/gates/README.md).
DEFECT_GATE_KEYWORDS = {
    "citation_fabrication": {
        "research-verification-gate": ["supporting evidence"],
        "research-report-reviewer": ["unsupported"],
    },
    "unchecked_scope": {
        "research-verification-gate": ["incomplete analysis"],
        "research-report-reviewer": ["incomplete analysis"],
    },
    "post_hoc_observed_power": {},
    "retracted_source": {
        "research-verification-gate": ["supporting evidence"],
        "research-report-reviewer": ["unsupported"],
    },
    "venue_ranking_without_comparator": {
        "submission-venue-selector": ["comparator"],
        "research-verification-gate": ["supporting evidence"],
    },
    "unsupported_citation": {
        "research-report-reviewer": ["unsupported"],
        "research-verification-gate": ["supporting evidence"],
    },
    "undisclosed_truncation": {
        "research-verification-gate": ["read whole", "incomplete analysis"],
        "research-report-reviewer": ["read only in part", "unsupported"],
    },
}

REQUIRED_FIELDS = {
    "fixture_id",
    "defect_class",
    "defect_label",
    "target_gates",
    "artifact",
    "expected_verdict",
    "expected_signals",
}


def _poison_predicates(artifact: dict) -> dict[str, bool]:
    claims = artifact.get("claims", [])
    evidence = {e.get("source_id"): e for e in artifact.get("evidence_list", [])}
    cited = [s for c in claims for s in c.get("cited_sources", [])]
    scope = artifact.get("scope", {})
    venue = artifact.get("venue_ranking", {})
    ingested = artifact.get("ingested_sources", [])
    return {
        "undisclosed_truncation": any(
            source.get("load_bearing") is True
            and source.get("complete") is False
            and not source.get("truncation_disclosed")
            for source in ingested
        ),
        "citation_fabrication": any(s not in evidence for s in cited),
        "unchecked_scope": (
            scope.get("materially_unchecked") is True
            and "incomplete analysis" not in artifact.get("report_text", "")
        ),
        "post_hoc_observed_power": (
            artifact.get("post_hoc") is True
            and "observed power" in artifact.get("analysis_text", "")
        ),
        "retracted_source": any(
            evidence.get(s, {}).get("retracted") is True for s in cited
        ),
        "venue_ranking_without_comparator": (
            bool(venue.get("ranked")) and not venue.get("comparator_paper_evidence")
        ),
        "unsupported_citation": any(
            c.get("presented_as") == "supporting"
            and evidence.get(s, {}).get("supports") is False
            for c in claims
            for s in c.get("cited_sources", [])
        ),
    }


class GateContractTest(unittest.TestCase):
    def test_core_gate_contracts(self):
        for gate, spec in CORE_GATES.items():
            with self.subTest(gate=gate):
                skill = SKILLS_DIR / gate / "SKILL.md"
                self.assertTrue(skill.is_file(), f"missing {skill}")
                text = skill.read_text(encoding="utf-8")
                for marker in spec["markers"]:
                    self.assertIn(marker, text, f"{gate} lost contract marker {marker!r}")
                for ref in spec["references"]:
                    self.assertTrue(
                        (SKILLS_DIR / gate / ref).is_file(),
                        f"{gate} references missing file {ref}",
                    )

    def test_covering_gates_have_catching_vocabulary(self):
        for defect_class, gates in DEFECT_GATE_KEYWORDS.items():
            for gate, keywords in gates.items():
                with self.subTest(defect_class=defect_class, gate=gate):
                    skill = SKILLS_DIR / gate / "SKILL.md"
                    self.assertTrue(skill.is_file(), f"missing {skill}")
                    text = skill.read_text(encoding="utf-8")
                    for keyword in keywords:
                        self.assertIn(keyword, text)


class SeededDefectCorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = {}
        for path in sorted(GATES_DIR.glob("*.json")):
            cls.fixtures[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        if not cls.fixtures:
            raise AssertionError(f"no fixtures found in {GATES_DIR}")

    def test_required_fields_and_taxonomy(self):
        known = set(DEFECT_GATE_KEYWORDS) | {"none"}
        for stem, fx in self.fixtures.items():
            with self.subTest(fixture=stem):
                self.assertEqual(fx["fixture_id"], stem)
                missing = REQUIRED_FIELDS - set(fx)
                self.assertFalse(missing, f"{stem} missing fields {missing}")
                self.assertIn(fx["defect_class"], known)
                self.assertIsInstance(fx["target_gates"], list)
                self.assertIsInstance(fx["expected_signals"], list)

    def test_declared_defect_is_present_exactly_once(self):
        for stem, fx in self.fixtures.items():
            with self.subTest(fixture=stem):
                fired = {
                    name
                    for name, hit in _poison_predicates(fx["artifact"]).items()
                    if hit
                }
                if fx["defect_class"] == "none":
                    self.assertFalse(fired, f"clean control trips {fired}")
                else:
                    self.assertEqual(
                        fired,
                        {fx["defect_class"]},
                        f"{stem} must carry exactly its declared defect",
                    )

    def test_expected_verdict_coherent(self):
        for stem, fx in self.fixtures.items():
            with self.subTest(fixture=stem):
                self.assertIn(fx["expected_verdict"], {"READY", "NOT READY"})
                if fx["defect_class"] == "none":
                    self.assertEqual(fx["expected_verdict"], "READY")
                else:
                    self.assertEqual(fx["expected_verdict"], "NOT READY")

    def test_target_gates_align_with_coverage_map(self):
        for stem, fx in self.fixtures.items():
            with self.subTest(fixture=stem):
                if fx["defect_class"] == "none":
                    continue
                mapped = set(DEFECT_GATE_KEYWORDS[fx["defect_class"]])
                self.assertEqual(set(fx["target_gates"]), mapped)
                if not mapped:
                    self.assertTrue(
                        fx.get("coverage_note"),
                        f"{stem} has no covering gate and must say why",
                    )


if __name__ == "__main__":
    unittest.main()
