from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "canonical"
    / "runtime"
    / "skills"
    / "tikz-draw"
    / "family_verifiers.py"
)
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tikz_draw"
HAS_SHAPELY = importlib.util.find_spec("shapely") is not None


def load_family_verifiers() -> ModuleType:
    """Load the verifier; without shapely, stub its geometry import for the text helpers."""
    spec = importlib.util.spec_from_file_location("tikz_family_verifiers_label_test", SOURCE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    if HAS_SHAPELY:
        spec.loader.exec_module(module)
        return module
    geometry = types.ModuleType("shapely.geometry")
    geometry.LineString = geometry.Point = geometry.box = object  # type: ignore[attr-defined]
    shapely = types.ModuleType("shapely")
    shapely.geometry = geometry  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"shapely": shapely, "shapely.geometry": geometry}):
        spec.loader.exec_module(module)
    return module


def bbox(x0: float, y0: float, x1: float, y1: float) -> dict[str, float]:
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "width": x1 - x0, "height": y1 - y0}


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TikzLabelNormalizationTests(unittest.TestCase):
    """TeX edge labels must normalize like the text PyMuPDF extracts from the rendered PDF."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fv = load_family_verifiers()

    def assert_same(self, tex: str, extracted: str) -> None:
        self.assertEqual(self.fv.normalize_label(tex), self.fv.normalize_label(extracted))

    def assert_different(self, tex: str, extracted: str) -> None:
        self.assertNotEqual(self.fv.normalize_label(tex), self.fv.normalize_label(extracted))

    def test_relation_labels_match_extracted_pdf_text(self) -> None:
        # Right-hand sides are the texts PyMuPDF 1.27 extracts from Computer Modern output.
        pairs = [
            ("$00a\\sim 01a$", "00a \u223c01a"),
            ("$a\\le b$", "a \u2264b"),
            ("$a\\leq b$", "a \u2264b"),
            ("$a\\geq b$", "a \u2265b"),
            ("$a\\to b$", "a \u2192b"),
            ("$a\\leftarrow b$", "a \u2190b"),
            ("$a\\Rightarrow b$", "a \u21d2b"),
            ("$a\\simeq b$", "a \u2243b"),
            ("$a\\cong b$", "a \u223c= b"),
            ("$a\\neq b$", "a \u0338= b"),
            ("$a\\mapsto b$", "a 7\u2192b"),
            ("$u\\in S$", "u \u2208S"),
            ("$A\\times B$", "A \u00d7 B"),
            ("$a\\land b$", "a \u2227b"),
            ("$a\\lor b$", "a \u2228b"),
            ("$a\\owns b$", "a \u220bb"),
            ("$f\\colon A\\to B$", "f: A \u2192B"),
        ]
        for tex, extracted in pairs:
            with self.subTest(tex=tex):
                self.assert_same(tex, extracted)

    def test_negated_relations_match_only_their_negated_rendering(self) -> None:
        for tex, negated, plain in [
            ("$a\\not\\in b$", "a \u0338\u2208b", "a \u2208b"),
            ("$a\\not\\le b$", "a \u0338\u2264b", "a \u2264b"),
            ("$a\\not\\sim b$", "a \u0338\u223cb", "a \u223cb"),
            ("$\\lnot p\\to q$", "\u00acp \u2192q", "p \u2192q"),
        ]:
            with self.subTest(tex=tex):
                self.assert_same(tex, negated)
                self.assert_different(tex, plain)

    def test_different_relations_stay_different(self) -> None:
        self.assert_different("$a\\le b$", "a \u2265b")
        self.assert_different("$a\\sim b$", "a \u2243b")
        self.assert_different("$a\\sim b$", "a b")

    def test_braced_arguments_are_kept(self) -> None:
        # Each spec label matches its own rendering and rejects a rendering without the argument.
        for tex, rendered, wrong in [
            ("$\\mathcal{U}_{00}\\sim\\mathcal{U}_{01}$", "U00 \u223cU01", "00 \u223c01"),
            ("$\\mathrm{id}\\times f$", "id \u00d7 f", "\u00d7f"),
            ("$\\mathbf{x}\\in S$", "x \u2208S", "\u2208S"),
            ("$\\mbox{if } x\\in S$", "if x \u2208S", "x \u2208S"),
        ]:
            with self.subTest(tex=tex):
                self.assert_same(tex, rendered)
                self.assert_different(tex, wrong)
        self.assertEqual(self.fv.normalize_label("$\\mathcal{U}_{00}$"), "U00")

    def test_unrendered_arguments_and_legacy_switches(self) -> None:
        self.assert_same("$\\color{red} u\\sim v$", "u \u223cv")
        self.assert_same("$u\\sim{\\color{red} v}$", "u \u223cv")
        self.assert_same("${\\rm id}\\times f$", "id \u00d7 f")
        self.assertEqual(self.fv.normalize_label("{\\color{red} Parse}"), "Parse")

    def test_unknown_commands_next_to_a_symbol_never_match(self) -> None:
        # The accent and the Greek letters have no entry, so these labels must not
        # match renderings that lack them.
        self.assert_different("$\\bar u\\sim v$", "u \u223cv")
        self.assert_different("$\\alpha\\to\\beta$", "\u2192")
        # Bars are rules, not text: a rendering without them would read the same.
        self.assert_different("$\\overline{A}\\cup B$", "A \u222aB")
        self.assert_different("$\\frac{1}{2}\\le x$", "12 \u2264x")
        # Font and sizing commands set no glyph and do not block a match.
        self.assert_same("$\\mathbf x\\to y$", "x \u2192y")
        self.assert_same("$\\left(a\\right)\\to b$", "(a) \u2192b")

    def test_command_names_are_replaced_whole(self) -> None:
        # Without the lookahead, \left would start with \le, \int with \in, \top with \to.
        self.assertEqual(self.fv.normalize_label("$\\left(a\\right)$"), "(a)")
        self.assertEqual(self.fv.normalize_label("$\\int f$"), "f")
        self.assertEqual(self.fv.normalize_label("$a\\top b$"), "ab")
        self.assertEqual(self.fv.normalize_label("$a\\leftarrow b$"), "a\u2190b")
        self.assertEqual(self.fv.normalize_label("$a\\simeq b$"), "a\u2243b")

    def test_existing_normalizations_are_unchanged(self) -> None:
        expected = {
            "$\\mathcal U_{00}$": "U00",
            "U 00": "U00",
            "Validate?": "Validate?",
            "Assumption A": "Assumption A",
            "retry loop": "retry loop",
            "$p_u^e$": "peu",
            "$c_{u,3}$": "cu,3",
            "$A$": "A",
            "$x_1$": "x1",
        }
        for raw, normalized in expected.items():
            with self.subTest(raw=raw):
                self.assertEqual(self.fv.normalize_label(raw), normalized)
        self.assertIsNone(self.fv.normalize_label(None))


@unittest.skipUnless(HAS_SHAPELY, "shapely is required for the geometric label checks")
class TikzEdgeLabelAssignmentTests(unittest.TestCase):
    """A free text line is the label of the edge its bounding box is placed next to."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fv = load_family_verifiers()
        from shapely.geometry import LineString

        cls.LineString = LineString

    def edge(self, points: list[tuple[float, float]]) -> dict[str, Any]:
        return {"from_label": "A", "to_label": "B", "label": None, "geom": self.LineString(points)}

    def test_side_label_of_vertical_edge_is_attached(self) -> None:
        # Box 3.9 pt right of the edge; its center is 23.2 pt away.
        edges = [self.edge([(202.3, 37.0), (202.3, 71.0)])]
        free_lines = [{"text": "01b \u223c11b", "bbox": bbox(206.2, 50.1, 244.8, 59.1)}]
        edges, remaining = self.fv.assign_labels_to_edges(edges, free_lines)
        self.assertEqual(edges[0]["label"], self.fv.normalize_label("01b \u223c11b"))
        self.assertEqual(remaining, [])

    def test_label_above_horizontal_edge_is_attached(self) -> None:
        edges = [self.edge([(102.7, 20.4), (153.4, 20.4)])]
        free_lines = [{"text": "00a \u223c01a", "bbox": bbox(107.8, 9.1, 148.3, 18.2)}]
        edges, remaining = self.fv.assign_labels_to_edges(edges, free_lines)
        self.assertEqual(edges[0]["label"], self.fv.normalize_label("00a \u223c01a"))
        self.assertEqual(remaining, [])

    def test_distant_text_is_not_attached(self) -> None:
        edges = [self.edge([(202.3, 37.0), (202.3, 71.0)])]
        free_lines = [{"text": "note", "bbox": bbox(220.0, 50.0, 250.0, 59.0)}]
        edges, remaining = self.fv.assign_labels_to_edges(edges, free_lines)
        self.assertIsNone(edges[0]["label"])
        self.assertEqual([line["text"] for line in remaining], ["note"])

    def test_label_goes_to_the_nearer_of_two_edges(self) -> None:
        edges = [self.edge([(100.0, 0.0), (100.0, 80.0)]), self.edge([(160.0, 0.0), (160.0, 80.0)])]
        free_lines = [{"text": "e", "bbox": bbox(104.0, 35.0, 112.0, 45.0)}]
        edges, _remaining = self.fv.assign_labels_to_edges(edges, free_lines)
        self.assertEqual(edges[0]["label"], "e")
        self.assertIsNone(edges[1]["label"])

    def test_near_tie_goes_to_the_longer_edge(self) -> None:
        # The short edge is 3.0 pt away and the long one 4.5 pt, within 2 pt of each other.
        edges = [self.edge([(112.0, 30.0), (112.0, 50.0)]), self.edge([(100.0, 0.0), (100.0, 80.0)])]
        free_lines = [{"text": "e", "bbox": bbox(104.5, 35.0, 109.0, 45.0)}]
        edges, _remaining = self.fv.assign_labels_to_edges(edges, free_lines)
        self.assertIsNone(edges[0]["label"])
        self.assertEqual(edges[1]["label"], "e")


@unittest.skipUnless(HAS_SHAPELY, "shapely is required for the flowchart verifier")
class TikzRenderedFlowchartRegressionTests(unittest.TestCase):
    """Rendered flowcharts whose labels sit beside edges or above groups."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fv = load_family_verifiers()

    def test_relation_labels_on_all_four_sides_of_a_square(self) -> None:
        spec = {
            "diagram_family": "flowchart",
            "nodes": [
                {"id": "b00", "label": "$\\mathcal U_{00}$", "style": "box"},
                {"id": "b01", "label": "$\\mathcal U_{01}$", "style": "box"},
                {"id": "b11", "label": "$\\mathcal U_{11}$", "style": "box"},
                {"id": "b10", "label": "$\\mathcal U_{10}$", "style": "box"},
            ],
            "edges": [
                {"from": "b00", "to": "b01", "label": "$00a\\sim 01a$", "label_pos": "above"},
                {"from": "b01", "to": "b11", "label": "$01b\\sim 11b$", "label_pos": "right"},
                {"from": "b11", "to": "b10", "label": "$11c\\sim 10c$", "label_pos": "below"},
                {"from": "b10", "to": "b00", "label": "$10e\\sim 00e$", "label_pos": "left"},
            ],
            "groups": [],
        }
        result = self.fv.verify_rendered_family(spec, load_fixture("four_part_gluing.render-semantics.json"))
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(result["recovered"]["unmatched_free_text"], [])
        recovered = {(edge["from_label"], edge["to_label"]) for edge in result["recovered"]["edges"]}
        self.assertEqual(recovered, {("U00", "U01"), ("U01", "U11"), ("U11", "U10"), ("U10", "U00")})

    def test_group_label_beside_an_unlabelled_edge_stays_a_group_label(self) -> None:
        # The label box of "Verification stage" is 6.9 pt from the edge Start -> Check A.
        spec = {
            "diagram_family": "flowchart",
            "nodes": [
                {"id": "s", "label": "Start", "style": "box"},
                {"id": "a", "label": "Check A", "style": "box"},
                {"id": "b", "label": "Check B", "style": "box"},
            ],
            "edges": [{"from": "s", "to": "a"}, {"from": "a", "to": "b"}],
            "groups": [{"id": "g", "label": "Verification stage", "members": ["a", "b"]}],
        }
        result = self.fv.verify_rendered_family(spec, load_fixture("group_label_beside_edge.render-semantics.json"))
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(result["recovered"]["ignored_free_text"], ["Verification stage"])
        self.assertTrue(all(edge["label"] is None for edge in result["recovered"]["edges"]))

    def test_group_label_that_is_also_an_edge_label(self) -> None:
        # "Verification stage" labels both the group and the edge Check A -> Check B.
        spec = {
            "diagram_family": "flowchart",
            "nodes": [
                {"id": "s", "label": "Start", "style": "box"},
                {"id": "a", "label": "Check A", "style": "box"},
                {"id": "b", "label": "Check B", "style": "box"},
            ],
            "edges": [{"from": "s", "to": "a"}, {"from": "a", "to": "b", "label": "Verification stage"}],
            "groups": [{"id": "g", "label": "Verification stage", "members": ["a", "b"]}],
        }
        result = self.fv.verify_rendered_family(spec, load_fixture("group_label_also_edge_label.render-semantics.json"))
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(result["recovered"]["ignored_free_text"], ["Verification stage"])

    def test_group_name_written_on_an_edge_is_rejected(self) -> None:
        # The rendering puts "Phase two" on the edge Check A -> Check B, inside the group box.
        spec = {
            "diagram_family": "flowchart",
            "nodes": [
                {"id": "s", "label": "Start", "style": "box"},
                {"id": "a", "label": "Check A", "style": "box"},
                {"id": "b", "label": "Check B", "style": "box"},
            ],
            "edges": [{"from": "s", "to": "a"}, {"from": "a", "to": "b"}],
            "groups": [{"id": "g", "label": "Phase two", "members": ["a", "b"]}],
        }
        result = self.fv.verify_rendered_family(spec, load_fixture("group_name_on_edge.render-semantics.json"))
        self.assertEqual(result["mismatch_codes"], ["WRONG_EDGE_LABEL"])


if __name__ == "__main__":
    unittest.main()
