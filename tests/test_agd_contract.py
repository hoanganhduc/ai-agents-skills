"""Pin the fixed-claim, counterexample-check and chaining sentences of agent-group-discuss.

Step 0 records the restated claim as ``fixed_claim``; a Stop-rule correction is
a new claim reported as ``corrected claim (delta: ...)``. A reported
counterexample is decisive only after the parent checks the witness, and the
files say what each fixed checker can confirm and how a check is recorded.
When templates are chained, the ``Rejected`` ledger travels with the accepted
claims under the relay caps. These tests pin that vocabulary in the three AGD
files. They measure vocabulary coverage, not behaviour: AGD rules are prompt
conventions.
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
CHAINING_SENTENCES = (
    "The `Rejected` ledger travels with the accepted claims",
    "taken: do not retry without an evidence delta",
    "reproduction ref",
    "never cut",
)
# Where the chaining text of each AGD file starts; it ends at the next level-2 heading.
CHAINING_STARTS = {
    "TEMPLATES.md": "\n## Template chaining\n",
    "EXECUTION.md": "\n## Template chaining\n",
    "SKILL.md": "\nTemplate chaining is allowed",
}
# The relay cap numbers. A chaining section cites "Relaying findings between
# rounds" in EXECUTION.md for them instead of stating them again.
RELAY_CAP_NUMBERS = ("1500", "1,500", "12 KB", "12288", "8 entries")


def normalized(text: str) -> str:
    """The text with each run of whitespace, line breaks included, made one space."""
    return " ".join(text.split())


def chaining_section(name: str) -> str:
    """The normalized chaining text of one AGD file, up to the next level-2 heading."""
    text = (AGD / name).read_text(encoding="utf-8")
    start = text.index(CHAINING_STARTS[name])
    return normalized(text[start:text.index("\n## ", start + 1)])


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


class AgdChainingRejectedLedgerTests(unittest.TestCase):
    """Chained templates forward the ``Rejected`` ledger with the accepted claims.

    Each forwarded entry names the claim or route, the reason, the
    counterexample or failing step and a reproduction ref, and is marked
    "taken: do not retry without an evidence delta". In EXECUTION.md the
    ledger goes into the ``Topic / Claim`` section of every later phase, after
    the data statement. Entries are framed, checked and capped as "Relaying
    findings between rounds" states for ``Dead ends`` rows, cited by name: an
    entry over a cap is refused and listed as not forwarded, never cut.
    """

    def test_each_agd_file_forwards_the_rejected_ledger_when_chaining(self) -> None:
        for name in AGD_FILES:
            section = chaining_section(name)
            for sentence in CHAINING_SENTENCES:
                with self.subTest(file=name, sentence=sentence):
                    self.assertIn(sentence, section)

    def test_forwarded_entries_use_the_relay_caps(self) -> None:
        """Each chaining section cites the relay rules by name and states no cap of its own.

        The absent cap numbers are a regression check: no chaining section
        stated one before this rule either.
        """
        for name in AGD_FILES:
            section = chaining_section(name)
            for sentence in ('"Relaying findings between rounds"', "never cut"):
                with self.subTest(file=name, sentence=sentence):
                    self.assertIn(sentence, section)
            for number in RELAY_CAP_NUMBERS:
                with self.subTest(file=name, absent=number):
                    self.assertNotIn(number, section)

    def test_forwarded_ledger_goes_into_topic_claim_after_the_data_statement(self) -> None:
        section = chaining_section("EXECUTION.md")
        for sentence in ("`Topic / Claim` section", "data statement"):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, section)
        text = (AGD / "EXECUTION.md").read_text(encoding="utf-8")
        start = text.index("\n## Topic / Claim\n")
        slot = text[start:text.index("\n## Prior round context\n", start)]
        self.assertIn('PER "TEMPLATE CHAINING"', slot)

    def test_round_one_independence_sentence_is_unchanged(self) -> None:
        """Regression check: the sentence held before chaining forwarded the ledger and must stay."""
        text = (AGD / "EXECUTION.md").read_text(encoding="utf-8")
        self.assertIn(
            "\n- Round 1 independent first pass: use fresh participants with no cross-role contamination.\n",
            text,
        )


if __name__ == "__main__":
    unittest.main()
