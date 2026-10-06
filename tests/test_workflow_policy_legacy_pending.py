"""An async formal check preserves an exact legacy append without rerunning its producer."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_workflow_policy_dispatch import runtime
from state_transaction import InjectedCrash


class LegacyPendingTests(unittest.TestCase):
    def fixture(self, root):
        loop = root / "loop"
        runtime.init_loop(runtime.selftest_init_args(loop, 1))
        (loop / "formal").mkdir(exist_ok=True)
        (loop / "formal/host_policy.pin.json").write_text(json.dumps({"policy": "on", "project": ".", "execution_backend": "kaggle-cpu"}), encoding="utf-8")
        (loop / "formal/terminal_state.json").write_text(json.dumps({"terminal_state": "sorry_free_artifact", "gate": {"scan": {"source_digest": "a" * 64}}}), encoding="utf-8")
        args = runtime.build_parser().parse_args(["append-iteration", "--dir", str(loop),
            "--mode", "bounded-research", "--objective", "verify result", "--output", "verified result",
            "--decision", "stop", "--stop-reason", "success", "--compute-none"])
        claim = {"terminal_state": "sorry_free_artifact", "source_digest": "a" * 64}
        return loop, args, claim

    def test_pending_never_banks_then_replays_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, args, claim = self.fixture(Path(tmp))
            with mock.patch.object(runtime, "_require_formal_terminal_state_for_success", return_value=claim), mock.patch.object(runtime, "reverify_formal_evidence", side_effect=[{"status": "verification_pending", "ok": False}, {"status": "reverified", "ok": True}]):
                pending = runtime.append_iteration(args)
                self.assertEqual(pending["verification_status"], "verification_pending")
                self.assertEqual(runtime.read_iterations(loop / "iterations.jsonl"), [])
                complete = runtime.resume_legacy_verification(loop)
            self.assertEqual(complete["status"], "ok")
            self.assertEqual(len(runtime.read_iterations(loop / "iterations.jsonl")), 1)
            self.assertFalse((loop / runtime.LEGACY_VERIFICATION_PENDING).exists())
            self.assertEqual(runtime.resume_legacy_verification(loop)["status"], "not_applicable")

    def test_changed_ledger_or_pin_cannot_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, args, claim = self.fixture(Path(tmp))
            with mock.patch.object(runtime, "_require_formal_terminal_state_for_success", return_value=claim), mock.patch.object(runtime, "reverify_formal_evidence", return_value={"status": "verification_pending", "ok": False}):
                runtime.append_iteration(args)
            (loop / "formal/host_policy.pin.json").write_text(json.dumps({"policy": "off"}), encoding="utf-8")
            with mock.patch.object(runtime, "reverify_formal_evidence") as verify:
                with self.assertRaises(ValueError):
                    runtime.resume_legacy_verification(loop)
            verify.assert_not_called()

    def test_driver_does_not_rerun_producer_while_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, args, claim = self.fixture(Path(tmp))
            with mock.patch.object(runtime, "_require_formal_terminal_state_for_success", return_value=claim), mock.patch.object(runtime, "reverify_formal_evidence", return_value={"status": "verification_pending", "ok": False}):
                runtime.append_iteration(args)
            drive = runtime.selftest_drive_args(loop, Path(tmp) / "registry", "/bin/false")
            drive.no_progress = True
            drive.max_review_waits = 1
            with mock.patch.object(runtime, "resume_legacy_verification", return_value={"status": "ok", "verification_status": "verification_pending"}), mock.patch.object(runtime, "run_primary_subprocess") as worker:
                result = runtime.drive_command(drive)
            self.assertEqual(result["reason"], "formal_verification_pending")
            self.assertEqual(result["status"], "pending")
            worker.assert_not_called()

    def test_unavailable_host_check_is_resumably_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, args, claim = self.fixture(Path(tmp))
            with mock.patch.object(runtime, "_require_formal_terminal_state_for_success", return_value=claim), mock.patch.object(runtime, "reverify_formal_evidence", return_value={"status": "verification_pending", "ok": False}):
                runtime.append_iteration(args)
            drive = runtime.selftest_drive_args(loop, Path(tmp) / "registry", "/bin/false")
            drive.no_progress = True
            with mock.patch.object(runtime, "resume_legacy_verification", side_effect=ValueError("host check unavailable")), mock.patch.object(runtime, "run_primary_subprocess") as worker:
                result = runtime.drive_command(drive)
            self.assertEqual(result["reason"], "formal_verification_unavailable")
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["exit_code"], 21)
            worker.assert_not_called()

    def test_stop_blocks_reverification_and_preserves_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, args, claim = self.fixture(Path(tmp))
            with mock.patch.object(runtime, "_require_formal_terminal_state_for_success", return_value=claim), mock.patch.object(runtime, "reverify_formal_evidence", return_value={"status": "verification_pending", "ok": False}):
                runtime.append_iteration(args)
            (loop / "STOP_REQUESTED").write_text("operator", encoding="utf-8")
            with mock.patch.object(runtime, "reverify_formal_evidence") as verify:
                result = runtime.resume_legacy_verification(loop)
            self.assertEqual(result["verification_status"], "paused_by_operator")
            verify.assert_not_called()
            self.assertTrue((loop / runtime.LEGACY_VERIFICATION_PENDING).exists())

    def test_interrupted_append_commit_recovers_without_double_bank(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, args, claim = self.fixture(Path(tmp))
            with mock.patch.object(runtime, "_require_formal_terminal_state_for_success", return_value=claim), mock.patch.object(runtime, "reverify_formal_evidence", return_value={"status": "verification_pending", "ok": False}):
                runtime.append_iteration(args)
            original = runtime.commit_transaction
            def crash(*a, **kw):
                return original(*a, **kw, crash_after=1)
            with mock.patch.object(runtime, "_require_formal_terminal_state_for_success", return_value=claim), mock.patch.object(runtime, "reverify_formal_evidence", return_value={"status": "reverified", "ok": True}), mock.patch.object(runtime, "commit_transaction", side_effect=crash):
                with self.assertRaises(InjectedCrash):
                    runtime.resume_legacy_verification(loop)
            result = runtime.resume_legacy_verification(loop)
            self.assertEqual(result["status"], "not_applicable")
            self.assertEqual(len(runtime.read_iterations(loop / "iterations.jsonl")), 1)
            self.assertEqual(json.loads((loop / "budget.json").read_text(encoding="utf-8"))["spent_iterations"], 1)

    def test_remote_dry_run_does_not_schedule_verification_or_save_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, args, claim = self.fixture(Path(tmp))
            args.dry_run = True
            with mock.patch.object(runtime, "_require_formal_terminal_state_for_success", return_value=claim), mock.patch.object(runtime, "reverify_formal_evidence") as verify:
                result = runtime.append_iteration(args)
            self.assertEqual(result["verification_status"], "verification_pending")
            self.assertTrue(result["dry_run"])
            verify.assert_not_called()
            self.assertFalse((loop / runtime.LEGACY_VERIFICATION_PENDING).exists())

    def test_exact_pending_is_durable_before_remote_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, args, claim = self.fixture(Path(tmp))
            def interrupted_remote(*a, **kw):
                self.assertTrue((loop / runtime.LEGACY_VERIFICATION_PENDING).exists())
                raise OSError("transport outcome unknown")
            with mock.patch.object(runtime, "_require_formal_terminal_state_for_success", return_value=claim), mock.patch.object(runtime, "reverify_formal_evidence", side_effect=interrupted_remote):
                with self.assertRaises(runtime.GuardError):
                    runtime.append_iteration(args)
            self.assertEqual(runtime.read_iterations(loop / "iterations.jsonl"), [])
            self.assertTrue((loop / runtime.LEGACY_VERIFICATION_PENDING).exists())

    def test_stop_arriving_during_verification_prevents_bank(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, args, claim = self.fixture(Path(tmp))
            def verified_then_stop(*a, **kw):
                (loop / "STOP_REQUESTED").write_text("operator", encoding="utf-8")
                return {"status": "reverified", "ok": True}
            with mock.patch.object(runtime, "_require_formal_terminal_state_for_success", return_value=claim), mock.patch.object(runtime, "reverify_formal_evidence", side_effect=verified_then_stop):
                with self.assertRaises(ValueError):
                    runtime.append_iteration(args)
            self.assertEqual(runtime.read_iterations(loop / "iterations.jsonl"), [])
            self.assertTrue((loop / runtime.LEGACY_VERIFICATION_PENDING).exists())


if __name__ == "__main__":
    unittest.main()
