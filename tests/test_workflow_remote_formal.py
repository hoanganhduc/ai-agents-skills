"""Offline lifecycle/admission tests. No provider or credential lookup is allowed."""
import importlib.util
import json
import os
import io
import struct
import threading
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ARL = ROOT / "canonical/runtime/skills/autonomous-research-loop-runtime"
sys.path.insert(0, str(ARL))
sys.path.insert(0, str(ROOT / "canonical/runtime/workspace"))
sys.path.insert(0, str(ROOT / "canonical/runtime/skills/kaggle-research-compute"))
import remote_formal as remote
import formal_policy as policy
import kaggle_driver as driver
if os.name == "nt":
    raise unittest.SkipTest("host remote formal credential broker is POSIX-only")
sys.path.insert(0, str(ROOT / "canonical/runtime/runners"))
import arl_credential_broker as broker
from provider_resources import BoundedProcessResult, process_evidence


def evidence(stdout=b""):
    return process_evidence(BoundedProcessResult(0, stdout, b"", False, False, None, None))


class FakeBackend:
    bundle_sha256 = staticmethod(driver.bundle_sha256)
    _submission_intent_path = staticmethod(driver._submission_intent_path)
    kernel_ref = staticmethod(driver.kernel_ref)
    _bind_saved_response = staticmethod(driver._bind_saved_response)

    def __init__(self):
        self.pushes = 0
        self.polls = 0
        self.provider_status = "complete"
        self.identity = None
        self.mutate = lambda outputs: None
        self.ambiguous = False

    def push(self, **kwargs):
        self.pushes += 1
        manifest = json.loads((kwargs["job_dir"] / "manifest.json").read_text(encoding="utf-8"))
        intent = self._submission_intent_path(kwargs["state_root"], job_id=manifest["job_id"], round_idx=0, chunk_idx=0)
        kernel = driver.kernel_ref(manifest["job_id"], 0, 0, username="tester")
        payload = {"schema": "ai-agents-skills.kaggle-submission-intent.v2", "state": "acceptance_unknown",
            "kernel": kernel, "bundle_sha256": kwargs["expected_bundle_sha256"], "attempt_id": "provider-attempt",
            "enable_internet": False, "gpu": False, "events": []}
        writer = kwargs.get("checkpoint_writer") or driver._write_submission_intent
        intent.parent.mkdir(exist_ok=True)
        writer(intent, payload, create=True)
        if self.ambiguous:
            raise driver.KaggleDriverError("synthetic ambiguous save")
        payload["provider_response"] = {"kernel_id": 12, "version_number": 3, "ref": kernel, "url": "https://www.kaggle.com/code/" + kernel}
        payload["submitted_source"] = {"code_sha256": "c" * 64, "metadata_sha256": "d" * 64}
        writer(intent, payload, create=False)
        self.identity = driver._bind_saved_response(payload)
        payload.update(state="submitted", accepted_identity=self.identity)
        writer(intent, payload, create=False)
        return {"submission_intent": str(intent), "accepted_identity": self.identity}

    def load_submission_identity(self, path, **kwargs):
        return driver.load_submission_identity(path, **kwargs)

    def recover_submission(self, path, **kwargs):
        return driver.recover_submission(path, **kwargs)

    def status(self, **kwargs):
        self.polls += 1
        self.identity = self.load_submission_identity(kwargs["submission_intent"])
        return {"status": self.provider_status, "host_provenance": {"accepted_identity": self.identity}}

    def fetch(self, **kwargs):
        assert kwargs["validate_checkpoints"] is False
        destination = kwargs["dest"]
        (destination / "out").mkdir(parents=True)
        manifest = json.loads((kwargs["job_dir"] / "manifest.json").read_text(encoding="utf-8"))
        plan = manifest["formal_request"]
        spec = importlib.util.spec_from_file_location("offline_gate", kwargs["job_dir"] / "gate.py")
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        scan = gate.scan_input(kwargs["job_dir"] / "project", "final_candidate", set())
        build = {**scan, "lean_check_status": "typechecked", "process_evidence": evidence(),
                 "typecheck_modules_built": ["Main"], "typecheck_modules_unbuilt": [], "typecheck_modules_stale": []}
        axiom = {"schema_version": "lean-strict-verification-gate.v1", "ok": True, "axiom_audit_status": "audited",
            "declarations": [{"declaration": target, "status": "sanctioned", "axioms": []} for target in plan["targets"]],
            "unsanctioned_axioms": [], "declarations_unparsed": [], "process_evidence": evidence()}
        reports = {"scan": scan, "build": build, "axiom": axiom}
        outputs = {"out/" + name + ".json": {"status": "passed", "report": reports.get(name),
            "process_evidence": evidence(remote.canonical(reports[name]) if name in reports else plan[name].encode() if name.endswith("_version") else b"")}
            for name in ("setup", "lean_version", "lake_version", "scan", "build", "axiom")}
        outputs["out/kernel.json"] = {"status": "not_run"}
        outputs["out/unit-0000.json"] = {"status": "completed", "unit": 0}
        phases = {name: "passed" for name in ("setup", "lean_version", "lake_version", "scan", "build", "axiom")}
        phases["kernel"] = "not_requested"
        outputs["out/result.json"] = {**plan, "schema_version": "remote_lean_execution.v1", "status": "completed",
            "correspondence": "not_checked", "gate_sha256": remote.digest((kwargs["job_dir"] / "gate.py").read_bytes()),
            "manifest_sha256": remote.digest(remote.canonical(manifest)), "phases": phases}
        self.mutate(outputs)
        entries = []
        for name, data in outputs.items():
            raw = remote.canonical(data)
            (destination / name).write_bytes(raw)
            entries.append({"path": name, "bytes": len(raw), "sha256": remote.digest(raw), "complete": True})
        return {"host_provenance": {"accepted_identity": self.identity, "download_version_number": 3},
            "pagination": {"complete": True}, "output_manifest": entries}


class RemoteFormalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.run = self.root / "run"
        self.run.mkdir()
        sources = {"Main.lean": "theorem checked : True := by trivial\n", "lakefile.toml": 'name = "example"\n',
            "lean-toolchain": "leanprover/lean4:v4.33.0\n", "lake-manifest.json": '{"packages":[]}\n'}
        for name, body in sources.items():
            (self.project / name).write_text(body, encoding="utf-8")
        bootstrap = self.root / "bootstrap.sh"
        bootstrap.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        bootstrap.chmod(0o600)
        (self.root / "compute.toml").write_text("[kaggle]\nenabled=true\n", encoding="utf-8")
        self.request = {"schema_version": remote.REQUEST_SCHEMA, "project_root": str(self.project), "state_root": str(self.root / "host-state"),
            "compute_config": str(self.root / "compute.toml"), "compute_config_sha256": remote.digest((self.root / "compute.toml").read_bytes()), "owner": "tester", "source_files": sorted(sources),
            "protected_files": {name: remote.digest((self.project / name).read_bytes()) for name in sources if name != "Main.lean"},
            "bootstrap_script": str(bootstrap), "bootstrap_sha256": remote.digest(bootstrap.read_bytes()),
            "gate_sha256": remote.digest((remote.HERE.parent / "lean-strict-verification-gate/lean_strict_verification_gate.py").read_bytes()),
            "targets": ["checked"], "lean_executable": "tools/lean", "lake_executable": "tools/lake",
            "lean_version": "Lean exact version", "lake_version": "Lake exact version",
            "dependency_files": {"tools/lean": "1" * 64, "tools/lake": "2" * 64}, "enable_internet": False,
            "require_kernel": False, "max_attempts": 3, "timeout_seconds": 10, "submission_authorized": True}
        self.request_path = self.root / "request.json"
        self.write_request()
        self.backend = FakeBackend()
        self.admitted_receipts = set()
        self.addCleanup(mock.patch.stopall)
        # All network surfaces fail unless the explicit offline backend handles them.
        mock.patch.object(driver, "PROVIDER_RUNNER", side_effect=AssertionError("live provider forbidden")).start()
        mock.patch.object(remote, "kaggle_modules", return_value=(self.backend, lambda path: SimpleNamespace())).start()
        mock.patch.dict("os.environ", {"AAS_ARL_BROKER_SOCKET": "", "AAS_ARL_BROKER_TOKEN": ""}).start()

    def write_request(self):
        self.request_path.write_bytes(remote.canonical(self.request))
        self.request_path.chmod(0o600)
        self.pin = {"execution_backend": "kaggle-cpu", "remote_request": str(self.request_path),
                    "remote_request_sha256": remote.digest(self.request_path.read_bytes())}

    def advance(self, purpose="a" * 64):
        result = remote.advance(self.run, self.project, self.pin, purpose, backend=self.backend, config=SimpleNamespace())
        if result.get("status") == "passed":
            self.admitted_receipts.add((result["receipt_path"], result["receipt_sha256"]))
        return result

    def test_prepare_submit_poll_fetch_and_host_admit(self):
        self.assertEqual(self.advance()["status"], "verification_pending")
        result = self.advance()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["execution_backend"], "kaggle-cpu")
        self.assertEqual(result["correspondence"], "not_checked")
        self.assertEqual(self.advance()["receipt_sha256"], result["receipt_sha256"])
        self.assertEqual(self.backend.pushes, 1)
        # A new host reverification purpose gets a fresh bounded attempt.
        self.assertEqual(self.advance("b" * 64)["status"], "verification_pending")
        self.assertEqual(self.backend.pushes, 2)

    def test_native_and_remote_terminal_rules_agree_and_reverify_is_pending(self):
        pol = policy.FormalPolicy(policy="on", project=str(self.project), execution_backend="kaggle-cpu", remote_request=str(self.request_path))
        pin = policy.pin_privileged_policy(pol)
        real_advance = remote.advance
        with mock.patch.object(remote, "advance", side_effect=lambda *args, **kwargs: real_advance(*args, **kwargs, backend=self.backend, config=SimpleNamespace())):
            first = policy.evaluate_formal_terminal_state(self.run, root=self.root, policy=pol, pin=pin)
        self.assertTrue(first["verification_pending"])
        with mock.patch.object(remote, "advance", side_effect=lambda *args, **kwargs: real_advance(*args, **kwargs, backend=self.backend, config=SimpleNamespace())):
            checked = policy.evaluate_formal_terminal_state(self.run, root=self.root, policy=pol, pin=pin)
        self.assertEqual(checked["terminal_state"], "sorry_free_artifact", checked)
        receipt = checked["remote_verification"]
        reports = remote.validate_host_receipt(Path(receipt["receipt_path"]), receipt["receipt_sha256"], project=self.project,
            authority_verifier=lambda path, sha, project: (str(path), sha) == (receipt["receipt_path"], receipt["receipt_sha256"]))["reports"]
        names = {"lean_strict_verification_gate.scan": "scan", "lean_strict_verification_gate.verify_typecheck": "build", "lean_strict_verification_gate.axiom_audit": "axiom"}
        local = policy.evaluate_formal_terminal_state(self.run, root=self.root, policy=policy.FormalPolicy(project=str(self.project)),
            runner=lambda name, payload: {"ok": True, "report": reports[names[name]]}, write=False)
        self.assertEqual(local["terminal_state"], checked["terminal_state"])
        with mock.patch.object(remote, "advance", side_effect=lambda *args, **kwargs: real_advance(*args, **kwargs, backend=self.backend, config=SimpleNamespace())):
            fresh = policy.reverify_formal_evidence(self.run, root=self.root, policy=pol, pin=pin)
        self.assertEqual(fresh["status"], "verification_pending")
        with mock.patch.object(remote, "advance", side_effect=lambda *args, **kwargs: real_advance(*args, **kwargs, backend=self.backend, config=SimpleNamespace())):
            fresh = policy.reverify_formal_evidence(self.run, root=self.root, policy=pol, pin=pin)
        self.assertEqual(fresh["status"], "reverified", fresh)
        self.assertEqual(self.backend.pushes, 2)

    def test_host_receipt_deep_research_binding_and_tamper_rejection(self):
        self.advance()
        admitted = self.advance()
        path = ROOT / "canonical/runtime/skills/deep-research-workflow/deep_research_workflow.py"
        spec = importlib.util.spec_from_file_location("bound_deep_research", path)
        deep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(deep)
        reference = self.root / "remote-reference.json"
        reference.write_text(json.dumps({"schema_version": "host_remote_lean_reference.v1", "receipt_path": admitted["receipt_path"],
            "receipt_sha256": admitted["receipt_sha256"], "project_root": str(self.project)}), encoding="utf-8")
        row = {"evidence_type": "formal_check", "verification_source": "host_verified_remote_lean", "inspection_status": "checked", "artifact_ref": reference.name}
        target = {"verification_evidence_ids": ["E1"], "lean_statement_ref": "project/Main.lean#checked"}
        verifier = lambda path, sha, project: (str(path), sha) in self.admitted_receipts
        self.assertFalse(deep.has_remote_formal_check_evidence(target, {"E1": row}, self.root))
        self.assertTrue(deep.has_remote_formal_check_evidence(target, {"E1": row}, self.root, authority_verifier=verifier))
        target["lean_statement_ref"] = "project/Main.lean#unchecked"
        self.assertFalse(deep.has_remote_formal_check_evidence(target, {"E1": row}, self.root, authority_verifier=verifier))
        target["lean_statement_ref"] = "project/Main.lean#checked"
        (self.project / "Main.lean").write_text("theorem checked : False := by sorry\n", encoding="utf-8")
        self.assertFalse(deep.has_remote_formal_check_evidence(target, {"E1": row}, self.root, authority_verifier=verifier))

    def test_broker_registration_is_parent_only_and_never_accepts_worker_argv(self):
        state = broker.CredentialState.__new__(broker.CredentialState)
        state.parent_token = "parent-master"
        state.lock = threading.Lock()
        state.private_root = self.root
        state.runtime_root = self.root / "runtime"
        state.runtime_root.mkdir()
        state.formal_authority_root = self.root / "host-authority"
        import panel_parent
        state.panel = panel_parent
        state.formal_registrations = {}
        state.formal_locks = {}
        state.formal_checkpoint_locks = {}
        state.formal_step_capabilities = {}
        request = {"operation": "formal_register", "run_dir": str(self.run), "project": str(self.project), "pin": self.pin}
        registration = state.formal_register(request)
        self.assertEqual(state.formal_register(request), registration)
        with self.assertRaisesRegex(ValueError, "unrecognized"):
            state.formal_register({**request, "argv": ["hostile"]})
        for operation in ("formal_register", "formal_advance"):
            handler = broker.BrokerHandler.__new__(broker.BrokerHandler)
            raw = json.dumps({**request, "operation": operation, "token": "worker-capability"}).encode()
            handler.rfile = io.BytesIO(struct.pack("!I", len(raw)) + raw)
            handler.wfile = io.BytesIO()
            handler.server = SimpleNamespace(credential_state=state)
            handler.handle()
            response = json.loads(handler.wfile.getvalue()[4:])
            self.assertFalse(response["ok"])
            self.assertIn("capability", response["error"])

    def test_broker_signs_fixed_controller_admission_and_survives_restart(self):
        self._broker_flow()

    def test_interrupted_state_mirror_recovers_from_protected_postimage(self):
        self._broker_flow(fault="state_mirror")

    def test_captured_provider_response_recovers_without_another_push(self):
        self._broker_flow(fault="saved_response_mirror")

    def test_unknown_provider_acceptance_never_resubmits_through_broker(self):
        self._broker_flow(fault="acceptance_unknown")

    def _broker_flow(self, fault=None):
        import panel_parent
        state = broker.CredentialState.__new__(broker.CredentialState)
        state.lock = threading.Lock()
        state.private_root = self.root / "broker-private"
        state.private_root.mkdir(mode=0o700)
        state.runtime_root = self.root / "runtime"
        state.runtime_root.mkdir()
        state.formal_authority_root = self.root / "host-authority"
        state.formal_registrations = {}
        state.formal_locks = {}
        state.formal_checkpoint_locks = {}
        state.formal_step_capabilities = {}
        state.providers = {"OPENAI_API_KEY": "provider-secret-not-for-formal"}
        state.compute = {"KAGGLE_API_TOKEN": "kaggle-only-private"}
        state.panel = panel_parent
        state.socket_path = str(self.root / "unused-offline.sock")
        state.skill_dir = state.runtime_root / "workspace/skills/autonomous-research-loop-runtime"
        state.skill_dir.mkdir(parents=True)
        controller = state.skill_dir / "remote_formal.py"
        controller.write_bytes((ARL / "remote_formal.py").read_bytes())
        controller.chmod(0o644)
        registration = state.formal_register({"operation": "formal_register", "run_dir": str(self.run), "project": str(self.project), "pin": self.pin})
        interrupted = []
        used_tokens = []
        if fault == "acceptance_unknown":
            self.backend.ambiguous = True
        def execute_fixed(command, env, cwd, timeout, **kwargs):
            self.assertEqual(command[1], str(controller))
            self.assertEqual(env["KAGGLE_API_TOKEN"], "kaggle-only-private")
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertIn(env["AAS_ARL_BROKER_TOKEN"], state.formal_step_capabilities)
            used_tokens.append(env["AAS_ARL_BROKER_TOKEN"])
            record = json.loads(Path(command[2]).read_text(encoding="utf-8"))
            def checkpoint(path, payload, *, kind, previous):
                packet = {"operation": "formal_checkpoint", "path": str(path), "payload": payload,
                    "kind": kind, "expected_sha256": previous}
                with self.assertRaises(ValueError):
                    state.formal_checkpoint({**packet, "path": str(self.project / "forbidden.json")}, env["AAS_ARL_BROKER_TOKEN"])
                should_interrupt = (fault == "state_mirror" and kind == "state"
                    or fault == "saved_response_mirror" and kind == "intent" and "provider_response" in payload)
                if should_interrupt and not interrupted:
                    interrupted.append(True)
                    with mock.patch.object(state, "_repair_formal_mirrors", side_effect=SystemExit("injected mirror crash")):
                        state.formal_checkpoint(packet, env["AAS_ARL_BROKER_TOKEN"])
                else:
                    state.formal_checkpoint(packet, env["AAS_ARL_BROKER_TOKEN"])
            with mock.patch.object(remote, "_CHECKPOINT_WRITER", checkpoint):
                try:
                    result = remote.advance(self.run, self.project, record["pin"], command[3], backend=self.backend,
                        config=SimpleNamespace(), broker_child=True, expected_state_sha256=record["expected_state_sha256"])
                except SystemExit:
                    return panel_parent.PanelProcessResult(124, "", "synthetic interrupted controller",
                        process_evidence(BoundedProcessResult(124, b"", b"", True, False, None, None)))
            result.pop("reports", None)
            state_path = Path(self.request["state_root"]) / "state.json"
            result["controller_state_sha256"] = remote.digest(state_path.read_bytes()) if state_path.exists() else None
            raw = json.dumps(result).encode()
            return panel_parent.PanelProcessResult(0, raw.decode(), "", evidence(raw))
        arguments = {"operation": "formal_advance", "registration_id": registration["registration_id"], "purpose_key": "a" * 64}
        with mock.patch.object(broker, "_attested_interpreter", return_value=Path(sys.executable)), mock.patch.object(
                broker, "_skill_python_argv0", return_value=(sys.executable, "/usr/bin:/bin")), mock.patch.object(
                panel_parent, "trusted_local_containment_command", side_effect=lambda command, **kwargs: command), mock.patch.object(
                panel_parent, "resource_limited_command", side_effect=lambda command, *args, **kwargs: (command, {}, "scope")), mock.patch.object(
                panel_parent, "resource_control_environment", side_effect=lambda env: env), mock.patch.object(
                panel_parent, "_default_runner", side_effect=execute_fixed):
            self.assertEqual(state.formal_advance(arguments)["result"]["status"], "verification_pending")
            for _ in range(3):
                admitted = state.formal_advance(arguments)["result"]
                if admitted["status"] == "passed":
                    break
        self.assertEqual(state.formal_step_capabilities, {})
        with self.assertRaisesRegex(ValueError, "capability"):
            state.formal_checkpoint({}, used_tokens[-1])
        self.assertEqual(self.backend.pushes, 1)
        if fault == "acceptance_unknown":
            self.assertEqual(admitted["status"], "verification_pending")
            self.assertEqual(self.backend.polls, 0)
            return
        self.assertEqual(admitted["status"], "passed", admitted)
        validation = {"operation": "formal_validate", "receipt_path": admitted["receipt_path"],
            "receipt_sha256": admitted["receipt_sha256"], "project": str(self.project)}
        self.assertTrue(state.formal_validate(validation)["authenticated"])
        restarted = broker.CredentialState.__new__(broker.CredentialState)
        restarted.formal_authority_root = state.formal_authority_root
        self.assertTrue(restarted.formal_validate(validation)["authenticated"])
        with self.assertRaises(ValueError):
            restarted.formal_validate({**validation, "receipt_sha256": "0" * 64})
        from state_transaction import RevisionConflict
        registered = state.formal_registrations[registration["registration_id"]]
        stale, stale_hash = state._read_formal_anchor(registered)
        changed = json.loads(json.dumps(stale))
        changed["independent_checkpoint_test"] = "preserved"
        state._commit_formal_anchor(registered, changed, expected_hash=stale_hash)
        with self.assertRaises(RevisionConflict):
            state._commit_formal_anchor(registered, stale, expected_hash=stale_hash)
        self.assertEqual(state._read_formal_anchor(registered)[0]["independent_checkpoint_test"], "preserved")
        key_path = state.formal_authority_root / "remote-formal.key"
        key_path.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            state._formal_authority_key(create=True)

    def test_state_without_broker_anchor_and_changed_compute_config_are_refused(self):
        state = broker.CredentialState.__new__(broker.CredentialState)
        state.lock = threading.Lock()
        state.private_root = self.root
        state.formal_authority_root = self.root / "host-authority"
        state.formal_registrations = {}
        state.formal_locks = {}
        state.formal_checkpoint_locks = {}
        state.formal_step_capabilities = {}
        import panel_parent
        state.panel = panel_parent
        self.advance()
        with self.assertRaisesRegex(ValueError, "no host admission anchor"):
            state.formal_register({"operation": "formal_register", "run_dir": str(self.run), "project": str(self.project), "pin": self.pin})
        (self.root / "compute.toml").write_text("[kaggle]\nallow_internet=true\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "compute policy identity"):
            self.advance()

    def test_ambiguous_submission_never_repushes(self):
        self.backend.ambiguous = True
        for _ in range(3):
            self.assertEqual(self.advance()["status"], "verification_pending")
        self.assertEqual(self.backend.pushes, 1)

    def test_changed_input_during_pending_verification_does_not_create_second_job(self):
        original = self.advance()
        state_path = Path(self.request["state_root"]) / "state.json"
        before = state_path.read_bytes()
        (self.project / "Main.lean").write_text("theorem checked : True := by constructor\n", encoding="utf-8")
        changed = self.advance()
        self.assertEqual(changed["status"], "verification_pending")
        self.assertEqual(changed["detail"], "input_changed_pending_reconciliation")
        self.assertEqual(self.backend.pushes, 1)
        self.assertEqual(state_path.read_bytes(), before)
        retained = changed["pending_attempts"][0]
        self.assertEqual(retained["attempt_id"], original["attempt_id"])
        self.assertTrue(Path(retained["submission_intent"]).is_file())
        self.assertNotEqual(retained["input_digest"], changed["input_digest"])

    def test_stop_blocks_submission_and_suspends_completed_admission(self):
        (self.run / "STOP_REQUESTED").touch()
        self.assertEqual(self.advance()["status"], "incomplete")
        self.assertEqual(self.backend.pushes, 0)
        (self.run / "STOP_REQUESTED").unlink()
        self.advance()
        (self.run / "STOP_REQUESTED").touch()
        result = self.advance()
        self.assertTrue(result["admission_suspended"])
        self.assertTrue((self.run / "STOP_REQUESTED").exists())

    def test_changed_pinned_request_or_lock_fails_closed(self):
        self.request_path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "request changed"):
            self.advance()
        self.write_request()
        (self.project / "lake-manifest.json").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            self.advance()
        self.assertEqual(self.backend.pushes, 0)

    def test_original_verifier_failures_and_wrong_targets_cannot_pass(self):
        mutations = [lambda data: data["out/build.json"]["report"].update(lean_check_status="typecheck_failed"),
            lambda data: data["out/axiom.json"]["report"].update(declarations=[]),
            lambda data: data["out/build.json"]["report"]["process_evidence"].update(timed_out=True),
            lambda data: data["out/result.json"].update(gate_sha256="0" * 64),
            lambda data: data["out/result.json"].update(status="incomplete", error_type="RuntimeError"),
            lambda data: data["out/result.json"]["phases"].update(setup="failed")]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.request["max_attempts"] = 10
                self.write_request()
                self.backend.mutate = mutation
                purpose = str(index) * 64
                self.advance(purpose)
                self.assertEqual(self.advance(purpose)["status"], "incomplete")

    def test_policy_pin_and_pending_do_not_run_local_checker(self):
        pol = policy.FormalPolicy(policy="on", project=str(self.project), execution_backend="kaggle-cpu", remote_request=str(self.request_path))
        pin = policy.pin_privileged_policy(pol)
        self.assertEqual(pin["remote_request_sha256"], self.pin["remote_request_sha256"])
        local_runner = mock.Mock(side_effect=AssertionError("local checker forbidden"))
        real_advance = remote.advance
        with mock.patch.object(remote, "advance", side_effect=lambda *args, **kwargs: real_advance(*args, **kwargs, backend=self.backend, config=SimpleNamespace())):
            verdict = policy.evaluate_formal_terminal_state(self.run, root=self.root, policy=pol, pin=pin, runner=local_runner)
        self.assertTrue(verdict["verification_pending"])
        local_runner.assert_not_called()

    def test_untrusted_receipt_reference_cannot_promote_deep_research(self):
        path = ROOT / "canonical/runtime/skills/deep-research-workflow/deep_research_workflow.py"
        spec = importlib.util.spec_from_file_location("remote_deep_research", path)
        deep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(deep)
        evidence_row = {"evidence_type": "formal_check", "verification_source": "host_verified_remote_lean", "inspection_status": "checked", "artifact_ref": "invented.json"}
        self.assertFalse(deep.has_remote_formal_check_evidence({"verification_evidence_ids": ["E1"], "lean_statement_ref": "Main.lean#checked"}, {"E1": evidence_row}, self.root))


if __name__ == "__main__":
    unittest.main()
