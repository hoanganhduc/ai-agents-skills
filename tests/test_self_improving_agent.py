from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "canonical" / "runtime" / "skills" / "self-improving-agent" / "self_improving_agent.py"


class SelfImprovingAgentRuntimeTests(unittest.TestCase):
    def run_helper(self, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HELPER), *args],
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_smoke_reports_portable_integration_features(self) -> None:
        completed = self.run_helper("smoke")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["smoke_mode"], "offline")
        self.assertFalse(payload["network_required"])
        self.assertTrue(payload["windows_error_patterns"])
        self.assertTrue(payload["windows_safety_patterns"])
        self.assertIn("Affected Install Targets", payload["integration_plan_fields"])
        self.assertIn("Affected OS/Substrates", payload["integration_plan_fields"])

    def test_windows_destructive_command_is_blocked(self) -> None:
        completed = self.run_helper("check-command-safety", "Remove-Item", "-Recurse", "-Force", "C:\\Users\\Example")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("BLOCKED", completed.stderr)

    def test_detect_common_errors_recognizes_powershell_markers(self) -> None:
        completed = self.run_helper("detect-common-errors", input_text="CategoryInfo : PermissionDenied\nFullyQualifiedErrorId : NativeCommandError\n")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Potential failure markers detected", completed.stdout)

    def test_review_pending_parses_integration_plan_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            learnings = Path(tmp) / ".learnings"
            learnings.mkdir()
            (learnings / "LEARNINGS.md").write_text(
                """# LEARNINGS

## [LRN-20260529-001] runtime

**Logged**: 2026-05-29T00:00:00Z
**Priority**: high
**Status**: pending

### Summary
Portable helper paths should use the runtime.

### Canonical Integration Plan
- Affected Install Targets: codex, claude, deepseek, copilot
- Affected OS/Substrates: linux, windows
""",
                encoding="utf-8",
            )
            completed = self.run_helper("review-pending", tmp, "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["pending_total"], 1)
        self.assertEqual(payload["shown"]["LEARNINGS.md"][0]["id"], "LRN-20260529-001")

    def test_integration_plan_output_has_target_and_os_fields(self) -> None:
        completed = self.run_helper(
            "integration-plan",
            "--summary",
            "Make helper routing portable",
            "--skill",
            "self-improving-agent",
            "--target",
            "codex",
            "--os",
            "windows",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Affected Install Targets", completed.stdout)
        self.assertIn("Affected OS/Substrates", completed.stdout)
        self.assertIn("Canonical Repo Change", completed.stdout)


if __name__ == "__main__":
    unittest.main()
