"""Offline execution identity, policy and reservation regressions."""
from __future__ import annotations

import json
import hashlib
import importlib.util
import os
import contextlib
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "canonical/runtime/workspace"))
sys.path.insert(0, str(ROOT / "canonical/runtime/skills/kaggle-research-compute"))
import kaggle_driver as driver
from research_compute import budget_ledger


class WorkflowComputeTests(unittest.TestCase):
    def setUp(self):
        self.provider = mock.patch.object(driver, "PROVIDER_RUNNER", side_effect=AssertionError("unconfigured provider operation"))
        self.provider.start()
        self.addCleanup(self.provider.stop)

    def bundle(self, root, *, internet=False):
        job = root / "job"
        job.mkdir()
        (job / "run.sh").write_text("true\n", encoding="utf-8")
        (job / "bootstrap.sh").write_text("echo reviewed-bootstrap\n", encoding="utf-8")
        (job / "manifest.json").write_text(json.dumps({"job_id": "fixture", "total_units": 1,
            "enable_internet": internet, "upload_files": ["manifest.json", "run.sh", "bootstrap.sh"]}), encoding="utf-8")
        return job

    def intent(self, root, job, *, state="submitted"):
        record = {"schema": "ai-agents-skills.kaggle-submission-intent.v2", "state": state,
                  "kernel": "owner/slug", "attempt_id": "one-attempt", "enable_internet": False,
                  "bundle_sha256": driver.bundle_sha256(job), "provider_response": {
                      "kernel_id": 17, "version_number": 3, "ref": "/code/owner/slug",
                      "url": "https://www.kaggle.com/code/owner/slug", "error": ""}}
        if state == "submitted":
            record["accepted_identity"] = driver._bind_saved_response(record)
        path = root / "intent.json"
        driver._write_submission_intent(path, record, create=True)
        return path

    def test_all_offline_verbs_and_module_import_avoid_provider_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self.bundle(Path(tmp))
            cfg = SimpleNamespace(kaggle_enabled=True)
            with mock.patch.object(driver, "COMMAND_RUNNER", side_effect=AssertionError("CLI probe")), \
                 mock.patch.object(driver.kaggle_backend, "KAGGLEHUB_VALIDATE", side_effect=AssertionError("auth probe")):
                driver.doctor(cfg)
                driver.preflight(job_dir=job, config=cfg)
                driver.push(job_dir=job, config=cfg, dry_run=True)
                driver.run(job_dir=job, config=cfg, state_root=Path(tmp), dry_run=True)
                spec = importlib.util.spec_from_file_location("_offline_driver_import", driver.__file__)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            driver.PROVIDER_RUNNER.assert_not_called()

    def test_policy_and_bootstrap_bytes_bind_bundle_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = self.bundle(root, internet=True)
            cfg = SimpleNamespace(kaggle_allow_internet=True)
            dry = driver.push(job_dir=job, config=cfg, dry_run=True)
            self.assertTrue(dry["enable_internet"])
            kernel = driver.build_kernel_dir(job_id="fixture", job_dir=job, round_idx=0, chunk_idx=0,
                num_chunks=1, gpu=False, checkpoints_dir=None, dest_root=root / "work", enable_internet=True)
            self.assertTrue(json.loads((kernel / "kernel-metadata.json").read_text(encoding="utf-8"))["enable_internet"])
            (job / "bootstrap.sh").write_text("echo changed\n", encoding="utf-8")
            self.assertNotEqual(dry["bundle_sha256"], driver.bundle_sha256(job))

    def test_saved_response_recovers_without_provider_or_latest_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.intent(root, self.bundle(root), state="acceptance_unknown")
            result = driver.recover_submission(path)
            self.assertEqual(result["accepted_identity"]["version_number"], 3)
            self.assertEqual(driver.load_submission_identity(path)["attempt_id"], "one-attempt")
            driver.PROVIDER_RUNNER.assert_not_called()

    def test_missing_response_stays_unknown_and_push_cannot_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = self.bundle(root)
            path = self.intent(root, job, state="acceptance_unknown")
            record = json.loads(path.read_text(encoding="utf-8"))
            record.pop("provider_response")
            driver._write_submission_intent(path, record, create=False)
            result = driver.recover_submission(path)
            self.assertEqual(result["state"], "acceptance_unknown")
            self.assertFalse(result["replay_permitted"])
            with self.assertRaises(driver.KaggleDriverError):
                driver.load_submission_identity(path)
            driver.PROVIDER_RUNNER.assert_not_called()

    def test_missing_provider_version_keeps_submission_fence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = self.bundle(root)
            cfg = SimpleNamespace(kaggle_enabled=True)
            with mock.patch.object(driver, "token_present", return_value=True), \
                 mock.patch.object(driver, "_resolve_username", return_value="owner"), \
                 mock.patch.object(driver.kaggle_backend, "probe", return_value={"adequate": True, "available": True}), \
                 mock.patch.object(driver, "PROVIDER_RUNNER", return_value={"kernel_id": 17}) as provider:
                args = dict(job_dir=job, config=cfg, state_root=root, work_root=root / "work", confirm=True,
                            expected_bundle_sha256=driver.bundle_sha256(job), expected_owner="owner")
                with self.assertRaises(driver.KaggleDriverError) as caught:
                    driver.push(**args)
                path = Path(caught.exception.evidence["submission_intent"])
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["state"], "acceptance_unknown")
                with self.assertRaisesRegex(driver.KaggleDriverError, "query status"):
                    driver.push(**args)
                self.assertEqual(provider.call_count, 1)

    def test_pagination_cap_preserves_partial_receipt_and_raw_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = b"\xff\x00partial log\n"
            def provider(op, **kwargs):
                if op == "list":
                    return {"files": ["out/provision.log"], "next_page_token": "another-page"}
                if op == "download":
                    return kwargs["consume"]([raw])
                raise AssertionError(op)
            with mock.patch.object(driver, "PROVIDER_RUNNER", side_effect=provider):
                with self.assertRaises(driver.KaggleDriverError) as caught:
                    driver._fetch_kernel("owner/slug", dest=root / "output", identity={"version_number": 3},
                                         allowed={"out/provision.log"}, max_pages=1)
            evidence = caught.exception.evidence
            self.assertEqual(evidence["pagination"], {"pages": 1, "complete": False})
            self.assertEqual(evidence["output_manifest"][0]["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual((root / "output/out/provision.log").read_bytes(), raw)

    def test_exact_output_allowlist_rejects_paths_before_access(self):
        for name in ["../x", "out/../x.log", "out/link/x.log", "out/*.log", "/out/x.log"]:
            with self.subTest(name=name), self.assertRaises(driver.KaggleDriverError):
                driver.output_allowlist({"output_files": [name]})

    def test_official_adapter_uses_endpoint_specific_version_types(self):
        calls = []
        service = SimpleNamespace(
            get_kernel_session_status=lambda request: (calls.append(("status", request)) or
                SimpleNamespace(status=SimpleNamespace(name="COMPLETE"), failure_message="")),
            list_kernel_session_output=lambda request: (calls.append(("list", request)) or
                SimpleNamespace(files=[], next_page_token="")),
            download_kernel_output=lambda request: (calls.append(("download", request)) or
                SimpleNamespace(iter_content=lambda size: [b"raw"], close=lambda: None)),
        )
        client = SimpleNamespace(kernels=SimpleNamespace(kernels_api_client=service))
        class Api:
            CONFIG_NAME_AUTH_METHOD = "method"
            CONFIG_NAME_TOKEN = "token"
            CONFIG_NAME_USER = "user"
            config_values = {"method": "ACCESS_TOKEN", "token": "fixture-token", "user": "owner"}
            def authenticate(self):
                calls.append(("authenticate", None))
            def build_kaggle_client(self):
                return contextlib.nullcontext(client)
        modules = {name: types.ModuleType(name) for name in ["kaggle", "kaggle.api",
            "kaggle.api.kaggle_api_extended", "kagglesdk", "kagglesdk.kernels",
            "kagglesdk.kernels.types", "kagglesdk.kernels.types.kernels_api_service"]}
        modules["kaggle.api.kaggle_api_extended"].KaggleApi = Api
        request_module = modules["kagglesdk.kernels.types.kernels_api_service"]
        for name in ["ApiGetKernelSessionStatusRequest", "ApiListKernelSessionOutputRequest", "ApiDownloadKernelOutputRequest"]:
            setattr(request_module, name, SimpleNamespace)
        # This fake-SDK test checks request shapes; POSIX timeout/IO admission
        # is exercised separately and must not require a native POSIX host here.
        with mock.patch.dict(sys.modules, modules), mock.patch.dict(os.environ, {"KAGGLE_API_TOKEN": "fixture-token"}), \
             mock.patch.object(driver, "_bounded_provider_io", side_effect=lambda *a, **k: contextlib.nullcontext()):
            identity = {"version_number": 3}
            driver._official_provider_operation("status", kernel="owner/slug", identity=identity)
            driver._official_provider_operation("list", kernel="owner/slug", identity=identity)
            value = driver._official_provider_operation("download", kernel="owner/slug", identity=identity,
                                                       file_path="out/result.json", consume=lambda chunks: b"".join(chunks))
        self.assertEqual(value, b"raw")
        requests = {name: request for name, request in calls if request is not None}
        self.assertEqual(requests["status"].version_label, "v3")
        self.assertEqual(requests["list"].version_label, "v3")
        self.assertEqual(requests["download"].version_number, 3)
        self.assertIs(type(requests["download"].version_number), int)

    def test_parallel_reservation_cannot_admit_two_sixty_unit_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            def reserve(attempt):
                return budget_ledger.check_and_reserve(state_root=Path(tmp), backend="modal", job_id=attempt,
                                                      worst_case=60, available=100, unit="usd")
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(reserve, ["a", "b"]))
            self.assertEqual(sum(row["ok"] for row in results), 1)
            self.assertEqual(budget_ledger.outstanding(Path(tmp), "modal"), 60)

    def test_unknown_or_unenforceable_reservation_amount_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for amount in [None, float("nan"), float("inf"), -1, True]:
                with self.subTest(amount=amount), self.assertRaises(ValueError):
                    budget_ledger.check_and_reserve(state_root=Path(tmp), backend="modal", job_id="a",
                                                  worst_case=amount, available=100, unit="usd")

    def test_internet_is_typed_requested_and_host_capped(self):
        self.assertFalse(driver.internet_policy({}, SimpleNamespace()))
        self.assertTrue(driver.internet_policy({"enable_internet": True},
                                              SimpleNamespace(kaggle_allow_internet=True)))
        for value in [True, "true", 1, None]:
            with self.subTest(value=value), self.assertRaises(driver.KaggleDriverError):
                driver.internet_policy({"enable_internet": value}, SimpleNamespace())

    def test_exact_server_identity_and_endpoint_selectors(self):
        reply = {"kernel_id": 17, "version_number": 3, "ref": "/code/owner/slug",
                 "url": "https://www.kaggle.com/code/owner/slug", "error": ""}
        identity = driver.accepted_identity(reply, "owner/slug")
        self.assertEqual(identity["kernel_id"], 17)
        self.assertEqual(driver.version_selector(identity, "status"), "v3")
        self.assertEqual(driver.version_selector(identity, "download"), 3)
        for change in [{"ref": "/code/other/slug"}, {"version_number": True},
                       {"ref": "owner/slug/2"}, {"url": "https://evil.invalid/code/owner/slug"}]:
            with self.subTest(change=change), self.assertRaises(driver.KaggleDriverError):
                driver.accepted_identity({**reply, **change}, "owner/slug")

    def test_fetch_never_uses_latest_without_accepted_identity(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(driver, "run_kaggle") as cli:
            with self.assertRaisesRegex(driver.KaggleDriverError, "submission intent"):
                driver.fetch(kernel="owner/slug", config=None, job_dir=tmp,
                             dest=Path(tmp) / "output")
            cli.assert_not_called()

    def test_preflight_never_validates_an_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp)
            (job / "run.sh").write_text("true\n", encoding="utf-8")
            (job / "manifest.json").write_text(json.dumps({
                "job_id": "offline", "total_units": 1, "upload_files": ["manifest.json", "run.sh"]}), encoding="utf-8")
            cfg = SimpleNamespace(kaggle_enabled=True)
            with mock.patch.object(driver.kaggle_backend, "token_present", return_value=True), \
                 mock.patch.object(driver.kaggle_backend, "KAGGLEHUB_VALIDATE",
                                   side_effect=AssertionError("network probe")):
                result = driver.preflight(job_dir=job, config=cfg)
            self.assertEqual(result["account_status"], "not_checked")

    def test_reservation_retry_is_idempotent_and_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = dict(state_root=Path(tmp), backend="modal", job_id="attempt-1",
                        worst_case=60, available=100, unit="usd")
            self.assertTrue(budget_ledger.check_and_reserve(**args)["ok"])
            self.assertTrue(budget_ledger.check_and_reserve(**args)["ok"])
            self.assertEqual(budget_ledger.outstanding(Path(tmp), "modal"), 60)
            with self.assertRaises(ValueError):
                budget_ledger.check_and_reserve(**{**args, "worst_case": 20})
            self.assertFalse(budget_ledger.check_and_reserve(**{**args, "job_id": "attempt-2"})["ok"])

    def test_unknown_spend_keeps_reservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            budget_ledger.check_and_reserve(state_root=root, backend="modal", job_id="a",
                                          worst_case=60, available=100, unit="usd")
            budget_ledger.reconcile(root, "modal", "a", outcome="unknown")
            self.assertEqual(budget_ledger.outstanding(root, "modal"), 60)
            budget_ledger.reconcile(root, "modal", "a", 12, outcome="known")
            budget_ledger.reconcile(root, "modal", "a", 12, outcome="known")
            self.assertEqual(budget_ledger.outstanding(root, "modal"), 12)


if __name__ == "__main__":
    unittest.main()
