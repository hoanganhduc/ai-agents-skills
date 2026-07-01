from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
GSP_ROOT = REPO_ROOT / "canonical" / "runtime" / "skills" / "getscipapers-requester"


def _load_module(name: str, filename: str):
    path = GSP_ROOT / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Do not write __pycache__ into the canonical runtime source tree; stray
    # .pyc files there are flagged as denied by the runtime inventory check.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class GetSciPapersSetupTests(unittest.TestCase):
    def test_requirements_pins_fork_master_branch_by_url(self) -> None:
        text = (GSP_ROOT / "requirements.txt").read_text(encoding="utf-8")
        # The fork's default branch is master, and the distribution is named
        # getscipapers-hoanganhduc, so a PEP 508 ``getscipapers @`` name prefix
        # triggers a pip name-mismatch error. Install by URL only.
        self.assertIn(
            "git+https://github.com/hoanganhduc/getscipapers.git@master",
            text,
        )
        self.assertNotIn("getscipapers @ git+", text)

    def test_venv_python_branches_on_os(self) -> None:
        setup = _load_module("gsp_setup_under_test", "run_gsp_setup.py")
        venv = Path("/tmp/example-venv")
        with mock.patch.object(setup.os, "name", "posix"):
            self.assertEqual(setup._venv_python(venv), venv / "bin" / "python")
            self.assertEqual(setup._venv_getscipapers(venv), venv / "bin" / "getscipapers")
        with mock.patch.object(setup.os, "name", "nt"):
            self.assertEqual(setup._venv_python(venv), venv / "Scripts" / "python.exe")
            self.assertEqual(setup._venv_getscipapers(venv), venv / "Scripts" / "getscipapers.exe")

    def test_venv_dir_honors_env_override(self) -> None:
        setup = _load_module("gsp_setup_under_test", "run_gsp_setup.py")
        with mock.patch.dict(os.environ, {"GETSCIPAPERS_VENV": "/custom/gsp"}, clear=False):
            self.assertEqual(setup._venv_dir(), Path("/custom/gsp"))
        env = {k: v for k, v in os.environ.items() if k != "GETSCIPAPERS_VENV"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(setup._venv_dir().name, ".getscipapers_venv")

    def test_setup_creates_venv_then_installs_requirements(self) -> None:
        setup = _load_module("gsp_setup_under_test", "run_gsp_setup.py")
        calls: list[list[str]] = []

        def fake_run(argv, check=False, **kwargs):
            calls.append([str(a) for a in argv])

            class _Done:
                returncode = 0

            return _Done()

        with (
            mock.patch.object(setup.subprocess, "run", side_effect=fake_run),
            mock.patch.object(setup, "_venv_dir", return_value=Path("/tmp/gsp-venv")),
            mock.patch.object(setup, "_emit"),
        ):
            rc = setup.cmd_setup(mock.Mock())

        self.assertEqual(rc, 0)
        # First action creates the venv with the launching interpreter.
        self.assertEqual(calls[0][1:3], ["-m", "venv"])
        self.assertEqual(calls[0][3], str(Path("/tmp/gsp-venv")))
        # Final action installs the fork from the skill's requirements.txt.
        install = calls[-1]
        self.assertEqual(install[1:4], ["-m", "pip", "install"])
        self.assertIn("-r", install)
        self.assertTrue(install[-1].endswith("requirements.txt"))
        joined = " ".join(" ".join(call) for call in calls)
        self.assertIn("ensurepip", joined)


class GetSciPapersResolverTests(unittest.TestCase):
    def test_env_var_takes_priority(self) -> None:
        helper = _load_module("gsp_helper_under_test", "gsp_openclaw_helper.py")
        with (
            mock.patch.dict(os.environ, {"GETSCIPAPERS_BIN": "/opt/gsp/getscipapers"}, clear=False),
            mock.patch.object(helper.Path, "is_file", return_value=True),
            mock.patch.object(helper.os, "access", return_value=True),
        ):
            self.assertEqual(helper.find_getscipapers(), "/opt/gsp/getscipapers")

    def test_windows_scripts_candidate_resolves_without_x_ok(self) -> None:
        helper = _load_module("gsp_helper_under_test", "gsp_openclaw_helper.py")

        # Stub the module's Path so the resolver never instantiates a real
        # WindowsPath while os.name is forced to "nt": pathlib refuses to build
        # WindowsPath on a POSIX host, which would error this test on Linux/macOS.
        class _FakePath:
            def __init__(self, raw: object) -> None:
                self._p = str(raw)

            def __truediv__(self, other: object) -> "_FakePath":
                return _FakePath(f"{self._p}/{other}")

            def __str__(self) -> str:
                return self._p

            def is_file(self) -> bool:
                # Only the venv Scripts/*.exe candidate "exists".
                return self._p.endswith("getscipapers.exe") and ".getscipapers_venv" in self._p

            @classmethod
            def home(cls) -> "_FakePath":
                return _FakePath("/fake/home")

        env = {k: v for k, v in os.environ.items() if k != "GETSCIPAPERS_BIN"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(helper, "Path", _FakePath),
            mock.patch.object(helper.os, "name", "nt"),
            mock.patch.object(helper.shutil, "which", return_value=None),
            # X_OK must be skipped on Windows; force it False to prove it is not consulted.
            mock.patch.object(helper.os, "access", return_value=False),
        ):
            resolved = helper.find_getscipapers()

        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.endswith("getscipapers.exe"))
        self.assertIn(".getscipapers_venv", resolved)

    def test_missing_everywhere_returns_none(self) -> None:
        helper = _load_module("gsp_helper_under_test", "gsp_openclaw_helper.py")
        env = {k: v for k, v in os.environ.items() if k != "GETSCIPAPERS_BIN"}
        env.setdefault("HOME", str(Path.home()))
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(helper.shutil, "which", return_value=None),
            mock.patch.object(helper.Path, "is_file", return_value=False),
        ):
            self.assertIsNone(helper.find_getscipapers())


class GetSciPapersRunnerTests(unittest.TestCase):
    def test_getpapers_defaults_to_no_proxy(self) -> None:
        helper = _load_module("gsp_helper_under_test", "gsp_openclaw_helper.py")
        apply = helper._apply_runner_proxy_default
        # getpapers gets --no-proxy appended (a stale proxy breaks doi.org resolution).
        self.assertEqual(
            apply(["getpapers", "--doi", "10.1/x"]),
            ["getpapers", "--doi", "10.1/x", "--no-proxy"],
        )
        # An explicit proxy flag is respected, not overridden.
        for flag in ("--proxy", "--no-proxy", "--auto-proxy"):
            self.assertEqual(
                apply(["getpapers", "--doi", "10.1/x", flag]),
                ["getpapers", "--doi", "10.1/x", flag],
            )
        # Other modules and empty argv are untouched.
        self.assertEqual(apply(["zlib", "--search", "x"]), ["zlib", "--search", "x"])
        self.assertEqual(apply([]), [])


if __name__ == "__main__":
    unittest.main()
