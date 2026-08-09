"""Evidence suite for the v0 formal loop: host-authored terminal states.

Covers the M1 verification core (default gate runner, terminal-state
evaluation, early-stop guards, the `formal-terminal-state` CLI verb) and the
M2 survivability wiring (bounded review waits, supervisor passthrough,
campaign budgets, notify preflight), plus a real-Lean end-to-end check that
runs only when the pinned toolchain is installed.
"""

from __future__ import annotations

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
coverage = {"files_total": len(files), "files_scanned": len(files)}
report = {"ok": not findings, "findings": findings, "coverage": coverage}
if mode == "verify":
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
    return _run_runtime(*args)


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


def _runner_for(scan: dict[str, Any], verify: dict[str, Any] | None = None):
    def runner(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "lean_strict_verification_gate.scan":
            return scan
        if name == "lean_strict_verification_gate.verify_typecheck":
            return verify or {"ok": False, "status": "not_run", "report": {}}
        return {"ok": False, "status": "forbidden_skill", "report": {}}

    return runner


CLEAN_SCAN = {
    "ok": True,
    "status": "ok",
    "report": {
        "ok": True,
        "findings": [],
        "coverage": {"files_total": 1, "files_scanned": 1},
    },
}
SORRY_SCAN = {
    "ok": False,
    "status": "failed",
    "report": {
        "ok": False,
        "findings": [
            {"file": "Demo/Bad.lean", "kind": "active_placeholder", "detail": "sorry"}
        ],
        "coverage": {"files_total": 2, "files_scanned": 2},
    },
}
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

            _write_terminal_file(run_dir, "sorry_free_artifact")
            accepted = _append_stop(
                run_dir, stop_reason="proof_found", evidence_id="proof-artifact-1"
            )
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            status = json.loads(_run_runtime("status", "--dir", str(run_dir)).stdout)
            self.assertEqual(status["state_status"], "stopped")

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


if __name__ == "__main__":
    unittest.main()
