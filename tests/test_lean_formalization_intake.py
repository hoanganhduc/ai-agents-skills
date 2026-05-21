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


FIXTURES = REPO_ROOT / "tests" / "fixtures" / "lean_formalization_intake"
HELPER = REPO_ROOT / "canonical" / "runtime" / "skills" / "lean-formalization-intake" / "lean_formalization_intake.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("lean_formalization_intake", HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load lean_formalization_intake.py")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class LeanFormalizationIntakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.helper = load_helper()

    def scan(self, fixture: str) -> dict:
        payload = self.helper.scan_repo(FIXTURES / fixture, source_id=fixture)
        errors = self.helper.validate_contract(payload)
        self.assertEqual(errors, [])
        return payload

    def test_manifest_entry_and_runtime_declaration(self) -> None:
        manifests = load_manifests()
        spec = manifests["skills"]["skills"]["lean-formalization-intake"]
        self.assertEqual(spec["supported_agents"], ["codex", "claude", "deepseek"])
        self.assertIn("workflow-tools", spec["profiles"])
        self.assertIn("math", spec["profiles"])
        self.assertEqual(spec["required_dependencies"], ["python-runtime"])
        self.assertTrue((REPO_ROOT / "canonical" / "skills" / "lean-formalization-intake" / "SKILL.md").is_file())
        self.assertIn("lean-formalization-intake", manifests["runtime"]["skills"])

    def test_json_contract_rejects_bad_status_and_trust_claims(self) -> None:
        payload = self.scan("minimal_lake_toml")
        self.assertEqual(payload["runtime_behavior"], "incomplete analysis")
        self.assertTrue(payload["incomplete_analysis"])
        self.assertEqual(payload["trust_claim"]["tier"], "T0_STATIC_INTAKE")
        self.assertEqual(payload["trust_claim"]["checks_run"], [])
        self.assertEqual(payload["trust_claim"]["allowed_axioms"], "unchecked")
        self.assertEqual(payload["trust_claim"]["sorries_policy"], "unchecked")
        self.assertEqual(payload["trust_claim"]["statement_intent_review"]["status"], "not_reviewed")

        broken = json.loads(json.dumps(payload))
        broken["runtime_behavior"] = "complete"
        broken["trust_claim"]["tier"] = "T3_STRICT_VERIFIER"
        self.assertTrue(self.helper.validate_contract(broken))

        with self.assertRaises(ValueError):
            self.helper.status_entry("checked")

    def test_static_metadata_detection_for_lean_lake_and_task_repos(self) -> None:
        minimal = self.scan("minimal_lake_toml")
        self.assertEqual(minimal["fields"]["lean_toolchain"]["status"], "detected")
        self.assertEqual(minimal["fields"]["lakefile_toml"]["status"], "detected")
        self.assertEqual(minimal["fields"]["lakefile_lean"]["status"], "missing")

        lakefile_lean = self.scan("lakefile_lean")
        self.assertEqual(lakefile_lean["fields"]["lakefile_lean"]["status"], "detected")

        manifest = self.scan("manifest_mathlib")
        self.assertEqual(manifest["fields"]["lake_manifest"]["status"], "detected")
        self.assertEqual(manifest["fields"]["mathlib_revision"]["revision"], "mathlib-rev-001")

        task = self.scan("task_repo")
        self.assertEqual(task["fields"]["environment"]["status"], "detected")
        self.assertEqual(task["fields"]["problem_files"]["status"], "detected")
        self.assertEqual(task["fields"]["solution_files"]["status"], "detected")
        self.assertEqual(task["fields"]["task"]["status"], "detected")
        self.assertEqual(task["fields"]["requirement"]["status"], "detected")
        self.assertEqual(task["fields"]["source_materials"]["status"], "detected")

    def test_incomplete_analysis_and_banned_phrase_scan(self) -> None:
        payload = self.scan("task_repo")
        markdown = self.helper.render_markdown(payload)
        self.assertIn("Runtime behavior: incomplete analysis", markdown)
        serialized = json.dumps(payload).lower() + markdown.lower()
        for phrase in self.helper.BANNED_PROOF_PHRASES:
            self.assertNotIn(phrase, serialized)

    def test_minimal_repo_matches_golden_json_snapshot(self) -> None:
        payload = self.scan("minimal_lake_toml")
        snapshot = json.loads((FIXTURES / "snapshots" / "minimal_lake_toml.json").read_text(encoding="utf-8"))
        self.assertEqual(payload, snapshot)

    def test_filesystem_boundary_and_denied_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            shutil.copytree(FIXTURES / "task_repo", root)
            (root / ".env").write_text("token=fixture-env-secret-123456\n", encoding="utf-8")
            outside = Path(tmp) / "outside.lean"
            outside.write_text("axiom outside_secret : True\n", encoding="utf-8")
            try:
                (root / "escape.lean").symlink_to(outside)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink unavailable: {exc}")

            payload = self.helper.scan_repo(root)
            serialized = json.dumps(payload)
            self.assertNotIn("fixture-env-secret-123456", serialized)
            self.assertNotIn("outside_secret", serialized)
            reasons = {gap.get("reason") for gap in payload["unchecked_gaps"]}
            self.assertIn("denied_source_input", reasons)
            self.assertIn("symlink_skipped", reasons)

    def test_no_subprocess_network_env_or_mcp_behavior(self) -> None:
        def fail(*args, **kwargs):
            raise AssertionError("forbidden side effect")

        with patch.object(subprocess, "run", side_effect=fail), patch.object(socket, "socket", side_effect=fail):
            payload = self.scan("task_repo")
        self.assertTrue(payload["ok"])

    def test_redaction_and_do_not_run_commands(self) -> None:
        payload = self.scan("suspicious")
        serialized = json.dumps(payload)
        self.assertNotIn("fixture-redact-secret", serialized)
        self.assertNotIn("fixturesecretvalue12345", serialized)
        self.assertIn("<REDACTED", serialized)
        self.assertEqual(payload["fields"]["ci"]["status"], "detected")
        self.assertTrue(payload["do_not_run_commands"])
        self.assertTrue(any("git push" in item["command"] for item in payload["do_not_run_commands"]))
        self.assertTrue(any("lake build" in item["command"] for item in payload["candidate_commands"]))

    def test_malformed_inputs_are_gaps_not_crashes(self) -> None:
        payload = self.scan("malformed")
        reasons = " ".join(gap.get("reason", "") for gap in payload["unchecked_gaps"])
        self.assertIn("malformed_json", reasons)
        self.assertIn("possibly_malformed_toml", json.dumps(payload))

    def test_soundness_signals_are_advisory_only(self) -> None:
        payload = self.scan("advisory")
        kinds = {item["kind"] for item in payload["advisory_soundness_signals"]}
        for expected in {"sorry", "axiom", "native_decide", "implemented_by", "ffi", "opaque", "oracle"}:
            self.assertIn(expected, kinds)
        self.assertTrue(all(item["advisory_only"] for item in payload["advisory_soundness_signals"]))
        self.assertEqual(payload["trust_claim"]["allowed_axioms"], "unchecked")
        self.assertEqual(payload["trust_claim"]["sorries_policy"], "unchecked")

    def test_runtime_inventory_covers_helper_and_smoke_fails_closed(self) -> None:
        inventory = runtime_inventory(RUNTIME_SOURCE_ROOT)
        self.assertEqual(inventory["status"], "ok")
        paths = {item["path"] for item in inventory["entries"]}
        self.assertIn("skills/lean-formalization-intake/lean_formalization_intake.py", paths)
        with self.assertRaisesRegex(ValueError, "skills do not have runtime smoke coverage"):
            selected_runtime_skills(load_manifests(), {"lean-formalization-intake"})


if __name__ == "__main__":
    unittest.main()
