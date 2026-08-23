"""Evidence suite for the v0 formal loop: host-authored terminal states.

Covers the M1 verification core (default gate runner, terminal-state
evaluation, early-stop guards, the `formal-terminal-state` CLI verb) and the
M2 survivability wiring (bounded review waits, supervisor passthrough,
campaign budgets, notify preflight), plus a real-Lean end-to-end check that
runs only when the pinned toolchain is installed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.dont_write_bytecode = True
# Subprocesses spawned by these tests (runtime CLI, bootstrap init) must not
# write __pycache__ into the canonical tree; the source-inventory test forbids it.
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "autonomous-research-loop-runtime"
)
RUNTIME_PY = RUNTIME_DIR / "autonomous_research_loop_runtime.py"
FORCE_LOOP = RUNTIME_DIR / "force-loop"
SUPERVISOR_SH = RUNTIME_DIR / "arl_drive_supervisor.sh"
sys.path.insert(0, str(RUNTIME_DIR))

import formal_policy as fp  # noqa: E402
import autonomous_research_loop_runtime as rt  # noqa: E402

LEAN_TOOLCHAIN = "leanprover/lean4:v4.32.2"

# A deterministic fake gate: findings iff the project contains "sorry".
STUB_GATE = """\
import hashlib
import json
import sys
from pathlib import Path

mode = sys.argv[1]
proj = Path(sys.argv[sys.argv.index("--input") + 1])
files = sorted(proj.rglob("*.lean"))
findings = []
for lean_file in files:
    if "sorry" in lean_file.read_text(encoding="utf-8"):
        findings.append(
            {"file": lean_file.name, "kind": "active_placeholder", "detail": "sorry"}
        )
coverage = {
    "files_total": len(files),
    "files_scanned": len(files),
    "files": [
        {
            "file": lean_file.name,
            "sha256": hashlib.sha256(lean_file.read_bytes()).hexdigest()[:16],
            "ok": True,
        }
        for lean_file in files
    ],
}
report = {"ok": not findings, "findings": findings, "coverage": coverage}
if mode == "axiom-audit":
    axioms = ["sorryAx"] if findings else ["propext", "Classical.choice", "Quot.sound"]
    report = {
        "ok": not findings,
        "axiom_audit_status": "audited",
        "declarations": [
            {
                "declaration": "t",
                "axioms": axioms,
                "status": "unsanctioned_axiom" if findings else "sanctioned",
            }
        ],
        "unsanctioned_axioms": ["sorryAx"] if findings else [],
        "findings": [],
    }
elif mode == "verify":
    report["lean_check_status"] = (
        "typechecked" if not findings else "refused_active_placeholders"
    )
print(json.dumps(report))
sys.exit(0 if not findings else 1)
"""


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _clean_env(**overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "AAS_STRICT_GATE_SCRIPT",
        "AAS_AUTOLOOP_FORMAL_POLICY",
        "AAS_AUTOLOOP_FORMAL_PROJECT",
        "AAS_AUTOLOOP_FORMAL_TYPECHECK",
        "AAS_AUTOLOOP_FORMAL_TYPECHECK_TIMEOUT",
        "AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS",
        "AAS_AUTOLOOP_HOST_MEDIATED_SUBMISSION",
    ):
        env.pop(key, None)
    # Never let inherited host notify secrets post real messages from tests.
    env["AAS_AUTOLOOP_NOTIFY"] = "off"
    env.update(overrides)
    return env


def _run_runtime(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNTIME_PY), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env or _clean_env(),
    )


def _init_loop(run_dir: Path, *, formal: bool = False, project: str | None = None) -> None:
    args = [
        "init",
        "--dir",
        str(run_dir),
        "--goal",
        "formalize demo theorem",
        "--success-criteria",
        "terminal_state sorry_free_artifact",
        "--max-iterations",
        "5",
        "--goal-focus-mode",
        "off",
    ]
    if formal:
        args.extend(["--formal-policy", "on"])
        if project:
            args.extend(["--formal-project", project])
    proc = _run_runtime(*args)
    assert proc.returncode == 0, proc.stderr or proc.stdout


def _set_formal_track(run_dir: Path) -> None:
    path = run_dir / "loop_state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["next_preferred_path"] = "formal-track: build the Lean artifact"
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _write_proof_artifact(run_dir: Path, evidence_id: str = "proof-artifact-1") -> None:
    proof_file = run_dir / "proofs" / "proof.txt"
    proof_file.parent.mkdir(parents=True, exist_ok=True)
    proof_file.write_text("machine-checkable proof fixture\n", encoding="utf-8")
    artifact_dir = run_dir / "proof_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / f"{evidence_id}.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": evidence_id,
                "artifact_type": "python-verifier",
                "machine_checkable": True,
                "target": "test theorem",
                "proof_path": "proofs/proof.txt",
                "checker": {"name": "fixture-checker", "status": "passed"},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_terminal_file(run_dir: Path, terminal_state: str) -> None:
    formal_dir = run_dir / "formal"
    formal_dir.mkdir(parents=True, exist_ok=True)
    (formal_dir / "terminal_state.json").write_text(
        json.dumps(
            {
                "schema_version": fp.TERMINAL_STATE_SCHEMA,
                "writer": fp.HOST_WRITER,
                "terminal_state": terminal_state,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _append_stop(
    run_dir: Path,
    *,
    stop_reason: str,
    evidence_id: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [
        "append-iteration",
        "--dir",
        str(run_dir),
        "--mode",
        "bounded-research",
        "--objective",
        "terminal decision",
        "--decision",
        "stop",
        "--stop-reason",
        stop_reason,
        "--source-id",
        "S1",
        "--guard-ref",
        "G1",
        "--remaining-gap",
        "none",
    ]
    if evidence_id:
        args.extend(["--evidence-id", evidence_id])
    return _run_runtime(*args, env=env)


def _policy(**overrides: Any) -> fp.FormalPolicy:
    values: dict[str, Any] = {
        "policy": "on",
        "project": "formal/",
        "force_credits": 3,
        "allow_path_steal": False,
        "typecheck": True,
        "force_after_iteration": False,
    }
    values.update(overrides)
    return fp.FormalPolicy(**values)


def _make_project(tmp: Path, lean_body: str) -> Path:
    """A minimal Lake project layout (resolve_formal_project needs a lakefile)."""
    proj = tmp / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "lakefile.toml").write_text(
        'name = "demo"\ndefaultTargets = ["Demo"]\n\n[[lean_lib]]\nname = "Demo"\n',
        encoding="utf-8",
    )
    (proj / "Demo.lean").write_text(lean_body, encoding="utf-8")
    return proj


def _runner_for(
    scan: dict[str, Any],
    verify: dict[str, Any] | None = None,
    audit: dict[str, Any] | None = None,
):
    def runner(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "lean_strict_verification_gate.scan":
            return scan
        if name == "lean_strict_verification_gate.verify_typecheck":
            return verify or {"ok": False, "status": "not_run", "report": {}}
        if name == "lean_strict_verification_gate.axiom_audit":
            return audit if audit is not None else AUDIT_CLEAN
        return {"ok": False, "status": "forbidden_skill", "report": {}}

    return runner


def _audit(status: str, **report_extra: Any) -> dict[str, Any]:
    report = {
        "ok": status == "audited" and not report_extra.get("unsanctioned_axioms"),
        "axiom_audit_status": status,
        "declarations": [],
        "unsanctioned_axioms": [],
    }
    report.update(report_extra)
    return {"ok": report["ok"], "status": status, "report": report}


AUDIT_CLEAN = _audit(
    "audited",
    declarations=[{"declaration": "t", "axioms": ["propext"], "status": "sanctioned"}],
)
AUDIT_SORRY_AX = _audit(
    "audited",
    declarations=[{"declaration": "t", "axioms": ["sorryAx"], "status": "unsanctioned_axiom"}],
    unsanctioned_axioms=["sorryAx"],
)
AUDIT_UNRESOLVED = _audit(
    "audited",
    ok=False,
    declarations=[{"declaration": "Ghost.t", "axioms": [], "status": "unresolved"}],
)
# The audit walked the sources, could not read a name off one declaration line,
# and audited the rest: status "audited" over an unknown fraction of the project.
AUDIT_UNPARSED = _audit(
    "audited",
    ok=False,
    declarations=[{"declaration": "t", "axioms": ["propext"], "status": "sanctioned"}],
    declarations_unparsed=["Demo: theorem"],
)
# Refused, and none of the fields the summary models say why.
AUDIT_REFUSED = _audit(
    "audited",
    ok=False,
    declarations=[{"declaration": "t", "axioms": ["propext"], "status": "sanctioned"}],
)
AUDIT_UNAVAILABLE = _audit("tool_unavailable")


def _scan(*files: tuple[str, str], findings: list[dict[str, Any]] | None = None):
    """A gate scan result carrying the per-file manifest a re-scan is diffed against."""
    found = findings or []
    return {
        "ok": not found,
        "status": "ok" if not found else "failed",
        "report": {
            "ok": not found,
            "findings": found,
            "coverage": {
                "files_total": len(files),
                "files_scanned": len(files),
                "files": [
                    {"file": name, "sha256": digest, "ok": True}
                    for name, digest in files
                ],
            },
        },
    }


CLEAN_SCAN = _scan(("Demo.lean", "aaaaaaaaaaaaaaaa"))
SORRY_SCAN = _scan(
    ("Demo.lean", "aaaaaaaaaaaaaaaa"),
    ("Demo/Bad.lean", "bbbbbbbbbbbbbbbb"),
    findings=[
        {"file": "Demo/Bad.lean", "kind": "active_placeholder", "detail": "sorry"}
    ],
)
TYPECHECK_OK = {
    "ok": True,
    "status": "typechecked",
    "report": {"lean_check_status": "typechecked"},
}
TYPECHECK_FAIL = {
    "ok": False,
    "status": "typecheck_failed",
    "report": {"lean_check_status": "typecheck_failed"},
}


class GateRunnerTests(unittest.TestCase):
    def test_missing_gate_script_is_unavailable_never_clean(self) -> None:
        with mock.patch.dict(os.environ, {fp.GATE_SCRIPT_ENV: "/nonexistent/gate.py"}):
            result = fp.default_gate_runner(
                "lean_strict_verification_gate.scan", {"project": "/tmp"}
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "tool_unavailable")
        self.assertEqual(result["report"], {})

    def test_unknown_skill_is_forbidden(self) -> None:
        result = fp.default_gate_runner("shutil.rmtree", {"project": "/tmp"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "forbidden_skill")

    def test_typecheck_timeout_is_clamped(self) -> None:
        cases = {"": 600.0, "banana": 600.0, "10": 60.0, "999999": 3600.0, "900": 900.0}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                with mock.patch.dict(os.environ, {fp.TYPECHECK_TIMEOUT_ENV: raw}):
                    self.assertEqual(fp.typecheck_timeout_s(), expected)


class EvaluateTerminalStateTests(unittest.TestCase):
    def _project(self, tmp: Path) -> Path:
        return _make_project(tmp, "theorem t : True := trivial\n")

    def test_clean_scan_and_host_build_yield_sorry_free_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir()
            proj = self._project(Path(tmp))
            verdict = fp.evaluate_formal_terminal_state(
                run_dir,
                root=Path(tmp),
                policy=_policy(project=str(proj)),
                runner=_runner_for(CLEAN_SCAN, TYPECHECK_OK),
                reason="unit",
            )
            self.assertEqual(verdict["terminal_state"], "sorry_free_artifact")
            self.assertEqual(verdict["schema_version"], fp.TERMINAL_STATE_SCHEMA)
            self.assertEqual(verdict["gate"]["typecheck_status"], "typechecked")
            loaded = fp.load_formal_terminal_state(run_dir)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["terminal_state"], "sorry_free_artifact")

    def _verdict_with_audit(self, tmp: str, audit: dict[str, Any]) -> dict[str, Any]:
        run_dir = Path(tmp) / "loop"
        run_dir.mkdir()
        proj = self._project(Path(tmp))
        return fp.evaluate_formal_terminal_state(
            run_dir,
            root=Path(tmp),
            policy=_policy(project=str(proj)),
            runner=_runner_for(CLEAN_SCAN, TYPECHECK_OK, audit),
            reason="unit",
        )

    def test_an_unsanctioned_axiom_downgrades_a_clean_build_to_open_ledger(self) -> None:
        """A scan and a build both pass; only the audit sees what the proof rests on."""
        with tempfile.TemporaryDirectory() as tmp:
            verdict = self._verdict_with_audit(tmp, AUDIT_SORRY_AX)
            self.assertEqual(verdict["terminal_state"], "open_ledger")
            self.assertIn(
                {"file": "", "kind": "unsanctioned_axiom", "detail": "sorryAx"},
                verdict["obligations"],
            )
            self.assertEqual(verdict["gate"]["axiom_audit"]["unsanctioned_axioms"], ["sorryAx"])

    def test_an_audit_that_never_ran_is_never_a_sorry_free_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            verdict = self._verdict_with_audit(tmp, AUDIT_UNAVAILABLE)
            self.assertEqual(verdict["terminal_state"], "indeterminate")
            self.assertEqual(verdict["detail"], "axiom_audit_tool_unavailable")

    def test_a_forbidden_audit_verb_cannot_pass_as_a_clean_trust_base(self) -> None:
        """An empty report means the audit never ran, never that it found nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            verdict = self._verdict_with_audit(
                tmp, {"ok": False, "status": "forbidden_skill", "report": {}}
            )
            self.assertEqual(verdict["terminal_state"], "indeterminate")
            self.assertEqual(verdict["detail"], "axiom_audit_forbidden_skill")

    def test_a_declaration_missing_from_the_build_blocks_certification(self) -> None:
        """Source and built environment disagree, so the host certifies nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            verdict = self._verdict_with_audit(tmp, AUDIT_UNRESOLVED)
            self.assertEqual(verdict["terminal_state"], "indeterminate")
            self.assertEqual(verdict["detail"], "axiom_audit_unresolved_declaration")

    def test_a_declaration_line_the_walk_could_not_read_blocks_certification(self) -> None:
        """The audit still says "audited" — over a scan with a hole in it.

        A wrapped `theorem` line may have hidden a proof, so the trust base
        reported covers an unknown subset of the project. Certifying it would
        turn the audit's own refusal into a pass.
        """
        with tempfile.TemporaryDirectory() as tmp:
            verdict = self._verdict_with_audit(tmp, AUDIT_UNPARSED)
            self.assertEqual(verdict["terminal_state"], "indeterminate")
            self.assertEqual(verdict["detail"], "axiom_audit_declaration_unparsed")
            self.assertEqual(
                verdict["gate"]["axiom_audit"]["unparsed_declarations"], ["Demo: theorem"]
            )

    def test_an_audit_refusal_this_host_cannot_name_is_still_a_refusal(self) -> None:
        """Fail closed on a reason the summary does not model, not open."""
        with tempfile.TemporaryDirectory() as tmp:
            verdict = self._verdict_with_audit(tmp, AUDIT_REFUSED)
            self.assertEqual(verdict["terminal_state"], "indeterminate")
            self.assertEqual(verdict["detail"], "axiom_audit_refused")

    def test_findings_yield_open_ledger_with_obligations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir()
            proj = self._project(Path(tmp))
            verdict = fp.evaluate_formal_terminal_state(
                run_dir,
                root=Path(tmp),
                policy=_policy(project=str(proj)),
                runner=_runner_for(SORRY_SCAN),
                reason="unit",
            )
            self.assertEqual(verdict["terminal_state"], "open_ledger")
            kinds = {o["kind"] for o in verdict["obligations"]}
            self.assertIn("active_placeholder", kinds)
            self.assertIn("typecheck", kinds)

    def test_failed_host_build_yields_open_ledger_even_when_scan_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir()
            proj = self._project(Path(tmp))
            verdict = fp.evaluate_formal_terminal_state(
                run_dir,
                root=Path(tmp),
                policy=_policy(project=str(proj)),
                runner=_runner_for(CLEAN_SCAN, TYPECHECK_FAIL),
                reason="unit",
            )
            self.assertEqual(verdict["terminal_state"], "open_ledger")
            kinds = {o["kind"] for o in verdict["obligations"]}
            self.assertEqual(kinds, {"typecheck"})

    def test_unavailable_gate_yields_indeterminate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir()
            proj = self._project(Path(tmp))
            unavailable = {"ok": False, "status": "tool_unavailable", "report": {}}
            verdict = fp.evaluate_formal_terminal_state(
                run_dir,
                root=Path(tmp),
                policy=_policy(project=str(proj)),
                runner=_runner_for(unavailable),
                reason="unit",
            )
            self.assertEqual(verdict["terminal_state"], "indeterminate")
            self.assertEqual(verdict["detail"], "gate_scan_unavailable")

    def test_missing_project_yields_indeterminate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir()
            verdict = fp.evaluate_formal_terminal_state(
                run_dir,
                root=Path(tmp),
                policy=_policy(project=str(Path(tmp) / "missing")),
                runner=_runner_for(CLEAN_SCAN, TYPECHECK_OK),
                reason="unit",
            )
            self.assertEqual(verdict["terminal_state"], "indeterminate")
            self.assertEqual(verdict["detail"], "no_lake_project")

    def test_scan_only_mode_never_grants_sorry_free(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir()
            proj = self._project(Path(tmp))
            verdict = fp.evaluate_formal_terminal_state(
                run_dir,
                root=Path(tmp),
                policy=_policy(project=str(proj)),
                runner=_runner_for(CLEAN_SCAN, TYPECHECK_OK),
                reason="unit",
                require_typecheck=False,
            )
            self.assertEqual(verdict["terminal_state"], "indeterminate")
            self.assertEqual(verdict["detail"], "scan_clean_but_unbuilt")

    def test_load_rejects_tampered_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir()
            _write_terminal_file(run_dir, "definitely_sorry_free_trust_me")
            self.assertIsNone(fp.load_formal_terminal_state(run_dir))

    def test_a_certified_verdict_stamps_the_coverage_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir()
            proj = self._project(Path(tmp))
            verdict = fp.evaluate_formal_terminal_state(
                run_dir,
                root=Path(tmp),
                policy=_policy(project=str(proj)),
                runner=_runner_for(CLEAN_SCAN, TYPECHECK_OK),
                reason="unit",
            )
            scan = verdict["gate"]["scan"]
            self.assertEqual(scan["manifest_files"], 1)
            self.assertTrue(scan["coverage_digest"])
            self.assertEqual(scan["source_digest_scope"], "project_sources")

    def test_a_manifest_entirely_inside_the_loop_says_so_in_the_stamp(self) -> None:
        """The fallback digest is the whole manifest and must be labelled it.

        With every scanned file under the loop directory the exclusion leaves
        nothing, so `source_digest` falls back to covering the loop's own
        staged copies. A reader comparing that field is otherwise told it
        excludes them, when here every later staging moves it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            proj = self._project(Path(tmp))
            run_dir = proj / "loop"
            run_dir.mkdir()
            verdict = fp.evaluate_formal_terminal_state(
                run_dir,
                root=Path(tmp),
                policy=_policy(project=str(proj)),
                runner=_runner_for(
                    _scan(("loop/Staged.lean", "aaaaaaaaaaaaaaaa")), TYPECHECK_OK
                ),
                reason="unit",
            )
            scan = verdict["gate"]["scan"]
            self.assertEqual(scan["source_digest_scope"], "whole_manifest")
            self.assertEqual(scan["source_digest"], scan["coverage_digest"])

    def test_a_scan_without_a_manifest_digests_to_nothing(self) -> None:
        # An empty manifest must not collide with a real one-file manifest.
        self.assertEqual(fp._coverage_digest({}), ("", 0))
        self.assertNotEqual(
            fp._coverage_digest(CLEAN_SCAN["report"]["coverage"])[0], ""
        )

    def test_the_source_digest_ignores_the_loop_directory(self) -> None:
        coverage = _scan(
            ("Demo.lean", "aaaaaaaaaaaaaaaa"),
            ("loop/proof_artifacts/Demo.lean", "bbbbbbbbbbbbbbbb"),
        )["report"]["coverage"]
        full, full_files = fp._coverage_digest(coverage)
        source, source_files = fp._coverage_digest(coverage, exclude_prefix="loop")
        self.assertEqual((full_files, source_files), (2, 1))
        self.assertNotEqual(full, source)
        self.assertEqual(
            source, fp._coverage_digest(_scan(("Demo.lean", "aaaaaaaaaaaaaaaa"))["report"]["coverage"])[0]
        )
        # A prefix must not match a sibling that merely starts with the same
        # characters.
        self.assertEqual(fp._coverage_digest(coverage, exclude_prefix="loo")[1], 2)

    def test_the_exclusion_holds_on_a_manifest_written_with_backslashes(self) -> None:
        """The gate records each path with the host's separator.

        A Windows manifest therefore reads `loop\\proof_artifacts\\Demo.lean`,
        which a POSIX prefix never matches: the exclusion would quietly cover
        nothing, and staging one artifact would move the digest and refuse the
        bank it was meant to allow.
        """
        coverage = _scan(
            ("Demo.lean", "aaaaaaaaaaaaaaaa"),
            ("loop\\proof_artifacts\\Demo.lean", "bbbbbbbbbbbbbbbb"),
        )["report"]["coverage"]
        self.assertEqual(fp._coverage_digest(coverage, exclude_prefix="loop")[1], 1)
        self.assertEqual(fp._coverage_digest(coverage, exclude_prefix="loop\\")[1], 1)

    def test_a_loop_directory_outside_the_project_has_no_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            proj = _make_project(tmp, "theorem t : True := trivial\n")
            inside = proj / "loop"
            inside.mkdir()
            self.assertEqual(fp._run_dir_prefix(inside, proj), "loop")
            self.assertEqual(fp._run_dir_prefix(tmp / "loop", proj), "")
            # The project itself is never its own excluded subtree.
            self.assertEqual(fp._run_dir_prefix(proj, proj), "")


class ReverificationTests(unittest.TestCase):
    """WS1: banking a certified proof re-runs the checks instead of trusting the stamp."""

    def _staged(
        self,
        tmp: Path,
        scan: dict[str, Any] = CLEAN_SCAN,
    ) -> tuple[Path, Path]:
        run_dir = tmp / "loop"
        run_dir.mkdir()
        proj = _make_project(tmp, "theorem t : True := trivial\n")
        verdict = fp.evaluate_formal_terminal_state(
            run_dir,
            root=tmp,
            policy=_policy(project=str(proj)),
            runner=_runner_for(scan, TYPECHECK_OK),
            reason="stage",
        )
        self.assertEqual(verdict["terminal_state"], "sorry_free_artifact")
        return run_dir, proj

    def _reverify(
        self,
        run_dir: Path,
        tmp: Path,
        proj: Path,
        scan: dict[str, Any],
        verify: dict[str, Any] | None = TYPECHECK_OK,
        audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return fp.reverify_formal_evidence(
            run_dir,
            root=tmp,
            policy=_policy(project=str(proj)),
            runner=_runner_for(scan, verify, audit),
        )

    def test_a_run_with_no_certified_verdict_has_nothing_to_recheck(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir = tmp / "loop"
            run_dir.mkdir()
            result = fp.reverify_formal_evidence(run_dir)
            self.assertEqual(result["status"], "not_applicable")
            self.assertTrue(result["ok"])
            self.assertEqual(result["detail"], "no_certified_verdict_staged")

    def test_an_unchanged_project_reverifies(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir, proj = self._staged(tmp)
            result = self._reverify(run_dir, tmp, proj, CLEAN_SCAN)
            self.assertEqual(result["status"], "reverified")
            self.assertTrue(result["ok"])
            self.assertEqual(
                result["observed"]["coverage_digest"],
                result["staged"]["coverage_digest"],
            )

    def test_a_project_edited_after_the_verdict_is_a_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir, proj = self._staged(tmp)
            # Same clean verdict, different bytes: the stamp no longer
            # describes the sources that were checked.
            edited = _scan(("Demo.lean", "cccccccccccccccc"))
            result = self._reverify(run_dir, tmp, proj, edited)
            self.assertEqual(result["status"], "mismatch")
            self.assertFalse(result["ok"])
            self.assertEqual(result["detail"], "source_digest_mismatch")

    def _staged_inside(self, tmp: Path, scan: dict[str, Any]) -> tuple[Path, Path]:
        """A loop directory that lives inside the project it checks."""
        proj = _make_project(tmp, "theorem t : True := trivial\n")
        run_dir = proj / "loop"
        run_dir.mkdir()
        verdict = fp.evaluate_formal_terminal_state(
            run_dir,
            root=tmp,
            policy=_policy(project=str(proj)),
            runner=_runner_for(scan, TYPECHECK_OK),
            reason="stage",
        )
        self.assertEqual(verdict["terminal_state"], "sorry_free_artifact")
        return run_dir, proj

    def test_staging_evidence_after_the_verdict_is_not_a_changed_project(self) -> None:
        """Banking stages a copy inside the project; that is not an edit.

        The loop writes proof artifacts under its own directory, which usually
        sits inside the project, so comparing the full manifest made every bank
        that staged one more file refuse itself.
        """
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            before = _scan(("Demo.lean", "aaaaaaaaaaaaaaaa"))
            run_dir, proj = self._staged_inside(tmp, before)
            after = _scan(
                ("Demo.lean", "aaaaaaaaaaaaaaaa"),
                ("loop/proof_artifacts/Demo.lean", "bbbbbbbbbbbbbbbb"),
            )
            result = self._reverify(run_dir, tmp, proj, after)
            self.assertEqual(result["status"], "reverified")
            self.assertTrue(result["ok"])
            self.assertEqual(result["staged"]["compared_digest"], "source_digest")
            self.assertNotEqual(
                result["observed"]["coverage_digest"],
                result["staged"]["coverage_digest"],
            )

    def test_a_source_edit_is_still_a_mismatch_with_a_loop_inside_the_project(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            before = _scan(("Demo.lean", "aaaaaaaaaaaaaaaa"))
            run_dir, proj = self._staged_inside(tmp, before)
            edited = _scan(
                ("Demo.lean", "cccccccccccccccc"),
                ("loop/proof_artifacts/Demo.lean", "bbbbbbbbbbbbbbbb"),
            )
            result = self._reverify(run_dir, tmp, proj, edited)
            self.assertEqual(result["status"], "mismatch")
            self.assertFalse(result["ok"])
            self.assertEqual(result["detail"], "source_digest_mismatch")

    def test_a_stamp_predating_source_digests_is_compared_the_old_way(self) -> None:
        """An older verdict still gets diffed, on the field it actually has."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir, proj = self._staged(tmp)
            path = run_dir / "formal" / "terminal_state.json"
            staged = json.loads(path.read_text(encoding="utf-8"))
            staged["gate"]["scan"].pop("source_digest", None)
            staged["gate"]["scan"].pop("source_files", None)
            path.write_text(json.dumps(staged, indent=2) + "\n", encoding="utf-8")
            same = self._reverify(run_dir, tmp, proj, CLEAN_SCAN)
            self.assertEqual(same["status"], "reverified")
            self.assertEqual(same["staged"]["compared_digest"], "coverage_digest")
            path.write_text(json.dumps(staged, indent=2) + "\n", encoding="utf-8")
            edited = self._reverify(
                run_dir, tmp, proj, _scan(("Demo.lean", "cccccccccccccccc"))
            )
            self.assertEqual(edited["detail"], "coverage_digest_mismatch")

    def test_a_sorry_that_appeared_after_the_verdict_is_a_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir, proj = self._staged(tmp)
            result = self._reverify(run_dir, tmp, proj, SORRY_SCAN, verify=None)
            self.assertEqual(result["status"], "mismatch")
            self.assertEqual(result["detail"], "terminal_state_regressed")
            self.assertEqual(result["observed"]["terminal_state"], "open_ledger")

    def test_an_unsanctioned_axiom_found_on_recheck_is_a_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir, proj = self._staged(tmp)
            result = self._reverify(
                run_dir, tmp, proj, CLEAN_SCAN, audit=AUDIT_SORRY_AX
            )
            self.assertEqual(result["status"], "mismatch")
            self.assertEqual(result["detail"], "terminal_state_regressed")

    def test_a_recheck_that_cannot_run_is_never_a_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir, proj = self._staged(tmp)
            unavailable = {"ok": False, "status": "tool_unavailable", "report": {}}
            result = self._reverify(run_dir, tmp, proj, unavailable)
            self.assertEqual(result["status"], "unavailable")
            self.assertFalse(result["ok"])

    def test_a_stamp_without_a_manifest_cannot_be_diffed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir = tmp / "loop"
            run_dir.mkdir()
            proj = _make_project(tmp, "theorem t : True := trivial\n")
            legacy = {
                "ok": True,
                "status": "ok",
                "report": {
                    "ok": True,
                    "findings": [],
                    "coverage": {"files_total": 1, "files_scanned": 1},
                },
            }
            fp.evaluate_formal_terminal_state(
                run_dir,
                root=tmp,
                policy=_policy(project=str(proj)),
                runner=_runner_for(legacy, TYPECHECK_OK),
                reason="stage",
            )
            result = self._reverify(run_dir, tmp, proj, legacy)
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["detail"], "staged_verdict_has_no_manifest")

    def _decoy_project(self, tmp: Path) -> Path:
        """A second Lake project the agent would rather have re-checked."""
        decoy = tmp / "decoy"
        decoy.mkdir(parents=True, exist_ok=True)
        (decoy / "lakefile.toml").write_text(
            'name = "decoy"\ndefaultTargets = ["Decoy"]\n\n[[lean_lib]]\nname = "Decoy"\n',
            encoding="utf-8",
        )
        (decoy / "Decoy.lean").write_text("theorem d : True := trivial\n", encoding="utf-8")
        return decoy

    def _agent_policy(self, run_dir: Path, project: Path) -> None:
        """The policy file the agent can write inside its own loop tree."""
        formal = run_dir / "formal"
        formal.mkdir(parents=True, exist_ok=True)
        (formal / "formal_policy.json").write_text(
            json.dumps({"policy": "on", "project": str(project)}, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_the_caller_pin_decides_which_project_is_rechecked(self) -> None:
        # `project` is privileged: without a pin the agent's own policy file
        # answers "which project", and the re-check confirms a verdict over a
        # directory the drive never looked at.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir, proj = self._staged(tmp)
            decoy = self._decoy_project(tmp)
            self._agent_policy(run_dir, decoy)

            pinned = fp.reverify_formal_evidence(
                run_dir,
                root=tmp,
                pin={"project": str(proj)},
                runner=_runner_for(CLEAN_SCAN, TYPECHECK_OK),
            )
            self.assertEqual(pinned["policy"]["pin_source"], "caller")
            self.assertEqual(pinned["policy"]["project"], str(proj))
            self.assertEqual(pinned["status"], "reverified")

            unpinned = fp.reverify_formal_evidence(
                run_dir, root=tmp, runner=_runner_for(CLEAN_SCAN, TYPECHECK_OK)
            )
            self.assertEqual(unpinned["policy"]["pin_source"], "unpinned")
            self.assertEqual(unpinned["policy"]["project"], str(decoy))

    def test_a_caller_without_a_pin_falls_back_to_the_drive_start_pin(self) -> None:
        # A separate append-iteration process has no in-memory pin, so it reads
        # the one the drive persisted rather than resolving policy unpinned.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir, proj = self._staged(tmp)
            self._agent_policy(run_dir, self._decoy_project(tmp))
            fp.write_host_pin(run_dir, {"project": str(proj), "root": str(tmp)})

            result = fp.reverify_formal_evidence(
                run_dir, runner=_runner_for(CLEAN_SCAN, TYPECHECK_OK)
            )
            self.assertEqual(result["policy"]["pin_source"], "drive_start_pin_file")
            self.assertEqual(result["policy"]["project"], str(proj))
            # The root travels with the pin, so the project resolves the way
            # the drive resolved it instead of against the loop directory.
            self.assertEqual(result["policy"]["root"], str(tmp))
            self.assertEqual(result["status"], "reverified")

    def test_a_missing_pin_file_reads_as_no_pin(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw) / "loop"
            run_dir.mkdir()
            self.assertEqual(fp.read_host_pin(run_dir), {})
            (run_dir / "formal").mkdir()
            (run_dir / "formal" / "host_policy.pin.json").write_text(
                "[not, a, pin]\n", encoding="utf-8"
            )
            self.assertEqual(fp.read_host_pin(run_dir), {})

    def test_a_staged_verdict_that_cannot_be_read_is_never_a_pass(self) -> None:
        # Truncating the stamp must not look like a run that never staged one:
        # "no verdict to check" passes, "the verdict is unreadable" cannot.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir, proj = self._staged(tmp)
            (run_dir / "formal" / "terminal_state.json").write_text(
                '{"terminal_state": "sorry_free_art', encoding="utf-8"
            )
            result = self._reverify(run_dir, tmp, proj, CLEAN_SCAN)
            self.assertEqual(result["status"], "unavailable")
            self.assertFalse(result["ok"])
            self.assertEqual(result["detail"], "staged_verdict_unreadable")

    def test_a_refused_recheck_leaves_the_staged_stamp_in_place(self) -> None:
        # Otherwise a first refusal would erase the certified stamp and the
        # retry would find nothing to check, banking unverified.
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            run_dir, proj = self._staged(tmp)
            first = self._reverify(run_dir, tmp, proj, SORRY_SCAN, verify=None)
            self.assertEqual(first["status"], "mismatch")
            staged = fp.load_formal_terminal_state(run_dir)
            self.assertEqual(staged["terminal_state"], "sorry_free_artifact")
            second = self._reverify(run_dir, tmp, proj, SORRY_SCAN, verify=None)
            self.assertEqual(second["status"], "mismatch")


class ForceTickLedgerTests(unittest.TestCase):
    def _force_policy(self, proj: Path) -> fp.FormalPolicy:
        return _policy(
            policy="force",
            project=str(proj),
            typecheck=False,
            force_after_iteration=True,
        )

    def test_gate_scan_is_recorded_as_gate_project_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir()
            proj = _make_project(Path(tmp), "theorem t : True := trivial\n")
            report = fp.formal_force_tick(
                run_dir,
                root=Path(tmp),
                policy=self._force_policy(proj),
                runner=_runner_for(CLEAN_SCAN),
            )
            decisions = {e["step"]: e["decision"] for e in report["ledger"]}
            self.assertEqual(decisions.get("scan"), "gate_project_scan")

    def test_unavailable_gate_falls_back_to_crude_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir()
            proj = _make_project(Path(tmp), "theorem t : True := sorry\n")
            unavailable = {"ok": False, "status": "tool_unavailable", "report": {}}
            report = fp.formal_force_tick(
                run_dir,
                root=Path(tmp),
                policy=self._force_policy(proj),
                runner=_runner_for(unavailable),
            )
            decisions = {e["step"]: e["decision"] for e in report["ledger"]}
            self.assertEqual(decisions.get("scan"), "crude_fallback")


class EarlyStopGuardTests(unittest.TestCase):
    def _legacy_formal_loop(self, tmp: Path, *, lean_body: str) -> tuple[Path, dict[str, str]]:
        """A legacy (Goal-Focus off) formal-track loop over a stub-gated project."""
        run_dir = tmp / "loop"
        proj = _make_project(tmp, lean_body)
        _init_loop(run_dir, formal=True, project=str(proj))
        _set_formal_track(run_dir)
        _write_proof_artifact(run_dir)
        stub = tmp / "stub_gate.py"
        stub.write_text(STUB_GATE, encoding="utf-8")
        return run_dir, _clean_env(AAS_STRICT_GATE_SCRIPT=str(stub))

    def test_formal_track_success_stop_requires_host_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            _init_loop(run_dir, formal=True)
            _set_formal_track(run_dir)
            _write_proof_artifact(run_dir)

            rejected = _append_stop(
                run_dir, stop_reason="proof_found", evidence_id="proof-artifact-1"
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("sorry_free_artifact", rejected.stdout + rejected.stderr)
            self.assertEqual(
                (run_dir / "iterations.jsonl").read_text(encoding="utf-8"), ""
            )

    def test_a_staged_verdict_with_no_manifest_cannot_be_re_verified(self) -> None:
        # A hand-written stamp carries no per-file manifest, so there is nothing
        # a re-run could agree with. Undiffable is refused, not waved through.
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            _init_loop(run_dir, formal=True)
            _set_formal_track(run_dir)
            _write_proof_artifact(run_dir)
            _write_terminal_file(run_dir, "sorry_free_artifact")

            rejected = _append_stop(
                run_dir, stop_reason="proof_found", evidence_id="proof-artifact-1"
            )
            self.assertNotEqual(rejected.returncode, 0)
            output = rejected.stdout + rejected.stderr
            self.assertIn("re-verification", output)
            self.assertIn("staged_verdict_has_no_manifest", output)
            self.assertEqual(
                (run_dir / "iterations.jsonl").read_text(encoding="utf-8"), ""
            )

    def test_a_legacy_success_stop_re_verifies_the_staged_verdict(self) -> None:
        # Legacy mode has no finalize step, so the append is where the host
        # re-runs its own checks before the proof claim banks.
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, env = self._legacy_formal_loop(
                Path(tmp), lean_body="theorem t : True := trivial\n"
            )
            stamped = _run_runtime(
                "formal-terminal-state", "--dir", str(run_dir), "--reason", "test", env=env
            )
            self.assertEqual(stamped.returncode, 0, stamped.stdout + stamped.stderr)

            accepted = _append_stop(
                run_dir,
                stop_reason="proof_found",
                evidence_id="proof-artifact-1",
                env=env,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            rows = [
                json.loads(line)
                for line in (run_dir / "iterations.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertEqual(rows[-1]["host_reverification"]["status"], "reverified")
            validated = _run_runtime("validate", "--dir", str(run_dir), env=env)
            self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)

    def test_a_source_edited_after_the_verdict_refuses_the_legacy_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, env = self._legacy_formal_loop(
                Path(tmp), lean_body="theorem t : True := trivial\n"
            )
            stamped = _run_runtime(
                "formal-terminal-state", "--dir", str(run_dir), "--reason", "test", env=env
            )
            self.assertEqual(stamped.returncode, 0, stamped.stdout + stamped.stderr)
            # Still clean, so the verdict itself does not regress: only the
            # manifest moves, which is exactly what the digest is there to see.
            (Path(tmp) / "proj" / "Demo.lean").write_text(
                "theorem t : True := trivial\ntheorem u : True := trivial\n",
                encoding="utf-8",
            )

            rejected = _append_stop(
                run_dir,
                stop_reason="proof_found",
                evidence_id="proof-artifact-1",
                env=env,
            )
            self.assertNotEqual(rejected.returncode, 0)
            output = rejected.stdout + rejected.stderr
            self.assertIn("mismatch", output)
            self.assertEqual(
                (run_dir / "iterations.jsonl").read_text(encoding="utf-8"), ""
            )

    def test_a_verdict_that_vanishes_before_the_recheck_refuses_the_stop(self) -> None:
        """Deleting the stamp must not read as "there was nothing to check".

        The gate is reached only after a certified verdict has been read off
        disk. If the re-check then finds none, the stamp went away in the
        window between — the one move that would otherwise turn a re-check the
        agent cannot survive into no re-check at all.
        """
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir(parents=True)
            vanished = {
                "status": "not_applicable",
                "ok": True,
                "detail": "no_certified_verdict_staged",
            }
            with mock.patch.object(
                rt, "reverify_formal_evidence", return_value=vanished
            ):
                with self.assertRaises(rt.GuardError) as caught:
                    rt._require_legacy_host_reverification(run_dir)
            self.assertIn("not_applicable", str(caught.exception))
            self.assertIn("no_certified_verdict_staged", str(caught.exception))

    def test_non_formal_loop_success_stop_is_unaffected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            _init_loop(run_dir, formal=False)
            _write_proof_artifact(run_dir)
            accepted = _append_stop(
                run_dir, stop_reason="proof_found", evidence_id="proof-artifact-1"
            )
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

    def test_formal_open_ledger_stop_requires_open_ledger_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            _init_loop(run_dir, formal=True)
            _set_formal_track(run_dir)

            rejected = _append_stop(run_dir, stop_reason="formal_open_ledger")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("open_ledger", rejected.stdout + rejected.stderr)

            _write_terminal_file(run_dir, "open_ledger")
            accepted = _append_stop(run_dir, stop_reason="formal_open_ledger")
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            status = json.loads(_run_runtime("status", "--dir", str(run_dir)).stdout)
            self.assertEqual(status["state_status"], "stopped")


class FormalTrackPinTests(unittest.TestCase):
    """The track a success claim is judged against is the one the host dispatched.

    ``is_formal_track`` reads ``loop_state.next_preferred_path`` and
    ``recovery.md``, both of which the agent under review maintains. The pin the
    drive writes before handing control over is what keeps an agent from
    spending an iteration on Lean and then rewriting the committed path so the
    terminal-state requirement no longer applies to its own proof claim.
    """

    @staticmethod
    def _clear_formal_track(run_dir: Path) -> None:
        path = run_dir / "loop_state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["next_preferred_path"] = "write up the counting argument"
        path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def test_no_pin_falls_back_to_the_derived_reading(self) -> None:
        # Archived runs and manual append-iteration calls never go through
        # drive, so the pin is absent and behaviour is unchanged.
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            _init_loop(run_dir, formal=True)
            self.assertEqual(fp.read_track_pin(run_dir), {})
            self.assertFalse(fp.is_formal_track(run_dir))

            _set_formal_track(run_dir)
            status = fp.formal_track_status(run_dir)
            self.assertTrue(status.formal_track)
            self.assertTrue(status.derived)
            self.assertIsNone(status.pinned)
            self.assertFalse(status.drift)

    def test_a_pinned_track_survives_a_rewritten_committed_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            _init_loop(run_dir, formal=True)
            _set_formal_track(run_dir)
            fp.write_track_pin(
                run_dir, formal_track=True, source="drive_dispatch", iteration=1
            )

            self._clear_formal_track(run_dir)
            status = fp.formal_track_status(run_dir)
            self.assertTrue(status.formal_track)
            self.assertFalse(status.derived)
            self.assertTrue(status.pinned)
            self.assertTrue(status.drift)
            self.assertEqual(status.pin_source, "drive_dispatch")
            self.assertEqual(status.pin_iteration, 1)

    def test_a_forged_false_pin_cannot_shed_the_derived_track(self) -> None:
        # The pin file sits in the agent-writable loop tree. Combining the two
        # readings with OR is what makes that safe: writing false changes
        # nothing, and writing true only adds a check the writer must pass.
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            _init_loop(run_dir, formal=True)
            _set_formal_track(run_dir)
            fp.write_track_pin(run_dir, formal_track=False, source="forged")

            status = fp.formal_track_status(run_dir)
            self.assertTrue(status.formal_track)
            self.assertTrue(status.drift)

    def test_a_pin_dropped_at_the_next_dispatch_releases_the_requirement(self) -> None:
        # The pin is refreshed per dispatch rather than latched, so a run that
        # genuinely leaves the formal track is not stuck carrying a Lean
        # terminal-state requirement it can never satisfy.
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            _init_loop(run_dir, formal=True)
            _set_formal_track(run_dir)
            fp.write_track_pin(run_dir, formal_track=True, source="drive_dispatch")
            self._clear_formal_track(run_dir)
            fp.write_track_pin(
                run_dir, formal_track=False, source="drive_dispatch", iteration=2
            )

            status = fp.formal_track_status(run_dir)
            self.assertFalse(status.formal_track)
            self.assertFalse(status.drift)

    def test_the_success_gate_holds_when_only_the_pin_says_formal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            _init_loop(run_dir, formal=True)
            _set_formal_track(run_dir)
            _write_proof_artifact(run_dir)
            fp.write_track_pin(
                run_dir, formal_track=True, source="drive_dispatch", iteration=1
            )
            self._clear_formal_track(run_dir)

            rejected = _append_stop(
                run_dir, stop_reason="proof_found", evidence_id="proof-artifact-1"
            )
            self.assertNotEqual(rejected.returncode, 0)
            output = rejected.stdout + rejected.stderr
            self.assertIn("sorry_free_artifact", output)
            self.assertIn("track.pin.json", output)
            self.assertEqual(
                (run_dir / "iterations.jsonl").read_text(encoding="utf-8"), ""
            )

    def test_a_run_never_dispatched_on_the_formal_track_is_not_gated(self) -> None:
        # The complement of the test above: a pin that says non-formal must not
        # invent a Lean requirement for a run that never did Lean work.
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            _init_loop(run_dir, formal=True)
            _write_proof_artifact(run_dir)
            fp.write_track_pin(
                run_dir, formal_track=False, source="drive_dispatch", iteration=1
            )

            accepted = _append_stop(
                run_dir, stop_reason="proof_found", evidence_id="proof-artifact-1"
            )
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

    def test_drive_start_pins_the_track_it_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            _init_loop(run_dir, formal=True)
            _set_formal_track(run_dir)
            args = argparse.Namespace(root=str(run_dir))
            # Drive start exports the resolved policy into os.environ so nested
            # tools inherit it. In-process that would leak AAS_AUTOLOOP_FORMAL_*
            # into every later test in the run, where load_formal_policy ranks
            # env above the loop's own policy file.
            with mock.patch.dict(os.environ, {}, clear=False):
                rt._apply_formal_drive_start(run_dir, args)

            pin = fp.read_track_pin(run_dir)
            self.assertIs(pin.get("formal_track"), True)
            self.assertEqual(pin.get("source"), "drive_start")


class TerminalStateCliTests(unittest.TestCase):
    def _setup(self, tmp: Path, *, lean_body: str) -> tuple[Path, dict[str, str]]:
        run_dir = tmp / "loop"
        proj = _make_project(tmp, lean_body)
        _init_loop(run_dir, formal=True, project=str(proj))
        stub = tmp / "stub_gate.py"
        stub.write_text(STUB_GATE, encoding="utf-8")
        return run_dir, _clean_env(AAS_STRICT_GATE_SCRIPT=str(stub))

    def test_clean_project_decides_sorry_free_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, env = self._setup(
                Path(tmp), lean_body="theorem t : True := trivial\n"
            )
            proc = _run_runtime(
                "formal-terminal-state", "--dir", str(run_dir), "--reason", "test", env=env
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["terminal_state"], "sorry_free_artifact")
            self.assertTrue(Path(report["state_file"]).is_file())

    def test_sorry_project_decides_open_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, env = self._setup(Path(tmp), lean_body="theorem t : True := sorry\n")
            proc = _run_runtime(
                "formal-terminal-state", "--dir", str(run_dir), "--reason", "test", env=env
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["terminal_state"], "open_ledger")
            kinds = {o["kind"] for o in report["obligations"]}
            self.assertIn("active_placeholder", kinds)

    def test_missing_gate_exits_nonzero_with_indeterminate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, env = self._setup(
                Path(tmp), lean_body="theorem t : True := trivial\n"
            )
            env["AAS_STRICT_GATE_SCRIPT"] = "/nonexistent/gate.py"
            proc = _run_runtime(
                "formal-terminal-state", "--dir", str(run_dir), "--reason", "test", env=env
            )
            self.assertNotEqual(proc.returncode, 0)
            report = json.loads(proc.stdout)
            self.assertEqual(report["terminal_state"], "indeterminate")
            self.assertEqual(report["detail"], "gate_scan_unavailable")


class DriveWiringTests(unittest.TestCase):
    def test_review_wait_exhausted_has_a_dedicated_resumable_exit_code(self) -> None:
        self.assertEqual(rt.DRIVE_EXIT_CODES.get("review_wait_exhausted"), 16)
        codes = list(rt.DRIVE_EXIT_CODES.values())
        self.assertEqual(len(codes), len(set(codes)), rt.DRIVE_EXIT_CODES)

    def test_drive_parser_accepts_max_review_waits(self) -> None:
        proc = _run_runtime("drive", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--max-review-waits", proc.stdout)

    def test_wait_sites_are_bounded_and_counter_resets_on_progress(self) -> None:
        source = RUNTIME_PY.read_text(encoding="utf-8")
        self.assertEqual(source.count("review_waits += 1"), 4)
        self.assertGreaterEqual(source.count("review_waits = 0"), 5)
        self.assertIn('reason = "review_wait_exhausted"', source)

    def test_supervisor_passes_max_review_waits_through(self) -> None:
        script = SUPERVISOR_SH.read_text(encoding="utf-8")
        self.assertIn("MAX_REVIEW_WAITS={int(dd.get('max_review_waits', 0))}", script)
        self.assertIn('--max-review-waits "$MAX_REVIEW_WAITS"', script)


@unittest.skipUnless(os.name == "posix", "force-loop bootstrap uses the POSIX policy loader")
class ForceLoopBootstrapTests(unittest.TestCase):
    def _bootstrap(self, tmp: Path, profile: str, *extra: str) -> tuple[int, Path]:
        cli = _load("force_loop_cli_evidence", FORCE_LOOP / "force_loop_cli.py")
        loop = tmp / "loop"
        with mock.patch.dict(os.environ):
            os.environ.pop("AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS", None)
            rc = cli.main(
                [
                    "bootstrap",
                    "--loop",
                    str(loop),
                    "--root",
                    str(tmp),
                    "--profile",
                    profile,
                    "--goal",
                    "formalize demo theorem",
                    "--success-criteria",
                    "terminal_state sorry_free_artifact",
                    "--policy-file",
                    str(tmp / "policy.env"),
                    "--no-backup",
                    *extra,
                ]
            )
        return rc, loop

    def test_formal_bootstrap_defaults_to_campaign_scale_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rc, loop = self._bootstrap(Path(tmp), "formal")
            self.assertEqual(rc, 0)
            budget = json.loads((loop / "budget.json").read_text(encoding="utf-8"))
            self.assertEqual(budget["max_iterations"], 40)
            self.assertEqual(budget["max_wall_time_seconds"], 259200)

    def test_explicit_budget_flags_override_the_profile_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rc, loop = self._bootstrap(
                Path(tmp),
                "general",
                "--max-iterations",
                "7",
                "--max-wall-time-seconds",
                "7200",
            )
            self.assertEqual(rc, 0)
            budget = json.loads((loop / "budget.json").read_text(encoding="utf-8"))
            self.assertEqual(budget["max_iterations"], 7)
            self.assertEqual(budget["max_wall_time_seconds"], 7200)


@unittest.skipUnless(os.name == "posix", "smoke checks use the POSIX backend selector")
class NotifyPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cli = _load("force_loop_cli_notify", FORCE_LOOP / "force_loop_cli.py")

    def _smoke(self, channels: Any, *, consent: bool) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp:
            missing_loop = Path(tmp) / "never-created"
            with mock.patch.dict(os.environ), mock.patch.object(
                self.cli, "_detect_notify_channels", return_value=channels
            ):
                if consent:
                    os.environ["AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS"] = "allow"
                else:
                    os.environ.pop("AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS", None)
                return self.cli._smoke_checks(missing_loop, "general")

    def test_without_consent_notify_stays_a_warning(self) -> None:
        out = self._smoke(["zulip"], consent=False)
        self.assertEqual(out["notify_status"], "blocked_no_consent")
        self.assertTrue(any("NOTIFY_EGRESS" in w for w in out["warnings"]))
        self.assertFalse(any("notify channel" in e for e in out["errors"]))

    def test_consent_without_channel_is_an_error(self) -> None:
        out = self._smoke([], consent=True)
        self.assertEqual(out["notify_status"], "no_channel")
        self.assertTrue(any("no notify channel" in e for e in out["errors"]))

    def test_consent_with_channel_reports_ready(self) -> None:
        out = self._smoke(["zulip"], consent=True)
        self.assertEqual(out["notify_status"], "ready:zulip")
        self.assertFalse(any("notify channel" in e for e in out["errors"]))

    def test_detection_failure_is_a_warning_not_a_false_ready(self) -> None:
        out = self._smoke(None, consent=True)
        self.assertEqual(out["notify_status"], "unknown")
        self.assertTrue(any("detection failed" in w for w in out["warnings"]))

    def test_live_detection_runs_without_crashing(self) -> None:
        result = self.cli._detect_notify_channels()
        self.assertTrue(result is None or isinstance(result, list))


def _lean_toolchain_ready() -> bool:
    if shutil.which("lake") is None or shutil.which("elan") is None:
        return False
    try:
        listed = subprocess.run(
            ["elan", "toolchain", "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return LEAN_TOOLCHAIN in listed.stdout


@unittest.skipUnless(
    _lean_toolchain_ready(), f"requires lake/elan with {LEAN_TOOLCHAIN} installed"
)
class RealLeanEndToEndTests(unittest.TestCase):
    """The full v0 loop verdict against a real Lean toolchain (no stubs)."""

    def test_host_gate_decides_both_terminal_states_on_a_real_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            proj.mkdir()
            (proj / "lakefile.toml").write_text(
                'name = "demo"\ndefaultTargets = ["Demo"]\n\n[[lean_lib]]\nname = "Demo"\n',
                encoding="utf-8",
            )
            (proj / "lean-toolchain").write_text(LEAN_TOOLCHAIN + "\n", encoding="utf-8")
            (proj / "Demo.lean").write_text(
                "theorem demo_add_comm (a b : Nat) : a + b = b + a := Nat.add_comm a b\n",
                encoding="utf-8",
            )
            run_dir = Path(tmp) / "loop"
            _init_loop(run_dir, formal=True, project=str(proj))

            proc = _run_runtime(
                "formal-terminal-state", "--dir", str(run_dir), "--reason", "e2e"
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["terminal_state"], "sorry_free_artifact")
            self.assertEqual(report["gate"]["typecheck_status"], "typechecked")
            self.assertEqual(report["obligations"], [])

            (proj / "Bad.lean").write_text(
                "theorem demo_bad (a b : Nat) : a * b = b * a := sorry\n",
                encoding="utf-8",
            )
            proc = _run_runtime(
                "formal-terminal-state", "--dir", str(run_dir), "--reason", "e2e"
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["terminal_state"], "open_ledger")
            kinds = {o["kind"] for o in report["obligations"]}
            self.assertIn("active_placeholder", kinds)

            # Sorry-free text with a false proof in the built target: only the
            # host-run lake build can catch this, so the fabricated success
            # must land in open_ledger.
            (proj / "Bad.lean").unlink()
            (proj / "Demo.lean").write_text(
                "theorem demo_bad (a b : Nat) : a + b = b := rfl\n",
                encoding="utf-8",
            )
            proc = _run_runtime(
                "formal-terminal-state", "--dir", str(run_dir), "--reason", "e2e"
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["terminal_state"], "open_ledger")
            self.assertEqual(report["gate"]["typecheck_status"], "typecheck_failed")
            kinds = {o["kind"] for o in report["obligations"]}
            self.assertEqual(kinds, {"typecheck"})


class TerminalStateWriteFailureIsNotADecidedVerdict(unittest.TestCase):
    """A verdict the host could not record certifies nothing.

    ``formal/terminal_state.json`` is what the downstream gates read; the
    write used to swallow OSError, so an unwritable stamp still answered with
    the verdict the loop was allowed to terminate on.
    """

    def _project(self, tmp: Path) -> Path:
        return _make_project(tmp, "theorem t : True := trivial\n")

    def test_an_unwritable_stamp_downgrades_sorry_free_to_indeterminate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir()
            # A regular file where the stamp directory belongs: mkdir fails,
            # so the verdict cannot be recorded anywhere.
            (run_dir / "formal").write_text("not a directory\n", encoding="utf-8")
            proj = self._project(Path(tmp))
            verdict = fp.evaluate_formal_terminal_state(
                run_dir,
                root=Path(tmp),
                policy=_policy(project=str(proj)),
                runner=_runner_for(CLEAN_SCAN, TYPECHECK_OK, AUDIT_CLEAN),
                reason="unit",
            )
            self.assertEqual(verdict["terminal_state"], "indeterminate")
            self.assertIn("terminal state could not be written", verdict["detail"])
            self.assertFalse((run_dir / "formal" / "terminal_state.json").exists())

    def test_the_same_fixture_certifies_when_the_stamp_can_be_written(self) -> None:
        """Anchors the test above: without the blocked write it is sorry-free."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir()
            proj = self._project(Path(tmp))
            verdict = fp.evaluate_formal_terminal_state(
                run_dir,
                root=Path(tmp),
                policy=_policy(project=str(proj)),
                runner=_runner_for(CLEAN_SCAN, TYPECHECK_OK, AUDIT_CLEAN),
                reason="unit",
            )
            self.assertEqual(verdict["terminal_state"], "sorry_free_artifact")
            self.assertTrue((run_dir / "formal" / "terminal_state.json").is_file())

    def test_a_write_failure_is_reported_rather_than_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            run_dir.mkdir()
            (run_dir / "formal").write_text("not a directory\n", encoding="utf-8")
            self.assertIsNotNone(
                fp._write_terminal_state(run_dir, {"terminal_state": "decided"})
            )

class FormalTrackPinFailureStopsTheDispatchTests(unittest.TestCase):
    """A track pin the host cannot write must stop the dispatch it protects.

    The append-time formal gate runs inside the agent's own ``append-iteration``
    process and reads the track from ``formal/track.pin.json``, so the pin is
    the only channel the host has for holding an agent to the terminal-state
    requirement its own Lean work incurs. ``write_track_pin`` used to swallow
    the OSError and return None whether or not the file landed, and the drive
    dispatched regardless: the agent then shed the requirement by rewriting
    ``next_preferred_path``, which is exactly what the pin exists to prevent.
    Nothing in process memory can substitute, because the reader is a different
    process, so refusing to dispatch is the only enforcement left.
    """

    @staticmethod
    def _block_formal_dir(run_dir: Path) -> None:
        # A file where the directory belongs stands in for every unwritable run
        # tree -- read-only mount, full disk, wrong owner -- and needs no
        # privileges to set up.
        formal_dir = run_dir / "formal"
        if formal_dir.is_dir():
            shutil.rmtree(formal_dir)
        formal_dir.write_text("occupied by a file\n", encoding="utf-8")

    @staticmethod
    def _iteration_command(run_dir: Path) -> str:
        return " ".join(
            [
                f'"{sys.executable}"',
                "-B",
                f'"{RUNTIME_PY}"',
                "append-iteration",
                "--dir",
                f'"{run_dir}"',
                "--mode bounded-research",
                '--objective "track pin fixture"',
                "--decision continue",
                '--output "fixture iteration banked"',
                "--source-id S1",
                "--guard-ref G1",
                '--remaining-gap "none"',
            ]
        )

    def _drive(self, tmp: Path, run_dir: Path) -> dict[str, Any]:
        args = rt.selftest_drive_args(
            run_dir, tmp / "registry", self._iteration_command(run_dir)
        )
        args.no_progress = True
        # drive start exports the resolved formal policy into os.environ, where
        # load_formal_policy ranks it above the loop's own policy file for every
        # later test in this process.
        with mock.patch.dict(os.environ, {}, clear=False):
            return rt.drive_command(args)

    @staticmethod
    def _banked(run_dir: Path) -> int:
        body = (run_dir / "iterations.jsonl").read_text(encoding="utf-8")
        return len([line for line in body.splitlines() if line.strip()])

    def test_write_track_pin_reports_the_write_it_could_not_do(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            _init_loop(run_dir, formal=True)
            self.assertIsNone(
                fp.write_track_pin(run_dir, formal_track=True, source="drive_dispatch")
            )

            self._block_formal_dir(run_dir)
            error = fp.write_track_pin(
                run_dir, formal_track=True, source="drive_dispatch"
            )
            self.assertIsInstance(error, str)
            self.assertTrue(error)

    def test_a_formal_dispatch_stops_when_the_track_cannot_be_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            _init_loop(run_dir, formal=True)
            _set_formal_track(run_dir)
            self._block_formal_dir(run_dir)

            result = self._drive(Path(tmp), run_dir)

            self.assertEqual(result.get("reason"), "formal_track_unpinnable", result)
            self.assertEqual(self._banked(run_dir), 0, result)

    def test_a_run_off_the_formal_track_still_dispatches(self) -> None:
        # The complement: the same unwritable tree must not stop a run that
        # never committed to the formal track, or one broken directory would
        # halt every drive on the host.  Mock the platform containment boundary:
        # macOS deliberately cannot run a real primary because it has no Linux
        # PID namespace, and that unrelated refusal must not decide this pin
        # gate test.
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            _init_loop(run_dir, formal=True)
            self.assertFalse(fp.formal_track_status(run_dir).derived)
            self._block_formal_dir(run_dir)

            with mock.patch.object(
                rt,
                "run_primary_subprocess",
                return_value=(1, False, None),
            ) as primary:
                result = self._drive(Path(tmp), run_dir)

            self.assertNotEqual(result.get("reason"), "formal_track_unpinnable", result)
            primary.assert_called_once()
            self.assertEqual(self._banked(run_dir), 0, result)


if __name__ == "__main__":
    unittest.main()
