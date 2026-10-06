"""Synthetic HTTP/time tests: no Kaggle import, authentication or network."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import signal
import sys
import time
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "canonical/runtime/workspace"))
sys.path.insert(0, str(ROOT / "canonical/runtime/skills/kaggle-research-compute"))
import kaggle_driver as driver
import requests


class UnsupportedProviderRuntimeTests(unittest.TestCase):
    def test_non_posix_runtime_is_refused_before_http(self):
        windows_os = SimpleNamespace(**{**vars(os), "name": "nt"})
        with mock.patch.object(driver, "os", windows_os), \
             mock.patch.object(requests.Session, "send") as send:
            with self.assertRaisesRegex(driver.KaggleDriverError, "POSIX main-thread"):
                with driver._bounded_provider_io("status"):
                    self.fail("unsupported runtime entered the provider boundary")
        send.assert_not_called()


@unittest.skipUnless(os.name == "posix" and hasattr(signal, "setitimer"), "POSIX main-thread timeout qualification")
class ProviderTimeoutTests(unittest.TestCase):
    def test_all_session_sends_receive_finite_limits_and_restore_method(self):
        with mock.patch.object(requests.Session, "send", return_value=object()) as send:
            original = requests.Session.send
            with driver._bounded_provider_io("status", timeout_seconds=0.5):
                requests.Session().send(object())
                requests.Session().send(object(), timeout=(0.01, 0.02))
            self.assertIs(requests.Session.send, original)
        first = send.call_args_list[0].kwargs["timeout"]
        self.assertTrue(all(0 < value <= 0.5 for value in first))
        self.assertEqual(send.call_args_list[1].kwargs["timeout"], (0.01, 0.02))

    def test_overall_deadline_escapes_sdk_exception_retry_and_cleans_up(self):
        original = signal.getsignal(signal.SIGALRM)
        swallowed = []
        started = time.monotonic()
        with self.assertRaisesRegex(driver.KaggleDriverError, "overall"):
            with driver._bounded_provider_io("download", timeout_seconds=0.04):
                try:
                    time.sleep(1)
                except Exception:
                    swallowed.append(True)
        self.assertFalse(swallowed)
        self.assertLess(time.monotonic() - started, 0.6)
        self.assertIs(signal.getsignal(signal.SIGALRM), original)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_existing_timer_restored_with_elapsed_time(self):
        original_handler = signal.getsignal(signal.SIGALRM)
        original_timer = signal.getitimer(signal.ITIMER_REAL)
        handler = lambda *_: None
        try:
            signal.signal(signal.SIGALRM, handler)
            signal.setitimer(signal.ITIMER_REAL, 0.8)
            with driver._bounded_provider_io("status", timeout_seconds=0.5):
                time.sleep(0.04)
            remaining, interval = signal.getitimer(signal.ITIMER_REAL)
            self.assertTrue(0 < remaining < 0.79)
            self.assertEqual(interval, 0)
            self.assertIs(signal.getsignal(signal.SIGALRM), handler)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, original_handler)
            signal.setitimer(signal.ITIMER_REAL, *original_timer)

    def test_earlier_caller_interrupt_is_not_masked(self):
        original = signal.getsignal(signal.SIGALRM)
        def interrupt(*_):
            raise KeyboardInterrupt()
        try:
            signal.signal(signal.SIGALRM, interrupt)
            signal.setitimer(signal.ITIMER_REAL, 0.03)
            with self.assertRaises(KeyboardInterrupt):
                with driver._bounded_provider_io("status", timeout_seconds=0.5):
                    time.sleep(1)
            self.assertIs(signal.getsignal(signal.SIGALRM), interrupt)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, original)

    def test_sdk_catching_caller_exception_does_not_remove_our_deadline(self):
        original = signal.getsignal(signal.SIGALRM)
        def caller_timeout(*_):
            raise TimeoutError("caller timer")
        try:
            signal.signal(signal.SIGALRM, caller_timeout)
            signal.setitimer(signal.ITIMER_REAL, 0.02)
            with self.assertRaisesRegex(driver.KaggleDriverError, "overall"):
                with driver._bounded_provider_io("status", timeout_seconds=0.06):
                    for _ in range(2):
                        try:
                            time.sleep(1)
                        except Exception:
                            pass
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, original)

    def test_other_threads_are_refused_before_any_io(self):
        def enter():
            with driver._bounded_provider_io("status"):
                raise AssertionError("unsupported thread entered")
        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.assertRaisesRegex(driver.KaggleDriverError, "main.thread"):
                executor.submit(enter).result()

    def test_whoami_has_same_overall_guard(self):
        with mock.patch.dict(driver.PROVIDER_OPERATION_TIMEOUTS, {"whoami": 0.04}), \
             mock.patch.object(driver.kaggle_backend, "KAGGLEHUB_VALIDATE", side_effect=lambda _: time.sleep(1)):
            with self.assertRaisesRegex(driver.KaggleDriverError, "overall"):
                driver._whoami(None)

    def test_wait_passes_remaining_budget_and_caps_sleep(self):
        identity = {"kernel": "owner/slug", "version_number": 3}
        started = time.monotonic()
        with mock.patch.object(driver, "load_submission_identity", return_value=identity), \
             mock.patch.object(driver, "PROVIDER_RUNNER", return_value={"status": "running"}) as provider:
            result = driver.wait(kernel="owner/slug", config=None, submission_intent="host-intent",
                                 timeout=0.04, interval=20)
        self.assertEqual(result["status"], "timeout")
        self.assertLess(time.monotonic() - started, 0.6)
        self.assertTrue(all(0 < call.kwargs["timeout_s"] <= 0.04 for call in provider.call_args_list))

    def test_wait_status_cannot_run_past_the_shorter_caller_cap(self):
        identity = {"kernel": "owner/slug", "version_number": 3}
        def stalled(operation, **kwargs):
            with driver._bounded_provider_io(operation, timeout_seconds=kwargs["timeout_s"]):
                time.sleep(1)
        started = time.monotonic()
        with mock.patch.object(driver, "load_submission_identity", return_value=identity), \
             mock.patch.object(driver, "PROVIDER_RUNNER", side_effect=stalled):
            result = driver.wait(kernel="owner/slug", config=None, submission_intent="host-intent", timeout=0.04)
        self.assertEqual(result["status"], "timeout")
        self.assertLess(time.monotonic() - started, 0.6)

    def test_diagnostic_wait_cli_inherits_remaining_budget(self):
        with mock.patch.object(driver, "run_kaggle", return_value={"stdout": "complete"}) as cli:
            result = driver.wait(kernel="owner/slug", config=None, timeout=0.1)
        self.assertEqual(result["status"], "complete")
        self.assertTrue(0 < cli.call_args.kwargs["timeout"] <= 0.1)

    def test_submission_timeout_keeps_unknown_fence_and_never_repushes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "bundle"; job.mkdir()
            (job / "run.sh").write_text("true\n", encoding="utf-8")
            (job / "manifest.json").write_text(json.dumps({"job_id": "timeout", "total_units": 1,
                "upload_files": ["manifest.json", "run.sh"]}), encoding="utf-8")
            def stalled(operation, **kwargs):
                with driver._bounded_provider_io(operation, timeout_seconds=0.04):
                    time.sleep(1)
            with mock.patch.object(driver, "token_present", return_value=True), \
                 mock.patch.object(driver, "_resolve_username", return_value="owner"), \
                 mock.patch.object(driver.kaggle_backend, "probe", return_value={"adequate": True, "available": True}), \
                 mock.patch.object(driver, "PROVIDER_RUNNER", side_effect=stalled) as provider:
                args = dict(job_dir=job, config=None, state_root=root, work_root=root / "work", confirm=True,
                            expected_bundle_sha256=driver.bundle_sha256(job), expected_owner="owner")
                with self.assertRaises(driver.KaggleDriverError) as caught:
                    driver.push(**args)
                intent = Path(caught.exception.evidence["submission_intent"])
                self.assertEqual(json.loads(intent.read_text(encoding="utf-8"))["state"], "acceptance_unknown")
                with self.assertRaisesRegex(driver.KaggleDriverError, "query status"):
                    driver.push(**args)
                self.assertEqual(provider.call_count, 1)


if __name__ == "__main__":
    unittest.main()
