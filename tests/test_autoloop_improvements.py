"""Tests for the T0–T4 benchmark-campaign improvement batch.

Covers the friction fixes (P1–P8) and integrity guards (IG-1..IG-4) added to
the ARL runtime after the closed-book Lean formalization campaign:

- GuardError structured payload on early-stop guard failures (P2)
- suggestion-only stop_reason / artifact_type hints, never aliased (P1/P4)
- compute-run metadata aliases: 'backend' synonym, 'completed' status (P5)
- append-iteration --dry-run (P3) and the validate-proof-artifact verb
- stage-proof scaffolding: copy, sha256, collision refusals (P6)
- formal_open_ledger append/validate exemption lockstep
- retract-iteration: happy path, refusals, audit trail, rollback (P8)
- LedgerIntegrityWatch (IG-2), BuildConfigWatch (IG-1),
  _ledger_consistency_errors (IG-3), snapshot writer, drive wiring
- prompt-contract text in formal_policy blocks (P7, IG-4)
- drive_stop carries a computed formal verdict, never the exception fallback
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "autonomous-research-loop-runtime"
)
HELPER = RUNTIME_DIR / "autonomous_research_loop_runtime.py"

sys.path.insert(0, str(RUNTIME_DIR))

import autonomous_research_loop_runtime as rt  # noqa: E402
import formal_policy as fp  # noqa: E402

from tests.test_autonomous_research_loop import (  # noqa: E402
    _primary_containment_available,
)


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Never let inherited host notify secrets post real messages from tests.
    env["AAS_AUTOLOOP_NOTIFY"] = "off"
    # Legacy-mode verbs under test must not inherit a host-mediated posture.
    env.pop("AAS_AUTOLOOP_HOST_MEDIATED_SUBMISSION", None)
    # Formal posture must come from each test's loop config, not the host env.
    for key in (
        "AAS_AUTOLOOP_FORMAL_POLICY",
        "AAS_AUTOLOOP_FORMAL_PROJECT",
        "AAS_STRICT_GATE_SCRIPT",
    ):
        env.pop(key, None)
    if extra:
        env.update(extra)
    return env


def _run(
    *args: str,
    env_extra: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(HELPER), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        env=_env(env_extra),
        check=False,
    )


def _out(res: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(res.stdout)


def _init(loop: Path, *, max_iterations: int = 3, extra: tuple[str, ...] = ()) -> None:
    res = _run(
        "init",
        "--dir",
        str(loop),
        "--goal",
        "improvement batch test",
        "--success-criteria",
        "ledger validates",
        "--max-iterations",
        str(max_iterations),
        "--goal-focus-mode",
        "off",
        *extra,
    )
    assert res.returncode == 0, res.stdout + res.stderr


def _append(
    loop: Path,
    decision: str,
    *,
    stop_reason: str | None = None,
    evidence_id: str | None = None,
    claim_id: str | None = None,
    dry_run: bool = False,
    tokens: int | None = None,
    usd: float | None = None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "append-iteration",
        "--dir",
        str(loop),
        "--mode",
        "bounded-research",
        "--objective",
        "exercise the improvement batch",
        "--decision",
        decision,
        "--source-id",
        "S1",
        "--guard-ref",
        "G1",
    ]
    if stop_reason is not None:
        cmd += ["--stop-reason", stop_reason]
    if evidence_id is not None:
        cmd += ["--evidence-id", evidence_id]
    if claim_id is not None:
        cmd += ["--claim-id", claim_id]
    if dry_run:
        cmd += ["--dry-run"]
    if tokens is not None:
        cmd += ["--tokens", str(tokens)]
    if usd is not None:
        cmd += ["--usd", str(usd)]
    return _run(*cmd, env_extra=env_extra)


def _retract(
    loop: Path,
    registry: Path,
    *,
    reason: str = "wrong decision recorded",
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return _run(
        "retract-iteration",
        "--dir",
        str(loop),
        "--reason",
        reason,
        "--registry-dir",
        str(registry),
        env_extra=env_extra,
    )


LOOP_FILES = ("loop_state.json", "budget.json", "iterations.jsonl", "recovery.md")


def _snapshot(loop: Path) -> dict[str, bytes | None]:
    return {
        name: ((loop / name).read_bytes() if (loop / name).exists() else None)
        for name in LOOP_FILES
    }


def _read_json(loop: Path, name: str) -> dict[str, object]:
    return json.loads((loop / name).read_text(encoding="utf-8"))


class SuggestionHelperTests(unittest.TestCase):
    def test_stop_reason_head_token_suggested(self) -> None:
        self.assertEqual(rt.suggest_stop_reason("proof: lemma closed"), "proof")

    def test_stop_reason_close_match_suggested(self) -> None:
        self.assertEqual(rt.suggest_stop_reason("proof_fond"), "proof_found")
        self.assertEqual(
            rt.suggest_stop_reason("formal_open_ledgerr"), "formal_open_ledger"
        )

    def test_stop_reason_no_suggestion_for_unrelated(self) -> None:
        self.assertIsNone(rt.suggest_stop_reason("celebrate loudly"))
        self.assertIsNone(rt.suggest_stop_reason(""))
        self.assertIsNone(rt.suggest_stop_reason(None))

    def test_suggestion_never_aliases_the_gate(self) -> None:
        # The verdict forbids stop_reason aliasing: a suggested token must not
        # make the raw value acceptable to the gate itself.
        raw = "proof: lemma closed"
        self.assertEqual(rt.suggest_stop_reason(raw), "proof")
        self.assertFalse(rt.is_success_stop_reason(raw))

    def test_artifact_type_suggestions(self) -> None:
        self.assertEqual(rt.suggest_artifact_type("lean4"), "lean")
        self.assertEqual(rt.suggest_artifact_type("python"), "python-verifier")
        self.assertIsNone(rt.suggest_artifact_type("zzz"))
        self.assertIsNone(rt.suggest_artifact_type(""))

    def test_early_stop_contract_shape(self) -> None:
        contract = rt.early_stop_contract()
        self.assertEqual(
            contract["accepted_stop_reasons"], sorted(rt.SUCCESS_STOP_REASONS)
        )
        self.assertEqual(
            contract["accepted_artifact_types"], sorted(rt.PROOF_ARTIFACT_TYPES)
        )
        self.assertIn("formal_open_ledger", contract["honest_negative_stop_reason"])
        self.assertIn("stage-proof", contract["evidence_requirement"])
        example = contract["expected_proof_artifact"]
        self.assertIn(example["artifact_type"], rt.PROOF_ARTIFACT_TYPES)
        self.assertEqual(example["checker"], {"name": "lake", "status": "passed"})

    def test_guard_error_carries_payload(self) -> None:
        exc = rt.GuardError("boom", accepted=["a", "b"])
        self.assertEqual(str(exc), "boom")
        self.assertEqual(exc.payload, {"accepted": ["a", "b"]})
        self.assertIsInstance(exc, ValueError)


class ComputeRunAliasTests(unittest.TestCase):
    def test_backend_synonym_and_completed_alias(self) -> None:
        parsed = rt.parse_compute_runs(['{"backend": "modal", "status": "completed"}'])
        self.assertEqual(parsed["services"][0]["service"], "modal")
        self.assertEqual(parsed["services"][0]["status"], "succeeded")
        self.assertEqual(parsed["usage"], "modal")

    def test_service_key_wins_over_backend(self) -> None:
        parsed = rt.parse_compute_runs(
            ['{"service": "local", "backend": "modal", "status": "succeeded"}']
        )
        self.assertEqual(parsed["services"][0]["service"], "local")

    def test_unknown_service_lists_recognized_keys(self) -> None:
        with self.assertRaisesRegex(ValueError, "recognized --compute-run keys"):
            rt.parse_compute_runs(['{"service": "mainframe", "status": "succeeded"}'])

    def test_invalid_status_lists_accepted_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "compute run status must be one of"):
            rt.parse_compute_runs(['{"service": "local", "status": "victorious"}'])


class EarlyStopGuardCliTests(unittest.TestCase):
    def test_bad_stop_reason_failure_carries_contract_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            _init(loop)
            res = _append(loop, "stop", stop_reason="done it")
            self.assertEqual(res.returncode, 1, res.stdout)
            body = _out(res)
            self.assertIn(
                "early stop before max_iterations requires a success/proof stop_reason",
                body["error"],
            )
            self.assertEqual(
                body["accepted_stop_reasons"], sorted(rt.SUCCESS_STOP_REASONS)
            )
            self.assertEqual(
                body["accepted_artifact_types"], sorted(rt.PROOF_ARTIFACT_TYPES)
            )
            self.assertIn("stage-proof", body["evidence_requirement"])
            self.assertIn("formal_open_ledger", body["honest_negative_stop_reason"])
            self.assertIn("artifact_type", body["expected_proof_artifact"])

    def test_did_you_mean_stop_reason_in_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            _init(loop)
            res = _append(loop, "stop", stop_reason="proof: lemma closed")
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn("did you mean 'proof'", _out(res)["error"])

    def test_formal_open_ledger_needs_host_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            _init(loop)
            res = _append(loop, "stop", stop_reason="formal_open_ledger")
            self.assertEqual(res.returncode, 1, res.stdout)
            body = _out(res)
            self.assertIn("terminal_state=open_ledger", body["error"])
            self.assertIn("accepted_stop_reasons", body)


class DryRunTests(unittest.TestCase):
    def test_dry_run_reports_record_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            _init(loop)
            before = _snapshot(loop)
            res = _append(loop, "continue", dry_run=True)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            body = _out(res)
            self.assertIs(body["dry_run"], True)
            self.assertIs(body["would_stage"], False)
            self.assertEqual(body["would_append"]["iteration"], 1)
            self.assertEqual(body["would_append"]["decision"], "continue")
            self.assertEqual(_snapshot(loop), before)
            self.assertEqual((loop / "iterations.jsonl").read_bytes(), b"")

    def test_dry_run_guard_failure_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            _init(loop)
            before = _snapshot(loop)
            res = _append(loop, "stop", stop_reason="done it", dry_run=True)
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertEqual(_snapshot(loop), before)

    def test_dry_run_refused_under_host_mediated_submission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            _init(loop)
            res = _append(
                loop,
                "continue",
                dry_run=True,
                env_extra={"AAS_AUTOLOOP_HOST_MEDIATED_SUBMISSION": "1"},
            )
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn(
                "unavailable under host-mediated submission", _out(res)["error"]
            )


class StageProofTests(unittest.TestCase):
    def _stage(
        self,
        loop: Path,
        source: Path,
        *,
        evidence_id: str = "proof-1",
        artifact_type: str = "lean",
        checker_status: str = "passed",
    ) -> subprocess.CompletedProcess[str]:
        return _run(
            "stage-proof",
            "--dir",
            str(loop),
            "--id",
            evidence_id,
            "--file",
            str(source),
            "--artifact-type",
            artifact_type,
            "--target",
            "theorem improvement_batch_ok",
            "--checker-name",
            "lake",
            "--checker-status",
            checker_status,
        )

    def test_stage_validate_append_stop_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            _init(loop)
            source = base / "final.lean"
            payload = b"theorem improvement_batch_ok : True := trivial\n"
            source.write_bytes(payload)

            staged = self._stage(loop, source)
            self.assertEqual(staged.returncode, 0, staged.stdout + staged.stderr)
            body = _out(staged)
            self.assertEqual(body["errors"], [])
            self.assertEqual(body["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual((loop / str(body["proof_path"])).read_bytes(), payload)
            record = _read_json(loop, str(body["artifact_path"]))
            self.assertEqual(record["staged_from"], str(source.resolve()))

            checked = _run(
                "validate-proof-artifact",
                "--dir",
                str(loop),
                "--evidence-id",
                "proof-1",
            )
            self.assertEqual(checked.returncode, 0, checked.stdout)
            self.assertEqual(_out(checked)["errors"], [])

            stopped = _append(
                loop, "stop", stop_reason="proof", evidence_id="proof-1"
            )
            self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
            validated = _run("validate", "--dir", str(loop))
            self.assertEqual(validated.returncode, 0, validated.stdout)
            self.assertEqual(_out(validated)["errors"], [])

    def test_stage_proof_id_collision_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            _init(loop)
            source = base / "final.lean"
            source.write_text("theorem t : True := trivial\n", encoding="utf-8")
            self.assertEqual(self._stage(loop, source).returncode, 0)
            res = self._stage(loop, source)
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn("already exists", _out(res)["error"])

    def test_stage_proof_artifact_type_suggestion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            _init(loop)
            source = base / "final.lean"
            source.write_text("theorem t : True := trivial\n", encoding="utf-8")
            res = self._stage(loop, source, artifact_type="lean4")
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn("did you mean 'lean'", _out(res)["error"])

    def test_stage_proof_source_named_like_record_is_renamed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            _init(loop)
            source = base / "proof-1.json"
            source.write_text('{"kind": "external certificate"}\n', encoding="utf-8")
            res = self._stage(loop, source, artifact_type="external-verifier")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            body = _out(res)
            self.assertNotEqual(body["proof_path"], body["artifact_path"])
            self.assertTrue(str(body["proof_path"]).endswith("proof-1__proof-1.json"))
            self.assertEqual(body["errors"], [])

    def test_shared_basename_sources_stage_under_distinct_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            _init(loop)
            # Lean sources routinely share a basename across artifacts.
            staged_paths: dict[str, bytes] = {}
            for index in (1, 2):
                source_dir = base / f"artifact-{index}"
                source_dir.mkdir()
                source = source_dir / "Proof.lean"
                payload = f"theorem t{index} : True := trivial\n".encode()
                source.write_bytes(payload)
                res = self._stage(loop, source, evidence_id=f"proof-{index}")
                self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
                body = _out(res)
                self.assertEqual(body["errors"], [])
                proof_path = str(body["proof_path"])
                self.assertTrue(proof_path.endswith(f"proof-{index}__Proof.lean"))
                staged_paths[proof_path] = payload
            self.assertEqual(len(staged_paths), 2)
            for proof_path, payload in staged_paths.items():
                self.assertEqual((loop / proof_path).read_bytes(), payload)
            for index in (1, 2):
                checked = _run(
                    "validate-proof-artifact",
                    "--dir",
                    str(loop),
                    "--evidence-id",
                    f"proof-{index}",
                )
                self.assertEqual(checked.returncode, 0, checked.stdout)
                self.assertEqual(_out(checked)["errors"], [])

    def test_non_passed_checker_status_fails_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            _init(loop)
            source = base / "final.lean"
            source.write_text("theorem t : True := trivial\n", encoding="utf-8")
            res = self._stage(loop, source, checker_status="failed")
            body = _out(res)
            self.assertEqual(body["status"], "failed")
            self.assertTrue(body["errors"], body)

    def test_validate_proof_artifact_missing_reports_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            _init(loop)
            res = _run(
                "validate-proof-artifact",
                "--dir",
                str(loop),
                "--evidence-id",
                "no-such-artifact",
            )
            self.assertEqual(res.returncode, 1, res.stdout)
            body = _out(res)
            self.assertEqual(body["status"], "failed")
            self.assertTrue(body["errors"])
            self.assertIn("expected_proof_artifact", body)


class FormalOpenLedgerValidateTests(unittest.TestCase):
    def test_append_and_validate_share_the_open_ledger_exemption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            _init(loop, extra=("--formal-policy", "on"))
            terminal = loop / "formal" / "terminal_state.json"
            terminal.parent.mkdir(parents=True, exist_ok=True)
            terminal.write_text(
                json.dumps({"terminal_state": "open_ledger"}), encoding="utf-8"
            )
            res = _append(loop, "stop", stop_reason="formal_open_ledger")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            validated = _run("validate", "--dir", str(loop))
            self.assertEqual(validated.returncode, 0, validated.stdout)
            self.assertEqual(_out(validated)["errors"], [])

    def test_validate_rejects_open_ledger_stop_without_host_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            _init(loop, extra=("--formal-policy", "on"))
            terminal = loop / "formal" / "terminal_state.json"
            terminal.parent.mkdir(parents=True, exist_ok=True)
            terminal.write_text(
                json.dumps({"terminal_state": "open_ledger"}), encoding="utf-8"
            )
            res = _append(loop, "stop", stop_reason="formal_open_ledger")
            self.assertEqual(res.returncode, 0, res.stdout)
            # An agent deleting the host verdict must not leave validate green.
            terminal.unlink()
            validated = _run("validate", "--dir", str(loop))
            self.assertEqual(validated.returncode, 1, validated.stdout)
            self.assertIn(
                "iteration 1 early stop with stop_reason formal_open_ledger "
                "requires a host-authored formal/terminal_state.json with "
                "terminal_state=open_ledger",
                _out(validated)["errors"],
            )

    def test_validate_mirrors_the_formal_track_success_verdict_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            _init(loop, extra=("--formal-policy", "on"))
            state = _read_json(loop, "loop_state.json")
            state["next_preferred_path"] = "formal-track: build the Lean artifact"
            (loop / "loop_state.json").write_text(
                json.dumps(state, indent=2) + "\n", encoding="utf-8"
            )
            source = base / "final.lean"
            source.write_bytes(b"theorem improvement_batch_ok : True := trivial\n")
            staged = _run(
                "stage-proof",
                "--dir",
                str(loop),
                "--id",
                "proof-1",
                "--file",
                str(source),
                "--artifact-type",
                "lean",
                "--target",
                "theorem improvement_batch_ok",
                "--checker-name",
                "lake",
                "--checker-status",
                "passed",
            )
            self.assertEqual(staged.returncode, 0, staged.stdout + staged.stderr)
            terminal = loop / "formal" / "terminal_state.json"
            terminal.parent.mkdir(parents=True, exist_ok=True)
            terminal.write_text(
                json.dumps({"terminal_state": "sorry_free_artifact"}),
                encoding="utf-8",
            )
            res = _append(loop, "stop", stop_reason="proof", evidence_id="proof-1")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            validated = _run("validate", "--dir", str(loop))
            self.assertEqual(validated.returncode, 0, validated.stdout)
            # An agent deleting the host verdict must not leave validate green.
            terminal.unlink()
            validated = _run("validate", "--dir", str(loop))
            self.assertEqual(validated.returncode, 1, validated.stdout)
            self.assertIn(
                "iteration 1 early success stop on a formal-track run requires "
                "a host-authored formal/terminal_state.json with "
                "terminal_state=sorry_free_artifact",
                _out(validated)["errors"],
            )


class RetractIterationTests(unittest.TestCase):
    def test_retract_only_record_restores_initialized_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop)
            self.assertEqual(_append(loop, "continue").returncode, 0)
            res = _retract(loop, reg, reason="stray append during smoke")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            body = _out(res)
            self.assertEqual(body["iteration"], 1)
            self.assertEqual(
                body["restored"],
                {"last_iteration": 0, "loop_status": "initialized", "spent_iterations": 0},
            )
            self.assertEqual((loop / "iterations.jsonl").read_bytes(), b"")
            state = _read_json(loop, "loop_state.json")
            self.assertEqual(state["status"], "initialized")
            self.assertEqual(state["last_iteration"], 0)
            self.assertEqual(_read_json(loop, "budget.json")["spent_iterations"], 0)
            validated = _run("validate", "--dir", str(loop))
            self.assertEqual(validated.returncode, 0, validated.stdout)
            audit = [
                json.loads(line)
                for line in (loop / "retractions.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertEqual(len(audit), 1)
            self.assertEqual(audit[0]["action"], "retract")
            self.assertEqual(audit[0]["reason"], "stray append during smoke")
            self.assertEqual(audit[0]["record"]["iteration"], 1)

    def test_retract_restores_previous_record_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop)
            self.assertEqual(_append(loop, "continue").returncode, 0)
            self.assertEqual(_append(loop, "revise").returncode, 0)
            res = _retract(loop, reg)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            body = _out(res)
            self.assertEqual(body["iteration"], 2)
            self.assertEqual(
                body["restored"],
                {"last_iteration": 1, "loop_status": "running", "spent_iterations": 1},
            )
            validated = _run("validate", "--dir", str(loop))
            self.assertEqual(validated.returncode, 0, validated.stdout)

    def test_retract_empty_ledger_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop)
            res = _retract(loop, reg)
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn("nothing to retract", _out(res)["error"])

    def test_retract_after_terminal_status_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop, max_iterations=1)
            # A stop at the final allowed iteration is a legitimate boundary stop.
            self.assertEqual(_append(loop, "stop").returncode, 0)
            res = _retract(loop, reg)
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn("terminal records are permanent", _out(res)["error"])

    def test_retract_refused_under_goal_focus_enforce(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            init = _run(
                "init",
                "--dir",
                str(loop),
                "--goal",
                "enforce refusal test",
                "--success-criteria",
                "never retracts",
                "--max-iterations",
                "3",
            )
            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            res = _retract(loop, reg)
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn("Goal-Focus enforce", _out(res)["error"])

    def test_retract_refused_under_host_mediated_submission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop)
            res = _retract(
                loop,
                reg,
                env_extra={"AAS_AUTOLOOP_HOST_MEDIATED_SUBMISSION": "1"},
            )
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn("the host owns the ledger", _out(res)["error"])

    def test_retract_refused_while_loop_is_armed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop)
            self.assertEqual(_append(loop, "continue").returncode, 0)
            active = reg / "active.d"
            active.mkdir(parents=True)
            (active / "armed.json").write_text(
                json.dumps({"loop_dir": str(loop.resolve())}), encoding="utf-8"
            )
            res = _retract(loop, reg)
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn("loop is armed", _out(res)["error"])

    @unittest.skipUnless(
        os.name == "posix" and os.geteuid() != 0,
        "requires POSIX permissions that bind for the current user",
    )
    def test_unreadable_registry_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop)
            self.assertEqual(_append(loop, "continue").returncode, 0)
            active = reg / "active.d"
            active.mkdir(parents=True)
            active.chmod(0)
            try:
                res = _retract(loop, reg)
            finally:
                active.chmod(stat.S_IRWXU)
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn(
                "could not prove the loop is disarmed", _out(res)["error"]
            )

    def test_per_iteration_retraction_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop)
            for _ in range(rt.RETRACTIONS_PER_ITERATION_CAP):
                self.assertEqual(_append(loop, "continue").returncode, 0)
                self.assertEqual(_retract(loop, reg).returncode, 0)
            self.assertEqual(_append(loop, "continue").returncode, 0)
            res = _retract(loop, reg)
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn("already been retracted", _out(res)["error"])

    def test_rotated_shard_tail_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop)
            self.assertEqual(_append(loop, "continue").returncode, 0)
            live = loop / "iterations.jsonl"
            (loop / "iterations.0001.jsonl").write_bytes(live.read_bytes())
            live.write_bytes(b"")
            res = _retract(loop, reg)
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn("rotated", _out(res)["error"])

    def test_retract_rolls_back_token_and_usd_spend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop)
            self.assertEqual(
                _append(loop, "continue", tokens=120, usd=0.5).returncode, 0
            )
            budget = _read_json(loop, "budget.json")
            self.assertEqual(budget["spent_tokens"], 120)
            self.assertEqual(budget["spent_usd"], 0.5)
            self.assertEqual(_retract(loop, reg).returncode, 0)
            budget = _read_json(loop, "budget.json")
            self.assertEqual(budget["spent_tokens"], 0)
            self.assertEqual(budget["spent_usd"], 0.0)

    def test_retract_refused_while_host_dispatch_is_in_flight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop)
            self.assertEqual(_append(loop, "continue").returncode, 0)
            before = _snapshot(loop)
            (loop / "iteration_dispatch.json").write_text(
                json.dumps({"dispatch_id": "dispatch-in-flight"}),
                encoding="utf-8",
            )
            # A bare dispatch file already fails closed at mode resolution
            # (partial Goal-Focus authority), before the dedicated guard.
            res = _retract(loop, reg)
            self.assertEqual(res.returncode, 1, res.stdout)
            self.assertIn("iteration_dispatch.json", _out(res)["error"])
            self.assertEqual(_snapshot(loop), before)
            # The dedicated guard still refuses even when mode resolution
            # reports the loop as not enforced.
            args = argparse.Namespace(
                dir=str(loop), reason="attempt during dispatch", registry_dir=str(reg)
            )
            clean = {
                key: value
                for key, value in os.environ.items()
                if key != "AAS_AUTOLOOP_HOST_MEDIATED_SUBMISSION"
            }
            with mock.patch.dict(os.environ, clean, clear=True), mock.patch.object(
                rt, "goal_focus_is_enforced", return_value=False
            ):
                with self.assertRaisesRegex(
                    ValueError, "host dispatch intent is in flight"
                ):
                    rt.retract_iteration_command(args)
            self.assertEqual(_snapshot(loop), before)

    def test_failed_validation_rolls_back_byte_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop)
            self.assertEqual(_append(loop, "continue").returncode, 0)
            before = _snapshot(loop)
            args = argparse.Namespace(
                dir=str(loop), reason="forced failure", registry_dir=str(reg)
            )
            clean = {
                key: value
                for key, value in os.environ.items()
                if key != "AAS_AUTOLOOP_HOST_MEDIATED_SUBMISSION"
            }
            with mock.patch.dict(os.environ, clean, clear=True), mock.patch.object(
                rt,
                "validate_loop_dir",
                return_value={"status": "failed", "errors": ["forced test failure"]},
            ):
                with self.assertRaisesRegex(
                    ValueError, "loop did not validate after retraction"
                ):
                    rt.retract_iteration_command(args)
            self.assertEqual(_snapshot(loop), before)
            audit = [
                json.loads(line)
                for line in (loop / "retractions.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertEqual([entry["action"] for entry in audit], ["retract", "rollback"])
            self.assertIn("forced test failure", audit[1]["error"])


class IntegrityUnitTests(unittest.TestCase):
    def test_ledger_watch_accepts_pure_appends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "iterations.jsonl"
            path.write_bytes(b'{"iteration": 1}\n')
            watch = rt.LedgerIntegrityWatch(path)
            with path.open("ab") as handle:
                handle.write(b'{"iteration": 2}\n')
            self.assertIsNone(watch.check())
            self.assertEqual(watch.violations, [])

    def test_ledger_watch_flags_truncation_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "iterations.jsonl"
            path.write_bytes(b'{"iteration": 1}\n{"iteration": 2}\n')
            watch = rt.LedgerIntegrityWatch(path)
            path.write_bytes(b'{"iteration": 1}\n')
            violation = watch.check()
            self.assertIsNotNone(violation)
            self.assertEqual(violation["kind"], "truncated")
            # Re-baselined: the same state does not produce a second violation.
            self.assertIsNone(watch.check())
            self.assertEqual(len(watch.violations), 1)

    def test_ledger_watch_flags_rewritten_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "iterations.jsonl"
            path.write_bytes(b'{"iteration": 1, "summary": "aaaa"}\n')
            watch = rt.LedgerIntegrityWatch(path)
            path.write_bytes(b'{"iteration": 1, "summary": "bbbb"}\n')
            violation = watch.check()
            self.assertIsNotNone(violation)
            self.assertEqual(violation["kind"], "rewritten")

    def test_ledger_watch_tolerates_host_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "iterations.jsonl"
            row_a = b'{"iteration": 1, "summary": "aaaa"}\n'
            row_b = b'{"iteration": 2, "summary": "bbbb"}\n'
            row_c = b'{"iteration": 3, "summary": "cccc"}\n'
            path.write_bytes(row_a + row_b)
            watch = rt.LedgerIntegrityWatch(path)
            # A host rotation moves banked rows into a shard; the logical
            # stream (shards then live file) is unchanged, so no violation.
            (root / "iterations.1.jsonl").write_bytes(row_a + row_b)
            path.write_bytes(row_c)
            self.assertIsNone(watch.check())
            self.assertEqual(watch.violations, [])
            # Tampering inside the rotated shard still trips the watch.
            (root / "iterations.1.jsonl").write_bytes(row_a)
            violation = watch.check()
            self.assertIsNotNone(violation)
            self.assertEqual(violation["kind"], "truncated")

    def test_build_config_watch_dedupes_and_tracks_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            project.mkdir()
            lakefile = project / "lakefile.lean"
            lakefile.write_text("-- original\n", encoding="utf-8")
            watch = rt.BuildConfigWatch([project, project])
            self.assertEqual(len(watch.dirs), 1)
            self.assertEqual(watch.check(), [])

            lakefile.write_text("-- rewritten\n", encoding="utf-8")
            changes = watch.check()
            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0]["change"], "modified")

            (project / "lean-toolchain").write_text(
                "leanprover/lean4:v4.0\n", encoding="utf-8"
            )
            added = watch.check()
            self.assertEqual([item["change"] for item in added], ["added"])

            lakefile.unlink()
            removed = watch.check()
            self.assertEqual([item["change"] for item in removed], ["removed"])
            # Re-baselined after each report: steady state is quiet.
            self.assertEqual(watch.check(), [])

    def test_ledger_consistency_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            _init(loop)
            self.assertEqual(_append(loop, "continue").returncode, 0)
            self.assertEqual(rt._ledger_consistency_errors(loop), [])

            budget = _read_json(loop, "budget.json")
            budget["spent_iterations"] = int(budget["spent_iterations"]) + 1
            (loop / "budget.json").write_text(json.dumps(budget), encoding="utf-8")
            errors = rt._ledger_consistency_errors(loop)
            self.assertTrue(
                any("does not equal the iterations.jsonl record count" in e for e in errors),
                errors,
            )

            budget["spent_iterations"] = 1
            (loop / "budget.json").write_text(json.dumps(budget), encoding="utf-8")
            state = _read_json(loop, "loop_state.json")
            state["last_iteration"] = 5
            (loop / "loop_state.json").write_text(json.dumps(state), encoding="utf-8")
            errors = rt._ledger_consistency_errors(loop)
            self.assertTrue(
                any("does not match the newest ledger record" in e for e in errors),
                errors,
            )

    def test_integrity_snapshot_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            path = Path(
                rt._write_integrity_snapshot(
                    log_dir, "ledger", {"kind": "truncated"}, b'{"iteration": 1}\n'
                )
            )
            self.assertTrue(path.is_file())
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(body["kind"], "truncated")
            self.assertIn('{"iteration": 1}', body["ledger_snapshot_utf8"])
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


@unittest.skipUnless(os.name == "posix", "symlink planting requires POSIX")
class TerminalStateWriteHardeningTests(unittest.TestCase):
    def test_planted_symlink_cannot_redirect_the_verdict_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_dir = base / "loop"
            formal = run_dir / "formal"
            formal.mkdir(parents=True)
            victim = base / "victim.txt"
            victim.write_text("host-owned bytes\n", encoding="utf-8")
            (formal / "terminal_state.json").symlink_to(victim)
            fp._write_terminal_state(run_dir, {"terminal_state": "open_ledger"})
            self.assertEqual(
                victim.read_text(encoding="utf-8"), "host-owned bytes\n"
            )
            final = formal / "terminal_state.json"
            self.assertFalse(final.is_symlink())
            self.assertEqual(
                json.loads(final.read_text(encoding="utf-8"))["terminal_state"],
                "open_ledger",
            )
            # No stray temp files remain after the atomic swap.
            leftovers = [
                p.name for p in formal.iterdir() if p.name != "terminal_state.json"
            ]
            self.assertEqual(leftovers, [])


class PromptContractTests(unittest.TestCase):
    def test_binding_block_declares_build_config_host_owned(self) -> None:
        self.assertIn("Build configuration is host-owned", fp.BINDING_BLOCK)
        for name in ("lakefile.lean", "lakefile.toml", "lake-manifest.json", "lean-toolchain"):
            self.assertIn(name, fp.BINDING_BLOCK)

    def test_parked_block_declares_build_config_host_owned(self) -> None:
        self.assertIn("host-owned; never rewrite them", fp.PARKED_BLOCK)

    def test_early_stop_contract_block_names_the_helpers(self) -> None:
        for token in (
            "stage-proof",
            "validate-proof-artifact",
            "--dry-run",
            "formal_open_ledger",
        ):
            self.assertIn(token, fp.EARLY_STOP_CONTRACT_BLOCK)

    def test_prompt_addon_appends_contract_for_on_and_force(self) -> None:
        parked = fp.formal_policy_prompt_addon(None, cli={"policy": "on"})
        self.assertIn("Early-stop evidence contract", parked)
        self.assertIn("Formal policy (parked)", parked)
        forced = fp.formal_policy_prompt_addon(None, cli={"policy": "force"})
        self.assertIn("Early-stop evidence contract", forced)
        self.assertIn("Build configuration is host-owned", forced)

    def test_mention_only_addon_stays_light(self) -> None:
        addon = fp.formal_policy_prompt_addon(None, cli={"policy": "mention-only"})
        self.assertIn("Formal policy (mention-only)", addon)
        self.assertNotIn("Early-stop evidence contract", addon)


@unittest.skipUnless(
    _primary_containment_available(),
    "driving real iterations requires a working primary containment",
)
class DriveIntegrityTests(unittest.TestCase):
    """Offline drive runs with scripted iteration commands exercising IG-1/2/3."""

    HONEST_STEP = (
        "import json, os\n"
        "d = os.environ['AUTOLOOP_DIR']\n"
        "def honest():\n"
        "    bp = os.path.join(d, 'budget.json')\n"
        "    b = json.load(open(bp))\n"
        "    n = int(b.get('spent_iterations', 0)) + 1\n"
        "    b['spent_iterations'] = n\n"
        "    json.dump(b, open(bp, 'w'))\n"
        "    rec = {'iteration': n, 'decision': 'continue',\n"
        "           'summary': 'honest %d' % n, 'evidence_ids': []}\n"
        "    with open(os.path.join(d, 'iterations.jsonl'), 'a') as h:\n"
        "        h.write(json.dumps(rec) + '\\n')\n"
        "    sp = os.path.join(d, 'loop_state.json')\n"
        "    s = json.load(open(sp))\n"
        "    s['last_iteration'] = n\n"
        "    json.dump(s, open(sp, 'w'))\n"
        "    return n\n"
    )

    def _drive(
        self,
        loop: Path,
        registry: Path,
        script: str,
        *extra: str,
        timeout: int = 90,
        env_extra: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        script_path = loop.parent / "iteration_step.py"
        script_path.write_text(script, encoding="utf-8")
        cmd = f'"{sys.executable}" "{script_path}"'
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(HELPER),
                "drive",
                "--dir",
                str(loop),
                "--root",
                str(loop),
                "--cmd",
                cmd,
                *extra,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            env=_env({"AAS_AUTOLOOP_REGISTRY": str(registry), **(env_extra or {})}),
            check=False,
        )

    def test_rewritten_ledger_prefix_fails_the_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop, max_iterations=5)
            script = self.HONEST_STEP + (
                "n = honest()\n"
                "if n == 2:\n"
                "    lp = os.path.join(d, 'iterations.jsonl')\n"
                "    text = open(lp).read().replace('honest 1', 'redacted')\n"
                "    open(lp, 'w').write(text)\n"
            )
            res = self._drive(loop, reg, script, "--max-failures", "1")
            self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
            body = _out(res)
            integrity = body["integrity"]
            self.assertIs(integrity["clean"], False)
            self.assertEqual(len(integrity["ledger_violations"]), 1, integrity)
            self.assertEqual(integrity["ledger_violations"][0]["kind"], "rewritten")
            self.assertEqual(len(integrity["snapshots"]), 1)
            self.assertTrue(Path(integrity["snapshots"][0]).is_file())
            self.assertIn("iterations.jsonl history changed", res.stderr)

    def test_budget_bump_without_record_is_desync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop, max_iterations=5)
            script = (
                "import json, os\n"
                "d = os.environ['AUTOLOOP_DIR']\n"
                "bp = os.path.join(d, 'budget.json')\n"
                "b = json.load(open(bp))\n"
                "b['spent_iterations'] = int(b.get('spent_iterations', 0)) + 1\n"
                "json.dump(b, open(bp, 'w'))\n"
            )
            res = self._drive(loop, reg, script, "--max-failures", "1")
            self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
            integrity = _out(res)["integrity"]
            self.assertIs(integrity["clean"], False)
            self.assertTrue(integrity["ledger_desync_events"], integrity)
            self.assertTrue(
                any(
                    "does not equal the iterations.jsonl record count" in error
                    for event in integrity["ledger_desync_events"]
                    for error in event["errors"]
                ),
                integrity,
            )

    def test_build_config_change_recorded_without_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop, max_iterations=5, extra=("--formal-policy", "on"))
            (loop / "lakefile.lean").write_text("-- host-owned\n", encoding="utf-8")
            script = self.HONEST_STEP + (
                "n = honest()\n"
                "if n == 1:\n"
                "    with open(os.path.join(d, 'lakefile.lean'), 'a') as h:\n"
                "        h.write('-- agent edit\\n')\n"
                "if n >= 2:\n"
                "    open(os.path.join(d, 'STOP_REQUESTED'), 'w').write('stop')\n"
            )
            res = self._drive(loop, reg, script)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            integrity = _out(res)["integrity"]
            self.assertIs(integrity["build_config_lock"], False)
            self.assertTrue(integrity["build_config_changes"], integrity)
            self.assertEqual(integrity["build_config_changes"][0]["change"], "modified")
            self.assertIn("lakefile.lean", integrity["build_config_changes"][0]["path"])
            self.assertIs(integrity["clean"], False)

    def test_graceful_stop_reports_a_computed_formal_verdict(self) -> None:
        # drive_stop's formal_terminal_state must be a host-computed verdict;
        # the empty string only ever comes from the shutdown exception
        # fallback, which a green suite would otherwise never distinguish.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop, max_iterations=5, extra=("--formal-policy", "on"))
            proj = loop / "formal"
            proj.mkdir(exist_ok=True)
            (proj / "lakefile.lean").write_text("-- host-owned\n", encoding="utf-8")
            (proj / "Demo.lean").write_text(
                "theorem demo : 1 = 1 := sorry\n", encoding="utf-8"
            )
            script = self.HONEST_STEP + (
                "n = honest()\n"
                "if n >= 2:\n"
                "    open(os.path.join(d, 'STOP_REQUESTED'), 'w').write('stop')\n"
            )
            # The sorry above fails the gate's scan, which alone forces
            # open_ledger on every host — the gate only typechecks a clean
            # scan, so the typecheck leg never runs here. Pinning both tool
            # envs to a missing path is defense-in-depth: even a scan-clean
            # fixture could never invoke a real toolchain.
            missing = str(base / "missing-toolchain")
            res = self._drive(
                loop,
                reg,
                script,
                env_extra={"AAS_LEAN": missing, "AAS_LAKE": missing},
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            body = _out(res)
            events = [
                json.loads(line)
                for line in Path(str(body["progress_jsonl"]))
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            stops = [e for e in events if e.get("event") == "drive_stop"]
            self.assertEqual(len(stops), 1, [e.get("event") for e in events])
            self.assertEqual(stops[0].get("terminal_reason"), "done")
            self.assertIn(
                stops[0].get("formal_terminal_state"), fp.TERMINAL_STATES, stops[0]
            )
            self.assertEqual(stops[0].get("formal_terminal_state"), "open_ledger")
            verdict = json.loads(
                (proj / "terminal_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(verdict["terminal_state"], "open_ledger")
            self.assertIn(
                "active_placeholder", {o["kind"] for o in verdict["obligations"]}
            )

    def test_build_config_lock_fails_the_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop, reg = base / "loop", base / "reg"
            _init(loop, max_iterations=5, extra=("--formal-policy", "on"))
            (loop / "lakefile.lean").write_text("-- host-owned\n", encoding="utf-8")
            script = self.HONEST_STEP + (
                "n = honest()\n"
                "if n == 1:\n"
                "    with open(os.path.join(d, 'lakefile.lean'), 'a') as h:\n"
                "        h.write('-- agent edit\\n')\n"
            )
            res = self._drive(
                loop, reg, script, "--build-config-lock", "--max-failures", "1"
            )
            self.assertNotEqual(res.returncode, 0, res.stdout + res.stderr)
            integrity = _out(res)["integrity"]
            self.assertIs(integrity["build_config_lock"], True)
            self.assertTrue(integrity["build_config_changes"], integrity)


if __name__ == "__main__":
    unittest.main()
