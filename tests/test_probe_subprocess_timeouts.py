"""Every fast capability probe must be bounded by a subprocess timeout.

A probe is a subprocess call that asks a tool to identify itself -- ``--version``,
``--help``, ``rev-parse``. It is expected to answer immediately, and nothing
downstream is designed to wait on it. Five of the seven such calls in the tree
already passed ``timeout=``; two did not:

* ``skills/docling/doctor.py`` ran ``[cli, "--help"]`` unbounded. A docling CLI
  that hangs took ``docling doctor`` with it -- the one command whose entire job
  is to report what is wrong printed nothing and never returned.
* ``skills/vnthuquan/vnthuquan_wrapper.py`` ran ``[*cmd, "--version"]`` unbounded
  inside ``package_version()``, which backs ``vnthuquan --version``, the
  ``vnthuquan_version`` field of the doctor payload, and the update check.

Both were reproduced by putting a shell script that never exits where the CLI
is looked up: neither command returned.

The invariant below is repo-wide rather than a pair of point fixes, so a new
unbounded probe fails here rather than in the field.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path

from tests.test_never_matching_predicates import ROOT, RUNTIME_SKILLS, load_module


SEARCH_ROOTS = (ROOT / "canonical", ROOT / "installer")
SUBPROCESS_CALLS = {"run", "check_output", "call", "check_call"}
# Argv shapes that only ever ask a tool to identify itself.
PROBE_ARGV = re.compile(r"--version|--help|-dumpversion|\brev-parse\b")


def _probe_calls() -> list[tuple[str, int, bool]]:
    """Every probe-shaped subprocess call, with whether it passes timeout=."""

    found: list[tuple[str, int, bool]] = []
    for root in SEARCH_ROOTS:
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute):
                    continue
                if not isinstance(func.value, ast.Name) or func.value.id != "subprocess":
                    continue
                if func.attr not in SUBPROCESS_CALLS or not node.args:
                    continue
                if not PROBE_ARGV.search(ast.unparse(node.args[0])):
                    continue
                bounded = any(keyword.arg == "timeout" for keyword in node.keywords)
                found.append((str(path.relative_to(ROOT)), node.lineno, bounded))
    return found


class ProbeTimeoutInvariantTests(unittest.TestCase):
    def test_the_scan_finds_probes_at_all(self):
        """Anchors the invariant: a zero result would pass it vacuously."""

        self.assertGreaterEqual(len(_probe_calls()), 5)

    def test_every_probe_is_bounded(self):
        unbounded = [
            f"{path}:{line}" for path, line, bounded in _probe_calls() if not bounded
        ]
        self.assertEqual(unbounded, [])


class DoclingDoctorTests(unittest.TestCase):
    def test_a_hung_cli_is_reported_not_propagated(self):
        doctor = load_module(
            "docling_doctor",
            RUNTIME_SKILLS / "docling" / "doctor.py",
            extra_syspath=RUNTIME_SKILLS / "docling",
        )
        expired = subprocess.TimeoutExpired(cmd=["docling", "--help"], timeout=10)
        captured: dict = {}

        def fake_run(*args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            raise expired

        original = doctor.subprocess.run
        original_which = doctor.shutil.which
        doctor.subprocess.run = fake_run
        doctor.shutil.which = lambda name: "/usr/bin/docling" if name == "docling" else None
        try:
            import contextlib
            import io
            import json

            buffer = io.StringIO()
            argv = sys.argv
            sys.argv = ["doctor"]
            try:
                with contextlib.redirect_stdout(buffer):
                    code = doctor.main()
            finally:
                sys.argv = argv
            payload = json.loads(buffer.getvalue())
        finally:
            doctor.subprocess.run = original
            doctor.shutil.which = original_which

        self.assertEqual(code, 0)
        self.assertIsNotNone(captured["timeout"], "the probe must pass a timeout")
        self.assertIn("timed out", payload.get("docling_cli_error", ""))
        self.assertFalse(payload["docling_cli"], "a timed-out probe is not a working CLI")


class VnthuquanVersionTests(unittest.TestCase):
    def test_a_hung_cli_reports_an_unknown_version(self):
        wrapper = load_module(
            "vnthuquan_wrapper_timeout",
            RUNTIME_SKILLS / "vnthuquan" / "vnthuquan_wrapper.py",
            extra_syspath=RUNTIME_SKILLS / "vnthuquan",
        )
        captured: dict = {}

        def fake_run(*args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            raise subprocess.TimeoutExpired(cmd=["vnthuquan", "--version"], timeout=10)

        original = wrapper.subprocess.run
        original_resolve = wrapper.resolve_vnthuquan
        wrapper.subprocess.run = fake_run
        wrapper.resolve_vnthuquan = lambda: (["vnthuquan"], "vnthuquan", sys.executable)
        try:
            self.assertIsNone(wrapper.package_version())
        finally:
            wrapper.subprocess.run = original
            wrapper.resolve_vnthuquan = original_resolve

        self.assertIsNotNone(captured["timeout"], "the probe must pass a timeout")


if __name__ == "__main__":
    unittest.main()
