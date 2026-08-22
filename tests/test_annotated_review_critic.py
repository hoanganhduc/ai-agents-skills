"""Regressions for the annotated-review schema validator.

``validate_review`` is the only gate between a reviewer agent's JSON and the
three artifacts the skill produces: ``review.py`` exits with ``VALIDATION_ERROR``
before rendering anything if it reports an error, and renders whatever it passes.

``_validate_verification`` is handed the size of the annotation list --

    errs = _validate_verification(ver, len(data.get("annotations", [])))

-- and used to ignore it, type-checking ``annotation_index`` and nothing more.
Every renderer keys its verification map on the annotation's enumerate position,
so an index outside the review matched no annotation and the verifier's response
disappeared from all three artifacts of a review that validated clean, while
``count_verification`` went on counting it into the summary header.

The same check accepted a JSON ``true`` for that index, because ``bool`` is a
subclass of ``int``: the response was then attached to annotation 1 -- a
different annotation than the one the verifier had judged.
"""

from __future__ import annotations

import unittest

from tests.test_never_matching_predicates import RUNTIME_SKILLS, load_module


SKILL_DIR = RUNTIME_SKILLS / "annotated-review"


def _load(name: str):
    return load_module(name, SKILL_DIR / f"{name}.py", extra_syspath=SKILL_DIR)


AGENT = {"role": "reviewer", "model": "m", "thinking": "high"}


def _annotation(title: str) -> dict:
    return {
        "page": 1,
        "type": "unsupported",
        "severity": "major",
        "title": title,
        "body": "The induction step is not justified.",
        "quote": "By induction on the number of vertices",
        "line_start": 3,
        "line_end": 3,
    }


def _review(annotations: list, results: list) -> dict:
    return {
        "meta": {"reviewed_at": "2026-08-22", "focus": "correctness", "agents": [AGENT]},
        "annotations": annotations,
        "verification": {
            "agent": {"role": "verifier", "model": "m", "thinking": "high"},
            "verified_at": "2026-08-22",
            "results": results,
        },
    }


def _result(index, comment: str, status: str = "disputed") -> dict:
    return {"annotation_index": index, "status": status, "comment": comment}


class AnnotationIndexIsBoundedByTheReviewTests(unittest.TestCase):
    """An index that matches no annotation is a validation error, not a silent drop."""

    def setUp(self) -> None:
        self.critic = _load("critic")

    def test_the_baseline_review_validates(self) -> None:
        """The control: nothing else in these fixtures is what trips the validator."""

        review = _review([_annotation("Only annotation")], [_result(0, "In range.")])
        self.assertEqual(self.critic.validate_review(review), [])

    def test_an_index_past_the_last_annotation_is_reported(self) -> None:
        review = _review([_annotation("Only annotation")], [_result(7, "Out of range.")])
        errors = self.critic.validate_review(review)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("annotation_index 7 is out of range", errors[0])
        self.assertIn("1 annotation(s)", errors[0])

    def test_a_negative_index_is_reported(self) -> None:
        """-1 indexes the last annotation in Python and no annotation in the map the
        renderers build, so it is out of range here rather than a wrap-around."""

        review = _review([_annotation("Only annotation")], [_result(-1, "Out of range.")])
        errors = self.critic.validate_review(review)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("annotation_index -1 is out of range", errors[0])

    def test_the_last_valid_index_is_accepted(self) -> None:
        """The boundary: len - 1 is in range, len is not."""

        annotations = [_annotation("First"), _annotation("Second")]
        self.assertEqual(
            self.critic.validate_review(_review(annotations, [_result(1, "Fits.")])), []
        )
        errors = self.critic.validate_review(_review(annotations, [_result(2, "Does not.")]))
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("out of range for 2 annotation(s)", errors[0])

    def test_a_result_against_an_empty_review_is_reported(self) -> None:
        errors = self.critic.validate_review(_review([], [_result(0, "Nothing to verify.")]))
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("out of range for 0 annotation(s)", errors[0])

    def test_every_offending_result_is_named_by_its_position(self) -> None:
        review = _review(
            [_annotation("Only annotation")],
            [_result(0, "Fine."), _result(7, "Lost."), _result(-1, "Also lost.")],
        )
        errors = self.critic.validate_review(review)
        self.assertEqual(len(errors), 2, errors)
        self.assertTrue(errors[0].startswith("verification.results[1]."), errors[0])
        self.assertTrue(errors[1].startswith("verification.results[2]."), errors[1])

    def test_a_json_true_is_not_an_index(self) -> None:
        """`bool` is a subclass of `int`; `true` used to pass and then index 1."""

        review = _review(
            [_annotation("First"), _annotation("Second")],
            [_result(True, "Meant for neither.")],
        )
        errors = self.critic.validate_review(review)
        self.assertEqual(errors, ["verification.results[0].annotation_index must be int"])

    def test_a_non_integer_index_is_still_reported_as_before(self) -> None:
        review = _review([_annotation("Only annotation")], [_result("0", "A string.")])
        errors = self.critic.validate_review(review)
        self.assertEqual(errors, ["verification.results[0].annotation_index must be int"])

    def test_a_missing_index_is_still_reported_as_before(self) -> None:
        review = _review([_annotation("Only annotation")], [{"status": "disputed",
                                                             "comment": "No index."}])
        errors = self.critic.validate_review(review)
        self.assertEqual(errors, ["verification.results[0] missing 'annotation_index'"])


class WhatTheRenderersDoWithAnUnmatchedIndexTests(unittest.TestCase):
    """Why the bound matters: this is what got through before it was checked."""

    def setUp(self) -> None:
        self.critic = _load("critic")
        self.latex = _load("latex_annotator")
        self.note = _load("zotero_note")

    TEX = "\n".join([
        r"\documentclass{article}",
        r"\begin{document}",
        r"By induction on the number of vertices, the claim holds.",
        r"\end{document}",
    ])

    REVIEW = _review(
        [_annotation("Only annotation")],
        [
            _result(0, "IN-RANGE-ZERO", status="confirmed"),
            _result(7, "SEVEN-OUT-OF-RANGE"),
            _result(-1, "MINUS-ONE-OUT-OF-RANGE"),
        ],
    )

    def _ver_map(self) -> dict:
        return {
            r["annotation_index"]: r
            for r in self.REVIEW["verification"]["results"]
        }

    def test_the_unmatched_results_reach_no_artifact(self) -> None:
        tex = self.latex.annotate_file(
            self.TEX, self.REVIEW["annotations"], self._ver_map(), {}, []
        )
        html = self.note.build_note_html(self.REVIEW, "A paper")
        for artifact in (tex, html):
            self.assertIn("IN-RANGE-ZERO", artifact)
            self.assertNotIn("SEVEN-OUT-OF-RANGE", artifact)
            self.assertNotIn("MINUS-ONE-OUT-OF-RANGE", artifact)

    def test_the_summary_counts_them_anyway(self) -> None:
        """The header and the body of one document disagreed: two disputed
        responses counted, none rendered."""

        counts = self.critic.count_verification(self.REVIEW["verification"])
        self.assertEqual(counts["disputed"], 2)
        html = self.note.build_note_html(self.REVIEW, "A paper")
        self.assertIn("2 disputed", html)

    def test_validation_now_stops_this_review_before_it_renders(self) -> None:
        """review.py exits VALIDATION_ERROR on a non-empty error list, so the
        contradiction above can no longer be produced."""

        self.assertNotEqual(self.critic.validate_review(self.REVIEW), [])


if __name__ == "__main__":
    unittest.main()
