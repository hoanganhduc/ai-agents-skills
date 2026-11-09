"""Pin the fixed-claim and counterexample-check sentences of agent-group-discuss.

Step 0 records the restated claim as ``fixed_claim``; a Stop-rule correction is
a new claim reported as ``corrected claim (delta: ...)``. A reported
counterexample is decisive only after the parent checks the witness, and the
files say what each fixed checker can confirm and how a check is recorded.
These tests pin that vocabulary in the three AGD files. They measure
vocabulary coverage, not behaviour: AGD rules are prompt conventions.
"""

from __future__ import annotations

import unittest
from pathlib import Path


AGD = Path(__file__).resolve().parents[1] / "canonical" / "skills" / "agent-group-discuss"
AGD_FILES = ("TEMPLATES.md", "EXECUTION.md", "SKILL.md")
FIXED_CLAIM_SENTENCES = ("`fixed_claim`", "corrected claim (delta:")
COUNTEREXAMPLE_SENTENCES = (
    "add-computation-evidence",
    "re-enumerates every graph up to `n_max`",
    "reports as `checked`",
    "stays a candidate counterexample",
)


def normalized(text: str) -> str:
    """The text with each run of whitespace, line breaks included, made one space."""
    return " ".join(text.split())


class AgdFixedClaimContractTests(unittest.TestCase):
    def test_each_agd_file_names_the_fixed_claim_and_its_correction_label(self) -> None:
        for name in AGD_FILES:
            text = (AGD / name).read_text(encoding="utf-8")
            for sentence in FIXED_CLAIM_SENTENCES:
                with self.subTest(file=name, sentence=sentence):
                    self.assertIn(sentence, text)

    def test_default_research_output_quotes_the_claim(self) -> None:
        text = (AGD / "TEMPLATES.md").read_text(encoding="utf-8")
        start = text.index("## Default research output format")
        section = text[start:text.index("\n---", start)]
        self.assertIn("fixed claim", section)
        self.assertIn("corrected claim (delta:", section)


class AgdCounterexampleConfirmationTests(unittest.TestCase):
    """A reported counterexample is decisive only after the parent checks the witness.

    graph-verifier checks a witness graph and confirms only the invariants it
    reports as ``checked``; the Sage counterexample template re-enumerates every
    graph up to ``n_max`` and checks no given witness; a report that no checker
    confirms stays a candidate counterexample; with a deep-research run the
    parent records the check through ``add-computation-evidence``. The bare
    words "candidate counterexample" are not pinned: TEMPLATES.md used them
    before this rule existed.
    """

    def test_each_agd_file_records_and_bounds_counterexample_checks(self) -> None:
        for name in AGD_FILES:
            text = normalized((AGD / name).read_text(encoding="utf-8"))
            for sentence in COUNTEREXAMPLE_SENTENCES:
                with self.subTest(file=name, sentence=sentence):
                    self.assertIn(sentence, text)

    def test_the_templates_stop_rule_names_lakatos_and_structured_research_team(self) -> None:
        text = (AGD / "TEMPLATES.md").read_text(encoding="utf-8")
        start = text.index("\n## Stop rule\n")
        section = normalized(text[start:text.index("\n## ", start + 1)])
        for name in ("Lakatos", "Structured Research Team", "add-computation-evidence"):
            with self.subTest(name=name):
                self.assertIn(name, section)

    def test_stop_rule_two_keeps_the_parent_witness_check(self) -> None:
        """Regression check: stop rule 2 already said this before the checker rules; it must stay."""
        text = normalized((AGD / "EXECUTION.md").read_text(encoding="utf-8"))
        self.assertIn("becomes decisive only after the parent checks the witness itself", text)

    def test_execution_says_which_claim_a_counterexample_is_recorded_against(self) -> None:
        """A counterexample is recorded with refutes against a claim that the predicate fails, never the claim it refutes.

        Only EXECUTION.md gives the command and how to choose --relation, so
        only it carries this rule; SKILL.md and TEMPLATES.md point to it.
        """
        text = normalized((AGD / "EXECUTION.md").read_text(encoding="utf-8"))
        for sentence in (
            "is recorded with `refutes` against a claim that states that the predicate fails on the checked domain",
            "never against the claim it refutes",
            "`add-computation-evidence` refuses `asserts` with a `failed` result",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, text)


if __name__ == "__main__":
    unittest.main()
