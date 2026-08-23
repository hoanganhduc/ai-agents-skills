"""Regression for the getscipapers probe asking the wrong interpreter.

``zot doctor`` reported getscipapers as missing whenever PATH's ``python3`` was
not the interpreter running the skill. A venv install is invisible to a
PATH-resolved ``python3``, and Windows ships no ``python3`` at all, so the probe
raised FileNotFoundError, swallowed it, and printed "Install in workspace venv"
about a module that was already importable. The fix asks ``sys.executable``,
which is what the other 47 interpreter-spawning sites in this repo do.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path

from tests.test_never_matching_predicates import RUNTIME_SKILLS, ROOT, load_module


DOCTOR = RUNTIME_SKILLS / "zotero" / "lib" / "doctor.py"


class GetscipapersProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doctor = load_module("zot_doctor_probe", DOCTOR)

    def _probe_with(self, run_stub, which_stub):
        original_run = self.doctor.subprocess.run
        original_which = self.doctor.shutil.which
        self.doctor.subprocess.run = run_stub
        self.doctor.shutil.which = which_stub
        try:
            return self.doctor._check_getscipapers()
        finally:
            self.doctor.subprocess.run = original_run
            self.doctor.shutil.which = original_which

    def test_the_probe_spawns_the_running_interpreter(self) -> None:
        seen: list[list[str]] = []

        class Result:
            returncode = 0

        def fake_run(argv, **kwargs):
            seen.append(list(argv))
            return Result()

        report = self._probe_with(fake_run, lambda name: None)

        self.assertEqual(len(seen), 1, "the probe must run exactly once")
        self.assertEqual(
            seen[0][0],
            sys.executable,
            "the probe must ask the interpreter running the skill, not PATH",
        )
        self.assertEqual(seen[0][1:], ["-m", "getscipapers", "--help"])
        self.assertTrue(report["ok"])
        self.assertEqual(report["message"], "Available as python module")

    def test_a_missing_path_python3_no_longer_hides_an_installed_module(self) -> None:
        """The Windows shape: no python3 on PATH, module importable anyway."""

        class Result:
            returncode = 0

        def fake_run(argv, **kwargs):
            if argv[0] != sys.executable:
                raise FileNotFoundError(argv[0])
            return Result()

        report = self._probe_with(fake_run, lambda name: None)
        self.assertTrue(
            report["ok"],
            "an absent PATH python3 must not be reported as an absent module",
        )

    def test_a_genuinely_missing_module_is_still_reported(self) -> None:
        class Result:
            returncode = 1

        report = self._probe_with(lambda argv, **kw: Result(), lambda name: None)
        self.assertFalse(report["ok"])
        self.assertIn("Not found", report["message"])

    def test_an_executable_on_path_short_circuits_the_probe(self) -> None:
        def forbidden(argv, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("PATH hit must not spawn an interpreter")

        report = self._probe_with(forbidden, lambda name: "/usr/bin/getscipapers")
        self.assertTrue(report["ok"])
        self.assertEqual(report["message"], "Found in PATH")


class InterpreterSelectionInvariantTests(unittest.TestCase):
    """No subprocess in this repo may hardcode a `python` interpreter name."""

    SEARCH_ROOTS = (ROOT / "canonical", ROOT / "installer")
    LITERALS = {"python", "python3", "python2", "py"}
    SPAWNERS = {"run", "check_output", "call", "check_call", "Popen"}

    def _hardcoded_spawns(self) -> list[tuple[str, int, str]]:
        found: list[tuple[str, int, str]] = []
        for root in self.SEARCH_ROOTS:
            for path in sorted(root.rglob("*.py")):
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    if not (isinstance(func, ast.Attribute) and func.attr in self.SPAWNERS):
                        continue
                    if not (isinstance(func.value, ast.Name) and func.value.id == "subprocess"):
                        continue
                    if not node.args:
                        continue
                    argv = node.args[0]
                    if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
                        continue
                    head = argv.elts[0]
                    if isinstance(head, ast.Constant) and head.value in self.LITERALS:
                        found.append(
                            (str(path.relative_to(ROOT)), node.lineno, str(head.value))
                        )
        return found

    def test_the_scan_sees_subprocess_calls_at_all(self) -> None:
        """Anchor: the AST walk really reaches argv lists in this tree."""
        seen = 0
        for root in self.SEARCH_ROOTS:
            for path in sorted(root.rglob("*.py")):
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in self.SPAWNERS
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "subprocess"
                        and node.args
                        and isinstance(node.args[0], (ast.List, ast.Tuple))
                        and node.args[0].elts
                    ):
                        seen += 1
        self.assertGreater(seen, 20, "the scan found almost no argv lists; it is broken")

    def test_no_call_hardcodes_an_interpreter_name(self) -> None:
        found = self._hardcoded_spawns()
        self.assertEqual(
            found,
            [],
            "spawn sys.executable, not a PATH-resolved interpreter: "
            + ", ".join(f"{p}:{n} -> {lit!r}" for p, n, lit in found),
        )


if __name__ == "__main__":
    unittest.main()
