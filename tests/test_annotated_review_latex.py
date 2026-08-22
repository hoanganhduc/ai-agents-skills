"""Regressions for the LaTeX annotator's \\todo labels.

Both defects pinned here live in the two functions that render an issue title
into a `\\todo` box, and both were invisible in a bare checkout: the skill's
``requirements.txt`` declares pylatexenc, but ``latex_annotator`` falls back to
an identity ``utf8tolatex`` when it is absent, and that fallback hides the
escaping defect completely.

* ``build_verifier_addition_todo`` read the issue's title and never used it.
  The other two renderers of the same ``additional_issues`` record print it
  (``pdf_annotator.py`` and ``zotero_note.py``), so the annotated LaTeX PDF --
  the skill's primary output -- was the one artifact of the three that showed a
  verifier-added issue with no title.
* ``build_reviewer_todo`` escaped the title, embedded it in the label, then
  escaped the label again. ``utf8tolatex`` is not idempotent, so a title reading
  "Erdős bound is off by 50%" reached the PDF as literal macro text.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.test_never_matching_predicates import RUNTIME_SKILLS, load_module


SKILL_DIR = RUNTIME_SKILLS / "annotated-review"


def _load(name: str):
    return load_module(name, SKILL_DIR / f"{name}.py", extra_syspath=SKILL_DIR)


class VerifierAdditionKeepsItsTitleTests(unittest.TestCase):
    """A verifier addition reaches every artifact with its title attached."""

    # ASCII-only, so the assertions hold whether or not pylatexenc is installed.
    TITLE = "Lemma 3.2 assumes a connectivity Theorem 1 never establishes"
    ISSUE = {
        "severity": "major",
        "type": "unsupported",
        "title": TITLE,
        "body": "The induction step needs G-v connected.",
        "quote": "By induction on the number of vertices",
        "line_start": 42,
        "line_end": 44,
    }

    def setUp(self) -> None:
        self.latex = _load("latex_annotator")

    def test_the_todo_box_carries_the_title(self) -> None:
        self.assertIn(self.TITLE, self.latex.build_verifier_addition_todo(self.ISSUE))

    def test_the_marker_and_severity_are_still_there(self) -> None:
        todo = self.latex.build_verifier_addition_todo(self.ISSUE)
        self.assertIn("+ VERIFIER ADDITION", todo)
        self.assertIn("MAJOR", todo)
        self.assertIn(self.ISSUE["body"], todo)

    def test_the_title_survives_into_the_annotated_tex(self) -> None:
        tex = "\n".join([
            r"\documentclass{article}",
            r"\begin{document}",
            r"By induction on the number of vertices, the claim holds.",
            r"\end{document}",
        ])
        out = self.latex.annotate_file(tex, [], {}, {}, [self.ISSUE])
        self.assertIn(self.TITLE, out)

    def test_the_companion_html_renders_the_same_title(self) -> None:
        """The cross-check: two artifacts built from one record must agree."""

        note = _load("zotero_note")
        html = note.build_note_html(
            {
                "meta": {"title": "A paper"},
                "annotations": [],
                "verification": {"results": [], "additional_issues": [self.ISSUE]},
            },
            paper_title="A paper",
        )
        self.assertIn(self.TITLE, html)

    def test_the_pdf_renderer_still_prints_it(self) -> None:
        """pdf_annotator needs PyMuPDF to import, so its contract is read from
        source: it is the third renderer of this record and the reason the LaTeX
        one dropping the title was a disagreement rather than a house style."""

        source = (SKILL_DIR / "pdf_annotator.py").read_text(encoding="utf-8")
        self.assertIn('f"[VERIFIER ADDITION', source)
        self.assertIn('f"{title}\\n\\n{body}"', source)


class ReviewerTodoEscapesExactlyOnceTests(unittest.TestCase):
    """The title is escaped where the label is emitted, and nowhere else."""

    TITLE = "50% of a_b cases"
    ANN = {
        "severity": "major",
        "type": "unsupported",
        "title": TITLE,
        "body": "See section 3.",
        "line_start": 42,
        "line_end": 44,
    }

    def setUp(self) -> None:
        self.latex = _load("latex_annotator")
        # A stand-in with the property that matters: escaping is not idempotent,
        # exactly like pylatexenc's utf8tolatex. Patching it keeps the test
        # honest in a checkout where the declared dependency is not installed,
        # where the module's fallback escaper is the identity and would let a
        # double escape pass unnoticed.
        self.latex.utf8tolatex = self._escape

    @staticmethod
    def _escape(text: str, non_ascii_only: bool = False, **_kwargs) -> str:
        return text.replace("\\", "{\\textbackslash}").replace("%", "{\\%}").replace("_", "{\\_}")

    def test_the_stand_in_is_not_idempotent(self) -> None:
        once = self._escape(self.TITLE)
        self.assertNotEqual(once, self._escape(once))

    def test_the_title_appears_escaped_once(self) -> None:
        todo = self.latex.build_reviewer_todo(self.ANN)
        self.assertIn(self._escape(self.TITLE), todo)

    def test_no_escape_of_an_escape_reaches_the_output(self) -> None:
        todo = self.latex.build_reviewer_todo(self.ANN)
        self.assertNotIn("{\\{\\textbackslash}", todo)
        self.assertNotIn(self._escape(self._escape(self.TITLE)), todo)

    def test_the_verifier_addition_label_is_escaped_once_too(self) -> None:
        todo = self.latex.build_verifier_addition_todo(self.ANN)
        self.assertIn(self._escape(self.TITLE), todo)
        self.assertNotIn(self._escape(self._escape(self.TITLE)), todo)

    def test_the_real_escaper_is_the_one_being_modelled(self) -> None:
        """When the declared dependency is present, pin the property on it."""

        try:
            from pylatexenc.latexencode import utf8tolatex
        except ImportError:  # pragma: no cover - dependency is optional here
            self.skipTest("pylatexenc not installed")
        once = utf8tolatex(self.TITLE, non_ascii_only=False)
        self.assertNotEqual(once, utf8tolatex(once, non_ascii_only=False))


if __name__ == "__main__":
    unittest.main()
