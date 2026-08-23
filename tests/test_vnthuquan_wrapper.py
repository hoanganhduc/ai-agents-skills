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



class HelpReportsAMissingPackageLikeEveryOtherPathTests(unittest.TestCase):
    """`--help` must fail through the wrapper's error contract, not a traceback.

    Every command in `main` runs inside a `try/except WrapperError` that turns a
    missing `vnthuquan` executable into `missing_executable` with exit 127. The
    `--help` branch returns before that block, so `native_help` -- which shells
    out to the package -- let the exception escape `main` entirely. An agent
    running `doctor --help` to find out whether the skill is usable got a Python
    traceback on stderr, nothing on stdout, and exit 1.
    """

    #: Every verb whose help is answered by the package rather than by a
    #: built-in string, i.e. every verb that reaches `run_pkg` from the help
    #: branch. Kept as a literal so adding a verb to the module's set without
    #: re-checking this path shows up as a failure here.
    NATIVE = (
        "archive", "categories", "completion", "config", "doctor", "download",
        "formats", "list", "mirrors", "search", "show", "validate",
    )

    def _run(self, argv):
        """Run `main(argv)` with the package absent.

        Returns (exit code, stdout-or-parsed-payload, stderr). Text mode reports
        failures on stderr as `error: <message>`, so both streams matter here.
        """

        import contextlib
        import io
        import json as _json

        with tempfile.TemporaryDirectory() as raw:
            vtq = load_wrapper(Path(raw))

            def no_executable():
                raise vtq.WrapperError(
                    "vnthuquan command not found", "missing_executable", 127
                )

            out_buf, err_buf = io.StringIO(), io.StringIO()
            with mock.patch.object(vtq, "resolve_vnthuquan", no_executable), \
                 contextlib.redirect_stdout(out_buf), \
                 contextlib.redirect_stderr(err_buf):
                code = vtq.main(argv)
            out = out_buf.getvalue()
        payload = _json.loads(out) if out.strip().startswith("{") else out
        return code, payload, err_buf.getvalue()

    def test_the_module_set_matches_the_verbs_this_test_covers(self) -> None:
        """Non-vacuity anchor: these verbs really do take the package help path."""

        with tempfile.TemporaryDirectory() as raw:
            vtq = load_wrapper(Path(raw))
        self.assertEqual(set(self.NATIVE), set(vtq.NATIVE_HELP_COMMANDS))

    def test_every_native_help_verb_reports_the_contract_error(self) -> None:
        for verb in self.NATIVE:
            with self.subTest(verb=verb):
                code, payload, _ = self._run([verb, "--help", "--json"])
                self.assertEqual(code, 127)
                self.assertIsInstance(payload, dict)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error_code"], "missing_executable")
                self.assertEqual(payload["exit_code"], 127)

    def test_help_and_non_help_agree_on_the_same_missing_package(self) -> None:
        """The bug was the divergence, so pin the two paths to each other."""

        help_code, help_payload, _ = self._run(["show", "--help", "--json"])
        run_code, run_payload, _ = self._run(["show", "123", "--json"])

        self.assertEqual(help_code, run_code)
        for key in ("ok", "error_code", "message", "exit_code", "command"):
            self.assertEqual(help_payload[key], run_payload[key], key)

    def test_text_mode_reports_it_too(self) -> None:
        """Without --json the same failure is reported on stderr, not raised."""

        code, out, err = self._run(["doctor", "--help"])

        self.assertEqual(code, 127)
        self.assertEqual(out, "")
        self.assertEqual(err.strip(), "error: vnthuquan command not found")

    def test_help_that_never_touches_the_package_is_unaffected(self) -> None:
        """The built-in help paths must keep printing help and exiting 0."""

        for argv in (["--help"], ["queue", "--help"], ["add-to-calibre", "--help"]):
            with self.subTest(argv=argv):
                code, out, _ = self._run(argv)
                self.assertEqual(code, 0)
                self.assertIn("vnthuquan assistant wrapper", out)


class BareInvocationHonoursJsonModeTests(unittest.TestCase):
    """`--json` must always leave parseable JSON on stdout.

    A bare invocation is a usage error, not a help request, so it reports the
    same way an unknown command does. Help itself stays text in both modes:
    `native_help` prints the package's own help through, so text-for-help is
    the wrapper's convention rather than an oversight.
    """

    def _run(self, argv):
        """Run `main(argv)`; return (exit code, stdout, parsed payload or None)."""
        import contextlib, io
        import json as _json

        with tempfile.TemporaryDirectory() as raw:
            vtq = load_wrapper(Path(raw))
            out_buf = io.StringIO()
            with contextlib.redirect_stdout(out_buf):
                code = vtq.main(argv)
            out = out_buf.getvalue()
        try:
            payload = _json.loads(out)
        except ValueError:
            payload = None
        return code, out, payload

    def test_an_unknown_command_really_does_answer_in_json(self) -> None:
        # Non-vacuity anchor: this is the contract the bare path must match,
        # so the test below is meaningless if this one ever stops holding.
        code, _out, payload = self._run(["__no_such_verb__", "--json"])
        self.assertIsNotNone(payload)
        self.assertEqual((code, payload["ok"], payload["error_code"]), (2, False, "usage"))

    def test_a_bare_invocation_in_json_mode_is_parseable(self) -> None:
        code, out, payload = self._run(["--json"])
        self.assertIsNotNone(payload, f"stdout was not JSON: {out!r}")
        self.assertIs(payload["ok"], False)
        self.assertEqual(payload["error_code"], "usage")
        self.assertEqual(payload["exit_code"], 2)
        self.assertEqual(code, 2)

    def test_the_bare_path_and_the_unknown_command_path_agree(self) -> None:
        bare_code, _o, bare = self._run(["--json"])
        unknown_code, _o2, unknown = self._run(["__no_such_verb__", "--json"])
        self.assertEqual(bare_code, unknown_code)
        self.assertEqual(
            (bare["ok"], bare["error_code"], bare["exit_code"]),
            (unknown["ok"], unknown["error_code"], unknown["exit_code"]),
        )

    def test_text_mode_still_prints_the_banner(self) -> None:
        code, out, payload = self._run([])
        self.assertEqual(code, 0)
        self.assertIsNone(payload)
        self.assertIn("vnthuquan assistant wrapper", out)

    def test_explicit_help_is_still_text_in_both_modes(self) -> None:
        for argv in (["--help"], ["-h"], ["help"], ["--help", "--json"]):
            with self.subTest(argv=argv):
                code, out, _payload = self._run(argv)
                self.assertEqual(code, 0)
                self.assertIn("vnthuquan assistant wrapper", out)


if __name__ == "__main__":
    unittest.main()
