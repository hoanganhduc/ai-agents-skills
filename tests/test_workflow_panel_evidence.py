"""Offline regressions for host-owned process evidence and panel admission."""
import base64
import concurrent.futures
import hashlib
import json
import ntpath
import os
from pathlib import Path, PureWindowsPath
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "canonical/runtime/skills/autonomous-research-loop-runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(ROOT / "canonical/runtime/runners"))
import panel_parent as pp
import provider_resources as resources
from state_transaction import _safe_relative_path
if os.name != "nt":
    import arl_credential_broker as broker
else:
    broker = None


class ProcessEvidenceTests(unittest.TestCase):
    def test_invalid_utf8_and_typed_failures_survive_presentation(self):
        bounded = resources.BoundedProcessResult(124, b"\xff\x00partial", b"original error", True, False, "capture failed", "cleanup failed")
        with mock.patch.object(pp, "run_bounded_resource_process", return_value=bounded):
            result = pp._default_runner(["fake"], {}, "/", 1)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], 126)
        evidence = result.process_evidence
        self.assertTrue(evidence["timed_out"])
        self.assertEqual(evidence["capture_error"], "capture failed")
        self.assertEqual(evidence["cleanup_error"], "cleanup failed")
        self.assertEqual(base64.b64decode(evidence["stdout"]["base64"]), bounded.stdout)
        self.assertEqual(base64.b64decode(evidence["stderr"]["base64"]), bounded.stderr)

    @unittest.skipIf(broker is None, "credential broker is POSIX-only")
    def test_broker_never_exports_secret_bytes_hidden_by_presentation(self):
        state = broker.CredentialState.__new__(broker.CredentialState)
        state.providers = {"TOKEN": "secret-credential-123"}
        state.compute = {}
        evidence = resources.process_evidence(resources.BoundedProcessResult(1, b"secret-credential-123", b"", False, False, None, "cleanup"))
        rc, stdout, stderr, filtered = state._filter_process_evidence(126, "", "cleanup failed", evidence)
        self.assertEqual(rc, 126)
        self.assertEqual(filtered["stdout"]["state"], "withheld")
        self.assertNotIn("base64", filtered["stdout"])
        self.assertNotIn("sha256", filtered["stdout"])
        self.assertNotIn("secret-credential-123", json.dumps([stdout, stderr, filtered]))
        self.assertNotIn(base64.b64encode(b"secret-credential-123").decode(), json.dumps(filtered))

    def test_sensitive_evidence_cannot_be_relabelled_as_original(self):
        evidence = resources.process_evidence(resources.BoundedProcessResult(0, b"content", b"", False, False, None, None))
        hidden = resources.withhold_process_evidence(evidence, "sensitive_output")
        self.assertEqual(hidden["stdout"], {"state": "withheld", "reason": "sensitive_output"})
        self.assertEqual(hidden["return_code"], 0)

    @unittest.skipIf(broker is None, "credential broker is POSIX-only")
    def test_broker_preserves_safe_byte_evidence_exactly(self):
        state = broker.CredentialState.__new__(broker.CredentialState)
        state.providers = state.compute = {}
        evidence = resources.process_evidence(resources.BoundedProcessResult(1, b"\xff\x00", b"failure", False, False, None, None))
        self.assertEqual(state._filter_process_evidence(1, "text view", "failure", evidence),
                         (1, "text view", "failure", evidence))
        forged = dict(evidence, raw_secret="unrecognized")
        self.assertIsNone(state._filter_process_evidence(1, "", "", forged)[3])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux containment contract")
    def test_authority_mountpoints_are_prepared_only_at_containment_and_masked_after_binds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project, dependency, child_home = (root / name for name in ("project", "dependency", "child-home"))
            for path in (project, dependency, child_home):
                path.mkdir()
            custom = root / "custom-authority"
            with mock.patch.object(Path, "home", return_value=root / "host-home"), mock.patch.object(
                    resources, "_HOST_FORMAL_AUTHORITY_ROOTS", {custom}), mock.patch.object(
                    resources, "_trusted_host_binary", return_value="/usr/bin/bwrap"):
                self.assertEqual(resources.formal_authority_mask_roots(), [])
                self.assertFalse(custom.exists())
                full = resources.trusted_local_containment_command(["/bin/true"], cwd=project)
                restricted = resources.brokered_provider_containment_command(["/bin/true"], cwd=project,
                    dependency_root=dependency, synthetic_home=child_home)
                for authority in resources.formal_authority_mask_roots():
                    self.assertEqual(authority.stat().st_mode & 0o777, 0o700)
                    self.assertEqual(list(authority.iterdir()), [])
                    for command in (full, restricted):
                        position = command.index(str(authority))
                        self.assertEqual(command[position - 1], "--tmpfs")
                        self.assertGreater(position, command.index("--bind"))


class AttemptAndRosterTests(unittest.TestCase):
    def test_windows_reservation_uses_portable_transaction_keys_on_every_cas_attempt(self):
        root = PureWindowsPath("C:/loop")
        for nested in (False, True):
            for preimage in (None, "a" * 64):
                with self.subTest(nested=nested, preimage=preimage):
                    iteration = root / "iterations/iter001" if nested else root
                    relative = "iterations/iter001/data/panel_attempts.json" if nested else "data/panel_attempts.json"
                    count = 0 if preimage is None else 1
                    def read_attempts(path):
                        return {"schema_version": "panel_attempts.v1", "phases": {"smoke": count}}, preimage
                    calls = []
                    def commit(run_dir, **kwargs):
                        for key in (*kwargs["json_files"], *kwargs["expected_absent"], *kwargs["expected_hashes"]):
                            _safe_relative_path(key)
                            self.assertEqual(key, relative)
                        self.assertEqual(run_dir, root)
                        self.assertEqual(kwargs["expected_absent"], [relative] if preimage is None else [])
                        self.assertEqual(kwargs["expected_hashes"], {relative: preimage} if preimage is not None else {})
                        self.assertEqual(kwargs["json_files"][relative]["phases"]["smoke"], count + 1)
                        calls.append(kwargs)
                        if len(calls) == 1:
                            raise pp.RevisionConflict("retry this reservation")
                    with mock.patch.object(pp, "Path", PureWindowsPath), mock.patch.object(
                            pp, "os", SimpleNamespace(path=ntpath)), mock.patch.object(
                            pp, "_panel_run_root", return_value=root), mock.patch.object(
                            pp, "_ensure_real_directory"), mock.patch.object(
                            pp, "_read_panel_attempts", side_effect=read_attempts), mock.patch.object(
                            pp, "commit_transaction", side_effect=commit):
                        self.assertEqual(pp.reserve_panel_attempt(iteration, "smoke"), (count + 1, True))
                    self.assertEqual(len(calls), 2)

    def test_windows_completion_uses_portable_payload_and_preimage_keys_on_retry(self):
        root = PureWindowsPath("C:/loop")
        for nested in (False, True):
            with self.subTest(nested=nested):
                iteration = root / "iterations/iter001" if nested else root
                prefix = "iterations/iter001/" if nested else ""
                context = {"dispatch_id": "current"}
                state = {"schema_version": "panel_attempts.v1", "phases": {"smoke": 2},
                         "owners": {"smoke": {**context, "attempt_number": 2}}}
                payloads = {iteration / "data/panel_dispatch_smoke.json": "summary",
                            iteration / "panel/smoke/dispatch_summary.json": "detail"}
                expected = {prefix + "data/panel_dispatch_smoke.json": "summary",
                            prefix + "panel/smoke/dispatch_summary.json": "detail"}
                calls = []
                def commit(run_dir, **kwargs):
                    for key in (*kwargs["text_files"], *kwargs["expected_hashes"]):
                        _safe_relative_path(key)
                    self.assertEqual(run_dir, root)
                    self.assertEqual(kwargs["text_files"], expected)
                    self.assertEqual(kwargs["expected_hashes"], {prefix + "data/panel_attempts.json": "a" * 64})
                    calls.append(kwargs)
                    if len(calls) == 1:
                        raise pp.RevisionConflict("retry this completion")
                with mock.patch.object(pp, "Path", PureWindowsPath), mock.patch.object(
                        pp, "os", SimpleNamespace(path=ntpath)), mock.patch.object(
                        pp, "_panel_run_root", return_value=root), mock.patch.object(
                        pp, "_read_panel_attempts", return_value=(state, "a" * 64)), mock.patch.object(
                        pp, "commit_transaction", side_effect=commit):
                    self.assertTrue(pp._publish_panel_attempt(iteration, "smoke", 2, context, payloads))
                self.assertEqual(len(calls), 2)

    def test_corrupt_attempt_counter_never_resets(self):
        for content in ('{broken', '{"schema_version":"panel_attempts.v1","phases":{"smoke":true}}', '{"schema_version":"panel_attempts.v1","phases":{"smoke":-1}}'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "data").mkdir()
                path = root / "data/panel_attempts.json"
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(pp.PanelArtifactError):
                    pp.reserve_panel_attempt(root, "smoke")
                self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_corrupt_panel_policy_cannot_restore_default_roster_or_retry_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "panel.json").write_text('{"max_attempts":', encoding="utf-8")
            with self.assertRaises(pp.PanelArtifactError):
                pp.load_panel_config(root)

    def test_concurrent_attempts_respect_one_shared_cap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "iterations/iter001"
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: pp.reserve_panel_attempt(root, "smoke", max_attempts=3), range(12)))
            self.assertEqual(sorted(n for n, allowed in results if allowed), [1, 2, 3])

    def test_explicit_empty_roster_and_exclusions_are_authoritative(self):
        self.assertEqual(pp.filter_panel_providers({"providers": []}), [])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "panel.json").write_text(json.dumps({"providers": ["codex"], "exclude_providers": ["codex"]}), encoding="utf-8")
            for roster in ([], ["codex"]):
                with mock.patch.object(pp, "dispatch_phase") as dispatch:
                    result = pp.run_panel_phase_for_drive(root, root, "smoke", providers=roster, prompt="test")
                dispatch.assert_not_called()
                self.assertTrue(result["panel_roster_withdrawn"])

    def test_obsolete_completion_cannot_overwrite_newer_attempt(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            self.assertTrue(release.wait(5))
            return 0, "obsolete original response", ""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            iteration = root / "iterations/iter001"
            kwargs = dict(iter_dir=iteration, phase="smoke", prompt="test", providers=["codex"], timeout_s=2, root=root)
            with mock.patch.object(pp, "build_cmd", return_value=(["fake"], {})), mock.patch.object(pp, "attest_provider_executable", return_value=None), concurrent.futures.ThreadPoolExecutor() as pool:
                old = pool.submit(pp.dispatch_phase, **kwargs, runner=slow)
                self.assertTrue(entered.wait(5))
                new = pp.dispatch_phase(**kwargs, runner=lambda *args: (0, "current successful response", ""))
                release.set()
                obsolete = old.result(5)
            self.assertTrue(obsolete["obsolete_completion"])
            self.assertFalse(obsolete["panel_content_pass"])
            published = json.loads((iteration / "data/panel_dispatch_smoke.json").read_text(encoding="utf-8"))
            self.assertEqual(published["dispatch_context"], new["dispatch_context"])
            self.assertEqual(Path(obsolete["results"]["codex"]["stdout_path"]).read_text(encoding="utf-8"), "obsolete original response")

    def test_exhausted_wall_budget_starts_no_runner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "budget.json").write_text('{"max_wall_time_seconds":1}', encoding="utf-8")
            (root / "loop_state.json").write_text('{"created_at":"2000-01-01T00:00:00Z"}', encoding="utf-8")
            runner = mock.Mock()
            result = pp.dispatch_phase(root / "iter001", "smoke", "test", ["codex"], 5, root, runner=runner, run_dir=root)
            runner.assert_not_called()
            self.assertEqual(result["error_class"], "wall_budget_exhausted")


class HostAdmissionTests(unittest.TestCase):
    def context(self):
        return {"dispatch_id": "dispatch-1", "attempt_id": "attempt-1", "owner_id": "owner-1", "host_task_id": "task-1", "host_session_id": "session-1", "input_sha256": "a" * 64, "transport": "host-native"}

    def test_native_receipt_is_bound_to_current_host_and_cannot_selfclaim_family(self):
        context = self.context()
        response = "PANEL_SMOKE_OK"
        receipt = {"schema_version": "host_panel_receipt.v1", **context, "output_sha256": hashlib.sha256(response.encode()).hexdigest()}
        admitted = pp.admit_host_panel_result(receipt, host_context=context, phase="smoke", response=response, required_assurances=("different_family",))
        self.assertTrue(admitted["content_valid"])
        self.assertFalse(admitted["assurance_pass"])
        self.assertIsNone(admitted["provider_execution_attestation"])
        for field in ("dispatch_id", "attempt_id", "owner_id", "host_task_id", "host_session_id", "input_sha256", "transport"):
            bad = dict(receipt, **{field: "obsolete"})
            self.assertFalse(pp.admit_host_panel_result(bad, host_context=context, phase="smoke", response=response)["admitted"])

    def test_disposition_requires_exactly_one_per_primary_and_new_stays_candidate(self):
        context = self.context()
        result = {"primary_finding_ids": ["f1"], "dispositions": [{"finding_id": "f1", "status": "confirmed"}, {"finding_id": "f1", "status": "rejected"}], "new_findings": [{"finding_id": "n1", "status": "confirmed"}]}
        errors = pp.validate_review_dispositions(result, primary_finding_ids=context.get("primary_finding_ids", ["f1"]))
        self.assertTrue(any("exactly one" in error for error in errors))
        self.assertTrue(any("candidate" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
