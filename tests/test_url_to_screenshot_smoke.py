from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.runtime_smoke import (
    runtime_command_target,
    runtime_smoke_skill_names,
    selected_runtime_skills,
    judge_expect,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DISPATCHER = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "url-to-screenshot-runtime"
    / "url_to_screenshot_runtime.py"
)


class UrlToScreenshotSmokeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.smoke_dir = Path(self.temporary.name)

    def test_runtime_enrolled_and_command_targets(self) -> None:
        manifests = load_manifests()
        self.assertIn("url-to-screenshot-runtime", selected_runtime_skills(manifests, None))
        self.assertEqual(
            runtime_command_target(manifests, "url-to-screenshot-runtime", "linux"),
            "skills/url-to-screenshot-runtime/run_url_to_screenshot.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "url-to-screenshot-runtime", "windows", "run_skill.ps1"),
            "skills/url-to-screenshot-runtime/run_url_to_screenshot.ps1",
        )
        self.assertEqual(
            runtime_command_target(manifests, "url-to-screenshot-runtime", "windows", "run_skill.ps1"),
            "skills/url-to-screenshot-runtime/run_url_to_screenshot.ps1",
        )

    def test_real_selftest_passes_validator_branch(self) -> None:
        manifests = load_manifests()
        completed = subprocess.run(
            [sys.executable, str(DISPATCHER), "selftest"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=True,
        )
        expect = manifests["runtime"]["skills"]["url-to-screenshot-runtime"]["smoke"]["expect"]
        failures = judge_expect(expect, completed, self.smoke_dir)
        self.assertEqual(failures, [])
        # These payload guards must remain in the declarative contract.
        self.assertIn({"path": "ok", "equals": True}, expect["stdout_json"])
        self.assertIn({"path": "browser_launched", "equals": False}, expect["stdout_json"])
        self.assertIn({"path": "network_required", "equals": False}, expect["stdout_json"])

    def test_validator_rejects_ok_false_with_exit_zero(self) -> None:
        manifests = load_manifests()
        # Synthetic ok:false but exit 0 must FAIL the validator (not pass on exit code alone).
        payload = (
            '{"ok": false, "passed": 1, "total": 2, "failures": [{"check": "x", "detail": ""}], '
            '"status": "failed", "smoke_mode": "offline", "network_required": false, '
            '"live_api_attempted": false, "package_install_attempted": false, '
            '"server_started": false, "config_written": false, "browser_launched": false}'
        )
        completed = subprocess.CompletedProcess([], 0, payload, "")
        expect = manifests["runtime"]["skills"]["url-to-screenshot-runtime"]["smoke"]["expect"]
        self.assertTrue(judge_expect(expect, completed, self.smoke_dir))

    def test_validator_rejects_browser_launched_true(self) -> None:
        manifests = load_manifests()
        payload = (
            '{"ok": true, "passed": 2, "total": 2, "failures": [], '
            '"status": "ok", "smoke_mode": "offline", "network_required": false, '
            '"live_api_attempted": false, "package_install_attempted": false, '
            '"server_started": false, "config_written": false, "browser_launched": true}'
        )
        completed = subprocess.CompletedProcess([], 0, payload, "")
        expect = manifests["runtime"]["skills"]["url-to-screenshot-runtime"]["smoke"]["expect"]
        self.assertTrue(judge_expect(expect, completed, self.smoke_dir))

    def test_validator_rejects_missing_safety_key(self) -> None:
        manifests = load_manifests()
        # A missing offline-safety key must fail (payload.get(...) is False semantics).
        payload = (
            '{"ok": true, "passed": 2, "total": 2, "failures": [], '
            '"status": "ok", "smoke_mode": "offline", "network_required": false}'
        )
        completed = subprocess.CompletedProcess([], 0, payload, "")
        expect = manifests["runtime"]["skills"]["url-to-screenshot-runtime"]["smoke"]["expect"]
        self.assertTrue(judge_expect(expect, completed, self.smoke_dir))

    def test_validator_rejects_missing_passed_and_total(self) -> None:
        manifests = load_manifests()
        # Both `passed` and `total` absent must FAIL all-passed (no None == None pass).
        payload = (
            '{"ok": true, "failures": [], "status": "ok", "smoke_mode": "offline", '
            '"network_required": false, "live_api_attempted": false, '
            '"package_install_attempted": false, "server_started": false, "config_written": false, "browser_launched": false}'
        )
        completed = subprocess.CompletedProcess([], 0, payload, "")
        expect = manifests["runtime"]["skills"]["url-to-screenshot-runtime"]["smoke"]["expect"]
        failures = judge_expect(expect, completed, self.smoke_dir)
        self.assertIn("stdout_json:passed:equals_path", failures)
        self.assertIn("stdout_json:passed:type", failures)
        self.assertIn("stdout_json:total:type", failures)


class OfflineSmokeValidatorParityTests(unittest.TestCase):
    """Every declared smoke case has executable expectations beyond its exit code."""

    def test_every_declared_smoke_case_has_meaningful_expect(self) -> None:
        manifests = load_manifests()
        output_operators = {"stdout_json", "stdout_regex", "stderr_regex", "files", "alternatives"}
        for skill, spec in manifests["runtime"]["skills"].items():
            contracts = {"smoke": spec["smoke"]} if "smoke" in spec else {}
            for kind in ("functional_smoke", "live_check"):
                contracts.update({f"{kind}:{name}": contract for name, contract in spec.get(kind, {}).items()})
            for name, contract in contracts.items():
                with self.subTest(skill=skill, case=name):
                    expect = contract.get("expect")
                    self.assertIsInstance(expect, dict)
                    self.assertTrue(expect, f"{skill}:{name} has no declared assertions")
                    self.assertTrue(any(expect.get(key) for key in output_operators),
                                    f"{skill}:{name} is checked only for its exit code")

    def test_url_to_screenshot_runtime_is_not_exit_code_only(self) -> None:
        manifests = load_manifests()
        self.assertIn("url-to-screenshot-runtime", runtime_smoke_skill_names(manifests))
        expect = manifests["runtime"]["skills"]["url-to-screenshot-runtime"]["smoke"]["expect"]
        self.assertTrue(expect["stdout_json"])


if __name__ == "__main__":
    unittest.main()
