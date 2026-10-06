"""Dispatch authority is checked before calls, not inferred from narration."""
from __future__ import annotations

import importlib.util
import io
import base64
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from tests import test_autonomous_research_loop as arl_tests

PACK = Path(__file__).resolve().parents[1] / "canonical/runtime/skills/autonomous-research-loop-runtime"
sys.path.insert(0, str(PACK))
spec = importlib.util.spec_from_file_location("workflow_policy_runtime", PACK / "autonomous_research_loop_runtime.py")
runtime = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runtime
spec.loader.exec_module(runtime)


class DispatchPolicyTests(unittest.TestCase):
    def test_primary_byte_capture_is_exact_and_secret_bytes_are_withheld(self):
        raw = b"invalid utf8: \xff\xfe\r\n"
        process = runtime.provider_resources.BoundedProcessResult(0, raw, b"", False, False, None, None, 0)
        evidence = runtime._primary_capture_evidence(process, {})
        self.assertEqual(base64.b64decode(evidence["stdout"]["base64"]), raw)
        secret = "primary-fixture-secret-77391"
        process = runtime.provider_resources.BoundedProcessResult(0, secret.encode(), b"", False, False, None, None, 0)
        evidence = runtime._primary_capture_evidence(process, {"OPENAI_API_KEY": secret})
        self.assertEqual(evidence["stdout"], {"state": "withheld", "reason": "sensitive_output"})
        self.assertNotIn(secret, json.dumps(evidence))
        self.assertNotIn("sha256", evidence["stdout"])

    @unittest.skipUnless(sys.platform.startswith("linux"), "the primary containment path requires Linux PID namespaces")
    def test_only_separate_error_stderr_sets_credit_class(self):
        envelope = json.dumps({"type": "error", "error": {"type": "insufficient_quota"}}).encode()
        for stdout, stderr, expected in ((envelope, b"", "failure"), (b"", envelope, "hard_quota")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                metadata = {}
                bounded = SimpleNamespace(stdout=stdout, stderr=stderr, return_code=1,
                    cleanup_error=None, timed_out=False, oversized=False, capture_error=None)
                with mock.patch.object(runtime.provider_resources, "_trusted_host_binary", return_value=Path("/usr/bin/bwrap")), mock.patch.object(runtime, "interpreter_bound_provider_command", side_effect=lambda x: x), mock.patch.object(runtime, "trusted_local_containment_command", side_effect=lambda argv, **kw: argv), mock.patch.object(runtime, "resource_limited_command", return_value=(["/bin/false"], {"output_max_bytes": 10000}, "test.scope")), mock.patch.object(runtime, "run_bounded_resource_process", return_value=bounded) as run:
                    runtime.run_primary_subprocess(["/bin/false"], use_shell=False, child_env={},
                        cwd=Path(tmp), timeout_s=5, output=io.StringIO(), provider="claude",
                        trusted_local=True, resource_metadata=metadata)
                self.assertFalse(run.call_args.kwargs["merge_stderr"])
                self.assertEqual(metadata["provider_failure_class"], expected)

    def drive_fixture(self, root: Path):
        loop = root / "loop"
        runtime.init_loop(runtime.selftest_init_args(loop, 5))
        args = runtime.selftest_drive_args(loop, root / "registry", "unused")
        args.cmd = None
        args.provider = "claude"
        args.max_failures = 3
        args.no_progress = True
        return loop, args

    def test_primary_attempt_cap_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, args = self.drive_fixture(Path(tmp))
            command = {"mode": "argv", "binary_found": True, "argv": ["/bin/false"], "prompt": "test", "prompt_transport": "stdin"}
            with mock.patch.object(runtime, "resolve_provider_command", return_value=command), mock.patch.object(runtime, "run_primary_subprocess", return_value=(1, False, None)) as worker:
                first = runtime.drive_command(args)
                second = runtime.drive_command(args)
            self.assertEqual(first["reason"], "max_failures")
            self.assertEqual(second["reason"], "max_failures")
            self.assertEqual(worker.call_count, 3)

    def test_hard_quota_excludes_before_a_restart_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, args = self.drive_fixture(Path(tmp))
            command = {"mode": "argv", "binary_found": True, "argv": ["/bin/false"], "prompt": "test", "prompt_transport": "stdin"}
            def fail(*a, **kw):
                kw["resource_metadata"]["provider_failure_class"] = "hard_quota"
                return 1, False, None
            with mock.patch.object(runtime, "resolve_provider_command", return_value=command) as resolve, mock.patch.object(runtime, "run_primary_subprocess", side_effect=fail) as worker:
                first = runtime.drive_command(args)
                second = runtime.drive_command(args)
            self.assertEqual(first["reason"], "hard_quota_exhausted")
            self.assertEqual(second["reason"], "provider_excluded")
            self.assertEqual(worker.call_count, 1)
            self.assertEqual(resolve.call_count, 1)

    def test_deadline_and_explicit_off_controls_reach_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, args = self.drive_fixture(Path(tmp))
            command = {"mode": "argv", "binary_found": True, "argv": ["/bin/false"], "prompt": "test", "prompt_transport": "stdin"}
            def stop(*a, **kw):
                (loop / "STOP_REQUESTED").write_text("operator", encoding="utf-8")
                return 1, False, None
            with mock.patch.object(runtime, "resolve_provider_command", return_value=command), mock.patch.object(runtime, "remaining_worker_timeout", return_value=7) as deadline, mock.patch.object(runtime, "run_primary_subprocess", side_effect=stop) as worker:
                runtime.drive_command(args)
            self.assertEqual(worker.call_count, 1)
            self.assertEqual(worker.call_args.kwargs["timeout_s"], 7)
            self.assertEqual(worker.call_args.kwargs["child_env"]["AAS_AUTOLOOP_PANEL"], "off")
            self.assertEqual(worker.call_args.kwargs["child_env"]["AAS_AUTOLOOP_NOTIFY"], "off")

    def test_narration_cannot_declare_provider_credit(self):
        self.assertEqual(runtime.classify_iteration_failure("I diagnosed quota exhausted; try another provider"), "failure")

    def test_diagnostic_separates_hard_quota_and_throttling(self):
        for message, expected in (("insufficient_quota", "hard_quota"), ("rate_limit_error", "quota"), ("authentication_error", "auth")):
            stderr = json.dumps({"type": "error", "error": {"type": message, "message": message}})
            self.assertEqual(runtime.classify_iteration_failure(stderr, host_diagnostic=True), expected)
        narration = json.dumps({"type": "assistant", "message": "out of credits"})
        self.assertEqual(runtime.classify_iteration_failure(narration, host_diagnostic=True), "failure")

    def test_worker_deadline_leaves_cleanup_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp)
            (loop / "loop_state.json").write_text(json.dumps({"created_at": (datetime.now(timezone.utc)-timedelta(seconds=40)).isoformat()}), encoding="utf-8")
            (loop / "budget.json").write_text(json.dumps({"max_wall_time_seconds": 60}), encoding="utf-8")
            remaining = runtime.remaining_worker_timeout(loop, 1800, cleanup_allowance=10)
            self.assertGreater(remaining, 8)
            self.assertLessEqual(remaining, 10)
            self.assertEqual(runtime.remaining_worker_timeout(loop, 4, cleanup_allowance=10), 4)
            self.assertEqual(runtime.remaining_worker_timeout(loop, 1800, cleanup_allowance=30), 0)
            derived = runtime.remaining_worker_timeout(loop, None, cleanup_allowance=10)
            self.assertGreater(derived, 8)
            self.assertLessEqual(derived, 10)
            (loop / "budget.json").write_text(json.dumps({"max_wall_time_seconds": 0}), encoding="utf-8")
            self.assertIsNone(runtime.remaining_worker_timeout(loop, None))

    def test_legacy_zero_timeout_preserves_explicit_unbounded_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            loop, args = self.drive_fixture(Path(tmp))
            args.iteration_timeout = 0
            budget = json.loads((loop / "budget.json").read_text(encoding="utf-8"))
            budget["max_wall_time_seconds"] = 0
            (loop / "budget.json").write_text(json.dumps(budget), encoding="utf-8")
            command = {"mode": "argv", "binary_found": True, "argv": ["/bin/false"], "prompt": "test", "prompt_transport": "stdin"}
            def stop(*a, **kw):
                (loop / "STOP_REQUESTED").write_text("operator", encoding="utf-8")
                return 1, False, None
            with mock.patch.object(runtime, "resolve_provider_command", return_value=command), mock.patch.object(runtime, "run_primary_subprocess", side_effect=stop) as worker:
                runtime.drive_command(args)
            self.assertIsNone(worker.call_args.kwargs["timeout_s"])

    def test_smoke_never_revives_empty_filtered_roster(self):
        args = SimpleNamespace(root=None, dir=None, providers=None, smoke=True, phase=None, timeout=1)
        with mock.patch.object(runtime, "load_panel_config", return_value={"providers": []}), mock.patch.object(runtime, "panel_smoke", return_value={}) as smoke:
            runtime.panel_command(args)
        self.assertEqual(smoke.call_args.kwargs["providers"], [])


class PersistentSupervisorPolicyTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "the supervisor is a POSIX shell runtime")
    def test_hard_exclusion_does_not_expire(self):
        helper = arl_tests.SupervisorBehaviorTests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            loop.mkdir(mode=0o700)
            helper._seed_excluded(loop, "claude\t0\n")
            result = helper._run(root, loop, helper._config(primary_order=["claude"], session_exclude_ttl_s=1))
            self.assertEqual(result.returncode, 11, result.stderr)
            self.assertFalse((loop / "stub-drive.jsonl").exists())

    @unittest.skipUnless(os.name == "posix", "the supervisor is a POSIX shell runtime")
    def test_user_exclusion_prevents_primary_selection(self):
        helper = arl_tests.SupervisorBehaviorTests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            loop.mkdir(mode=0o700)
            (loop / "panel.json").write_text(json.dumps({"exclude_providers": ["claude"]}), encoding="utf-8")
            result = helper._run(root, loop, helper._config(primary_order=["claude"]))
            self.assertEqual(result.returncode, 11, result.stderr)
            self.assertFalse((loop / "stub-drive.jsonl").exists())

    def test_explicit_available_provider_remains_allowed(self):
        args = SimpleNamespace(root=None, dir=None, providers="grok", smoke=True, phase=None, timeout=1)
        with mock.patch.object(runtime, "load_panel_config", return_value={"providers": ["codex"], "exclude_until_credit": ["claude"]}), mock.patch.object(runtime, "panel_smoke", return_value={}) as smoke:
            runtime.panel_command(args)
        self.assertEqual(smoke.call_args.kwargs["providers"], ["grok"])

    def test_explicit_provider_does_not_override_credit_exclusion(self):
        args = SimpleNamespace(root=None, dir=None, providers="claude", smoke=True, phase=None, timeout=1)
        with mock.patch.object(runtime, "load_panel_config", return_value={"providers": [], "exclude_until_credit": ["claude"]}), mock.patch.object(runtime, "panel_smoke", return_value={}) as smoke:
            runtime.panel_command(args)
        self.assertEqual(smoke.call_args.kwargs["providers"], [])


if __name__ == "__main__":
    unittest.main()
