"""A resumable research state must not hide a stopped driver invocation."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


RUNTIME = (Path(__file__).resolve().parents[1] / "canonical/runtime/skills"
           / "autonomous-research-loop-runtime")
sys.dont_write_bytecode = True
sys.path.insert(0, str(RUNTIME))
SPEC = importlib.util.spec_from_file_location(
    "live_driver_status_runtime", RUNTIME / "autonomous_research_loop_runtime.py")
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)


class LiveDriverStatusTests(unittest.TestCase):
    def test_stop_reason_visible_without_ending_research(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp)
            state = loop / "loop_state.json"
            state.write_text('{"status": "running"}\n', encoding="utf-8")
            before = state.read_bytes()
            runtime.write_live_status(loop, {
                "event": "drive_stop", "status": "running",
                "terminal_reason": "max_failures", "drive_cycle": 2,
                "spent_iterations": 6, "max_iterations": 1000,
            })
            body = (loop / "LIVE_STATUS.md").read_text(encoding="utf-8")
            self.assertIn("Driver invocation: **stopped**", body)
            self.assertIn("Driver stop reason: `max_failures`", body)
            self.assertIn("Loop status: `running`", body)
            self.assertEqual(state.read_bytes(), before)
            event = json.loads((loop / "driver_logs/progress.jsonl").read_text())
            self.assertEqual(event["terminal_reason"], "max_failures")

    def test_iteration_event_does_not_invent_a_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp)
            runtime.write_live_status(loop, {
                "event": "iteration_ok", "status": "running",
            })
            body = (loop / "LIVE_STATUS.md").read_text(encoding="utf-8")
            self.assertNotIn("Driver invocation: **stopped**", body)


if __name__ == "__main__":
    unittest.main()
