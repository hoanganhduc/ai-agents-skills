from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.runtime_smoke import (
    runtime_command_target,
    runtime_smoke_skill_names,
    selected_runtime_skills,
    validate_smoke_output,
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


class _FakeCompleted:
    """Minimal stand-in for subprocess.CompletedProcess for the validator."""

    def __init__(self, returncode: int, stdout: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class UrlToScreenshotSmokeContractTests(unittest.TestCase):
    def test_runtime_enrolled_and_command_targets(self) -> None:
        manifests = load_manifests()
        self.assertIn("url-to-screenshot-runtime", selected_runtime_skills(manifests, None))
        self.assertEqual(
            runtime_command_target(manifests, "url-to-screenshot-runtime", "linux"),
            "skills/url-to-screenshot-runtime/run_url_to_screenshot.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "url-to-screenshot-runtime", "windows", "run_skill.bat"),
            "skills/url-to-screenshot-runtime/run_url_to_screenshot.bat",
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
            timeout=60,
            check=True,
        )
        checks = validate_smoke_output(manifests, "url-to-screenshot-runtime", completed, ["selftest"])
        self.assertTrue(all(check["ok"] for check in checks), checks)
        names = {c["name"] for c in checks}
        # The branch must machine-check the JSON contract, not just the exit code.
        self.assertIn("json-ok", names)
        self.assertIn("browser-not-launched", names)
        self.assertIn("network-not-required", names)

    def test_validator_rejects_ok_false_with_exit_zero(self) -> None:
        manifests = load_manifests()
        # Synthetic ok:false but exit 0 must FAIL the validator (not pass on exit code alone).
        payload = (
            '{"ok": false, "passed": 1, "total": 2, "failures": [{"check": "x", "detail": ""}], '
            '"status": "failed", "smoke_mode": "offline", "network_required": false, '
            '"live_api_attempted": false, "package_install_attempted": false, '
            '"server_started": false, "browser_launched": false}'
        )
        completed = _FakeCompleted(0, payload)
        checks = validate_smoke_output(manifests, "url-to-screenshot-runtime", completed, ["selftest"])
        self.assertFalse(all(check["ok"] for check in checks), checks)

    def test_validator_rejects_browser_launched_true(self) -> None:
        manifests = load_manifests()
        payload = (
            '{"ok": true, "passed": 2, "total": 2, "failures": [], '
            '"status": "ok", "smoke_mode": "offline", "network_required": false, '
            '"live_api_attempted": false, "package_install_attempted": false, '
            '"server_started": false, "browser_launched": true}'
        )
        completed = _FakeCompleted(0, payload)
        checks = validate_smoke_output(manifests, "url-to-screenshot-runtime", completed, ["selftest"])
        self.assertFalse(all(check["ok"] for check in checks), checks)

    def test_validator_rejects_missing_safety_key(self) -> None:
        manifests = load_manifests()
        # A missing offline-safety key must fail (payload.get(...) is False semantics).
        payload = (
            '{"ok": true, "passed": 2, "total": 2, "failures": [], '
            '"status": "ok", "smoke_mode": "offline", "network_required": false}'
        )
        completed = _FakeCompleted(0, payload)
        checks = validate_smoke_output(manifests, "url-to-screenshot-runtime", completed, ["selftest"])
        self.assertFalse(all(check["ok"] for check in checks), checks)

    def test_validator_rejects_missing_passed_and_total(self) -> None:
        manifests = load_manifests()
        # Both `passed` and `total` absent must FAIL all-passed (no None == None pass).
        payload = (
            '{"ok": true, "failures": [], "status": "ok", "smoke_mode": "offline", '
            '"network_required": false, "live_api_attempted": false, '
            '"package_install_attempted": false, "server_started": false, "browser_launched": false}'
        )
        completed = _FakeCompleted(0, payload)
        checks = validate_smoke_output(manifests, "url-to-screenshot-runtime", completed, ["selftest"])
        all_passed = next(c for c in checks if c["name"] == "all-passed")
        self.assertFalse(all_passed["ok"], checks)


class OfflineSmokeValidatorParityTests(unittest.TestCase):
    """M5: every offline-smoke runtime skill that emits a JSON safety body must have a
    dedicated validate_smoke_output branch (no silent fallthrough to exit-code-only)."""

    # Skills whose selftest intentionally does not emit a JSON safety body and for
    # which exit-code-only validation is explicitly accepted.
    EXIT_CODE_ONLY_ACCEPTED = {"slides-to-video", "send-email", "manim-math-animation"}

    @staticmethod
    def _validate_smoke_branch_skills(validator_src: str) -> set:
        """Skill names that appear in an actual ``validate_smoke_output`` branch condition.

        Recognizes the two structural branch styles the validator uses --
        ``skill == "<name>"`` and ``skill in {... "<name>" ...}`` -- by parsing the
        function body, so a bare skill-name mention (a comment or coverage row)
        never counts as a branch. This is the M5 tightening: the old guard's
        ``f'"{skill}"' in validator_src`` disjunct matched any literal anywhere.
        """
        import ast

        tree = ast.parse(validator_src)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "validate_smoke_output":
                target = node
                break
        assert target is not None, "validate_smoke_output not found"
        found: set = set()
        for node in ast.walk(target):
            # skill == "name"
            if isinstance(node, ast.Compare):
                left = node.left
                if (
                    isinstance(left, ast.Name)
                    and left.id == "skill"
                    and node.ops
                    and isinstance(node.ops[0], (ast.Eq, ast.In))
                ):
                    for comp in node.comparators:
                        if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                            found.add(comp.value)
                        elif isinstance(comp, (ast.Set, ast.List, ast.Tuple)):
                            for elt in comp.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    found.add(elt.value)
        return found

    def test_offline_smoke_skills_have_validator_branch_or_are_accepted(self) -> None:
        manifests = load_manifests()
        validator_src = (
            REPO_ROOT / "installer" / "ai_agents_skills" / "runtime_smoke.py"
        ).read_text(encoding="utf-8")
        branch_skills = self._validate_smoke_branch_skills(validator_src)
        for skill in runtime_smoke_skill_names(manifests):
            if skill in self.EXIT_CODE_ONLY_ACCEPTED:
                continue
            self.assertIn(
                skill,
                branch_skills,
                f"offline-smoke skill {skill!r} has no validate_smoke_output branch and is not accepted as exit-code-only",
            )

    def test_url_to_screenshot_runtime_is_not_exit_code_only(self) -> None:
        manifests = load_manifests()
        self.assertIn("url-to-screenshot-runtime", runtime_smoke_skill_names(manifests))
        self.assertNotIn("url-to-screenshot-runtime", self.EXIT_CODE_ONLY_ACCEPTED)


if __name__ == "__main__":
    unittest.main()
