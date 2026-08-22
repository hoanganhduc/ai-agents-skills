from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.ai_agents_skills.manifest import REPO_ROOT


RUNTIME_DIR = REPO_ROOT / "canonical" / "runtime" / "skills" / "vnthuquan"
SCRIPT = RUNTIME_DIR / "vnthuquan_wrapper.py"


def load_wrapper(data_root: Path):
    """Import the wrapper with its data directories pointed at a temp root.

    Every path the module resolves at import time comes from an environment
    variable, so a test can keep the wrapper entirely inside its own tempdir.
    """

    env = {
        "VNTHUQUAN_ASSISTANT_HOME": str(data_root),
        "VNTHUQUAN_RUN_DIR": str(data_root / "runs"),
        "VNTHUQUAN_STATE_DIR": str(data_root / "state"),
    }
    with mock.patch.dict(os.environ, env, clear=False):
        spec = importlib.util.spec_from_file_location("vtq_under_test", SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


DOCTOR_OK = {"ok": True, "json": {"status": "ok"}}
DRY_RUN_OK = {"ok": True, "json": {"status": "dry_run"}}
WRITE_OK = {"ok": True, "json": {"status": "ok", "id": 41, "title": "T", "authors": "A"}}
# What Calibre returns when the write ran and the library rejected it.
WRITE_FAILED = {
    "ok": False,
    "exit_code": 2,
    "json": {"status": "error", "error": "drive quota exceeded"},
}


class ExecutedMeansTheWriteRanTests(unittest.TestCase):
    """`executed` answers whether the write ran; `ok` answers whether it worked.

    Every other producer of the key in this module means "did it run" --
    `bool(execute)` on the queue path, a literal `False` next to
    `write_attempted: False` on the dry-run path, a literal `True` on the executed
    path.  The Calibre handoff published the OUTCOME instead, so a write that
    reached Calibre and was rejected came back `write_attempted: true` and
    `executed: false` in the same payload, under `recovery_notes` whose first line
    is "Do not retry a failed Calibre write automatically".
    """

    def _add(self, write_result, *, flags=("--execute", "--yes", "--duplicates-reviewed")):
        """Run one add against a stubbed Calibre and return the payload.

        `calibre_display_command` is stubbed with the rest: `add_to_calibre` calls
        it only to render the handoff lines it publishes, but it resolves a real
        runner script beside `VNTHUQUAN_ASSISTANT_HOME` and, on Windows, raises
        "PowerShell Calibre runner not found" when the `.ps1` is absent -- which
        it always is under a tempdir. These tests are about `executed` versus
        `ok`, so the display command is scaffolding, not subject.
        """

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            vtq = load_wrapper(root)
            book = root / "book.epub"
            book.write_bytes(b"PK\x03\x04 stand-in archive")

            def fake_run_calibre(args, *, timeout=None):
                if args[:1] == ["doctor"]:
                    return DOCTOR_OK
                if "--dry-run" in args:
                    return DRY_RUN_OK
                return write_result

            with mock.patch.object(vtq, "run_calibre", fake_run_calibre), \
                 mock.patch.object(
                     vtq, "validate_cmd", lambda a: {"ok": True, "validation": {"ok": True}}
                 ), \
                 mock.patch.object(vtq, "load_archive_record", lambda p: None), \
                 mock.patch.object(
                     vtq,
                     "calibre_cache_candidates",
                     lambda t, a, n: {"ok": True, "count": 0, "candidates": []},
                 ), \
                 mock.patch.object(
                     vtq, "calibre_display_command", lambda a: ["calibre-stub", *a]
                 ):
                return vtq.add_to_calibre([str(book), *flags])

    def test_a_failed_write_still_reports_that_it_ran(self) -> None:
        payload = self._add(WRITE_FAILED)

        self.assertTrue(payload["write_attempted"])
        self.assertTrue(payload["executed"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_code"], "calibre_write_failed")

    def test_a_successful_write_reports_both(self) -> None:
        payload = self._add(WRITE_OK)

        self.assertTrue(payload["write_attempted"])
        self.assertTrue(payload["executed"])
        self.assertTrue(payload["ok"])

    def test_the_dry_run_path_reports_neither(self) -> None:
        payload = self._add(WRITE_FAILED, flags=("--dry-run",))

        self.assertFalse(payload["write_attempted"])
        self.assertFalse(payload["executed"])
        self.assertTrue(payload["dry_run"])

    def test_the_two_keys_never_contradict_each_other(self) -> None:
        """A payload cannot say the write was attempted and not executed."""

        for label, result in (("failed", WRITE_FAILED), ("succeeded", WRITE_OK)):
            with self.subTest(write=label):
                payload = self._add(result)
                self.assertFalse(
                    payload["write_attempted"] and not payload["executed"],
                    f"attempted but not executed: {payload['write_attempted']}"
                    f"/{payload['executed']}",
                )

    def test_the_retry_warning_accompanies_the_failure_it_describes(self) -> None:
        """The notes are what makes the wrong verdict costly; pin them together."""

        payload = self._add(WRITE_FAILED)

        self.assertIn(
            "Do not retry a failed Calibre write automatically.",
            payload["recovery_notes"],
        )
        self.assertTrue(payload["executed"], "an agent must not read this as a no-op")


if __name__ == "__main__":
    unittest.main()
