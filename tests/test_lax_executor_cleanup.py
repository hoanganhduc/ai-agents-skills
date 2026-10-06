"""A daemon error must not become evidence that an executor was removed."""
from pathlib import Path
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

RUNTIME = Path(__file__).resolve().parents[1] / "canonical/runtime/skills/lax-formalization"
sys.path.insert(0, str(RUNTIME))
import lax_executor as executor


class ExecutorCleanupTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX catchable termination; native executor is Linux only")
    def test_sigterm_unwinds_verification_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "cleanup"
            script = """
import os, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import lax_executor as executor
def body(*args, **kwargs):
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    finally:
        Path(sys.argv[2]).write_text('cleanup-ran', encoding='utf-8')
executor._verify = body
executor.verify(None, None)
"""
            result = subprocess.run([sys.executable, "-c", script, str(RUNTIME), str(marker)],
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 128 + signal.SIGTERM)
            self.assertEqual(marker.read_text(encoding="utf-8"), "cleanup-ran")

    def test_termination_guard_restores_callers_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        with executor.verification_termination_guard():
            self.assertNotEqual(signal.getsignal(signal.SIGTERM), previous)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def phase(self, daemon_status, remaining=b"", interrupt=None):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp); (job / "control").mkdir()
            cfg = {key: str(job) for key in ["package_root", "elan_home", "warm_root", "tools_root"]}
            cfg["image"] = "sha256:" + "a" * 64
            process = mock.Mock()
            process.poll.return_value = None
            selector = mock.Mock()
            selector.select.return_value = []
            self.docker_calls = []

            def terminate():
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

            if interrupt == "late-cleanup": selector.close.side_effect = terminate

            def launch(*args, **kwargs):
                if interrupt == "startup": terminate()
                return process

            def docker(args, **kwargs):
                self.docker_calls.append(args[1])
                if args[:2] == ["docker", "exec"]:
                    if interrupt in {"active", "repeat"}: terminate()
                    return subprocess.CompletedProcess(args, 0, b'{"mode":"static","exit":0}', b"")
                if args[:2] == ["docker", "rm"]:
                    if interrupt in {"cleanup", "repeat"}: terminate()
                    return subprocess.CompletedProcess(args, daemon_status, b"", b"")
                if args[:2] == ["docker", "inspect"]:
                    return subprocess.CompletedProcess(args, 0 if remaining else 1, b"", b"daemon unavailable" if daemon_status else b"No such object")
                if args[:2] == ["docker", "ps"]:
                    return subprocess.CompletedProcess(args, daemon_status, remaining, b"daemon unavailable" if daemon_status else b"")
                raise AssertionError(args)

            with mock.patch.object(executor, "container_options", return_value=["docker", "run"]), \
                 mock.patch.object(executor.subprocess, "Popen", side_effect=launch), \
                 mock.patch.object(executor.subprocess, "run", side_effect=docker), \
                 mock.patch.object(executor.selectors, "DefaultSelector", return_value=selector), \
                 mock.patch.object(executor, "run", return_value=b"descendants-stopped"), \
                 mock.patch.object(executor, "collect_container"), \
                 executor.verification_termination_guard():
                return executor.phase(cfg, "static", job, [])

    def test_termination_at_startup_or_cleanup_still_confirms_removal(self):
        for timing in ["startup", "active", "cleanup", "late-cleanup", "repeat"]:
            with self.subTest(timing=timing), self.assertRaises(SystemExit) as raised:
                self.phase(0, interrupt=timing)
            self.assertEqual(raised.exception.code, 128 + signal.SIGTERM)
            self.assertIn("rm", self.docker_calls)
            self.assertEqual(self.docker_calls[-1], "ps")

    def test_interrupted_cleanup_does_not_hide_daemon_failure(self):
        with self.assertRaisesRegex(ValueError, "teardown"):
            self.phase(1, interrupt="repeat")
        self.assertEqual(self.docker_calls[-1], "ps")

    def test_daemon_error_does_not_confirm_teardown(self):
        with self.assertRaisesRegex(ValueError, "teardown"):
            self.phase(1)

    def test_successful_query_with_no_container_confirms_teardown(self):
        self.assertTrue(self.phase(0)["teardown_confirmed"])

    def test_still_present_container_is_refused(self):
        with self.assertRaisesRegex(ValueError, "teardown"):
            self.phase(0, b"fixture-container-id\n")


if __name__ == "__main__": unittest.main()
