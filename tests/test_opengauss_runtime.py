"""Offline unit tests for opengauss inert runtime helper."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.runtime_smoke import (
    runtime_command_target,
    selected_runtime_skills,
    validate_smoke_output,
)

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "canonical" / "runtime" / "skills" / "opengauss" / "opengauss.py"


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base = os.environ.copy()
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_API_KEY", "AAS_GAUSS"):
        base.pop(key, None)
    if env:
        base.update(env)
    return subprocess.run(
        [sys.executable, str(HELPER), *args],
        capture_output=True,
        text=True,
        env=base,
        check=False,
    )


class OpenGaussRuntimeTests(unittest.TestCase):
    def test_smoke_offline_ok(self) -> None:
        res = _run("smoke", env={"ANTHROPIC_API_KEY": "OPENGAUSS-SMOKE-CANARY"})
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("smoke_mode"), "offline")
        self.assertTrue(data.get("no_auto_install"))
        self.assertFalse(data.get("installs_attempted"))
        self.assertFalse(data.get("network_required"))
        self.assertFalse(data.get("live_api_attempted"))
        self.assertFalse(data.get("server_started"))
        self.assertFalse(data.get("config_written"))
        self.assertFalse(data.get("gauss_launched"))
        self.assertTrue(data.get("snippet_contains_placeholder"))
        self.assertTrue(data.get("snippet_has_install_pointer"))
        self.assertTrue(data.get("native_windows_refused"))
        self.assertNotIn("OPENGAUSS-SMOKE-CANARY", res.stdout)
        policy = data.get("evidence_policy") or {}
        self.assertIn("opengauss_run", policy)
        self.assertIn("formal_check", policy)

    def test_doctor_does_not_execute_tools(self) -> None:
        res = _run("doctor")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        for name, status in (data.get("tool_status") or {}).items():
            self.assertFalse(status.get("executed"), name)
            self.assertIn(status.get("status"), {"available", "tool_unavailable"})

    def test_config_snippet_placeholders(self) -> None:
        res = _run("config-snippet")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        blob = json.dumps(data)
        self.assertIn("<ANTHROPIC_OR_OPENAI_API_KEY>", blob)
        self.assertIn("https://github.com/math-inc/OpenGauss", blob)
        self.assertEqual(data.get("redaction_status"), "placeholder-only")
        self.assertEqual(
            data.get("local_install_snippet", {}).get("native_windows", {}).get("status"),
            "unsupported",
        )

    def test_selftest_alias(self) -> None:
        res = _run("selftest")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("smoke_mode"), "offline")

    def test_auth_presence_redacted(self) -> None:
        canary = "TESTONLY_AUTH_VALUE_SHOULD_NOT_ECHO"
        res = _run("doctor", env={"ANTHROPIC_API_KEY": canary})
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn(canary, res.stdout)
        data = json.loads(res.stdout)
        self.assertEqual(data["backend_auth_status"]["claude"], "present")

    def test_validate_smoke_branch(self) -> None:
        res = _run("smoke", env={"ANTHROPIC_API_KEY": "OPENGAUSS-SMOKE-CANARY"})
        self.assertEqual(res.returncode, 0)
        checks = validate_smoke_output({}, "opengauss", res, ["smoke"])
        self.assertTrue(all(c["ok"] for c in checks), checks)

    def test_live_prove_smoke_refuses_without_opt_in(self) -> None:
        env = os.environ.copy()
        env.pop("AAS_OPENGAUSS_LIVE_PROVE", None)
        res = _run(
            "live-prove-smoke",
            "--project-root",
            "/tmp/no-such-lean-project",
            env=env,
        )
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("error_code"), "live_prove_disabled")
        self.assertFalse(data.get("live_api_attempted"))
        self.assertFalse(data.get("gauss_launched"))

    def test_live_preflight_reports_tools(self) -> None:
        res = _run("live-preflight")
        self.assertIn(res.returncode, (0, 2), res.stdout + res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("command"), "live-preflight")
        self.assertIn("tools", data)
        self.assertIn("claude", data["tools"])
        self.assertIn("gauss", data["tools"])
        self.assertFalse(data.get("live_api_attempted"))
        self.assertIn("live_test_policy", data)

    def test_doctor_lists_claude_discovery(self) -> None:
        res = _run("doctor")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        tools = data.get("tool_status") or {}
        self.assertIn("claude", tools)
        self.assertFalse(tools["claude"].get("executed"))

    def test_spike_and_fail_closed_launch(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            res = _run("spike", "--work-dir", tmp)
            self.assertIn(res.returncode, (0, 2), res.stdout + res.stderr)
            data = json.loads(res.stdout)
            self.assertIn(data.get("outcome"), {"headless_qualified", "interactive_only", "failed"})
            self.assertFalse(data.get("unlocks_phase3_auto") and data.get("outcome") != "headless_qualified")

            res2 = _run("launch", "--work-dir", tmp, "--workflow", "prove")
            self.assertNotEqual(res2.returncode, 0)
            launch = json.loads(res2.stdout)
            self.assertFalse(launch.get("gauss_launched"))
            self.assertFalse(launch.get("ok"))

            # Even forced unqualified requires env flag; still no live spawn.
            res3 = _run(
                "launch",
                "--work-dir",
                tmp,
                "--workflow",
                "prove",
                "--force-unqualified",
                env={"AAS_OPENGAUSS_ALLOW_FORCE": "1"},
            )
            body = json.loads(res3.stdout)
            self.assertFalse(body.get("gauss_launched"))
            self.assertIn(body.get("error_code"), {"no_headless_driver", "force_forbidden", "not_headless_qualified"})

    def test_handoff_helpers(self) -> None:
        res = _run(
            "handoff-intake",
            "--claim-id",
            "C1",
            "--informal-statement-ref",
            "claims/C1.md",
            "--project-root",
            "/tmp/lean-project",
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data["handoff"]["schema"], "opengauss.intake_handoff.v1")
        self.assertTrue(data["handoff"]["no_claim_support"])


class OpenGaussManifestTests(unittest.TestCase):
    def test_profiles_and_runtime_wired(self) -> None:
        manifests = load_manifests()
        skill = manifests["skills"]["skills"]["opengauss"]
        self.assertEqual(
            set(skill["profiles"]),
            {"formal-research", "formal-research-remote", "full-research"},
        )
        self.assertIn("offline-smoke", skill["verification"])
        self.assertIn("opengauss", manifests["runtime"]["runtime_profiles"]["full"]["skills"])
        selected = set(selected_runtime_skills(manifests, {"opengauss"}))
        self.assertEqual(selected, {"opengauss"})
        self.assertEqual(
            runtime_command_target(manifests, "opengauss", "linux"),
            "skills/opengauss/run_opengauss.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "opengauss", "windows", "run_skill.bat"),
            "skills/opengauss/run_opengauss.bat",
        )
        self.assertEqual(
            runtime_command_target(manifests, "opengauss", "windows", "run_skill.ps1"),
            "skills/opengauss/run_opengauss.ps1",
        )


if __name__ == "__main__":
    unittest.main()
