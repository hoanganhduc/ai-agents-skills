"""Unit tests for goal_priority.v1 soft path discipline."""

from __future__ import annotations

import ast
import json
import os
import subprocess
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

    def test_missing_enabled_key_inactive_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(run_dir, {"primary_campaign": "A1"})
            cfg = gp.load_goal_priority(run_dir)
            # Opt-in: missing enabled is not active; default discipline soft.
            self.assertFalse(cfg["_active"])
            self.assertEqual(cfg["discipline_mode"], "soft")
            self.assertTrue(any("enabled" in w for w in cfg["_warnings"]))

    def test_no_config_defaults_enabled_advise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            cfg = gp.load_goal_priority(run_dir)
            self.assertFalse(cfg["_active"])
            self.assertEqual(cfg["discipline_mode"], "soft")
            self.assertTrue(cfg.get("enabled") is False)

    def test_explicit_enabled_false_opts_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(run_dir, {"enabled": False, "primary_campaign": "A1"})
            cfg = gp.load_goal_priority(run_dir)
            self.assertFalse(cfg["_active"])

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
            self.assertEqual(data["discipline_mode"], "soft")

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
            block = gp.goal_priority_prompt_addon(run_dir)
            self.assertIn("forbid", block.lower())
            self.assertIn("A1", block)

    def test_panel_rank_false_still_has_goal_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(
                run_dir,
                {"enabled": True, "panel_rank_by_goal_ev": False, "primary_campaign": "main"},
            )
            block = gp.goal_priority_prompt_addon(run_dir)
            self.assertIn("goal_priority", block)
            self.assertNotIn("Rank candidate", block)



class ResidualIdentityTests(unittest.TestCase):
    """A residual id labels the work; it does not decide whether the work moved.

    `_counts_as_local` used to compare each row against the newer one beside it and
    count a low-value label only when it continued the same residual. Two clauses
    below that comparison already covered every low-value row, same residual or not,
    so the comparison never changed a verdict -- but it read as though residual
    identity mattered, and the comment under it named a condition it did not test.
    The streak is specified on iterations, not residuals: three bare `advance` rows
    are three iterations without a goal delta either way. These tests pin that, so a
    residual comparison cannot come back as a live discriminator.
    """

    CFG = {
        "enabled": True,
        "discipline_mode": "hard",
        "primary_campaign": "A2",
        "next_campaigns_ordered": ["A2"],
        "campaign_registry": {"A2": {"objective": "encoding residual"}},
        "require_goal_contribution_in_ledger": True,
        "max_consecutive_local_without_goal_delta": 3,
        "host_signal_epoch_iteration": 1,
    }

    def _streak_over(self, residual_ids: list[str]) -> tuple[int, bool]:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(run_dir, dict(self.CFG))
            _append_rows(run_dir, [
                {"iteration": n, "goal_contribution": "advance",
                 "campaign_id": "A2", "residual_id": rid}
                for n, rid in enumerate(residual_ids, start=3)
            ])
            return (gp.local_without_goal_delta_streak(run_dir),
                    gp.replan_required(run_dir))

    def test_bare_advance_on_one_residual_forces_a_replan(self) -> None:
        self.assertEqual(self._streak_over(["k3", "k3", "k3"]), (3, True))

    def test_the_same_run_of_advances_counts_the_same_however_it_is_labelled(self) -> None:
        same = self._streak_over(["k3", "k3", "k3"])
        spread = self._streak_over(["k3", "k7", "k9"])
        unlabelled = self._streak_over(["", "", ""])

        self.assertEqual(same, spread)
        self.assertEqual(same, unlabelled)

    def test_a_progress_label_still_breaks_the_run_on_one_residual(self) -> None:
        """The control: collapsing the residual clauses must not make the streak
        indiscriminate. A progress label on the same residual still stops it."""

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(run_dir, dict(self.CFG))
            _append_rows(run_dir, [
                {"iteration": 3, "goal_contribution": "advance",
                 "campaign_id": "A2", "residual_id": "k3"},
                {"iteration": 4, "goal_contribution": "construct",
                 "campaign_id": "A2", "residual_id": "k3"},
                {"iteration": 5, "goal_contribution": "advance",
                 "campaign_id": "A2", "residual_id": "k3"},
            ])

            self.assertEqual(gp.local_without_goal_delta_streak(run_dir), 1)
            self.assertFalse(gp.replan_required(run_dir))


if __name__ == "__main__":
    unittest.main()


class GoalPriorityV2Ship1Tests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("AAS_AUTOLOOP_GOAL_PRIORITY", None)

    def test_residual_inventory_open_leaves_in_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(
                run_dir,
                {
                    "schema_version": "goal_priority.v1",
                    "enabled": True,
                    "discipline_mode": "soft",
                    "primary_campaign": "A2",
                    "require_goal_contribution_in_ledger": True,
                },
            )
            (run_dir / "residual_inventory.json").write_text(
                json.dumps(
                    {
                        "schema_version": "residual_inventory.v1",
                        "host_signal_epoch_iteration": 10,
                        "leaves": [
                            {
                                "id": "k2_lr",
                                "status": "open",
                                "scope_lock": "encoding_only",
                            },
                            {"id": "done_leaf", "status": "closed"},
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            addon = gp.goal_priority_prompt_addon(run_dir)
            self.assertIn("k2_lr", addon)
            self.assertNotIn("done_leaf", addon)
            self.assertIn("Host-signal epoch iteration: 10", addon)
            self.assertIn("never authorizes", addon)
            self.assertIn("does **not** stop the loop", addon)

    def test_soft_mode_no_advance_deprecation_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(
                run_dir,
                {
                    "enabled": True,
                    "discipline_mode": "soft",
                    "require_goal_contribution_in_ledger": True,
                },
            )
            warns = gp.collect_goal_priority_warnings(
                run_dir, latest_record={"goal_contribution": "advance", "campaign_id": "A2"}
            )
            self.assertFalse(any("bare goal_contribution" in w for w in warns), warns)

    def test_advise_mode_warns_on_bare_advance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(
                run_dir,
                {
                    "enabled": True,
                    "discipline_mode": "advise",
                    "require_goal_contribution_in_ledger": True,
                },
            )
            warns = gp.collect_goal_priority_warnings(
                run_dir, latest_record={"goal_contribution": "advance", "campaign_id": "A2"}
            )
            self.assertTrue(any("bare goal_contribution" in w for w in warns), warns)

    def test_malformed_inventory_degrades(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(run_dir, {"enabled": True})
            (run_dir / "residual_inventory.json").write_text("{not json", encoding="utf-8")
            cfg = gp.load_goal_priority(run_dir)
            self.assertTrue(cfg.get("_active"))
            inv = cfg.get("_residual_inventory") or {}
            self.assertTrue(inv.get("_warnings"))
            # prompt still works
            self.assertIn("goal_priority", gp.goal_priority_prompt_addon(run_dir, cfg))

    def test_append_residual_id_and_scope_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(run_dir, {"enabled": True, "discipline_mode": "soft"})
            args = type(
                "A",
                (),
                {
                    "dir": str(run_dir),
                    "mode": "bounded-research",
                    "objective": "close leaf",
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
                    "goal_contribution": "eliminate",
                    "campaign_id": "A2",
                    "local_without_goal_delta": False,
                    "local_without_goal_delta_tag": "",
                    "residual_id": "k2_lr",
                    "scope_lock": "encoding_only",
                    "goal_contribution_detail": "no lock word",
                },
            )()
            out = rt.append_iteration(args)
            self.assertEqual(out["status"], "ok")
            rows = gp.read_iterations_jsonl(run_dir)
            self.assertEqual(rows[-1].get("residual_id"), "k2_lr")
            self.assertEqual(rows[-1].get("scope_lock"), "encoding_only")
            self.assertEqual(rows[-1].get("goal_contribution_detail"), "no lock word")


class GoalPriorityHardSteerTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("AAS_AUTOLOOP_GOAL_PRIORITY", None)

    def test_hard_replan_rewrites_path_not_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(
                run_dir,
                {
                    "enabled": True,
                    "discipline_mode": "hard",
                    "primary_campaign": "A2",
                    "next_campaigns_ordered": ["A2", "A3"],
                    "campaign_registry": {
                        "A2": {"objective": "encoding residual"},
                        "A3": {"objective": "hardness premise pack"},
                    },
                    "require_goal_contribution_in_ledger": True,
                    "max_consecutive_local_without_goal_delta": 3,
                    "host_signal_epoch_iteration": 1,
                },
            )
            (run_dir / "residual_inventory.json").write_text(
                json.dumps(
                    {
                        "schema_version": "residual_inventory.v1",
                        "host_signal_epoch_iteration": 1,
                        "leaves": [
                            {
                                "id": "k2_lr",
                                "status": "closed",
                                "campaign_id": "A2",
                                "scope_lock": "encoding_only",
                                "recovery_aliases": ["k2_lr"],
                            },
                            {
                                "id": "k3",
                                "status": "open",
                                "campaign_id": "A2",
                                "goal_ev": "high",
                                "scope_lock": "encoding_only",
                                "description": "T3 k=3 residual",
                                "recovery_aliases": ["k3"],
                            },
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            # Path targets closed leaf only
            state = json.loads((run_dir / "loop_state.json").read_text(encoding="utf-8"))
            state["status"] = "running"
            state["next_preferred_path"] = (
                "SINGLE PATH: continue k2_lr residual only; ignore k3"
            )
            state["last_iteration"] = 5
            (run_dir / "loop_state.json").write_text(
                json.dumps(state, indent=2) + "\n", encoding="utf-8"
            )
            (run_dir / "recovery.md").write_text(
                "\n".join(
                    [
                        "# Recovery",
                        "- **Status:** running.",
                        "- **Last completed iteration:** 5",
                        "- **Next safe action:** continue k2_lr residual only",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            # Three low-value advances on same residual -> replan by streak too
            _append_rows(
                run_dir,
                [
                    {
                        "iteration": 3,
                        "goal_contribution": "advance",
                        "campaign_id": "A2",
                        "residual_id": "k2_lr",
                    },
                    {
                        "iteration": 4,
                        "goal_contribution": "advance",
                        "campaign_id": "A2",
                        "residual_id": "k2_lr",
                    },
                    {
                        "iteration": 5,
                        "goal_contribution": "advance",
                        "campaign_id": "A2",
                        "residual_id": "k2_lr",
                    },
                ],
            )
            self.assertTrue(gp.replan_required(run_dir))
            out = gp.apply_hard_path_discipline(run_dir)
            self.assertTrue(out.get("applied"), out)
            state2 = json.loads((run_dir / "loop_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state2.get("status"), "running")  # never rewritten
            self.assertIn("k3", state2.get("next_preferred_path", ""))
            self.assertIn("hard replan", state2.get("next_preferred_path", ""))
            rec = (run_dir / "recovery.md").read_text(encoding="utf-8")
            self.assertIn("k3", rec)
            self.assertIn("Next safe action", rec)

    def test_soft_mode_does_not_rewrite_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(
                run_dir,
                {
                    "enabled": True,
                    "discipline_mode": "soft",
                    "require_goal_contribution_in_ledger": True,
                    "max_consecutive_local_without_goal_delta": 2,
                    "host_signal_epoch_iteration": 1,
                },
            )
            _append_rows(
                run_dir,
                [
                    {"iteration": 1, "campaign_id": "A2"},
                    {"iteration": 2, "campaign_id": "A2"},
                ],
            )
            state = json.loads((run_dir / "loop_state.json").read_text(encoding="utf-8"))
            old = "KEEP THIS PATH"
            state["next_preferred_path"] = old
            (run_dir / "loop_state.json").write_text(
                json.dumps(state, indent=2) + "\n", encoding="utf-8"
            )
            out = gp.apply_hard_path_discipline(run_dir)
            self.assertFalse(out.get("applied"))
            state2 = json.loads((run_dir / "loop_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state2.get("next_preferred_path"), old)

    def test_path_targets_closed_triggers_replan_hard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(
                run_dir,
                {
                    "enabled": True,
                    "discipline_mode": "hard",
                    "primary_campaign": "A2",
                    "host_signal_epoch_iteration": 1,
                },
            )
            (run_dir / "residual_inventory.json").write_text(
                json.dumps(
                    {
                        "leaves": [
                            {
                                "id": "k2_lr",
                                "status": "closed",
                                "recovery_aliases": ["k2_lr"],
                            },
                            {
                                "id": "k3",
                                "status": "open",
                                "goal_ev": "high",
                                "description": "k3 residual",
                                "recovery_aliases": ["k3"],
                            },
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            state = json.loads((run_dir / "loop_state.json").read_text(encoding="utf-8"))
            state["next_preferred_path"] = "continue k2_lr residual only"
            state["last_iteration"] = 1
            (run_dir / "loop_state.json").write_text(
                json.dumps(state, indent=2) + "\n", encoding="utf-8"
            )
            (run_dir / "recovery.md").write_text(
                "- **Next safe action:** continue k2_lr residual only\n",
                encoding="utf-8",
            )
            self.assertTrue(gp.path_targets_closed_residual(run_dir))
            self.assertTrue(gp.replan_required(run_dir))
            out = gp.apply_hard_path_discipline(run_dir)
            self.assertTrue(out.get("applied"), out)
            self.assertIn("k3", out.get("path", ""))


class IterationLedgerRotationTests(unittest.TestCase):
    """Every ledger reader spans rotated shards, in record order."""

    def test_readers_span_rotated_shards_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            rows = [
                {"iteration": index, "decision": "continue", "mode": "bounded-research"}
                for index in range(1, 8)
            ]
            for name, chunk in (
                ("iterations.1.jsonl", rows[:3]),
                ("iterations.2.jsonl", rows[3:5]),
                ("iterations.jsonl", rows[5:]),
            ):
                (run_dir / name).write_text(
                    "".join(json.dumps(row, sort_keys=True) + "\n" for row in chunk),
                    encoding="utf-8",
                )
            # A double-digit shard must not sort before a single-digit one.
            (run_dir / "iterations.10.jsonl").write_text("", encoding="utf-8")

            ordered = [path.name for path in gp.iteration_ledger_paths(run_dir)]
            self.assertEqual(
                ordered,
                [
                    "iterations.1.jsonl",
                    "iterations.2.jsonl",
                    "iterations.10.jsonl",
                    "iterations.jsonl",
                ],
            )
            self.assertEqual(
                [row["iteration"] for row in gp.read_iterations_jsonl(run_dir)],
                [1, 2, 3, 4, 5, 6, 7],
            )
            self.assertEqual(
                [row["iteration"] for row in rt.read_iterations(run_dir / "iterations.jsonl")],
                [1, 2, 3, 4, 5, 6, 7],
            )

    def test_an_unrotated_ledger_reads_exactly_as_before(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.assertEqual(gp.read_iterations_jsonl(run_dir), [])
            self.assertEqual(rt.read_iterations(run_dir / "iterations.jsonl"), [])
            (run_dir / "iterations.jsonl").write_text(
                json.dumps({"iteration": 1}, sort_keys=True) + "\n", encoding="utf-8"
            )
            self.assertEqual(gp.read_iterations_jsonl(run_dir), [{"iteration": 1}])
            self.assertEqual(
                rt.read_iterations(run_dir / "iterations.jsonl"), [{"iteration": 1}]
            )


# Runs in a child so the file-size limit dies with it. The limit is the cheapest
# real way to make a write fail partway: the kernel refuses past the cap, which
# is what a full disk or a killed supervisor looks like from inside the write.
_CRASHING_WRITE_CHILD = """
import json, resource, sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import goal_priority as gp
run_dir = Path(sys.argv[2])
resource.setrlimit(resource.RLIMIT_FSIZE, (int(sys.argv[3]), int(sys.argv[3])))
out = gp.apply_hard_path_discipline(run_dir)
print(json.dumps({"applied": out.get("applied"), "reason": out.get("reason")}))
"""


class HardReplanWriteDurabilityTests(unittest.TestCase):
    """A hard replan that cannot finish its write must change nothing.

    ``apply_hard_path_discipline`` rewrote ``loop_state.json`` and ``recovery.md``
    with plain ``write_text``, which truncates the destination before writing.
    A write that died partway left the loop's own state file cut off mid-token:
    no status, no goal, no success criteria, and no way back. The result dict
    reported the failure, so the caller could see something went wrong -- and
    could do nothing about it, because the file it would have retried from was
    already gone.
    """

    CAP = 1024

    def _replan_fixture(self, tmp: Path) -> Path:
        run_dir = _init_loop(tmp)
        _write_gp(
            run_dir,
            {
                "enabled": True,
                "discipline_mode": "hard",
                "primary_campaign": "A2",
                "next_campaigns_ordered": ["A2", "A3"],
                "campaign_registry": {
                    "A2": {"objective": "encoding residual"},
                    "A3": {"objective": "hardness premise pack"},
                },
                "require_goal_contribution_in_ledger": True,
                "max_consecutive_local_without_goal_delta": 3,
                "host_signal_epoch_iteration": 1,
            },
        )
        (run_dir / "residual_inventory.json").write_text(
            json.dumps(
                {
                    "schema_version": "residual_inventory.v1",
                    "host_signal_epoch_iteration": 1,
                    "leaves": [
                        {
                            "id": "k2_lr",
                            "status": "closed",
                            "campaign_id": "A2",
                            "scope_lock": "encoding_only",
                            "recovery_aliases": ["k2_lr"],
                        },
                        {
                            "id": "k3",
                            "status": "open",
                            "campaign_id": "A2",
                            "goal_ev": "high",
                            "scope_lock": "encoding_only",
                            "description": "T3 k=3 residual",
                            "recovery_aliases": ["k3"],
                        },
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        state = json.loads((run_dir / "loop_state.json").read_text(encoding="utf-8"))
        state["status"] = "running"
        state["next_preferred_path"] = "SINGLE PATH: continue k2_lr residual only"
        state["last_iteration"] = 5
        (run_dir / "loop_state.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        (run_dir / "recovery.md").write_text(
            "# Recovery\n"
            "- **Status:** running.\n"
            "- **Last completed iteration:** 5\n"
            "- **Next safe action:** continue k2_lr residual only\n",
            encoding="utf-8",
        )
        _append_rows(
            run_dir,
            [
                {
                    "iteration": i,
                    "goal_contribution": "advance",
                    "campaign_id": "A2",
                    "residual_id": "k2_lr",
                }
                for i in (3, 4, 5)
            ],
        )
        return run_dir

    @unittest.skipUnless(
        os.name == "posix", "RLIMIT_FSIZE is the POSIX way to fail a write partway"
    )
    def test_a_write_that_dies_partway_leaves_loop_state_intact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._replan_fixture(Path(tmp))
            state_before = (run_dir / "loop_state.json").read_bytes()
            recovery_before = (run_dir / "recovery.md").read_bytes()
            child = Path(tmp) / "crashing_write.py"
            child.write_text(_CRASHING_WRITE_CHILD, encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(child),
                    str(RUNTIME_DIR),
                    str(run_dir),
                    str(self.CAP),
                ],
                check=False,
                text=True,
                capture_output=True,
                encoding="utf-8",
                timeout=120,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            reported = json.loads(proc.stdout.strip().splitlines()[-1])
            # The write really did fail -- otherwise this test proves nothing.
            self.assertFalse(reported["applied"], reported)
            self.assertIn("write_failed", reported["reason"])

            after = (run_dir / "loop_state.json").read_bytes()
            self.assertEqual(after, state_before)
            state = json.loads(after.decode("utf-8"))
            self.assertEqual(state["status"], "running")
            self.assertEqual(state["goal"], "Prove X")
            self.assertEqual(state["success_criteria"], "artifact exists")
            self.assertEqual((run_dir / "recovery.md").read_bytes(), recovery_before)

    def test_no_loop_file_is_rewritten_with_a_truncating_open(self) -> None:
        """Holds on every host, including the ones without RLIMIT_FSIZE.

        ``Path.write_text`` is the truncating open; the guarantee above is only
        as good as this module never reaching for it again.
        """

        tree = ast.parse((RUNTIME_DIR / "goal_priority.py").read_text(encoding="utf-8"))
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_text"
        ]
        self.assertEqual(offenders, [], f"write_text at lines {offenders}")


class ContributionVocabularyTests(unittest.TestCase):
    """The labels the module recommends must not be the ones it penalises.

    ``goal_priority`` tells the agent which contribution labels to prefer over a
    bare ``advance``, and separately counts a streak of low-value rows toward a
    forced replan. The two lists were maintained by hand and drifted:
    ``verify_trust`` was recommended in the guidance and simultaneously sat in
    ``LOW_VALUE_CONTRIBUTIONS``, so an agent that followed the advice was
    penalised for it. These tests pin both the behaviour and the single source
    the guidance is rendered from.
    """

    # Three results, each independently audited. Different residual ids, so no
    # row here repeats the work of another.
    AUDITED = ["k3", "k7", "k9"]

    CFG = {
        "enabled": True,
        "discipline_mode": "hard",
        "primary_campaign": "A2",
        "next_campaigns_ordered": ["A2"],
        "campaign_registry": {"A2": {"objective": "encoding residual"}},
        "require_goal_contribution_in_ledger": True,
        "max_consecutive_local_without_goal_delta": 3,
        "host_signal_epoch_iteration": 1,
    }

    def _streak_for(self, label: str) -> tuple[int, bool]:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(run_dir, dict(self.CFG))
            _append_rows(
                run_dir,
                [
                    {
                        "iteration": n,
                        "goal_contribution": label,
                        "campaign_id": "A2",
                        "residual_id": rid,
                    }
                    for n, rid in enumerate(self.AUDITED, start=3)
                ],
            )
            return (
                gp.local_without_goal_delta_streak(run_dir),
                gp.replan_required(run_dir),
            )

    def test_independent_audits_do_not_force_a_replan(self) -> None:
        streak, replan = self._streak_for("verify_trust")
        self.assertEqual(streak, 0)
        self.assertFalse(replan)

    def test_an_audit_counts_the_same_as_the_formal_gate_beside_it(self) -> None:
        """``verify_trust`` and ``formalize`` are both gates discharged."""

        self.assertEqual(self._streak_for("verify_trust"), self._streak_for("formalize"))

    def test_bare_advance_on_separate_residuals_still_forces_a_replan(self) -> None:
        """The control: the label the guidance discourages is still penalised."""

        streak, replan = self._streak_for("advance")
        self.assertEqual(streak, 3)
        self.assertTrue(replan)

    def test_no_label_is_both_recommended_and_penalised(self) -> None:
        self.assertEqual(
            gp.PROGRESS_CONTRIBUTIONS & gp.LOW_VALUE_CONTRIBUTIONS, frozenset()
        )

    def test_the_rendered_guidance_names_exactly_the_progress_labels(self) -> None:
        self.assertEqual(
            frozenset(gp.PREFERRED_CONTRIBUTIONS_TEXT.split("/")),
            gp.PROGRESS_CONTRIBUTIONS,
        )
        self.assertEqual(
            len(gp.PREFERRED_CONTRIBUTIONS_TEXT.split("/")),
            len(gp.PROGRESS_CONTRIBUTIONS),
        )

    def test_both_places_the_agent_reads_carry_that_one_list(self) -> None:
        """The prompt addon and the append warning render the same source."""

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _init_loop(Path(tmp))
            _write_gp(run_dir, dict(self.CFG))
            addon = gp.goal_priority_prompt_addon(run_dir)
            warnings = gp.collect_goal_priority_warnings(
                run_dir,
                latest_record={
                    "iteration": 3,
                    "goal_contribution": "advance",
                    "campaign_id": "A2",
                },
            )
        self.assertIn(gp.PREFERRED_CONTRIBUTIONS_TEXT, addon)
        discouraged = [w for w in warnings if "discouraged" in w]
        self.assertTrue(discouraged, warnings)
        self.assertIn(gp.PREFERRED_CONTRIBUTIONS_TEXT, discouraged[0])

    def test_the_open_vocabulary_covers_both_categories(self) -> None:
        """A recommended label must not also be flagged as unknown on append."""

        known = gp.PROGRESS_CONTRIBUTIONS | gp.LOW_VALUE_CONTRIBUTIONS
        self.assertEqual(known - {""}, gp.CONTRIBUTION_VOCABULARY)
