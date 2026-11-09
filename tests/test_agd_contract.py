"""Pin the fixed-claim sentences of agent-group-discuss.

Step 0 records the restated claim as ``fixed_claim``; a Stop-rule correction is
a new claim reported as ``corrected claim (delta: ...)``. These tests pin that
vocabulary in the three AGD files. They measure vocabulary coverage, not
behaviour: AGD rules are prompt conventions.
"""

from __future__ import annotations

import unittest
from pathlib import Path


AGD = Path(__file__).resolve().parents[1] / "canonical" / "skills" / "agent-group-discuss"
AGD_FILES = ("TEMPLATES.md", "EXECUTION.md", "SKILL.md")
FIXED_CLAIM_SENTENCES = ("`fixed_claim`", "corrected claim (delta:")


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


if __name__ == "__main__":
    unittest.main()
