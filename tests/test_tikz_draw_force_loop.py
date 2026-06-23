from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "canonical"
    / "runtime"
    / "skills"
    / "tikz-draw"
    / "tikz_draw.py"
)


def load_tikz_draw():
    sys.path.insert(0, str(SOURCE.parent))
    try:
        spec = importlib.util.spec_from_file_location("tikz_draw_force_loop_test", SOURCE)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SOURCE.parent))


class TikzDrawForceLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tikz = load_tikz_draw()

    def test_force_loop_stops_on_structural_pass_without_consuming_credits(self) -> None:
        report = {
            "overlap_status": "PASS",
            "symmetry_status": "PASS",
            "visual_review": {"findings": []},
            "symmetry_review": {"findings": []},
            "warnings": [],
        }
        with (
            patch.object(self.tikz, "force_stop_requested", return_value=None),
            patch.object(self.tikz, "run_force_structural_report", return_value=(report, 0)) as run_check,
            patch.object(self.tikz, "load_manifest", return_value={"diagram_family": "flowchart", "semantic_review": None}),
            patch.object(self.tikz, "write_semantic_report"),
        ):
            final_report, exit_code = self.tikz.run_force_structural_loop(
                Path("/tmp/F1.artifacts.json"),
                Path("/tmp"),
                repair_credits=3,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(final_report["force_loop"]["terminal_reason"], "issue_free_no_overlap_and_no_symmetry_failures")
        self.assertEqual(final_report["force_loop"]["repair_credits_remaining"], 3)
        self.assertEqual(run_check.call_count, 1)

    def test_force_loop_credit_exhaustion_is_terminal_condition(self) -> None:
        report = {
            "overlap_status": "FAIL",
            "symmetry_status": "PASS",
            "visual_review": {"findings": [{"pass_id": "V4_TEXT_TEXT_OVERLAP"}]},
            "symmetry_review": {"findings": []},
            "warnings": [],
        }
        with (
            patch.object(self.tikz, "force_stop_requested", return_value=None),
            patch.object(self.tikz, "run_force_structural_report", return_value=(report, 1)),
            patch.object(self.tikz, "load_manifest", return_value={"diagram_family": "flowchart", "semantic_review": None}),
            patch.object(self.tikz, "write_semantic_report"),
        ):
            final_report, exit_code = self.tikz.run_force_structural_loop(
                Path("/tmp/F1.artifacts.json"),
                Path("/tmp"),
                repair_credits=0,
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(final_report["force_loop"]["terminal_reason"], "credit_budget_exhausted")
        self.assertEqual(final_report["force_loop"]["status"], "EXHAUSTED")
        self.assertEqual(final_report["force_loop"]["ledger"][-1]["decision"], "credit_budget_exhausted")

    def test_force_loop_exhausts_credits_when_no_actionable_structural_finding_exists(self) -> None:
        report = {
            "overlap_status": "BLOCKED",
            "symmetry_status": "PASS",
            "visual_review": {"findings": []},
            "symmetry_review": {"findings": []},
            "warnings": [],
        }
        with (
            patch.object(self.tikz, "force_stop_requested", return_value=None),
            patch.object(self.tikz, "run_force_structural_report", return_value=(report, 1)),
            patch.object(self.tikz, "load_manifest", return_value={"diagram_family": "flowchart", "semantic_review": None}),
            patch.object(self.tikz, "write_semantic_report"),
        ):
            final_report, exit_code = self.tikz.run_force_structural_loop(
                Path("/tmp/F1.artifacts.json"),
                Path("/tmp"),
                repair_credits=4,
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(final_report["force_loop"]["terminal_reason"], "credit_budget_exhausted")
        self.assertEqual(final_report["force_loop"]["repair_credits_remaining"], 0)
        self.assertIn("no actionable overlap or symmetry finding", final_report["warnings"][-1])

    def test_force_loop_user_stop_preempts_check(self) -> None:
        with (
            patch.object(self.tikz, "force_stop_requested", return_value={"kind": "sentinel", "path": "/tmp/STOP"}),
            patch.object(self.tikz, "run_force_structural_report") as run_check,
            patch.object(self.tikz, "load_manifest", return_value={"diagram_family": "flowchart", "semantic_review": None}),
            patch.object(self.tikz, "write_semantic_report"),
        ):
            final_report, exit_code = self.tikz.run_force_structural_loop(
                Path("/tmp/F1.artifacts.json"),
                Path("/tmp"),
                repair_credits=3,
            )

        self.assertEqual(exit_code, 130)
        self.assertEqual(final_report["force_loop"]["terminal_reason"], "user_stop_requested")
        run_check.assert_not_called()

    def test_force_stop_requested_detects_work_dir_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            (work_dir / "tikz-draw-force-stop").write_text("stop\n", encoding="utf-8")

            stop = self.tikz.force_stop_requested(work_dir)

        self.assertEqual(stop["kind"], "sentinel")
        self.assertTrue(stop["path"].endswith("tikz-draw-force-stop"))

    def test_overlap_repair_changes_layout_only_not_semantics(self) -> None:
        spec = {
            "diagram_family": "flowchart",
            "tikz_backend": "positioning",
            "title": "Pipeline",
            "caption": "",
            "global_styles": {},
            "nodes": [
                {"id": "input", "label": "Input", "style": "io", "width": "28mm"},
                {
                    "id": "parse",
                    "label": "Parse",
                    "style": "box",
                    "width": "28mm",
                    "placement": {"kind": "relative", "target": "input", "relation": "right"},
                },
            ],
            "edges": [{"from": "input", "to": "parse", "label": "next"}],
            "groups": [],
            "layout_constraints": ["Fit within text width using adjustbox."],
            "validation_rules": [],
            "symmetry_contract": {"status": "not_required", "justification": "pipeline is directional"},
        }
        report = {
            "overlap_status": "FAIL",
            "symmetry_status": "PASS",
            "visual_review": {"findings": [{"pass_id": "V6_LINE_TEXT_OVERLAP"}]},
            "symmetry_review": {"findings": []},
        }

        repaired, changes = self.tikz.repair_spec_for_force_findings(spec, report, 1)

        self.assertTrue(changes)
        self.assertEqual([node["label"] for node in repaired["nodes"]], ["Input", "Parse"])
        self.assertEqual(repaired["edges"][0]["from"], "input")
        self.assertEqual(repaired["edges"][0]["to"], "parse")
        self.assertIn("label_pos", repaired["edges"][0])
        self.assertTrue(
            any(str(item).startswith("tikz-draw-force:spacing_multiplier=") for item in repaired["layout_constraints"])
        )
        self.assertEqual(spec["edges"][0]["label"], "next")
        self.assertNotIn("label_pos", spec["edges"][0])

    def test_absolute_symmetry_repair_aligns_required_row_pairs(self) -> None:
        spec = {
            "diagram_family": "graph",
            "tikz_backend": "raw-tikz",
            "title": "Pair",
            "caption": "",
            "global_styles": {},
            "nodes": [
                {"id": "a", "label": "A", "placement": {"kind": "absolute", "x": "0", "y": "0"}},
                {"id": "b", "label": "B", "placement": {"kind": "absolute", "x": "4", "y": "1"}},
            ],
            "edges": [],
            "groups": [],
            "layout_constraints": [],
            "validation_rules": [],
            "symmetry_contract": {
                "status": "required",
                "mode": "row_alignment",
                "pairs": [["a", "b"]],
                "justification": "paired vertices should align",
            },
        }
        report = {
            "overlap_status": "PASS",
            "symmetry_status": "FAIL",
            "visual_review": {"findings": []},
            "symmetry_review": {"findings": [{"rule_id": "P8_SYMMETRY_CONTRACT"}]},
        }

        repaired, changes = self.tikz.repair_spec_for_force_findings(spec, report, 1)

        self.assertTrue(any("common rows" in change for change in changes))
        self.assertEqual(repaired["nodes"][0]["placement"]["y"], "0.5000")
        self.assertEqual(repaired["nodes"][1]["placement"]["y"], "0.5000")
        self.assertEqual(repaired["nodes"][0]["placement"]["x"], "0")
        self.assertEqual(repaired["nodes"][1]["placement"]["x"], "4")

    def test_pass_status_suppresses_stale_structural_findings(self) -> None:
        report = {
            "overlap_status": "PASS",
            "symmetry_status": "PASS",
            "visual_review": {"findings": [{"pass_id": "V4_TEXT_TEXT_OVERLAP"}]},
            "symmetry_review": {"findings": [{"rule_id": "P8_SYMMETRY_CONTRACT"}]},
        }

        self.assertEqual(self.tikz.structural_issue_counts(report), (0, 0))
        self.assertTrue(self.tikz.structural_issue_free(report))

    def test_parser_exposes_force_check_and_render_credits(self) -> None:
        parser = self.tikz.build_parser()

        force_args = parser.parse_args(["force-check", "--artifacts", "F1.artifacts.json", "--work-dir", "."])
        render_args = parser.parse_args(["render", "--request", "Draw a pipeline"])

        self.assertEqual(force_args.command, "force-check")
        self.assertEqual(force_args.repair_credits, self.tikz.DEFAULT_FORCE_REPAIR_CREDITS)
        self.assertEqual(render_args.force_repair_credits, self.tikz.DEFAULT_FORCE_REPAIR_CREDITS)


if __name__ == "__main__":
    unittest.main()
