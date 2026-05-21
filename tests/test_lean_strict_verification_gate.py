from __future__ import annotations

import importlib.util
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.ai_agents_skills.manifest import REPO_ROOT, load_manifests
from installer.ai_agents_skills.runtime import RUNTIME_SOURCE_ROOT, runtime_inventory
from installer.ai_agents_skills.runtime_smoke import selected_runtime_skills


FIXTURES = REPO_ROOT / "tests" / "fixtures" / "lean_strict_verification_gate"
INTAKE_HELPER = REPO_ROOT / "canonical" / "runtime" / "skills" / "lean-formalization-intake" / "lean_formalization_intake.py"
GATE_HELPER = REPO_ROOT / "canonical" / "runtime" / "skills" / "lean-strict-verification-gate" / "lean_strict_verification_gate.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class LeanStrictVerificationGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.intake = load_module("lean_formalization_intake_for_gate", INTAKE_HELPER)
        cls.gate = load_module("lean_strict_verification_gate", GATE_HELPER)

    def scan_ready(self) -> dict:
        payload = self.intake.scan_repo(
            FIXTURES / "ready_repo",
            source_id="AxiomMath/ready_repo@abc1234",
            expected_family="AxiomMath",
        )
        self.assertEqual(self.intake.validate_contract(payload), [])
        return payload

    def test_manifest_entry_and_runtime_declaration(self) -> None:
        manifests = load_manifests()
        spec = manifests["skills"]["skills"]["lean-strict-verification-gate"]
        self.assertEqual(spec["supported_agents"], ["codex", "claude", "deepseek"])
        self.assertIn("workflow-tools", spec["profiles"])
        self.assertIn("math", spec["profiles"])
        self.assertEqual(spec["required_dependencies"], ["python-runtime"])
        self.assertIn("lean-strict-verification-gate", manifests["runtime"]["skills"])

    def test_ready_intake_passes_static_preflight_only(self) -> None:
        payload = self.scan_ready()
        result = self.gate.evaluate_gate(FIXTURES / "ready_repo", payload, expected_source_id="AxiomMath/ready_repo@abc1234")
        self.assertTrue(result["ok"])
        self.assertEqual(result["gate_status"], "pass")
        self.assertEqual(result["gate_claim"]["tier"], "T1_STATIC_PREFLIGHT_READY")
        self.assertEqual(result["gate_claim"]["allowed_axioms"], "unchecked")
        self.assertEqual(result["gate_claim"]["sorries_policy"], "unchecked")
        self.assertEqual(result["gate_claim"]["statement_intent_review"]["status"], "not_reviewed")
        serialized = json.dumps(result).lower()
        self.assertIn("no proof-validity claim", serialized)
        for phrase in self.intake.BANNED_PROOF_PHRASES:
            self.assertNotIn(phrase, serialized)

    def test_missing_required_fields_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            shutil.copytree(FIXTURES / "ready_repo", repo)
            (repo / ".github" / "workflows" / "ci.yml").unlink()
            (repo / "LICENSE").unlink()
            payload = self.intake.scan_repo(repo, source_id="AxiomMath/repo@abc1234")
            result = self.gate.evaluate_gate(repo, payload)
        self.assertFalse(result["ok"])
        blocker_ids = {item["id"] for item in result["blockers"]}
        self.assertIn("field.ci", blocker_ids)
        self.assertIn("field.license", blocker_ids)

    def test_allow_missing_ci_and_license_is_explicit_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "ready_repo"
            shutil.copytree(FIXTURES / "ready_repo", repo)
            (repo / ".github" / "workflows" / "ci.yml").unlink()
            (repo / "LICENSE").unlink()
            payload = self.intake.scan_repo(repo, source_id="AxiomMath/ready_repo@abc1234")
            result = self.gate.evaluate_gate(repo, payload, require_ci=False, require_license=False)
        self.assertTrue(result["ok"])
        warning_ids = {item["id"] for item in result["warnings"]}
        self.assertEqual(warning_ids, {"field.ci", "field.license"})

    def test_overclaimed_intake_fails_closed(self) -> None:
        payload = self.scan_ready()
        payload["trust_claim"]["tier"] = "T3_STRICT_VERIFIER"
        payload["trust_claim"]["checks_run"] = ["lean_build"]
        result = self.gate.evaluate_gate(FIXTURES / "ready_repo", payload)
        self.assertFalse(result["ok"])
        blocker_ids = {item["id"] for item in result["blockers"]}
        self.assertIn("intake.trust_claim.tier", blocker_ids)
        self.assertIn("intake.trust_claim.checks_run", blocker_ids)

    def test_expected_source_and_commit_mismatch_fail_closed(self) -> None:
        payload = self.scan_ready()
        result = self.gate.evaluate_gate(
            FIXTURES / "ready_repo",
            payload,
            expected_source_id="AxiomMath/ready_repo@def5678",
            expected_commit="def5678",
        )
        self.assertFalse(result["ok"])
        blocker_ids = {item["id"] for item in result["blockers"]}
        self.assertIn("input.expected_source_id", blocker_ids)
        self.assertIn("input.expected_commit", blocker_ids)

    def test_do_not_run_redactions_and_gaps_fail_closed(self) -> None:
        payload = self.scan_ready()
        payload["do_not_run_commands"] = [{"status": "reported", "command": "git push origin main"}]
        payload["redactions"] = [{"status": "reported", "path": "task.md"}]
        payload["unchecked_gaps"] = [{"status": "unchecked", "path": "escape.lean", "reason": "symlink_skipped"}]
        result = self.gate.evaluate_gate(FIXTURES / "ready_repo", payload)
        self.assertFalse(result["ok"])
        blocker_ids = {item["id"] for item in result["blockers"]}
        self.assertIn("surface.do_not_run_commands", blocker_ids)
        self.assertIn("surface.redactions", blocker_ids)
        self.assertIn("surface.unchecked_gaps", blocker_ids)

    def test_no_subprocess_network_or_env_behavior(self) -> None:
        def fail(*args, **kwargs):
            raise AssertionError("forbidden side effect")

        payload = self.scan_ready()
        with patch.object(subprocess, "run", side_effect=fail), patch.object(socket, "socket", side_effect=fail):
            result = self.gate.evaluate_gate(FIXTURES / "ready_repo", payload)
        self.assertTrue(result["ok"])

    def test_cli_writes_outputs_and_exit_codes(self) -> None:
        payload = self.scan_ready()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            intake_path = tmp_path / "intake.json"
            output_json = tmp_path / "gate.json"
            output_md = tmp_path / "gate.md"
            intake_path.write_text(json.dumps(payload), encoding="utf-8")
            code = self.gate.main(
                [
                    str(FIXTURES / "ready_repo"),
                    "--intake-json",
                    str(intake_path),
                    "--expected-commit",
                    "abc1234",
                    "--output-json",
                    str(output_json),
                    "--output-md",
                    str(output_md),
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output_json.read_text(encoding="utf-8"))["gate_status"], "pass")
            self.assertIn("Gate status: `pass`", output_md.read_text(encoding="utf-8"))

            failed = self.gate.main(
                [
                    str(FIXTURES / "ready_repo"),
                    "--intake-json",
                    str(intake_path),
                    "--expected-commit",
                    "wrong",
                    "--output-json",
                    str(tmp_path / "failed-gate.json"),
                ]
            )
            self.assertEqual(failed, 1)

    def test_runtime_inventory_covers_helper_and_smoke_fails_closed(self) -> None:
        inventory = runtime_inventory(RUNTIME_SOURCE_ROOT)
        self.assertEqual(inventory["status"], "ok")
        paths = {item["path"] for item in inventory["entries"]}
        self.assertIn("skills/lean-strict-verification-gate/lean_strict_verification_gate.py", paths)
        with self.assertRaisesRegex(ValueError, "skills do not have runtime smoke coverage"):
            selected_runtime_skills(load_manifests(), {"lean-strict-verification-gate"})


if __name__ == "__main__":
    unittest.main()
