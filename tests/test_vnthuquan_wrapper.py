from __future__ import annotations

import importlib.util
import os
import shutil
import sys
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


class UpstreamDefaultVenvIsFoundTests(unittest.TestCase):
    """upstream's installer puts the executable where the wrapper never looked.

    `scripts/install.sh` creates `VENV_PATH="${VNTHUQUAN_VENV:-$HOME/.vnthuquan}"` and
    installs no `~/.local/bin` shim, while the resolver searched only `~/.local/bin` and
    `~/.vnthuquan_venv`. A default upstream install therefore raised
    `missing_executable` / 127, which is what both vnthuquan live checks reported.
    """

    def _venv_exe(self, home: Path, prefix: str) -> Path:
        exe = home / prefix / "bin" / "vnthuquan"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)
        return exe

    def _venv_python(self, home: Path, prefix: str) -> Path:
        python = home / prefix / "bin" / "python"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        python.chmod(0o755)
        return python

    def _local_bin_shim(self, home: Path) -> Path:
        shim = home / ".local" / "bin" / "vnthuquan"
        shim.parent.mkdir(parents=True, exist_ok=True)
        shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        shim.chmod(0o755)
        return shim

    def _resolve_with_home(self, home: Path):
        with tempfile.TemporaryDirectory() as data_root:
            wrapper = load_wrapper(Path(data_root))
        with mock.patch.object(wrapper, "HOME", home), \
                mock.patch.object(wrapper, "SOURCE_DIR", home / "no-such-source"):
            return wrapper.resolve_vnthuquan()

    @unittest.skipIf(os.name == "nt", "POSIX candidate order")
    def test_the_upstream_default_venv_is_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            exe = self._venv_exe(home, ".vnthuquan")
            cmd, shown, _python = self._resolve_with_home(home)
            self.assertEqual(cmd, [str(exe)])
            self.assertEqual(shown, str(exe))

    @unittest.skipIf(os.name == "nt", "POSIX candidate order")
    def test_the_historical_venv_still_wins_when_both_exist(self) -> None:
        """Adding the upstream default must not re-point an existing install."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            historical = self._venv_exe(home, ".vnthuquan_venv")
            self._venv_exe(home, ".vnthuquan")
            cmd, _shown, _python = self._resolve_with_home(home)
            self.assertEqual(cmd, [str(historical)])

    @unittest.skipIf(os.name == "nt", "POSIX candidate order")
    def test_no_install_anywhere_is_still_missing_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception) as caught:
                self._resolve_with_home(Path(tmp))
            self.assertEqual(getattr(caught.exception, "code", None), "missing_executable")
            self.assertEqual(getattr(caught.exception, "exit_code", None), 127)


    @unittest.skipIf(os.name == "nt", "POSIX venv layout")
    def test_the_resolved_command_reports_its_own_venv_interpreter(self) -> None:
        """The venv that owns the console script also owns the Python it runs under.

        Reporting a PATH lookup answers a different question: the managed launcher
        fixes PATH to /usr/bin:/bin, so the name resolves to the system interpreter
        for a command that is not running under it, and `doctor` names the wrong one.
        """

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._venv_exe(home, ".vnthuquan")
            python = self._venv_python(home, ".vnthuquan")
            _cmd, _shown, reported = self._resolve_with_home(home)
        self.assertEqual(reported, str(python))
        self.assertNotEqual(reported, shutil.which("python3"))

    @unittest.skipIf(os.name == "nt", "POSIX venv layout")
    def test_a_shim_without_a_sibling_interpreter_still_reports_a_real_one(self) -> None:
        """~/.local/bin is not a venv, so no interpreter sits beside the shim there.

        The fallback still has to name a Python that exists rather than a guessed name.
        """

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self._local_bin_shim(home)
            _cmd, _shown, reported = self._resolve_with_home(home)
        self.assertEqual(reported, sys.executable)
        self.assertTrue(Path(reported).is_file())

    def test_detection_survives_a_host_with_no_python3_on_path(self) -> None:
        """A host that ships `python` without `python3` makes a name lookup return None.

        Detection has to answer with whatever interpreter the host actually has.
        """

        with tempfile.TemporaryDirectory() as data_root:
            wrapper = load_wrapper(Path(data_root))
        only_python = lambda name: "/usr/bin/python" if name == "python" else None
        with mock.patch.object(wrapper.sys, "executable", ""), \
                mock.patch.object(wrapper.shutil, "which", side_effect=only_python):
            self.assertEqual(wrapper.host_interpreter(), "/usr/bin/python")


class CurrentPackageDefaultsTests(unittest.TestCase):
    def test_https_default_and_author_key_alias_are_exposed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            wrapper = load_wrapper(Path(raw))

        self.assertEqual(
            wrapper.default_config()["default_mirror"],
            "https://vietnamthuquan.eu",
        )
        self.assertIn("--author-key", wrapper.QUEUE_SELECTOR_OPTIONS)
        self.assertEqual(
            wrapper.display_query(["--author-key", "kim-dung-284", "--limit", "2"]),
            "",
        )


class APackageFailureStillReportsWhatTheWrapperKnowsTests(unittest.TestCase):
    """A dead site and a missing install must not answer with the same payload.

    `require_success` collapses every nonzero package exit into one
    `package_error` envelope, so `doctor` reported "the site is unreachable" and
    "the package is not installed" identically, minus every field the wrapper had
    already resolved. The read-only probes print a complete JSON verdict and only
    then return 5, so both halves of the answer exist at once and the wrapper has
    to keep them.
    """

    #: Synthetic package failure preserving the current JSON contract.
    DOCTOR_DOWN = {
        "ok": False,
        "version": "0.1.2.dev1",
        "config_path": "/home/.../.config/vnthuquan/config.json",
        "download_dir": "/home/.../Downloads/vnthuquan",
        "download_dir_exists": False,
        "mirror": {
            "url": "https://vietnamthuquan.eu",
            "ok": False,
            "status_code": 404,
            "elapsed_seconds": 0.066,
            "error": "Not Found",
        },
    }

    #: Synthetic mirror failure preserving the current JSON contract.
    MIRRORS_DOWN = {
        "ok": False,
        "mirrors": [
            {
                "url": "https://vietnamthuquan.eu",
                "ok": False,
                "status_code": 404,
                "elapsed_seconds": 0.067,
                "error": "Not Found",
            },
            {
                "url": "https://vnthuquan.net",
                "ok": False,
                "status_code": 404,
                "elapsed_seconds": 0.765,
                "error": "Not Found",
            },
        ],
    }

    CONFIG_SHOW = {"ok": True, "config": {"default_mirror": "https://vietnamthuquan.eu"}}

    def _wrapper(self, responses, *, installed: bool = True):
        """The wrapper talking to a package that answers `responses` and nothing else.

        `responses` pairs the leading words of a package argv with the
        (status, stdout, stderr) triple `run_pkg` would have returned; a dict
        stdout is serialised the way the package serialises it. `installed=False`
        makes the resolver report the install missing instead.
        """

        import json as _json

        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        wrapper = load_wrapper(Path(root.name))

        def run_pkg(args, *, json_mode=True):
            key = " ".join(args)
            for prefix, (status, stdout, stderr) in responses:
                if key.startswith(prefix):
                    text = stdout if isinstance(stdout, str) else _json.dumps(stdout)
                    return status, text, stderr
            raise AssertionError(f"unexpected package call: {key}")

        def resolve():
            if not installed:
                raise wrapper.WrapperError("vnthuquan command not found", "missing_executable", 127)
            return ["vnthuquan"], "/opt/vnthuquan/bin/vnthuquan", "/opt/vnthuquan/bin/python"

        for name, replacement in (
            ("run_pkg", run_pkg),
            ("resolve_vnthuquan", resolve),
            ("package_version", lambda: "0.1.2.dev1" if installed else None),
        ):
            patcher = mock.patch.object(wrapper, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        return wrapper

    def _doctor_on_a_dead_site(self):
        wrapper = self._wrapper(
            [
                ("config show", (0, self.CONFIG_SHOW, "")),
                ("doctor", (5, self.DOCTOR_DOWN, "")),
            ]
        )
        return wrapper.doctor()

    def test_a_dead_site_still_reports_the_install_the_wrapper_resolved(self) -> None:
        payload = self._doctor_on_a_dead_site()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_code"], "package_error")
        self.assertTrue(payload["local_ready"])
        self.assertFalse(payload["live_site_ready"])
        self.assertTrue(payload["diagnose"]["ready"])

    def test_the_mirror_verdict_survives_the_nonzero_exit(self) -> None:
        """The package printed why it failed; collapsing the exit must not lose it."""

        payload = self._doctor_on_a_dead_site()
        self.assertEqual(payload["package_payload"]["mirror"]["status_code"], 404)
        self.assertEqual(payload["package_payload"]["mirror"]["url"], "https://vietnamthuquan.eu")

    def test_a_dead_site_and_a_missing_install_are_told_apart(self) -> None:
        """The two failures the wrapper used to report identically."""

        dead_site = self._doctor_on_a_dead_site()
        missing = self._wrapper([], installed=False).doctor()
        self.assertFalse(dead_site["ok"])
        self.assertFalse(missing["ok"])
        self.assertNotEqual(dead_site["local_ready"], missing["local_ready"])
        self.assertTrue(dead_site["local_ready"])
        self.assertFalse(missing["local_ready"])
        self.assertNotIn("package_payload", missing)

    def test_mirrors_check_on_a_dead_site_keeps_the_per_mirror_rows(self) -> None:
        wrapper = self._wrapper(
            [
                ("config show", (0, self.CONFIG_SHOW, "")),
                ("mirrors check", (5, self.MIRRORS_DOWN, "")),
            ]
        )
        payload = wrapper.mirrors(["check"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["subcommand"], "check")
        self.assertEqual(payload["default_mirror"], "https://vietnamthuquan.eu")
        self.assertEqual(payload["count"], 2)
        self.assertEqual([row["status_code"] for row in payload["mirrors"]], [404, 404])
        self.assertEqual([row["latency_ms"] for row in payload["mirrors"]], [67, 765])
        self.assertNotIn("elapsed_seconds", payload["mirrors"][0])

    def test_the_reported_message_is_a_reason_not_the_whole_payload(self) -> None:
        """With nothing on stderr the message was the JSON `package_payload` repeats.

        Text mode prints `error: <message>`, so the payload arrived twice and the
        reason arrived once, buried.
        """

        payload = self._doctor_on_a_dead_site()
        self.assertEqual(payload["message"], "mirror unreachable -- https://vietnamthuquan.eu: Not Found")

    def test_mirrors_check_names_every_mirror_that_failed(self) -> None:
        wrapper = self._wrapper(
            [
                ("config show", (0, self.CONFIG_SHOW, "")),
                ("mirrors check", (5, self.MIRRORS_DOWN, "")),
            ]
        )
        message = wrapper.mirrors(["check"])["message"]
        self.assertIn("https://vietnamthuquan.eu: Not Found", message)
        self.assertIn("https://vnthuquan.net: Not Found", message)

    def test_a_failed_search_reports_the_reason_and_the_query(self) -> None:
        """`search` is the only live check that exercises the surface the skill uses.

        The package answers a failed search with its own `error` object, so the
        row has to name the HTTP failure rather than repeat the payload.
        """

        failure = {"ok": False, "error": {"type": "SearchError", "message": "Search failed with HTTP 405", "exit_code": 1}}
        wrapper = self._wrapper([("search", (1, failure, ""))])
        payload = wrapper.search(["Kim Dung", "--limit", "3"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["message"], "Search failed with HTTP 405")
        self.assertEqual(payload["query"], "Kim Dung")
        self.assertEqual(payload["package_payload"], failure)

    def test_a_package_that_printed_no_verdict_invents_none(self) -> None:
        """A crash leaves nothing to merge, and nothing may be filled in for it."""

        wrapper = self._wrapper(
            [
                ("config show", (0, self.CONFIG_SHOW, "")),
                ("doctor", (1, "", "Traceback (most recent call last):\nRuntimeError: boom")),
            ]
        )
        payload = wrapper.doctor()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_code"], "package_error")
        self.assertTrue(payload["local_ready"])
        self.assertFalse(payload["live_site_ready"])
        self.assertNotIn("package_payload", payload)
        self.assertIn("RuntimeError: boom", payload["message"])

if __name__ == "__main__":
    unittest.main()
