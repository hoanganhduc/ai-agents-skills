"""Unit tests for goal_priority.v1 soft path discipline."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "autonomous-research-loop-runtime"
)
sys.path.insert(0, str(RUNTIME_DIR))

import goal_priority as gp  # noqa: E402
import autonomous_research_loop_runtime as rt  # noqa: E402


def _init_loop(tmp: Path, **kwargs: object) -> Path:
    run_dir = tmp / "loop"
    args = type(
        "A",
        (),
        {
            "dir": str(run_dir),
            "goal": "Prove X",
            "success_criteria": "artifact exists",
            "mode": "bounded-research",
            "max_iterations": 10,
            "max_wall_time_seconds": 3600,
            "max_tokens": 0,
            "max_usd": 0.0,
            "max_depth": 3,
            "max_hops": 20,
            "max_child_workers": 2,
            "plateau_rule": rt.DEFAULT_PLATEAU_RULE,
            "budget_owner": "user",
            "force": True,
            "stop_on_guard_fail": True,
            "stop_on_missing_evidence": True,
            "stop_on_scope_change": True,
            "success_check": "",
            "require_user_stop_only": False,
            "stop_condition": None,
            "goal_priority_template": bool(kwargs.get("goal_priority_template", False)),
        },
    )()
    rt.init_loop(args)
    return run_dir


def _write_gp(run_dir: Path, data: dict) -> None:
    (run_dir / "goal_priority.json").write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )


def _append_rows(run_dir: Path, rows: list[dict]) -> None:
    path = run_dir / "iterations.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


class GoalPriorityTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("AAS_AUTOLOOP_GOAL_PRIORITY", None)

    def test_missing_enabled_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(run_dir, {"primary_campaign": "A1"})
            cfg = gp.load_goal_priority(run_dir)
            self.assertFalse(cfg["_active"])
            self.assertTrue(any("enabled" in w for w in cfg["_warnings"]))

    def test_explicit_enabled_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(run_dir, {"enabled": True, "primary_campaign": "main"})
            cfg = gp.load_goal_priority(run_dir)
            self.assertTrue(cfg["_active"])

    def test_env_on_without_config_inert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            os.environ["AAS_AUTOLOOP_GOAL_PRIORITY"] = "on"
            cfg = gp.load_goal_priority(run_dir)
            self.assertFalse(cfg["_active"])
            self.assertTrue(any("inert" in w for w in cfg["_warnings"]))

    def test_env_on_with_config_activates_missing_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(run_dir, {"primary_campaign": "main"})
            os.environ["AAS_AUTOLOOP_GOAL_PRIORITY"] = "on"
            cfg = gp.load_goal_priority(run_dir)
            self.assertTrue(cfg["_active"])
            # Must not claim inactive when env forces on
            self.assertFalse(any("treating as inactive" in w for w in cfg["_warnings"]))

    def test_streak_boundary_and_replan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(
                run_dir,
                {
                    "enabled": True,
                    "max_consecutive_local_without_goal_delta": 2,
                    "require_goal_contribution_in_ledger": True,
                },
            )
            # Pre-boundary row without goal fields
            _append_rows(
                run_dir,
                [
                    {
                        "schema_version": "1.0",
                        "iteration": 1,
                        "mode": "bounded-research",
                        "objective": "bootstrap",
                        "decision": "continue",
                    },
                    {
                        "schema_version": "1.0",
                        "iteration": 2,
                        "mode": "bounded-research",
                        "objective": "start",
                        "decision": "continue",
                        "goal_contribution": "advance",
                        "campaign_id": "main",
                    },
                    {
                        "schema_version": "1.0",
                        "iteration": 3,
                        "mode": "bounded-research",
                        "objective": "local",
                        "decision": "continue",
                        # missing contribution after boundary
                    },
                    {
                        "schema_version": "1.0",
                        "iteration": 4,
                        "mode": "bounded-research",
                        "objective": "local2",
                        "decision": "continue",
                    },
                ],
            )
            self.assertEqual(gp.local_without_goal_delta_streak(run_dir), 2)
            self.assertTrue(gp.replan_required(run_dir))
            addon = gp.goal_priority_prompt_addon(run_dir)
            self.assertIn("REPLAN_REQUIRED", addon)

    def test_both_contribution_and_local_flag_counts_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(run_dir, {"enabled": True, "require_goal_contribution_in_ledger": True})
            _append_rows(
                run_dir,
                [
                    {
                        "iteration": 1,
                        "mode": "bounded-research",
                        "objective": "x",
                        "decision": "continue",
                        "goal_contribution": "advance",
                        "local_without_goal_delta": True,
                    }
                ],
            )
            self.assertEqual(gp.local_without_goal_delta_streak(run_dir), 1)

    def test_validate_always_has_warnings_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            result = rt.validate_loop_dir(run_dir)
            self.assertIn("warnings", result)
            self.assertIsInstance(result["warnings"], list)
            self.assertEqual(result["warnings"], [])

    def test_append_flags_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(run_dir, {"enabled": True})
            args = type(
                "A",
                (),
                {
                    "dir": str(run_dir),
                    "mode": "bounded-research",
                    "objective": "do work",
                    "decision": "continue",
                    "input_ref": None,
                    "source_id": None,
                    "claim_id": None,
                    "evidence_id": None,
                    "guard_ref": None,
                    "action_taken": None,
                    "output": "ok",
                    "remaining_gap": None,
                    "tokens": 0,
                    "usd": 0.0,
                    "wall_time_seconds": 0,
                    "stop_reason": "",
                    "goal_contribution": "advance",
                    "campaign_id": "main",
                    "local_without_goal_delta": False,
                    "local_without_goal_delta_tag": "",
                },
            )()
            out = rt.append_iteration(args)
            self.assertEqual(out["status"], "ok")
            self.assertIn("warnings", out)
            rows = gp.read_iterations_jsonl(run_dir)
            self.assertEqual(rows[-1]["goal_contribution"], "advance")
            self.assertEqual(rows[-1]["campaign_id"], "main")

    def test_init_goal_priority_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp), goal_priority_template=True)
            path = run_dir / "goal_priority.json"
            self.assertTrue(path.is_file())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIs(data["enabled"], False)

    def test_example_json_matches_template_file(self) -> None:
        template = REPO_ROOT / "canonical" / "templates" / "goal-priority.example.json"
        self.assertTrue(template.is_file())
        self.assertEqual(template.read_text(encoding="utf-8"), gp.example_goal_priority_json())

    def test_closed_forbid_in_brief(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(
                run_dir,
                {
                    "enabled": True,
                    "primary_campaign": "A1",
                    "closed_campaigns": [
                        {"id": "A1", "forbid_as_sole_primary": True}
                    ],
                },
            )
            block = gp.goal_priority_brief_block(run_dir)
            self.assertIn("forbid", block.lower())
            self.assertIn("A1", block)

    def test_panel_rank_false_still_has_goal_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(
                run_dir,
                {"enabled": True, "panel_rank_by_goal_ev": False, "primary_campaign": "main"},
            )
            block = gp.goal_priority_brief_block(run_dir)
            self.assertIn("goal_priority", block)
            self.assertNotIn("Rank candidate", block)


if __name__ == "__main__":
    unittest.main()
