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
import hashlib
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
            ("theorem t : n = 0 := ofReduceNat n rfl\n", "ofReduceNat"),
            ("theorem t : True := Lean.trustCompiler\n", "trustCompiler"),
            ("#check Lean.reduceBool\n", "reduceBool"),
            ("#check Lean.reduceNat\n", "reduceNat"),
            ("axiom convenient : False\n", "axiom"),
            ("@[deprecated] private axiom modified : False\n", "axiom"),
            ("unsafe def f : Nat := 0\n", "unsafe"),
        )
        for text, name in cases:
            payload = _scan_text(text)
            self.assertFalse(payload["ok"], payload)
            self.assertEqual(
                payload["trust_base_status"], "unsanctioned_axiom_or_unsafe", payload
            )
            self.assertIn(name, _details(payload, "trust_base_blocker"), payload)

    def test_every_native_decide_spelling_expands_the_trust_base(self) -> None:
        cases = (
            "theorem t : True := by native_decide\n",
            "theorem t : True := by decide +native\n",
            "theorem t : True := by\n  decide\n    +native\n",
            "theorem t : True := by decide (native := true)\n",
        )
        for text in cases:
            with self.subTest(text=text):
                payload = _scan_text(text)
                self.assertFalse(payload["ok"], payload)
                self.assertEqual(
                    payload["trust_base_status"],
                    "unsanctioned_axiom_or_unsafe",
                    payload,
                )


class SafetyPatternTests(unittest.TestCase):
    def test_each_effectful_construct_fails_safety(self) -> None:
        cases = (
            ("#eval 1 + 1\n", "#eval"),
            ("#exit\ntheorem unchecked : True := True.intro\n", "#exit"),
            ("#check_failure (True.intro : False)\n", "#check_failure"),
            ("#guard_msgs in #check (True.intro : False)\n", "#guard_msgs"),
            ("def r := IO.Process.output {}\n", "IO.Process"),
            ("run_cmd doSomething\n", "run_cmd"),
            ("theorem t : True := by run_tac doSomething\n", "run_tac"),
            ('elab "#effect" : command => do IO.println "effect"\n', "elab"),
            ("elab_rules : term | _ => do pure default\n", "elab_rules"),
            ("def x : Nat := by_elab do pure default\n", "by_elab"),
            (
                "@[\ncommand_elab custom\n] def effectful := fun _ => do pure ()\n",
                "elaborator_attribute",
            ),
            (
                "attribute [local term_elab custom] effectful\n",
                "elaborator_attribute",
            ),
            ("initialize registry : Unit ← pure ()\n", "initialize"),
            ("builtin_initialize registry : Unit ← pure ()\n", "initialize"),
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

    def test_comment_delimiters_inside_strings_cannot_hide_active_code(self) -> None:
        payload = _scan_text(
            'def openMarker : String := "/-"\n'
            "unsafe def escaped : Nat := 0\n"
            'def closeMarker : String := "-/"\n'
        )
        self.assertFalse(payload["ok"], payload)
        self.assertIn("unsafe", _details(payload, "trust_base_blocker"))

    def test_character_literals_cannot_turn_active_code_into_string_text(self) -> None:
        payload = _scan_text(
            "def leftQuote : Char := '\"'\n"
            "unsafe def hidden : Nat := 0\n"
            "def rightQuote : Char := '\"'\n"
        )
        self.assertFalse(payload["ok"], payload)
        self.assertIn("unsafe", _details(payload, "trust_base_blocker"))

    def test_nested_block_comments_stay_inert(self) -> None:
        payload = _scan_text(
            "/- outer /- nested -/ unsafe def fake : Nat := 0 -/\n"
            "theorem real : True := trivial\n"
        )
        self.assertTrue(payload["ok"], payload)

    def test_terms_inside_interpolated_strings_remain_active(self) -> None:
        cases = (
            ('def message := s!"value {by run_tac exact True.intro}"\n', "run_tac"),
            ('def message := m!"value {by native_decide}"\n', "native_decide"),
        )
        for source, expected in cases:
            with self.subTest(expected=expected):
                payload = _scan_text(source)
            self.assertFalse(payload["ok"], payload)
            kinds = {item["detail"] for item in payload["findings"]}
            self.assertIn(expected, kinds, payload)

        inert = _scan_text('def message := "literal run_tac native_decide"\n')
        self.assertTrue(inert["ok"], inert)


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

    def test_every_module_on_one_import_line_is_checked(self) -> None:
        flagged = _scan_text(
            "import Mathlib Unreviewed.Module\ntheorem t : True := trivial\n"
        )
        self.assertFalse(flagged["ok"], flagged)
        self.assertIn(
            "Unreviewed.Module",
            _details(flagged, "non_allowlisted_import"),
        )

    def test_module_system_meta_imports_cannot_bypass_the_allowlist(self) -> None:
        for directive in (
            "meta import Unreviewed.Module",
            "public meta import Unreviewed.Module",
            "import all Unreviewed.Module",
        ):
            with self.subTest(directive=directive):
                payload = _scan_text(
                    f"module\n{directive}\ntheorem t : True := trivial\n"
                )
                self.assertFalse(payload["ok"], payload)
                self.assertIn(
                    "Unreviewed.Module",
                    _details(payload, "non_allowlisted_import"),
                )


class ProjectScanTests(unittest.TestCase):
    def test_project_scan_reports_per_file_coverage_and_excludes_metadata_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Good.lean").write_text(
                "theorem good : True := trivial\n", encoding="utf-8"
            )
            (root / "Sub").mkdir()
            (root / "Sub" / "Bad.lean").write_text(
                "theorem bad : True := sorry\n", encoding="utf-8"
            )
            for excluded in (".lake", ".git"):
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
            self.assertEqual(len(row["sha256"]), 64, row)
        self.assertEqual({f["file"] for f in payload["findings"]}, {bad_rel})
        self.assertEqual(
            {f["kind"] for f in payload["findings"]}, {"active_placeholder"}
        )
        # The excluded dirs' native_decide must not leak into the verdict.
        self.assertEqual(payload["trust_base_status"], "accepted_trust_base")

    def test_a_directory_named_build_is_scanned_as_project_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "build" / "Hidden.lean"
            source.parent.mkdir()
            source.write_text(
                "axiom hidden : False\n",
                encoding="utf-8",
            )
            payload = gate.scan_project(root, "final_candidate", set())
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(
            {row["file"] for row in payload["coverage"]["files"]},
            {str(Path("build") / "Hidden.lean")},
        )
        self.assertIn("axiom", _details(payload, "trust_base_blocker"))

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

    def test_tree_entry_cap_refuses_unbounded_non_source_fanout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "noise-a.txt").write_text("a", encoding="utf-8")
            (root / "noise-b.txt").write_text("b", encoding="utf-8")
            with mock.patch.object(gate, "TREE_MAX_ENTRIES", 1):
                payload = gate.scan_project(root, "final_candidate", set())
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(
            [finding["kind"] for finding in payload["findings"]],
            ["too_many_tree_entries"],
        )

    def test_coverage_hash_is_of_the_bytes_the_scanner_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "Candidate.lean"
            clean = b"theorem clean : True := trivial\n"
            target.write_bytes(clean)
            original_scan = gate.scan_path

            def scan_then_replace(path, stage, allowed):
                result = original_scan(path, stage, allowed)
                path.write_text("unsafe def changed : Nat := 0\n", encoding="utf-8")
                return result

            with mock.patch.object(gate, "scan_path", side_effect=scan_then_replace):
                payload = gate.scan_project(root, "final_candidate", set())
        self.assertEqual(
            payload["coverage"]["files"][0]["sha256"],
            hashlib.sha256(clean).hexdigest(),
        )

    def test_unreadable_project_file_is_not_counted_as_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Invalid.lean").write_bytes(b"\xff\xfe")
            payload = gate.scan_project(root, "final_candidate", set())
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(payload["coverage"]["files_total"], 1)
        self.assertEqual(payload["coverage"]["files_scanned"], 0)
        self.assertEqual(payload["coverage"]["files"][0]["sha256"], "")

    def test_source_byte_limit_refuses_unbounded_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "Huge.lean"
            target.write_bytes(b"x" * 9)
            with mock.patch.object(gate, "SOURCE_MAX_BYTES", 8):
                payload = gate.scan_path(target, "final_candidate", set())
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(payload["findings"][0]["kind"], "input_too_large")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_project_scan_refuses_symlinked_lean_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside.txt"
            outside.write_text("theorem external : True := trivial\n", encoding="utf-8")
            linked = root / "Linked.lean"
            linked.symlink_to(outside)
            payload = gate.scan_project(root, "final_candidate", set())
        self.assertFalse(payload["ok"], payload)
        self.assertIn("symlink_input", {item["kind"] for item in payload["findings"]})
        self.assertEqual(payload["coverage"]["files_scanned"], 0)

    def test_project_scan_never_silently_skips_an_unreadable_subtree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Visible.lean").write_text(
                "theorem visible : True := trivial\n", encoding="utf-8"
            )
            blocked = root / "blocked"
            blocked.mkdir()
            (blocked / "Hidden.lean").write_text(
                "unsafe def hidden : Nat := 0\n", encoding="utf-8"
            )
            original_scandir = os.scandir

            def refuse(path):
                if Path(path) == blocked:
                    raise PermissionError("blocked fixture")
                return original_scandir(path)

            with mock.patch.object(gate.os, "scandir", side_effect=refuse):
                payload = gate.scan_project(root, "final_candidate", set())
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(payload["coverage"]["files_scanned"], 0)
        self.assertIn(
            "project_traversal_error",
            {item["kind"] for item in payload["findings"]},
        )


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


class BoundedCommandTests(unittest.TestCase):
    def test_timeout_terminates_and_reaps_the_child(self) -> None:
        command = [
            sys.executable,
            "-c",
            "import os, time; print(os.getpid(), flush=True); time.sleep(30)",
        ]
        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            gate.run_bounded_command(
                command,
                timeout=0.1,
                max_output_bytes=1024,
            )
        pid = int((raised.exception.output or b"").strip())
        if Path("/proc").is_dir():
            self.assertFalse(Path("/proc", str(pid)).exists())

    def test_relative_tool_candidate_is_bound_to_an_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            tool = Path(tmp) / "tool"
            tool.write_text("tool", encoding="utf-8")
            candidate = os.path.relpath(tool, Path.cwd())
            resolved = gate.resolve_candidate(candidate)
        self.assertTrue(Path(resolved).is_absolute(), resolved)
        self.assertEqual(Path(resolved), tool.resolve())


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
            encoding="utf-8",
            errors="replace",
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

    def test_lake_build_requires_post_build_source_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            source = root / "Proj" / "Basic.lean"
            source.parent.mkdir(parents=True)
            (root / "lakefile.toml").write_text('name = "project"\n', encoding="utf-8")
            source.write_text("theorem t : True := True.intro\n", encoding="utf-8")
            tools = base / "tools"
            tools.mkdir()
            lake = _fake_lean(tools, exit_code=0)
            with mock.patch.dict(os.environ, {"AAS_LAKE": str(lake)}):
                missing = gate.typecheck_lake_build(timeout=10, project_root=root)
                artifact = (
                    root
                    / ".lake"
                    / "build"
                    / "lib"
                    / "lean"
                    / "Proj"
                    / "Basic.olean"
                )
                artifact.parent.mkdir(parents=True)
                artifact.write_bytes(b"compiled")
                complete = gate.typecheck_lake_build(timeout=10, project_root=root)
        self.assertEqual(missing["lean_check_status"], "command_failed", missing)
        self.assertEqual(missing["typecheck_modules_unbuilt"], ["Proj.Basic"])
        self.assertEqual(complete["lean_check_status"], "typechecked", complete)
        self.assertEqual(complete["typecheck_coverage_status"], "complete")

    def test_verification_refuses_input_changed_by_the_typecheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "Candidate.lean"
            target.write_text("theorem clean : True := trivial\n", encoding="utf-8")

            def mutate(*_args, **_kwargs):
                target.write_text("unsafe def changed : Nat := 0\n", encoding="utf-8")
                return {
                    "lean_check_status": "typechecked",
                    "runner": "direct-lean",
                    "typecheck_command": "lean <input>",
                    "typecheck_cwd": "",
                    "typecheck_stdout": "",
                    "typecheck_stderr": "",
                }

            with mock.patch.object(gate, "typecheck", side_effect=mutate), mock.patch.object(
                gate, "emit"
            ) as emit:
                rc = gate.main(["verify", "--input", str(target), "--strict"])
            payload = emit.call_args.args[0]
        self.assertEqual(rc, 1)
        self.assertFalse(payload["ok"], payload)
        self.assertIn(
            "input_changed_during_verification",
            {item["kind"] for item in payload["findings"]},
        )

    def test_verification_binds_the_lake_configuration_around_typecheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lakefile.toml").write_text('name = "proof"\n', encoding="utf-8")
            target = root / "Candidate.lean"
            target.write_text("theorem clean : True := trivial\n", encoding="utf-8")

            def mutate(*_args, **_kwargs):
                (root / "lean-toolchain").write_text(
                    "leanprover/lean4:v4.33.1\n", encoding="utf-8"
                )
                return {
                    "lean_check_status": "typechecked",
                    "runner": "lake-env-lean",
                    "typecheck_command": "lake env lean <input>",
                    "typecheck_cwd": str(root),
                    "typecheck_stdout": "",
                    "typecheck_stderr": "",
                }

            with mock.patch.object(gate, "typecheck", side_effect=mutate), mock.patch.object(
                gate, "emit"
            ) as emit:
                rc = gate.main(
                    [
                        "verify",
                        "--input",
                        str(target),
                        "--strict",
                        "--runner",
                        "lake-env-lean",
                        "--project-root",
                        str(root),
                    ]
                )
            payload = emit.call_args.args[0]
        self.assertEqual(rc, 1)
        self.assertFalse(payload["ok"], payload)
        self.assertIn(
            "project_context_changed_during_verification",
            {item["kind"] for item in payload["findings"]},
        )

    def test_timeouts_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "Candidate.lean"
            target.write_text("theorem clean : True := trivial\n", encoding="utf-8")
            for command in (
                ["verify", "--input", str(target), "--timeout", "0"],
                ["axiom-audit", "--input", str(tmp), "--timeout", "-1"],
                ["kernel-check", "--input", str(tmp), "--timeout", "0"],
            ):
                with self.subTest(command=command), self.assertRaises(SystemExit):
                    gate.main(command)

    def test_lake_build_cannot_certify_a_file_or_a_different_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            (first / "lakefile.toml").write_text('name = "first"\n', encoding="utf-8")
            (second / "lakefile.toml").write_text('name = "second"\n', encoding="utf-8")
            target = first / "Candidate.lean"
            target.write_text("theorem clean : True := trivial\n", encoding="utf-8")
            cases = (
                [
                    "verify",
                    "--input",
                    str(target),
                    "--strict",
                    "--runner",
                    "lake-build",
                    "--project-root",
                    str(first),
                ],
                [
                    "verify",
                    "--input",
                    str(first),
                    "--strict",
                    "--project-root",
                    str(second),
                ],
            )
            for command in cases:
                with self.subTest(command=command), mock.patch.object(
                    gate, "typecheck"
                ) as typecheck, mock.patch.object(gate, "emit") as emit:
                    rc = gate.main(command)
                payload = emit.call_args.args[0]
                self.assertEqual(rc, 1)
                self.assertFalse(payload["ok"], payload)
                self.assertIn(
                    "runner_input_mismatch",
                    {item["kind"] for item in payload["findings"]},
                )
                typecheck.assert_not_called()


def _fake_tool(root: Path, name: str, body: str) -> Path:
    """A stub executable named ``name`` whose behaviour is the given Python body."""
    impl = root / f"fake_{name}_impl.py"
    impl.write_text(body, encoding="utf-8")
    if os.name == "nt":
        wrapper = root / f"{name}.cmd"
        wrapper.write_text(
            "@echo off\r\nchcp 65001 >nul\r\n"
            f'"{sys.executable}" "{impl}" %*\r\nexit /b %ERRORLEVEL%\r\n',
            encoding="utf-8",
        )
        return wrapper
    wrapper = root / name
    wrapper.write_text(
        f'#!/usr/bin/env sh\nexec "{sys.executable}" "{impl}" "$@"\n', encoding="utf-8"
    )
    wrapper.chmod(0o755)
    return wrapper


# `lake env lean <harness>` stand-in: echoes one #print axioms line per requested
# declaration, reporting the axioms named in FAKE_AXIOMS and skipping any
# declaration listed in FAKE_UNKNOWN (which also makes the process fail, as a
# real unknown identifier would).
_FAKE_LAKE_AXIOMS = """
import os
import sys

harness = sys.argv[-1]
requested = [
    line.split(None, 2)[2].strip()
    for line in open(harness, encoding="utf-8")
    if line.startswith("#print axioms ")
]
unknown = set(filter(None, os.environ.get("FAKE_UNKNOWN", "").split(",")))
axioms = os.environ.get("FAKE_AXIOMS", "propext, Classical.choice, Quot.sound")
for declaration in requested:
    if declaration in unknown:
        continue
    print(f"'{declaration}' depends on axioms: [{axioms}]")
if unknown & set(requested):
    print("error: unknown identifier", file=sys.stderr)
    sys.exit(1)
"""

# `lake env <tool> <args...>` stand-in: runs the tool it was handed.
_FAKE_LAKE_ENV = """
import subprocess
import sys

args = sys.argv[1:]
assert args and args[0] == "env", args
sys.exit(subprocess.run(args[1:], check=False).returncode)
"""


class AxiomAuditTests(unittest.TestCase):
    """The audit's parsing and verdicts, plus its CLI exit mapping."""

    def test_declaration_names_qualify_by_namespace_not_section(self) -> None:
        source = (
            "namespace Foo.Bar\n"
            "section Helpers\n"
            "private theorem alpha : True := trivial\n"
            "@[simp] lemma beta (n : Nat) : n = n := rfl\n"
            "end Helpers\n"
            "theorem gamma : True := trivial\n"
            "end Foo.Bar\n"
            "-- theorem commented_out : True := trivial\n"
            "theorem top : True := trivial\n"
        )
        self.assertEqual(
            gate.scan_declarations(source),
            (["Foo.Bar.beta", "Foo.Bar.gamma", "top"], ["Foo.Bar.alpha"], []),
        )

    def test_unicode_names_remain_valid_harness_identifiers(self) -> None:
        source = (
            "namespace Θεωρία\n"
            "theorem αλήθεια : True := trivial\n"
            "end Θεωρία\n"
        )
        self.assertEqual(
            gate.scan_declarations(source),
            (["Θεωρία.αλήθεια"], [], []),
        )
        self.assertTrue(
            gate.valid_lean_name("Θεωρία.αλήθεια", allow_root_prefix=True)
        )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_declaration_walk_refuses_a_symlinked_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside.txt"
            outside.write_text("theorem escaped : True := trivial\n", encoding="utf-8")
            linked = root / "Linked.lean"
            linked.symlink_to(outside)
            result = gate.project_declaration_scan(root)
        self.assertEqual(result.names, [])
        self.assertEqual(result.private, [])
        self.assertTrue(
            any("symlink_input" in detail for detail in result.unparsed),
            result,
        )

    def test_a_mutual_block_does_not_close_the_enclosing_namespace(self) -> None:
        """`end` closing a `mutual` must not pop the namespace above it.

        The walk pops a scope on every `end`, so a block opener it does not
        push for silently unqualifies every later declaration in the file —
        names the built environment then rejects as `unresolved`.
        """
        source = (
            "namespace Foo\n"
            "mutual\n"
            "theorem alpha : True := trivial\n"
            "theorem beta : True := trivial\n"
            "end\n"
            "theorem gamma : True := trivial\n"
            "end Foo\n"
        )
        self.assertEqual(
            gate.declaration_names(source),
            ["Foo.alpha", "Foo.beta", "Foo.gamma"],
        )

    def test_multiple_declarations_on_one_line_are_refused_as_partial_coverage(self) -> None:
        scan = gate.scan_declarations(
            "theorem first : True := True.intro theorem second : True := True.intro\n"
        )
        self.assertEqual(scan.names, ["first"])
        self.assertEqual(len(scan.unparsed), 1)
        self.assertIn("theorem second", scan.unparsed[0])

    def test_compound_scope_lines_are_refused_before_they_can_corrupt_names(self) -> None:
        scan = gate.scan_declarations(
            "namespace Imported end\ntheorem actual : True := True.intro\n"
        )
        self.assertEqual(scan.names, ["actual"])
        self.assertEqual(scan.unparsed, ["namespace Imported end"])

    def test_a_noncomputable_section_does_not_close_the_enclosing_namespace(self) -> None:
        """The modifier form of a section opener still has to push a scope.

        `noncomputable section` is the common one in real Lean sources, and
        matching bare `section` alone let its `end` pop the namespace above
        it: `Foo.gamma` would then be audited as `gamma`, which either fails
        to resolve or resolves to an entirely different declaration.
        """
        source = (
            "namespace Foo\n"
            "noncomputable section\n"
            "theorem alpha : True := trivial\n"
            "end\n"
            "theorem gamma : True := trivial\n"
            "end Foo\n"
        )
        self.assertEqual(gate.declaration_names(source), ["Foo.alpha", "Foo.gamma"])

    def test_modifiers_and_an_open_prefix_do_not_hide_a_declaration(self) -> None:
        """A declaration the walk misses is never audited at all."""
        source = (
            "nonrec theorem alpha : True := trivial\n"
            "private nonrec theorem beta : True := trivial\n"
            "noncomputable private theorem beta2 : True := trivial\n"
            "open Nat in theorem gamma : True := trivial\n"
            "open Nat in\n"
            "@[simp] theorem delta : True := trivial\n"
        )
        self.assertEqual(
            gate.scan_declarations(source),
            (["alpha", "gamma", "delta"], ["beta", "beta2"], []),
        )

    def test_any_command_prefix_on_the_declaration_line_stays_visible(self) -> None:
        """`open ... in` is one of several commands that can share the line.

        Matching only `open` hid `set_option`, `attribute`, `variable` and
        `notation` prefixes from the walk, and a declaration the walk cannot
        see is never asked about — the audit reports a clean trust base over a
        theorem it skipped.
        """
        source = (
            "set_option maxHeartbeats 400000 in theorem alpha : True := trivial\n"
            "attribute [simp] Nat.add_zero in theorem beta : True := trivial\n"
            "variable (n : Nat) in theorem gamma : True := trivial\n"
            'local notation "srt" => 1 in theorem delta : True := trivial\n'
            "set_option pp.all true in open Nat in @[simp] theorem eps : True := trivial\n"
        )
        self.assertEqual(
            gate.scan_declarations(source),
            (["alpha", "beta", "gamma", "delta", "eps"], [], []),
        )

    def test_an_in_prefix_does_not_swallow_an_ordinary_declaration(self) -> None:
        """The widened prefix must not eat a statement that contains ` in `."""
        source = (
            "theorem in_bounds : True := trivial\n"
            "theorem mem_all : forall x in S, True := trivial\n"
            "  exact Foo.lemma h\n"
            "  have h := my_theorem x\n"
        )
        self.assertEqual(
            gate.scan_declarations(source),
            (["in_bounds", "mem_all"], [], []),
        )

    def test_a_declaration_line_without_a_readable_name_is_reported(self) -> None:
        """Silent non-coverage is the one failure the audit cannot survive.

        A line the walk cannot read a name off may have hidden a theorem, so it
        comes back as `unparsed` for the caller to refuse on, rather than
        vanishing into a report that looks complete.
        """
        source = "theorem\n    split_over_two_lines : True := trivial\n"
        self.assertEqual(gate.scan_declarations(source), ([], [], ["theorem"]))

    def test_an_axiom_after_a_same_line_command_prefix_is_a_trust_base_hit(self) -> None:
        """`set_option ... in axiom` never reaches the start of a line."""
        payload = _scan_text("set_option pp.all true in axiom evil : False\n")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["trust_base_status"], "unsanctioned_axiom_or_unsafe")
        self.assertIn(
            {"kind": "trust_base_blocker", "detail": "axiom"}, payload["findings"]
        )

    def test_private_theorems_are_separated_rather_than_audited(self) -> None:
        """Lean mangles a private name, so an importing harness cannot ask.

        Auditing them anyway makes every project with a private lemma fail on
        `unresolved`, which is a refusal caused by Lean's naming rather than by
        anything wrong with the proof.
        """
        source = (
            "namespace Foo\n"
            "private theorem helper : True := trivial\n"
            "theorem public_result : True := trivial\n"
            "end Foo\n"
        )
        self.assertEqual(
            gate.scan_declarations(source),
            (["Foo.public_result"], ["Foo.helper"], []),
        )
        self.assertEqual(gate.declaration_names(source), ["Foo.public_result"])

    def test_definitions_stay_out_of_scope(self) -> None:
        """Definitions surface through the theorems that use them."""
        source = (
            "def f : Nat := 0\n"
            "abbrev g : Nat := 0\n"
            "instance i : Inhabited Nat := ⟨0⟩\n"
            "example : True := trivial\n"
            "theorem alpha : True := trivial\n"
        )
        self.assertEqual(gate.declaration_names(source), ["alpha"])

    def test_axiom_report_parses_wrapped_lines_and_empty_dependencies(self) -> None:
        text = (
            "'Foo.alpha' depends on axioms: [propext,\n"
            " Classical.choice, Quot.sound]\n"
            "'Foo.beta' does not depend on any axioms\n"
            "'Foo.gamma' depends on axioms: [sorryAx]\n"
        )
        self.assertEqual(
            gate.parse_axiom_report(text),
            {
                "Foo.alpha": ["propext", "Classical.choice", "Quot.sound"],
                "Foo.beta": [],
                "Foo.gamma": ["sorryAx"],
            },
        )

    def test_project_modules_skip_the_lakefile_and_build_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
            (root / "Proj").mkdir()
            (root / "Proj" / "Basic.lean").write_text("theorem t : True := trivial\n", encoding="utf-8")
            (root / ".lake" / "build").mkdir(parents=True)
            (root / ".lake" / "build" / "Stale.lean").write_text("theorem s : True := trivial\n", encoding="utf-8")
            self.assertEqual(gate.project_modules(root), ["Proj.Basic"])
            self.assertEqual(gate.project_declarations(root), ["t"])

    def test_only_modules_lake_built_are_importable(self) -> None:
        """A staged copy under the root is a file, not a module.

        The loop stages proof artifacts inside the project directory, so the
        walk finds `loop/proof_artifacts/Copy.lean` next to the real source.
        Naming it in the harness aborts the whole audit with `unknown module
        prefix`, so the built set decides and the copy is reported as skipped.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lakefile.toml").write_text('name = "proj"\n', encoding="utf-8")
            (root / "Proj").mkdir()
            (root / "Proj" / "Basic.lean").write_text(
                "theorem real : True := trivial\n", encoding="utf-8"
            )
            staged = root / "loop" / "proof_artifacts"
            staged.mkdir(parents=True)
            (staged / "Copy.lean").write_text(
                "theorem staged_only : True := trivial\n", encoding="utf-8"
            )
            built = root / ".lake" / "build" / "lib" / "lean" / "Proj"
            built.mkdir(parents=True)
            (built / "Basic.olean").write_bytes(b"")

            present, missing = gate.built_project_modules(root)
            self.assertEqual(present, ["Proj.Basic"])
            self.assertEqual(missing, ["loop.proof_artifacts.Copy"])
            # The staged declaration is not in the compiled environment, so
            # asking about it would only produce a spurious `unresolved`.
            self.assertEqual(gate.project_declarations(root, present), ["real"])
            self.assertEqual(
                gate.project_declarations(root), ["real", "staged_only"]
            )

    def test_module_order_does_not_depend_on_the_host_filesystem(self) -> None:
        """`Zeta` sorts before `alpha` by byte, after it when case-folded.

        Sorting `Path` objects picks the second order on Windows and the first
        everywhere else, so the same project reported two different
        declaration orders depending on which runner audited it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lakefile.toml").write_text('name = "proj"\n', encoding="utf-8")
            for stem in ("Zeta", "alpha"):
                (root / f"{stem}.lean").write_text(
                    f"theorem {stem.lower()}_thm : True := trivial\n", encoding="utf-8"
                )
            self.assertEqual(gate.project_modules(root), ["Zeta", "alpha"])
            self.assertEqual(gate.project_declarations(root), ["zeta_thm", "alpha_thm"])

    def test_an_unbuilt_project_reports_that_rather_than_no_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lakefile.toml").write_text('name = "proj"\n', encoding="utf-8")
            (root / "Proj.lean").write_text("theorem t : True := trivial\n", encoding="utf-8")
            self.assertEqual(gate.built_project_modules(root), ([], ["Proj"]))


class AuditCliTests(unittest.TestCase):
    """CLI exit mapping for axiom-audit and kernel-check, with stubbed tools."""

    def _project(self, tmp: str) -> Path:
        root = Path(tmp) / "proj"
        (root / "Proj").mkdir(parents=True)
        (root / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
        (root / "Proj" / "Basic.lean").write_text(
            "theorem good : True := trivial\ntheorem bad : True := trivial\n",
            encoding="utf-8",
        )
        # Both verbs consume compiled modules, so the fixture has to look like a
        # project Lake already built.
        olean = root / ".lake" / "build" / "lib" / "lean" / "Proj"
        olean.mkdir(parents=True)
        (olean / "Basic.olean").write_bytes(b"")
        return root

    @staticmethod
    def _mark_basic_built(root: Path) -> None:
        source = root / "Proj" / "Basic.lean"
        artifact = root / ".lake" / "build" / "lib" / "lean" / "Proj" / "Basic.olean"
        source_mtime = source.stat().st_mtime_ns
        os.utime(artifact, ns=(source_mtime + 1, source_mtime + 1))

    def _run_gate(
        self, *args: str, env_extra: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        for key in ("AAS_LEAN", "AAS_LAKE", "AAS_LEAN4CHECKER"):
            env.pop(key, None)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, "-B", str(GATE_PY), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=env,
            check=False,
        )

    def test_a_missing_lake_is_unavailable_lax_but_fails_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            missing = {"AAS_LAKE": str(Path(tmp) / "missing-lake")}
            lax = self._run_gate("axiom-audit", "--input", str(root), env_extra=missing)
            self.assertEqual(lax.returncode, 0, lax.stdout + lax.stderr)
            payload = json.loads(lax.stdout)
            self.assertEqual(payload["axiom_audit_status"], "tool_unavailable")
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["declarations_requested"], 2)
            strict = self._run_gate(
                "axiom-audit", "--input", str(root), "--strict", env_extra=missing
            )
            self.assertEqual(strict.returncode, 1, strict.stdout + strict.stderr)

    def test_the_sanctioned_trio_audits_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_AXIOMS)
            result = self._run_gate(
                "axiom-audit", "--input", str(root), "--strict",
                env_extra={"AAS_LAKE": str(lake)},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["axiom_audit_status"], "audited")
            self.assertEqual(payload["unsanctioned_axioms"], [])
            self.assertEqual(
                sorted(row["declaration"] for row in payload["declarations"]),
                ["bad", "good"],
            )

    def test_sorry_ax_fails_and_cannot_be_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_AXIOMS)
            dirty = {"AAS_LAKE": str(lake), "FAKE_AXIOMS": "propext, sorryAx"}
            result = self._run_gate("axiom-audit", "--input", str(root), env_extra=dirty)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["axiom_audit_status"], "audited")
            self.assertEqual(payload["unsanctioned_axioms"], ["sorryAx"])
            # An operator allowlist widens the sanctioned set but never to sorryAx.
            allowed = self._run_gate(
                "axiom-audit", "--input", str(root), "--allow-axiom", "sorryAx",
                env_extra=dirty,
            )
            self.assertEqual(allowed.returncode, 1, allowed.stdout + allowed.stderr)
            self.assertNotIn("sorryAx", json.loads(allowed.stdout)["sanctioned_axioms"])

    def test_an_operator_allowlist_admits_a_named_axiom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_AXIOMS)
            env_extra = {"AAS_LAKE": str(lake), "FAKE_AXIOMS": "propext, Nat.Custom"}
            blocked = self._run_gate("axiom-audit", "--input", str(root), env_extra=env_extra)
            self.assertEqual(blocked.returncode, 1, blocked.stdout + blocked.stderr)
            allowed = self._run_gate(
                "axiom-audit", "--input", str(root), "--allow-axiom", "Nat.Custom",
                "--strict", env_extra=env_extra,
            )
            self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)

    def test_an_allowlisted_compiler_trust_axiom_still_reads_as_compiler_trust(self) -> None:
        """`native_decide` may be accepted, but never as a kernel-checked proof.

        Both axioms mark a complete proof, so unlike `sorryAx` they stay
        allowlistable; what must not happen is a payload that a caller reads as
        an ordinary clean trust base.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_AXIOMS)
            env_extra = {
                "AAS_LAKE": str(lake),
                "FAKE_AXIOMS": (
                    "propext, Lean.ofReduceBool, Lean.ofReduceNat, Lean.trustCompiler"
                ),
            }
            blocked = self._run_gate("axiom-audit", "--input", str(root), env_extra=env_extra)
            self.assertEqual(blocked.returncode, 1, blocked.stdout + blocked.stderr)
            self.assertEqual(
                json.loads(blocked.stdout)["unsanctioned_axioms"],
                ["Lean.ofReduceBool", "Lean.ofReduceNat", "Lean.trustCompiler"],
            )
            allowed = self._run_gate(
                "axiom-audit", "--input", str(root), "--strict",
                "--allow-axiom", "Lean.ofReduceBool",
                "--allow-axiom", "Lean.ofReduceNat",
                "--allow-axiom", "Lean.trustCompiler",
                env_extra=env_extra,
            )
            self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
            payload = json.loads(allowed.stdout)
            self.assertEqual(payload["unsanctioned_axioms"], [])
            self.assertEqual(
                payload["compiler_trust_axioms"],
                ["Lean.ofReduceBool", "Lean.ofReduceNat", "Lean.trustCompiler"],
            )
            self.assertEqual(
                sorted({row["status"] for row in payload["declarations"]}),
                ["sanctioned_compiler_trust"],
            )
            self.assertTrue(
                any("native evaluation" in line for line in payload["limitations"]),
                payload["limitations"],
            )

    def test_modern_declaration_local_native_axioms_remain_compiler_trust(self) -> None:
        native_axiom = "good._native.decide.ax_1_1"
        self.assertTrue(gate.is_compiler_trust_axiom(native_axiom))
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_AXIOMS)
            env_extra = {"AAS_LAKE": str(lake), "FAKE_AXIOMS": native_axiom}
            blocked = self._run_gate(
                "axiom-audit", "--input", str(root), env_extra=env_extra
            )
            self.assertEqual(blocked.returncode, 1, blocked.stdout + blocked.stderr)
            blocked_payload = json.loads(blocked.stdout)
            self.assertEqual(blocked_payload["compiler_trust_axioms"], [native_axiom])

            allowed = self._run_gate(
                "axiom-audit",
                "--input",
                str(root),
                "--strict",
                "--allow-axiom",
                native_axiom,
                env_extra=env_extra,
            )
            self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
            payload = json.loads(allowed.stdout)
            self.assertEqual(payload["compiler_trust_axioms"], [native_axiom])
            self.assertEqual(
                {row["status"] for row in payload["declarations"]},
                {"sanctioned_compiler_trust"},
            )

    def test_a_private_theorem_is_reported_as_skipped_not_unresolved(self) -> None:
        """Verified against Lean v4.24.0: `#print axioms` cannot name one.

        Asking anyway fails the whole audit on `unresolved`, so the audit names
        what it could not ask about and says why in its limitations.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            (root / "Proj" / "Basic.lean").write_text(
                "theorem good : True := trivial\n"
                "private theorem helper : True := trivial\n",
                encoding="utf-8",
            )
            self._mark_basic_built(root)
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_AXIOMS)
            result = self._run_gate(
                "axiom-audit", "--input", str(root), "--strict",
                env_extra={"AAS_LAKE": str(lake)},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                [row["declaration"] for row in payload["declarations"]], ["good"]
            )
            self.assertEqual(payload["declarations_skipped_private"], ["helper"])
            self.assertTrue(
                any("private theorems" in line for line in payload["limitations"]),
                payload["limitations"],
            )

    def test_a_declaration_line_the_walk_cannot_read_refuses_the_audit(self) -> None:
        """Partial coverage must never be reported as a clean trust base.

        The walk is a regex, so a declaration written in a shape it does not
        recognize would otherwise be skipped in silence and the audit would
        pass on the theorems it happened to see.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            (root / "Proj" / "Basic.lean").write_text(
                "theorem good : True := trivial\n"
                "theorem\n"
                "    wrapped_name : True := trivial\n",
                encoding="utf-8",
            )
            self._mark_basic_built(root)
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_AXIOMS)
            result = self._run_gate(
                "axiom-audit", "--input", str(root), "--strict",
                env_extra={"AAS_LAKE": str(lake)},
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["declarations_unparsed"], ["Proj.Basic: theorem"])
            self.assertIn(
                {"kind": "declaration_unparsed", "detail": "Proj.Basic: theorem"},
                payload["findings"],
            )

    def test_a_clean_audit_reports_no_compiler_trust(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_AXIOMS)
            result = self._run_gate(
                "axiom-audit", "--input", str(root), "--strict",
                env_extra={"AAS_LAKE": str(lake)},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["compiler_trust_axioms"], [])

    def test_a_declaration_the_audit_cannot_resolve_is_never_silently_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_AXIOMS)
            result = self._run_gate(
                "axiom-audit", "--input", str(root), "--declaration", "good",
                "--declaration", "Ghost.missing",
                env_extra={"AAS_LAKE": str(lake), "FAKE_UNKNOWN": "Ghost.missing"},
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(
                {row["declaration"]: row["status"] for row in payload["declarations"]},
                {"good": "sanctioned", "Ghost.missing": "unresolved"},
            )

    def test_an_audit_that_printed_nothing_is_a_command_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            silent = _fake_tool(Path(tmp), "lake", "import sys\nsys.exit(1)\n")
            result = self._run_gate(
                "axiom-audit", "--input", str(root), env_extra={"AAS_LAKE": str(silent)}
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["axiom_audit_status"], "command_failed")
            self.assertFalse(payload["ok"])

    def test_a_staged_artifact_does_not_abort_the_audit(self) -> None:
        """The ARL stages `.lean` copies inside the project it is auditing."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            staged = root / "loop_t1" / "proof_artifacts"
            staged.mkdir(parents=True)
            (staged / "Basic_final.lean").write_text(
                "theorem staged_only : True := trivial\n", encoding="utf-8"
            )
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_AXIOMS)
            result = self._run_gate(
                "axiom-audit", "--input", str(root), "--strict",
                env_extra={"AAS_LAKE": str(lake)},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["axiom_audit_status"], "audited")
            self.assertEqual(
                payload["modules_skipped_unbuilt"],
                ["loop_t1.proof_artifacts.Basic_final"],
            )
            self.assertEqual(
                sorted(row["declaration"] for row in payload["declarations"]),
                ["bad", "good"],
            )

    def test_an_unbuilt_project_is_refused_by_both_verbs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "unbuilt"
            root.mkdir()
            (root / "lakefile.toml").write_text('name = "proj"\n', encoding="utf-8")
            (root / "Proj.lean").write_text("theorem t : True := trivial\n", encoding="utf-8")
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_AXIOMS)
            for verb in ("axiom-audit", "kernel-check"):
                result = self._run_gate(
                    verb, "--input", str(root), env_extra={"AAS_LAKE": str(lake)}
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                payload = json.loads(result.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(
                    [f["kind"] for f in payload["findings"]], ["project_not_built"]
                )

    def test_a_project_without_a_lakefile_is_refused_by_both_verbs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "bare"
            root.mkdir()
            (root / "Solo.lean").write_text("theorem t : True := trivial\n", encoding="utf-8")
            for verb in ("axiom-audit", "kernel-check"):
                result = self._run_gate(verb, "--input", str(root))
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                payload = json.loads(result.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(
                    [f["kind"] for f in payload["findings"]], ["missing_lakefile"]
                )

    def test_a_missing_lean4checker_is_unavailable_lax_but_fails_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_ENV)
            missing = {
                "AAS_LAKE": str(lake),
                "AAS_LEAN4CHECKER": str(Path(tmp) / "missing-checker"),
            }
            lax = self._run_gate("kernel-check", "--input", str(root), env_extra=missing)
            self.assertEqual(lax.returncode, 0, lax.stdout + lax.stderr)
            payload = json.loads(lax.stdout)
            self.assertEqual(payload["kernel_check_status"], "tool_unavailable")
            self.assertTrue(payload["ok"])
            self.assertTrue(
                any("lean4checker" in note for note in payload["limitations"]),
                payload["limitations"],
            )
            strict = self._run_gate(
                "kernel-check", "--input", str(root), "--strict", env_extra=missing
            )
            self.assertEqual(strict.returncode, 1, strict.stdout + strict.stderr)

    def test_project_local_lean4checker_is_never_selected_implicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            self._mark_basic_built(root)
            checker_dir = root / ".lake" / "build" / "bin"
            checker_dir.mkdir(parents=True)
            checker = _fake_tool(
                checker_dir, "lean4checker", "import sys\nsys.exit(0)\n"
            )
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_ENV)
            with mock.patch.dict(os.environ, {"AAS_LAKE": str(lake)}), mock.patch.object(
                gate.shutil, "which", return_value=None
            ):
                os.environ.pop("AAS_LEAN4CHECKER", None)
                payload = gate.kernel_check_payload(
                    root,
                    project_root=root,
                    timeout=10,
                    modules=[],
                    strict=True,
                )
            self.assertTrue(payload["ok"], payload)
            self.assertEqual(payload["kernel_check_status"], "tool_unavailable")
            self.assertEqual(
                payload["tool_status"]["lean4checker"],
                {"status": "tool_unavailable", "path": "", "source": "not-found"},
            )

            with mock.patch.dict(
                os.environ,
                {"AAS_LAKE": str(lake), "AAS_LEAN4CHECKER": str(checker)},
            ):
                selected = gate.kernel_check_payload(
                    root,
                    project_root=root,
                    timeout=10,
                    modules=[],
                    strict=True,
                )
            self.assertTrue(selected["ok"], selected)
            self.assertEqual(selected["kernel_check_status"], "kernel_checked")
            self.assertTrue(
                selected["tool_status"]["lean4checker"]["inside_project_root"]
            )
            self.assertTrue(
                any("project-controlled" in note for note in selected["limitations"]),
                selected,
            )

    def test_kernel_check_reports_each_module_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_ENV)
            passing = _fake_tool(Path(tmp), "lean4checker", "import sys\nsys.exit(0)\n")
            ok = self._run_gate(
                "kernel-check", "--input", str(root), "--strict",
                env_extra={"AAS_LAKE": str(lake), "AAS_LEAN4CHECKER": str(passing)},
            )
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            payload = json.loads(ok.stdout)
            self.assertEqual(payload["kernel_check_status"], "kernel_checked")
            self.assertEqual(
                payload["modules"], [{"module": "Proj.Basic", "status": "kernel_checked"}]
            )
            failing_dir = Path(tmp) / "failing"
            failing_dir.mkdir()
            rejecting = _fake_tool(failing_dir, "lean4checker", "import sys\nsys.exit(1)\n")
            bad = self._run_gate(
                "kernel-check", "--input", str(root),
                env_extra={"AAS_LAKE": str(lake), "AAS_LEAN4CHECKER": str(rejecting)},
            )
            self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
            self.assertEqual(
                json.loads(bad.stdout)["kernel_check_status"], "kernel_check_failed"
            )

    def test_harness_identifiers_are_validated_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            cases = (
                {
                    "declarations": ["good\n#eval 1 + 1"],
                    "imports": ["Proj.Basic"],
                    "finding": "invalid_declaration",
                },
                {
                    "declarations": ["good"],
                    "imports": ["Proj.Basic\n#eval 1 + 1"],
                    "finding": "invalid_import",
                },
            )
            for case in cases:
                with self.subTest(case=case), mock.patch.object(
                    gate, "run_bounded_command"
                ) as run:
                    payload = gate.axiom_audit_payload(
                        root,
                        project_root=root,
                        timeout=10,
                        declarations=case["declarations"],
                        imports=case["imports"],
                        allowed_axioms=set(),
                        strict=True,
                    )
                self.assertFalse(payload["ok"], payload)
                self.assertIn(
                    case["finding"],
                    {item["kind"] for item in payload["findings"]},
                )
                run.assert_not_called()

    def test_kernel_module_options_are_validated_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            with mock.patch.object(gate, "run_bounded_command") as run:
                payload = gate.kernel_check_payload(
                    root,
                    project_root=root,
                    timeout=10,
                    modules=["--help"],
                    strict=True,
                )
            self.assertFalse(payload["ok"], payload)
            self.assertIn(
                "invalid_module",
                {item["kind"] for item in payload["findings"]},
            )
            run.assert_not_called()

    def test_axiom_harness_module_count_is_bounded_before_io(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            imports = [f"Module{i}" for i in range(gate.AXIOM_AUDIT_MAX_MODULES + 1)]
            with mock.patch.object(gate, "project_evidence_snapshot") as snapshot, mock.patch.object(
                gate, "run_bounded_command"
            ) as run:
                payload = gate.axiom_audit_payload(
                    root,
                    project_root=root,
                    timeout=10,
                    declarations=["good"],
                    imports=imports,
                    allowed_axioms=set(),
                    strict=True,
                )
            self.assertFalse(payload["ok"], payload)
            self.assertIn(
                "too_many_modules",
                {item["kind"] for item in payload["findings"]},
            )
            snapshot.assert_not_called()
            run.assert_not_called()

    def test_compiled_evidence_byte_limit_fails_before_checker_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            artifact = root / ".lake" / "build" / "lib" / "lean" / "Proj" / "Basic.olean"
            artifact.write_bytes(b"too large")
            with mock.patch.object(gate, "COMPILED_MODULE_MAX_BYTES", 4), mock.patch.object(
                gate, "run_bounded_command"
            ) as run:
                payload = gate.kernel_check_payload(
                    root,
                    project_root=root,
                    timeout=10,
                    modules=[],
                    strict=True,
                )
            self.assertFalse(payload["ok"], payload)
            self.assertIn(
                "evidence_unreadable",
                {item["kind"] for item in payload["findings"]},
            )
            run.assert_not_called()

    def test_evidence_snapshot_hashes_without_materializing_artifact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            with mock.patch.object(gate, "stable_regular_file_bytes") as materialize:
                snapshot = gate.project_evidence_snapshot(root, ["Proj.Basic"])
        materialize.assert_not_called()
        self.assertEqual(snapshot["errors"], [])
        self.assertEqual(len(snapshot["modules"][0]["compiled_sha256"]), 64)

    def test_explicit_local_compiled_module_requires_its_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            (root / "Proj" / "Basic.lean").unlink()
            cases = (
                lambda: gate.axiom_audit_payload(
                    root,
                    project_root=root,
                    timeout=10,
                    declarations=["good"],
                    imports=["Proj.Basic"],
                    allowed_axioms=set(),
                    strict=True,
                ),
                lambda: gate.kernel_check_payload(
                    root,
                    project_root=root,
                    timeout=10,
                    modules=["Proj.Basic"],
                    strict=True,
                ),
            )
            for invoke in cases:
                with self.subTest(invoke=invoke), mock.patch.object(
                    gate, "run_bounded_command"
                ) as run:
                    payload = invoke()
                self.assertFalse(payload["ok"], payload)
                self.assertIn(
                    "evidence_unreadable",
                    {item["kind"] for item in payload["findings"]},
                )
                run.assert_not_called()

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_compiled_evidence_refuses_a_symlinked_lake_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            root.mkdir()
            (root / "lakefile.lean").write_text("package P\n", encoding="utf-8")
            outside = base / "outside-lake"
            artifact = outside / "build" / "lib" / "lean" / "Proj" / "Basic.olean"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"compiled outside the project")
            try:
                (root / ".lake").symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"cannot create directory symlink: {exc}")

            with self.assertRaises(gate.StableReadError) as raised:
                gate.built_module_artifacts(root)

        self.assertEqual(raised.exception.kind, "symlink_directory")

    def test_evidence_verbs_reject_a_different_reported_input_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            other = Path(tmp) / "other"
            other.mkdir()
            cases = (
                lambda: gate.axiom_audit_payload(
                    other,
                    project_root=root,
                    timeout=10,
                    declarations=[],
                    imports=[],
                    allowed_axioms=set(),
                    strict=True,
                ),
                lambda: gate.kernel_check_payload(
                    other,
                    project_root=root,
                    timeout=10,
                    modules=[],
                    strict=True,
                ),
            )
            for invoke in cases:
                with mock.patch.object(gate, "run_bounded_command") as run:
                    payload = invoke()
                self.assertFalse(payload["ok"], payload)
                self.assertIn(
                    "input_project_mismatch",
                    {item["kind"] for item in payload["findings"]},
                )
                run.assert_not_called()

    def test_unreadable_built_source_is_a_coverage_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            (root / "Unreadable.lean").write_bytes(b"\xff\xfe")
            (root / ".lake" / "build" / "lib" / "lean" / "Unreadable.olean").write_bytes(b"")
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_AXIOMS)
            result = self._run_gate(
                "axiom-audit",
                "--input",
                str(root),
                "--strict",
                env_extra={"AAS_LAKE": str(lake)},
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"], payload)
            self.assertIn(
                "source_unreadable",
                {item["kind"] for item in payload["findings"]},
            )

    def test_nonzero_audit_command_cannot_pass_with_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            noisy_failure = _fake_tool(
                Path(tmp),
                "lake",
                _FAKE_LAKE_AXIOMS + "\nsys.exit(1)\n",
            )
            result = self._run_gate(
                "axiom-audit",
                "--input",
                str(root),
                "--strict",
                env_extra={"AAS_LAKE": str(noisy_failure)},
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"], payload)
            self.assertEqual(payload["axiom_audit_status"], "command_failed")

    def test_audit_output_limit_cannot_pass_with_a_partial_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            noisy = _fake_tool(
                Path(tmp),
                "lake",
                "import sys\n"
                "print(\"'good' depends on axioms: [propext]\")\n"
                "sys.stdout.write('x' * 4096)\n",
            )
            with mock.patch.dict(os.environ, {"AAS_LAKE": str(noisy)}), mock.patch.object(
                gate,
                "COMMAND_OUTPUT_MAX_BYTES",
                512,
            ):
                payload = gate.axiom_audit_payload(
                    root,
                    project_root=root,
                    timeout=10,
                    declarations=["good"],
                    imports=["Proj.Basic"],
                    allowed_axioms=set(),
                    strict=True,
                )

        self.assertFalse(payload["ok"], payload)
        self.assertEqual(payload["axiom_audit_status"], "command_failed")
        self.assertEqual(payload["declarations"], [])
        self.assertIn(
            "audit_output_limit",
            {item["kind"] for item in payload["findings"]},
        )

    def test_source_newer_than_compiled_module_refuses_both_evidence_verbs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            source = root / "Proj" / "Basic.lean"
            artifact = root / ".lake" / "build" / "lib" / "lean" / "Proj" / "Basic.olean"
            artifact_mtime = artifact.stat().st_mtime_ns
            os.utime(source, ns=(artifact_mtime + 1_000_000, artifact_mtime + 1_000_000))
            lake = _fake_tool(Path(tmp), "lake", _FAKE_LAKE_AXIOMS)
            checker = _fake_tool(
                Path(tmp), "lean4checker", "import sys\nsys.exit(0)\n"
            )
            env_extra = {
                "AAS_LAKE": str(lake),
                "AAS_LEAN4CHECKER": str(checker),
            }
            for verb in ("axiom-audit", "kernel-check"):
                with self.subTest(verb=verb):
                    result = self._run_gate(
                        verb,
                        "--input",
                        str(root),
                        "--strict",
                        env_extra=env_extra,
                    )
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertIn(
                        "stale_compiled_module",
                        {item["kind"] for item in payload["findings"]},
                    )

    def test_explicit_local_modules_cannot_bypass_the_stale_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            source = root / "Proj" / "Basic.lean"
            artifact = root / ".lake" / "build" / "lib" / "lean" / "Proj" / "Basic.olean"
            artifact_mtime = artifact.stat().st_mtime_ns
            os.utime(source, ns=(artifact_mtime + 1_000_000, artifact_mtime + 1_000_000))
            cases = (
                lambda: gate.axiom_audit_payload(
                    root,
                    project_root=root,
                    timeout=10,
                    declarations=["good"],
                    imports=["Proj.Basic"],
                    allowed_axioms=set(),
                    strict=True,
                ),
                lambda: gate.kernel_check_payload(
                    root,
                    project_root=root,
                    timeout=10,
                    modules=["Proj.Basic"],
                    strict=True,
                ),
            )
            for invoke in cases:
                with self.subTest(invoke=invoke), mock.patch.object(
                    gate, "run_bounded_command"
                ) as run:
                    payload = invoke()
                self.assertFalse(payload["ok"], payload)
                self.assertIn(
                    "stale_compiled_module",
                    {item["kind"] for item in payload["findings"]},
                )
                run.assert_not_called()

    def test_evidence_verbs_bind_compiled_bytes_around_the_command(self) -> None:
        mutation_body = (
            "from pathlib import Path\n"
            "artifact = Path('.lake/build/lib/lean/Proj/Basic.olean')\n"
            "artifact.write_bytes(b'changed during evidence command')\n"
            "print(\"'good' depends on axioms: [propext, Classical.choice, Quot.sound]\")\n"
            "print(\"'bad' depends on axioms: [propext, Classical.choice, Quot.sound]\")\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tool_dir = Path(tmp) / "tools"
            tool_dir.mkdir()
            lake = _fake_tool(tool_dir, "lake", mutation_body)
            checker = _fake_tool(
                tool_dir, "lean4checker", "import sys\nsys.exit(0)\n"
            )
            for verb, finding in (
                ("axiom-audit", "evidence_changed_during_audit"),
                ("kernel-check", "evidence_changed_during_kernel_check"),
            ):
                with self.subTest(verb=verb):
                    root = self._project(str(Path(tmp) / verb))
                    result = self._run_gate(
                        verb,
                        "--input",
                        str(root),
                        "--strict",
                        env_extra={
                            "AAS_LAKE": str(lake),
                            "AAS_LEAN4CHECKER": str(checker),
                        },
                    )
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertIn(
                        finding,
                        {item["kind"] for item in payload["findings"]},
                    )


if __name__ == "__main__":
    unittest.main()
