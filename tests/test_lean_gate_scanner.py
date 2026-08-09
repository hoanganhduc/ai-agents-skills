"""Hermetic evidence suite for the lean-strict-verification-gate scanner.

The gate's ``scan`` mode is pure-Python regex — no Lean toolchain, no
network — so every test here runs unconditionally on every host, including
CI runners that never install elan. Gating these on toolchain presence
would reproduce the silent-skip gap this file closes. Covers the pattern
tables (placeholders including sorryAx, trust-base including native_decide
and ofReduceBool, safety constructs), comment/string stripping, the import
allowlist, project-mode coverage and exclusions, the file cap, and the CLI
exit mapping for ``scan`` and ``verify --strict`` (strict's "typecheck
never ran is a failure" rule is pinned with a fake lean tool). The fake
tool is a stub script spawned from the test tempdir, so the one test that
executes it additionally assumes an exec-permitted temp directory
(standard on mainstream CI); no test here is ever skip-gated.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_PY = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "lean-strict-verification-gate"
    / "lean_strict_verification_gate.py"
)


def _load_gate() -> Any:
    spec = importlib.util.spec_from_file_location("lean_strict_verification_gate", GATE_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["lean_strict_verification_gate"] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def _scan_text(
    text: str,
    *,
    stage: str = "final_candidate",
    allow: set[str] | None = None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "Candidate.lean"
        target.write_text(text, encoding="utf-8")
        return gate.scan_path(target, stage, allow or set())


def _details(payload: dict[str, Any], kind: str) -> set[str]:
    return {f["detail"] for f in payload["findings"] if f["kind"] == kind}


class PlaceholderPatternTests(unittest.TestCase):
    def test_each_placeholder_blocks_a_final_candidate(self) -> None:
        cases = (
            ("theorem t : True := sorry\n", "sorry"),
            ("theorem t : True := by admit\n", "admit"),
            # `exact sorryAx _ _` slips past \bsorry\b; the dedicated pattern
            # must catch it on its own.
            ("theorem t : True := sorryAx _ _\n", "sorryAx"),
        )
        for text, name in cases:
            payload = _scan_text(text)
            self.assertFalse(payload["ok"], payload)
            self.assertEqual(payload["placeholder_status"], "active_placeholders_found")
            self.assertIn(name, _details(payload, "active_placeholder"), payload)

    def test_stub_stage_permits_placeholders_but_not_trust_base(self) -> None:
        stubbed = _scan_text("theorem t : True := sorry\n", stage="stub")
        self.assertTrue(stubbed["ok"], stubbed)
        self.assertEqual(stubbed["placeholder_status"], "placeholders_allowed_for_stub")
        self.assertEqual(stubbed["findings"], [])
        cheating_stub = _scan_text(
            "theorem t : 2 + 2 = 4 := by native_decide\n", stage="stub"
        )
        self.assertFalse(cheating_stub["ok"], cheating_stub)
        self.assertIn("native_decide", _details(cheating_stub, "trust_base_blocker"))


class TrustBasePatternTests(unittest.TestCase):
    def test_each_trust_base_expansion_is_a_blocker(self) -> None:
        cases = (
            ("theorem t : 2 + 2 = 4 := by native_decide\n", "native_decide"),
            ("theorem t : b = true := ofReduceBool b rfl\n", "ofReduceBool"),
            ("axiom convenient : False\n", "axiom"),
            ("unsafe def f : Nat := 0\n", "unsafe"),
        )
        for text, name in cases:
            payload = _scan_text(text)
            self.assertFalse(payload["ok"], payload)
            self.assertEqual(
                payload["trust_base_status"], "unsanctioned_axiom_or_unsafe", payload
            )
            self.assertIn(name, _details(payload, "trust_base_blocker"), payload)


class SafetyPatternTests(unittest.TestCase):
    def test_each_effectful_construct_fails_safety(self) -> None:
        cases = (
            ("#eval 1 + 1\n", "#eval"),
            ("def r := IO.Process.output {}\n", "IO.Process"),
            ("run_cmd doSomething\n", "run_cmd"),
            ("initialize registry : Unit ← pure ()\n", "initialize"),
            ('@[extern "c_fn"] def f : Nat := 0\n', "@[extern]"),
            # Both alternatives of the "foreign" pattern. Bare "@extern" at
            # the start of a line is the documented past regression: a shared
            # leading \b demanded a word character before "@" and never
            # matched it (and "[" keeps @[extern ...] out of this pattern, so
            # the bare form is the only test of the second alternative).
            ("foreign import g : Nat\n", "foreign"),
            ("@extern def f : Nat := 0\n", "foreign"),
        )
        for text, name in cases:
            payload = _scan_text(text)
            self.assertFalse(payload["ok"], payload)
            self.assertEqual(payload["safety_status"], "failed", payload)
            self.assertIn(name, _details(payload, "unsafe_construct"), payload)


class CommentStrippingTests(unittest.TestCase):
    def test_comments_and_string_literals_never_trigger(self) -> None:
        text = (
            "-- sorry admit native_decide\n"
            "/- block comment mentioning\n"
            "   #eval and IO.Process -/\n"
            'def msg : String := "sorry #eval"\n'
            "theorem fine : True := trivial\n"
        )
        payload = _scan_text(text)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["findings"], [])
        self.assertEqual(payload["placeholder_status"], "no_active_placeholders")
        self.assertEqual(payload["trust_base_status"], "accepted_trust_base")
        self.assertEqual(payload["safety_status"], "passed")


class ImportAllowlistTests(unittest.TestCase):
    def test_default_allowlist_admits_mathlib_std_init_only(self) -> None:
        allowed = _scan_text(
            "import Mathlib\n"
            "import Mathlib.Data.Nat.Basic\n"
            "import Std.Data.HashMap\n"
            "import Init\n"
            "theorem t : True := trivial\n"
        )
        self.assertTrue(allowed["ok"], allowed)
        flagged = _scan_text("import Paperproof\ntheorem t : True := trivial\n")
        self.assertFalse(flagged["ok"], flagged)
        self.assertEqual(flagged["safety_status"], "failed")
        self.assertIn("Paperproof", _details(flagged, "non_allowlisted_import"))

    def test_explicit_allowlist_replaces_the_default(self) -> None:
        allowed = _scan_text(
            "import MyLib\ntheorem t : True := trivial\n", allow={"MyLib"}
        )
        self.assertTrue(allowed["ok"], allowed)
        flagged = _scan_text(
            "import Mathlib\ntheorem t : True := trivial\n", allow={"MyLib"}
        )
        self.assertIn("Mathlib", _details(flagged, "non_allowlisted_import"))


class ProjectScanTests(unittest.TestCase):
    def test_project_scan_reports_per_file_coverage_and_excludes_build_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Good.lean").write_text(
                "theorem good : True := trivial\n", encoding="utf-8"
            )
            (root / "Sub").mkdir()
            (root / "Sub" / "Bad.lean").write_text(
                "theorem bad : True := sorry\n", encoding="utf-8"
            )
            for excluded in (".lake", "build"):
                (root / excluded).mkdir()
                (root / excluded / "Skip.lean").write_text(
                    "theorem cheat : 2 + 2 = 4 := by native_decide\n",
                    encoding="utf-8",
                )
            payload = gate.scan_project(root, "final_candidate", set())
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(payload["mode"], "project")
        bad_rel = str(Path("Sub") / "Bad.lean")
        coverage = payload["coverage"]
        self.assertEqual(coverage["files_total"], 2)
        self.assertEqual(coverage["files_scanned"], 2)
        self.assertEqual(
            {row["file"] for row in coverage["files"]}, {"Good.lean", bad_rel}
        )
        for row in coverage["files"]:
            self.assertEqual(len(row["sha256"]), 16, row)
        self.assertEqual({f["file"] for f in payload["findings"]}, {bad_rel})
        self.assertEqual(
            {f["kind"] for f in payload["findings"]}, {"active_placeholder"}
        )
        # The excluded dirs' native_decide must not leak into the verdict.
        self.assertEqual(payload["trust_base_status"], "accepted_trust_base")

    def test_empty_project_is_an_explicit_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = gate.scan_project(Path(tmp), "final_candidate", set())
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(
            [f["kind"] for f in payload["findings"]], ["empty_project"]
        )
        self.assertEqual(payload["coverage"]["files_total"], 0)

    def test_file_cap_refuses_a_partial_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("A.lean", "B.lean"):
                (root / name).write_text(
                    "theorem t : True := trivial\n", encoding="utf-8"
                )
            with mock.patch.object(gate, "PROJECT_MAX_FILES", 1):
                payload = gate.scan_project(root, "final_candidate", set())
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(
            [f["kind"] for f in payload["findings"]], ["too_many_files"]
        )
        self.assertEqual(payload["coverage"]["files_scanned"], 0)
        self.assertEqual(payload["coverage"]["files"], [])


def _fake_lean(root: Path, *, exit_code: int) -> Path:
    impl = root / "fake_lean_impl.py"
    impl.write_text(f"import sys\nsys.exit({exit_code})\n", encoding="utf-8")
    if os.name == "nt":
        # cmd.exe reads batch files in the OEM code page, not UTF-8;
        # switching to 65001 first keeps the embedded interpreter/impl
        # paths intact when either contains non-ASCII characters.
        wrapper = root / "lean.cmd"
        wrapper.write_text(
            "@echo off\r\nchcp 65001 >nul\r\n"
            f'"{sys.executable}" "{impl}" %*\r\nexit /b %ERRORLEVEL%\r\n',
            encoding="utf-8",
        )
        return wrapper
    wrapper = root / "lean"
    wrapper.write_text(
        f'#!/usr/bin/env sh\nexec "{sys.executable}" "{impl}" "$@"\n', encoding="utf-8"
    )
    wrapper.chmod(0o755)
    return wrapper


class CliExitMappingTests(unittest.TestCase):
    def _run_gate(
        self, *args: str, env_extra: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        for key in ("AAS_LEAN", "AAS_LAKE"):
            env.pop(key, None)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, "-B", str(GATE_PY), *args],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            check=False,
        )

    def test_scan_exit_maps_findings_to_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clean = Path(tmp) / "Clean.lean"
            clean.write_text("theorem t : True := trivial\n", encoding="utf-8")
            dirty = Path(tmp) / "Dirty.lean"
            dirty.write_text("theorem t : True := sorry\n", encoding="utf-8")
            ok = self._run_gate("scan", "--input", str(clean))
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            self.assertTrue(json.loads(ok.stdout)["ok"])
            bad = self._run_gate("scan", "--input", str(dirty))
            self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
            self.assertFalse(json.loads(bad.stdout)["ok"])

    def test_strict_treats_an_unrun_typecheck_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clean = Path(tmp) / "Clean.lean"
            clean.write_text("theorem t : True := trivial\n", encoding="utf-8")
            missing = {"AAS_LEAN": str(Path(tmp) / "missing-lean")}
            strict = self._run_gate(
                "verify", "--input", str(clean), "--strict", env_extra=missing
            )
            self.assertEqual(strict.returncode, 1, strict.stdout + strict.stderr)
            payload = json.loads(strict.stdout)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["strict"])
            self.assertEqual(payload["lean_check_status"], "tool_unavailable")
            # Without --strict the same unrun typecheck is not a failure.
            lax = self._run_gate(
                "verify", "--input", str(clean), "--typecheck", env_extra=missing
            )
            self.assertEqual(lax.returncode, 0, lax.stdout + lax.stderr)
            self.assertEqual(
                json.loads(lax.stdout)["lean_check_status"], "tool_unavailable"
            )

    def test_strict_exit_tracks_the_typecheck_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clean = Path(tmp) / "Clean.lean"
            clean.write_text("theorem t : True := trivial\n", encoding="utf-8")
            passing = {"AAS_LEAN": str(_fake_lean(Path(tmp), exit_code=0))}
            ok = self._run_gate(
                "verify", "--input", str(clean), "--strict", env_extra=passing
            )
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            self.assertEqual(
                json.loads(ok.stdout)["lean_check_status"], "typechecked"
            )
            failing_dir = Path(tmp) / "failing"
            failing_dir.mkdir()
            failing = {"AAS_LEAN": str(_fake_lean(failing_dir, exit_code=1))}
            bad = self._run_gate(
                "verify", "--input", str(clean), "--strict", env_extra=failing
            )
            self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
            self.assertEqual(
                json.loads(bad.stdout)["lean_check_status"], "typecheck_failed"
            )


if __name__ == "__main__":
    unittest.main()
