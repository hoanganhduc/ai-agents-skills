"""Regression checks for prompt delivery independent of child read speed."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

RUNTIME_DIR = (
    Path(__file__).resolve().parents[1]
    / "canonical"
    / "runtime"
    / "skills"
    / "autonomous-research-loop-runtime"
)
sys.path.insert(0, str(RUNTIME_DIR))

from process_input import prepared_stdin  # noqa: E402


LARGE_PROMPT = "private prompt: caf\u00e9 \U0001f989\r\n" * 65536


class PreparedStdinTests(unittest.TestCase):
    def test_slow_reader_gets_complete_large_utf8_prompt_and_eof(self):
        expected = LARGE_PROMPT.encode("utf-8")
        self.assertGreater(len(expected), 1024 * 1024)
        child = """
import sys
import time
time.sleep(0.1)
while True:
    chunk = sys.stdin.buffer.read(8192)
    if not chunk:
        break
    sys.stdout.buffer.write(chunk)
    time.sleep(0.001)
sys.stdout.buffer.flush()
"""
        with tempfile.TemporaryDirectory() as directory:
            with tempfile.TemporaryFile(dir=directory) as output:
                with prepared_stdin(LARGE_PROMPT, directory=directory) as stream:
                    with subprocess.Popen(
                        [sys.executable, "-B", "-c", child],
                        stdin=stream,
                        stdout=output,
                        stderr=subprocess.DEVNULL,
                    ) as proc:
                        deadline = time.monotonic() + 5
                        try:
                            while True:
                                try:
                                    proc.wait(timeout=0.02)
                                    break
                                except subprocess.TimeoutExpired:
                                    if time.monotonic() >= deadline:
                                        self.fail("slow reader did not reach EOF")
                        finally:
                            if proc.poll() is None:
                                proc.kill()
                                proc.wait(timeout=2)
                        self.assertEqual(proc.returncode, 0)
                self.assertTrue(stream.closed)
                output.seek(0)
                self.assertEqual(output.read(), expected)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_short_prompt_and_absent_prompts(self):
        with tempfile.TemporaryDirectory() as directory:
            for prompt in ("caf\u00e9\n\x00end", "", None):
                with self.subTest(prompt=prompt):
                    with prepared_stdin(prompt, directory=directory) as stream:
                        if not prompt:
                            self.assertEqual(stream, subprocess.DEVNULL)
                        result = subprocess.run(
                            [
                                sys.executable,
                                "-B",
                                "-c",
                                "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
                            ],
                            stdin=stream,
                            capture_output=True,
                            timeout=3,
                            check=True,
                        )
                        self.assertEqual(result.stdout, (prompt or "").encode("utf-8"))
            self.assertEqual(list(Path(directory).iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "POSIX file mode and unlink semantics")
    def test_prompt_file_is_private_regular_and_anonymous(self):
        with tempfile.TemporaryDirectory() as directory:
            with prepared_stdin("private prompt", directory=directory) as stream:
                metadata = os.fstat(stream.fileno())
                self.assertTrue(stat.S_ISREG(metadata.st_mode))
                self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
                self.assertEqual(metadata.st_nlink, 0)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_context_exception_closes_and_removes_prompt_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "child launch failed"):
                with prepared_stdin("private prompt", directory=directory) as stream:
                    raise RuntimeError("child launch failed")
            self.assertTrue(stream.closed)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_encoding_failure_removes_prompt_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(UnicodeEncodeError):
                with prepared_stdin("\ud800", directory=directory):
                    self.fail("invalid UTF-8 text was accepted")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_child_that_never_reads_can_be_timed_out_and_terminated(self):
        with tempfile.TemporaryDirectory() as directory:
            with prepared_stdin(LARGE_PROMPT, directory=directory) as stream:
                with subprocess.Popen(
                    [sys.executable, "-B", "-c", "import time; time.sleep(60)"],
                    stdin=stream,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ) as proc:
                    try:
                        with self.assertRaises(subprocess.TimeoutExpired):
                            proc.wait(timeout=0.05)
                    finally:
                        proc.kill()
                        proc.wait(timeout=2)
                    self.assertIsNotNone(proc.returncode)
            self.assertTrue(stream.closed)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_prompt_stays_out_of_child_arguments_and_environment(self):
        prompt = "secret-prompt-marker-170ea4\n"
        child = """
import json
import os
import sys
received = sys.stdin.read()
marker = received.strip()
print(json.dumps({
    "in_argv": any(marker in arg for arg in sys.argv),
    "in_environment": any(
        marker in key or marker in value for key, value in os.environ.items()
    ),
    "input": received,
}))
"""
        with tempfile.TemporaryDirectory() as directory:
            with prepared_stdin(prompt, directory=directory) as stream:
                result = subprocess.run(
                    [sys.executable, "-B", "-c", child],
                    stdin=stream,
                    capture_output=True,
                    timeout=3,
                    check=True,
                )
        received = json.loads(result.stdout)
        self.assertEqual(received["input"], prompt)
        self.assertFalse(received["in_argv"])
        self.assertFalse(received["in_environment"])

    def test_invalid_prompt_types_are_rejected_before_creating_files(self):
        with mock.patch("process_input.tempfile.TemporaryFile") as create:
            for prompt in (b"", b"bytes", 0, False, []):
                with self.subTest(prompt=prompt):
                    with self.assertRaisesRegex(TypeError, "text or None"):
                        with prepared_stdin(prompt, directory="unused"):
                            self.fail("non-text prompt was accepted")
            create.assert_not_called()

    def test_nonempty_prompt_requires_explicit_directory(self):
        with mock.patch("process_input.tempfile.TemporaryFile") as create:
            with self.assertRaises(TypeError):
                with prepared_stdin("prompt"):
                    self.fail("missing directory was accepted")
            with self.assertRaisesRegex(ValueError, "explicit private directory"):
                with prepared_stdin("prompt", directory=None):
                    self.fail("default temporary directory was accepted")
            create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
