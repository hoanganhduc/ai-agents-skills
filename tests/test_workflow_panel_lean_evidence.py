"""The existing native Lean runner retains binary evidence without a second engine."""
import base64
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

PATH = Path(__file__).resolve().parents[1] / "canonical/runtime/skills/lean-strict-verification-gate/lean_strict_verification_gate.py"
spec = importlib.util.spec_from_file_location("workflow_lean_gate", PATH)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class NativeEvidenceTests(unittest.TestCase):
    def test_existing_typecheck_wrapper_attaches_full_bytes(self):
        result = gate.run_typecheck([sys.executable, "-c", "import os; os.write(1,b'\\xffraw')"],
            timeout=5, command_label="offline capture probe", runner="direct-lean", cwd=None,
            tool_status_payload={})
        self.assertEqual(base64.b64decode(result["process_evidence"]["stdout"]["base64"]), b"\xffraw")

    def test_binary_evidence_is_separate_from_text_presentation(self):
        result = gate.run_bounded_command([sys.executable, "-c", "import os; os.write(1,b'\\xff\\x00original')"], timeout=5, capture_evidence=True)
        self.assertEqual(base64.b64decode(result.process_evidence["stdout"]["base64"]), b"\xff\x00original")
        self.assertEqual(result.process_evidence["schema_version"], "process_evidence.v1")

    def test_timeout_preserves_partial_bytes_and_typed_outcome(self):
        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            gate.run_bounded_command([sys.executable, "-c", "import os,time; os.write(1,b'\\xffpartial'); time.sleep(5)"], timeout=0.2, capture_evidence=True)
        evidence = raised.exception.process_evidence
        self.assertTrue(evidence["timed_out"])
        self.assertEqual(base64.b64decode(evidence["stdout"]["base64"]), b"\xffpartial")

    def test_overflow_is_typed_and_retained_bytes_are_explicitly_partial(self):
        with self.assertRaises(gate.CommandOutputLimitExceeded) as raised:
            gate.run_bounded_command([sys.executable, "-c", "import os; os.write(1,b'x'*100)"], timeout=5, max_output_bytes=32, capture_evidence=True)
        evidence = raised.exception.process_evidence
        self.assertTrue(evidence["oversized"])
        self.assertFalse(evidence["capture_complete"])
        self.assertEqual(evidence["stdout"]["state"], "partial")
        self.assertEqual(len(base64.b64decode(evidence["stdout"]["base64"])), 32)


if __name__ == "__main__":
    unittest.main()
