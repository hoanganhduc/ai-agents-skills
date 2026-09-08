"""Behavioural tests for the secret env loader's child launch.

The loader fixes the child ``PATH`` to ``/usr/bin:/bin`` and never consults the
caller's.  Only an admitted skill Python venv, passed as ``--exec-argv0``,
prepends that venv's ``bin`` and replaces ``argv[0]`` so CPython adopts the venv
while the executed file stays the attested system interpreter.  These tests run
the real loader under ``/usr/bin/python3 -I`` against venvs built from that
interpreter; nothing here depends on secrets (``--no-load``).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import os_child_env

REPO = Path(__file__).resolve().parents[1]
SECRET_LOADER = REPO / "canonical" / "runtime" / "runners" / "load_secret_env.py"
SYSTEM_PYTHON = "/usr/bin/python3"
FIXED_CHILD_PATH = "/usr/bin:/bin"
INADMISSIBLE = "AAS_RUNTIME_PYTHON_PREFIX does not name a venv of the selected Python"
ATTESTED_REALPATH = re.compile(r"/usr/bin/python3(\.[0-9]+)?")

PROBE = """#!/usr/bin/env python3
import json, os, sys
print(json.dumps({
    "base": sys.prefix == sys.base_prefix,
    "prefix": sys.prefix,
    "executable": sys.executable,
    "path": os.environ.get("PATH"),
    "env": {k: os.environ.get(k) for k in (
        "AAS_RUNTIME_PYTHON_PREFIX", "AAS_SKILL_VENV", "PYTHONPATH", "HOME")},
}))
"""

REPORT = (
    "import json, os, sys\n"
    "argv0 = open('/proc/self/cmdline', 'rb').read().split(b'\\0')[0].decode()\n"
    "print(json.dumps({'argv0': argv0, 'executable': sys.executable, 'prefix': sys.prefix,"
    " 'base': sys.prefix == sys.base_prefix, 'exe': os.readlink('/proc/self/exe'),"
    " 'path': os.environ.get('PATH')}))\n"
)


@unittest.skipIf(os.name == "nt", "the secret env loader's child launch is POSIX-only")
class LoadSecretEnvChildLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        if not os.path.exists(SYSTEM_PYTHON):
            self.skipTest(f"{SYSTEM_PYTHON} is absent")
        previous = os.umask(0o077)
        self.addCleanup(os.umask, previous)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.root.chmod(0o700)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)

    def make_venv(self, name: str) -> Path:
        venv = self.root / name
        subprocess.run(
            [SYSTEM_PYTHON, "-I", "-m", "venv", "--without-pip", str(venv)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return venv

    def loader(
        self,
        *loader_args: str,
        command: list[str],
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        child_env = {**os_child_env(), "HOME": str(self.home), "PATH": FIXED_CHILD_PATH}
        child_env.update(env or {})
        argv = [
            SYSTEM_PYTHON,
            "-I",
            str(SECRET_LOADER),
            "--pointer-env",
            "AAS_SKILL_SECRETS_FILE",
            "--no-load",
            *loader_args,
            "--",
            *command,
        ]
        return subprocess.run(
            argv,
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )

    def report(self, completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_child_path_prepends_admitted_prefix_bin(self) -> None:
        venv = self.make_venv("venv")
        completed = self.loader(
            "--exec-argv0",
            str(venv / "bin" / "python"),
            command=[SYSTEM_PYTHON, "-c", 'import os, sys; print(os.environ["PATH"]); print(sys.prefix)'],
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.splitlines(), [f"{venv}/bin:{FIXED_CHILD_PATH}", str(venv)])

    def test_child_path_ignores_caller_path(self) -> None:
        venv = self.make_venv("venv")
        completed = self.loader(
            command=[SYSTEM_PYTHON, "-c", 'import os, sys; print(os.environ["PATH"]); print(sys.prefix == sys.base_prefix)'],
            env={"PATH": f"/nowhere:{venv}/bin"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.splitlines(), [FIXED_CHILD_PATH, "True"])

    def test_inadmissible_prefix_exits_127(self) -> None:
        attested = os.path.realpath(SYSTEM_PYTHON)
        no_cfg = self.root / "no-cfg"
        (no_cfg / "bin").mkdir(parents=True, mode=0o700)
        (no_cfg / "bin" / "python").symlink_to(attested)
        copied = self.make_venv("copied")
        (copied / "bin" / "python").unlink()
        shutil.copy2(attested, copied / "bin" / "python")
        shell = self.make_venv("shell")
        (shell / "bin" / "python").unlink()
        (shell / "bin" / "python").symlink_to("/bin/sh")
        fixtures = {
            "no pyvenv.cfg": no_cfg,
            "bin/python is a regular file": copied,
            "bin/python does not resolve to the command": shell,
        }
        for label, prefix in fixtures.items():
            with self.subTest(label):
                completed = self.loader(
                    "--exec-argv0",
                    str(prefix / "bin" / "python"),
                    command=[SYSTEM_PYTHON, "-c", "print('reached')"],
                )
                self.assertEqual(completed.returncode, 127, completed.stderr)
                self.assertIn(INADMISSIBLE, completed.stderr)
                self.assertNotIn("reached", completed.stdout)
        with self.subTest("relative --exec-argv0 is a usage error"):
            completed = self.loader("--exec-argv0", "venv/bin/python", command=[SYSTEM_PYTHON, "-c", "print('reached')"])
            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertIn("--exec-argv0 must be an absolute path", completed.stderr)
            self.assertNotIn("reached", completed.stdout)

    @unittest.skipUnless(os.path.exists("/proc/self/exe"), "requires Linux procfs")
    def test_exec_argv0_replaces_only_argv0(self) -> None:
        venv = self.make_venv("venv")
        argv0 = str(venv / "bin" / "python")
        completed = self.loader("--exec-argv0", argv0, command=[SYSTEM_PYTHON, "-c", REPORT])
        report = self.report(completed)
        self.assertEqual(report["argv0"], argv0)
        self.assertEqual(report["executable"], argv0)
        self.assertEqual(report["prefix"], str(venv))
        self.assertFalse(report["base"])
        self.assertRegex(str(report["exe"]), ATTESTED_REALPATH.pattern + "$")
        self.assertNotIn("/proc/self/fd/", str(report["exe"]))
        self.assertEqual(report["path"], f"{venv}/bin:{FIXED_CHILD_PATH}")

    def test_stdlib_commands_stay_on_base_prefix(self) -> None:
        venv = self.make_venv("venv")
        marker = self.root / "hostile-python3-ran"
        hostile = venv / "bin" / "python3"
        hostile.unlink()
        hostile.write_text(f"#!/bin/sh\n: > '{marker}'\nexec {SYSTEM_PYTHON} \"$@\"\n", encoding="utf-8")
        hostile.chmod(0o700)
        probe = self.root / "probe.py"
        probe.write_text(PROBE, encoding="utf-8")
        probe.chmod(0o700)
        completed = self.loader(
            command=[str(probe)],
            env={"AAS_RUNTIME_PYTHON_PREFIX": str(venv), "PATH": f"{venv}/bin:{FIXED_CHILD_PATH}"},
        )
        report = self.report(completed)
        self.assertTrue(report["base"], report)
        self.assertEqual(report["path"], FIXED_CHILD_PATH)
        self.assertEqual(report["env"]["AAS_RUNTIME_PYTHON_PREFIX"], str(venv))  # type: ignore[index]
        self.assertFalse(marker.exists(), "the hostile <venv>/bin/python3 was executed")

    def test_retained_keys_include_prefix_and_venv(self) -> None:
        venv = self.make_venv("venv")
        probe = self.root / "probe.py"
        probe.write_text(PROBE, encoding="utf-8")
        probe.chmod(0o700)
        completed = self.loader(
            command=[str(probe)],
            env={
                "AAS_RUNTIME_PYTHON_PREFIX": str(venv),
                "AAS_SKILL_VENV": str(venv),
                "PYTHONPATH": str(self.root / "leak"),
            },
        )
        report = self.report(completed)
        self.assertEqual(
            report["env"],
            {
                "AAS_RUNTIME_PYTHON_PREFIX": str(venv),
                "AAS_SKILL_VENV": str(venv),
                "PYTHONPATH": None,
                "HOME": str(self.home),
            },
        )


if __name__ == "__main__":
    unittest.main()
