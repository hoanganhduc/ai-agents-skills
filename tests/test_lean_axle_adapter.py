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
ADAPTER_HELPER = REPO_ROOT / "canonical" / "runtime" / "skills" / "lean-axle-adapter" / "lean_axle_adapter.py"


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


class LeanAxleAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.intake = load_module("lean_formalization_intake_for_axle", INTAKE_HELPER)
        cls.gate = load_module("lean_strict_verification_gate_for_axle", GATE_HELPER)
        cls.adapter = load_module("lean_axle_adapter", ADAPTER_HELPER)

    def ready_payloads(self) -> tuple[dict, dict]:
        intake_payload = self.intake.scan_repo(
            FIXTURES / "ready_repo",
            source_id="AxiomMath/ready_repo@abc1234",
            expected_family="AxiomMath",
        )
        self.assertEqual(self.intake.validate_contract(intake_payload), [])
        gate_payload = self.gate.evaluate_gate(
            FIXTURES / "ready_repo",
            intake_payload,
            expected_source_id="AxiomMath/ready_repo@abc1234",
            expected_commit="abc1234",
        )
        self.assertTrue(gate_payload["ok"])
        return intake_payload, gate_payload

    def test_manifest_entry_and_runtime_declaration(self) -> None:
        manifests = load_manifests()
        spec = manifests["skills"]["skills"]["lean-axle-adapter"]
        self.assertEqual(spec["supported_agents"], ["codex", "claude", "deepseek"])
        self.assertIn("workflow-tools", spec["profiles"])
        self.assertIn("math", spec["profiles"])
        self.assertEqual(spec["required_dependencies"], ["python-runtime"])
        self.assertIn("lean-axle-adapter", manifests["runtime"]["skills"])
        self.assertIn("lean-axle-adapter", manifests["runtime"]["runtime_profiles"]["full"]["skills"])

    def test_passed_gate_produces_noop_contract_only(self) -> None:
        intake_payload, gate_payload = self.ready_payloads()
        result = self.adapter.evaluate_adapter(
            FIXTURES / "ready_repo",
            gate_payload,
            intake_payload=intake_payload,
            expected_source_id="AxiomMath/ready_repo@abc1234",
            expected_commit="abc1234",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["adapter_status"], "noop_contract_ready")
        self.assertEqual(result["dry_run_claim"]["tier"], "T1_AXLE_NOOP_DRY_RUN")
        contract = result["request_contract"]
        self.assertFalse(contract["would_call_axle"])
        self.assertEqual(contract["endpoint_allowlist"], [])
        self.assertEqual(contract["credential_env_allowlist"], [])
        self.assertFalse(contract["mcp_config_mutation"])
        self.assertFalse(contract["background_server"])
        self.assertFalse(contract["network_access"])
        self.assertEqual(contract["theorem_intent_review"]["status"], "not_reviewed")
        self.assertIn("problem.lean", contract["formal_input_file_hashes"])
        self.assertIn("solution.lean", contract["formal_input_file_hashes"])
        self.assertTrue(contract["formal_input_bundle_sha256"].startswith("sha256:"))
        serialized = json.dumps(result).lower()
        self.assertIn("no axle call", serialized)
        self.assertIn("no proof-validity claim", serialized)
        self.assertNotIn("formally verified", serialized)

    def test_failed_gate_blocks_contract(self) -> None:
        intake_payload, gate_payload = self.ready_payloads()
        gate_payload["gate_status"] = "fail"
        gate_payload["blockers"] = [{"id": "field.ci", "status": "fail", "severity": "blocker"}]
        result = self.adapter.evaluate_adapter(FIXTURES / "ready_repo", gate_payload, intake_payload=intake_payload)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["request_contract"])
        blocker_ids = {item["id"] for item in result["blockers"]}
        self.assertIn("gate.status", blocker_ids)
        self.assertIn("gate.blockers", blocker_ids)

    def test_gate_warnings_and_intake_mismatch_fail_closed(self) -> None:
        intake_payload, gate_payload = self.ready_payloads()
        gate_payload["warnings"] = [{"id": "field.ci", "status": "warn", "severity": "warning"}]
        intake_payload["repo"]["source_id"] = "AxiomMath/other@abc1234"
        result = self.adapter.evaluate_adapter(FIXTURES / "ready_repo", gate_payload, intake_payload=intake_payload)
        self.assertFalse(result["ok"])
        blocker_ids = {item["id"] for item in result["blockers"]}
        self.assertIn("gate.warnings", blocker_ids)
        self.assertIn("intake.source_id", blocker_ids)

    def test_bundle_hashing_rejects_path_escape(self) -> None:
        intake_payload, gate_payload = self.ready_payloads()
        intake_payload["fields"]["problem_files"]["files"] = [{"status": "detected", "path": "../escape.lean"}]
        result = self.adapter.evaluate_adapter(FIXTURES / "ready_repo", gate_payload, intake_payload=intake_payload)
        self.assertFalse(result["ok"])
        self.assertIn("bundle.problem_files", {item["id"] for item in result["blockers"]})

    def test_no_subprocess_network_or_env_behavior(self) -> None:
        def fail(*args, **kwargs):
            raise AssertionError("forbidden side effect")

        intake_payload, gate_payload = self.ready_payloads()
        with patch.object(subprocess, "run", side_effect=fail), patch.object(socket, "socket", side_effect=fail):
            result = self.adapter.evaluate_adapter(FIXTURES / "ready_repo", gate_payload, intake_payload=intake_payload)
        self.assertTrue(result["ok"])

    def test_cli_writes_outputs_and_exit_codes(self) -> None:
        intake_payload, gate_payload = self.ready_payloads()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            intake_path = tmp_path / "intake.json"
            gate_path = tmp_path / "gate.json"
            output_json = tmp_path / "axle.json"
            output_md = tmp_path / "axle.md"
            intake_path.write_text(json.dumps(intake_payload), encoding="utf-8")
            gate_path.write_text(json.dumps(gate_payload), encoding="utf-8")
            code = self.adapter.main(
                [
                    str(FIXTURES / "ready_repo"),
                    "--gate-json",
                    str(gate_path),
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
            self.assertEqual(json.loads(output_json.read_text(encoding="utf-8"))["adapter_status"], "noop_contract_ready")
            self.assertIn("Adapter status: `noop_contract_ready`", output_md.read_text(encoding="utf-8"))

            gate_payload["gate_status"] = "fail"
            gate_path.write_text(json.dumps(gate_payload), encoding="utf-8")
            failed = self.adapter.main(
                [
                    str(FIXTURES / "ready_repo"),
                    "--gate-json",
                    str(gate_path),
                    "--intake-json",
                    str(intake_path),
                    "--output-json",
                    str(tmp_path / "failed-axle.json"),
                ]
            )
            self.assertEqual(failed, 1)

    def test_runtime_inventory_covers_helper_and_smoke_fails_closed(self) -> None:
        inventory = runtime_inventory(RUNTIME_SOURCE_ROOT)
        self.assertEqual(inventory["status"], "ok")
        paths = {item["path"] for item in inventory["entries"]}
        self.assertIn("skills/lean-axle-adapter/lean_axle_adapter.py", paths)
        with self.assertRaisesRegex(ValueError, "skills do not have runtime smoke coverage"):
            selected_runtime_skills(load_manifests(), {"lean-axle-adapter"})


if __name__ == "__main__":
    unittest.main()
