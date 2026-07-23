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
            )
            self.assertTrue(summary["panel_content_pass"])
            self.assertIn("codex", summary["usable_providers"])
            self.assertNotIn("claude", summary["usable_providers"])


if __name__ == "__main__":
    unittest.main()
