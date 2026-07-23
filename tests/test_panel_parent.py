"""Unit tests for ARL host-owned panel_parent (hybrid multi-agent model)."""

from __future__ import annotations

import json
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

import panel_parent as pp  # noqa: E402


class PanelParentUnitTests(unittest.TestCase):
    def test_usable_stdout_accepts_short_smoke(self) -> None:
        self.assertTrue(pp.usable_stdout("PANEL_SMOKE_OK\n"))
        self.assertTrue(pp.usable_stdout("• PANEL_SMOKE_OK\n"))
        self.assertFalse(pp.usable_stdout(""))
        self.assertFalse(pp.usable_stdout("tokens used\n29\n"))

    def test_classify_error(self) -> None:
        self.assertEqual(pp.classify_error("timeout exceeded", 124), "timeout")
        self.assertEqual(
            pp.classify_error("Read-only file system (os error 30)", 1),
            "read_only_filesystem",
        )
        self.assertEqual(pp.classify_error("rate limit exceeded", 1), "quota_or_credit")

    def test_dispatch_phase_with_fake_runner(self) -> None:
        def runner(cmd, env, cwd, timeout_s):  # noqa: ANN001
            # cmd ends with prompt for codex/claude; last arg or -p next
            return 0, "PANEL_SMOKE_OK\n", ""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            iter_dir = root / "iterations" / "iter001"
            summary = pp.dispatch_phase(
                iter_dir=iter_dir,
                phase="target_advice",
                prompt="Reply PANEL_SMOKE_OK",
                providers=["claude", "codex", "codewhale", "kimi"],
                timeout_s=5,
                root=root,
                runner=runner,
            )
            self.assertTrue(summary["panel_content_pass"])
            self.assertEqual(len(summary["usable_providers"]), 4)
            self.assertTrue((iter_dir / "panel" / "01_target_advice" / "claude.md").is_file())
            self.assertTrue((iter_dir / "data" / "panel_dispatch_target_advice.json").is_file())

    def test_resolve_panel_mode_auto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.assertFalse(pp.resolve_panel_mode("off", run_dir))
            self.assertTrue(pp.resolve_panel_mode("on", run_dir))
            self.assertFalse(pp.resolve_panel_mode("auto", run_dir))
            (run_dir / "panel.json").write_text(
                json.dumps({"enabled": True, "providers": ["claude"]}),
                encoding="utf-8",
            )
            self.assertTrue(pp.resolve_panel_mode("auto", run_dir))
            cfg = pp.load_panel_config(run_dir)
            self.assertEqual(cfg["providers"], ["claude"])

    def test_host_synthesis_and_prompt_addon(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            iter_dir = Path(tmp) / "iter001"
            summary = {
                "usable_providers": ["claude"],
                "panel_content_pass": True,
                "different_family_logic_available": True,
                "results": {
                    "claude": {
                        "status": "ok",
                        "error_class": None,
                        "exit_code": 0,
                    }
                },
            }
            path = pp.write_host_synthesis(
                iter_dir, "target_advice", summary, next_path="SINGLE PATH: M3"
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn("SINGLE PATH: M3", text)
            self.assertIn("claude", text)
            addon = pp.panel_prompt_addon(Path(tmp), iter_dir)
            self.assertIn("Do NOT", addon)
            self.assertIn("nest multi-agent", addon)

    def test_one_provider_fails_still_pass(self) -> None:
        def runner(cmd, env, cwd, timeout_s):  # noqa: ANN001
            joined = " ".join(cmd)
            if "claude" in joined:
                return 1, "", "connection failed"
            return 0, "enough usable content for panel advice here\n", ""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = pp.dispatch_phase(
                iter_dir=root / "i",
                phase="result_review",
                prompt="review",
                providers=["claude", "codex"],
                timeout_s=5,
                root=root,
                runner=runner,
                panel_cfg={"timeout_mode": "fixed", "timeouts": {"result_review": 5}},
            )
            self.assertTrue(summary["panel_content_pass"])
            self.assertIn("codex", summary["usable_providers"])
            self.assertNotIn("claude", summary["usable_providers"])

    def test_timeout_fixed_mode_same_for_all(self) -> None:
        budgets = pp.compute_provider_timeouts(
            "result_review",
            "short",
            ["claude", "kimi", "codex"],
            {
                "timeout_mode": "fixed",
                "timeouts": {"result_review": 400},
                "timeout_calc": {"min_s": 1, "max_s": 2400},
            },
            explicit_timeout_s=400,
        )
        vals = {b["timeout_s"] for b in budgets.values()}
        self.assertEqual(vals, {400})
        self.assertTrue(all(b["timeout_mode"] == "fixed" for b in budgets.values()))

    def test_timeout_adaptive_size_and_provider_mult(self) -> None:
        small = pp.compute_provider_timeouts(
            "result_review",
            "x" * 100,
            ["codex", "kimi"],
            {"timeout_mode": "adaptive", "timeouts": {"result_review": 900}},
        )
        large = pp.compute_provider_timeouts(
            "result_review",
            "x" * 20000,
            ["codex", "kimi"],
            {"timeout_mode": "adaptive", "timeouts": {"result_review": 900}},
        )
        self.assertGreater(large["codex"]["timeout_s"], small["codex"]["timeout_s"])
        self.assertGreaterEqual(large["kimi"]["timeout_s"], large["codex"]["timeout_s"])
        self.assertLessEqual(large["kimi"]["timeout_s"], 2400)

    def test_timeout_clamp(self) -> None:
        budgets = pp.compute_provider_timeouts(
            "result_review",
            "x" * 500000,
            ["kimi"],
            {
                "timeout_mode": "adaptive",
                "timeouts": {"result_review": 900},
                "timeout_calc": {"max_s": 1000, "min_s": 120},
            },
        )
        self.assertEqual(budgets["kimi"]["timeout_s"], 1000)

    def test_timeout_history_pad(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            data = run_dir / "iterations" / "iter001" / "data"
            data.mkdir(parents=True)
            (data / "panel_dispatch_result_review.json").write_text(
                json.dumps(
                    {
                        "phase": "result_review",
                        "results": {
                            "kimi": {
                                "usable": True,
                                "elapsed_s": 1100,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            budgets = pp.compute_provider_timeouts(
                "result_review",
                "short",
                ["kimi"],
                {"timeout_mode": "adaptive", "timeouts": {"result_review": 900}},
                run_dir=run_dir,
            )
            # hist 1100 * 1.25 = 1375, times mult 1.5 → well above 900
            self.assertGreaterEqual(budgets["kimi"]["timeout_s"], 1300)
            self.assertIn("timeout_inputs", budgets["kimi"])

    def test_dispatch_records_timeout_inputs(self) -> None:
        def runner(cmd, env, cwd, timeout_s):  # noqa: ANN001
            return 0, "enough usable content for panel advice here\n", ""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = pp.dispatch_phase(
                iter_dir=root / "i",
                phase="result_review",
                prompt="review me",
                providers=["codex"],
                timeout_s=180,
                root=root,
                runner=runner,
                panel_cfg={
                    "timeout_mode": "fixed",
                    "timeouts": {"result_review": 180},
                    "timeout_calc": {"min_s": 1, "max_s": 2400},
                },
            )
            meta = summary["results"]["codex"]
            self.assertEqual(meta["timeout_s"], 180)
            self.assertEqual(meta["timeout_mode"], "fixed")
            self.assertIn("timeout_inputs", meta)

    def test_target_brief_order_goal_before_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "recovery.md").write_text(
                "# recovery\nlong recovery body\n", encoding="utf-8"
            )
            (run_dir / "loop_state.json").write_text(
                json.dumps(
                    {
                        "goal": "G",
                        "success_criteria": "S",
                        "next_preferred_path": "PATH-A",
                        "last_iteration": 1,
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "goal_priority.json").write_text(
                json.dumps({"enabled": True, "primary_campaign": "main"}),
                encoding="utf-8",
            )
            (run_dir / "iterations.jsonl").write_text("", encoding="utf-8")
            brief = pp.build_target_brief(run_dir)
            goal_i = brief.find("Goal-EV") if "Goal-EV" in brief else brief.find("goal_priority")
            path_i = brief.find("next_preferred_path")
            rec_i = brief.find("recovery.md")
            self.assertGreaterEqual(goal_i, 0)
            self.assertGreater(path_i, goal_i)
            self.assertGreater(rec_i, path_i)
            self.assertIn("PATH-A", brief)


if __name__ == "__main__":
    unittest.main()
