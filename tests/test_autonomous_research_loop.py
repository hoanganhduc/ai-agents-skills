from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from installer.ai_agents_skills.agents import detect_agents, target_for
from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.planner import build_plan
from installer.ai_agents_skills.runtime_smoke import (
    runtime_command_target,
    selected_runtime_skills,
    validate_smoke_output,
)
from installer.ai_agents_skills.verify import verify


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "autonomous-research-loop-runtime"
    / "autonomous_research_loop_runtime.py"
)
SUPERVISOR = HELPER.with_name("arl_drive_supervisor.sh")

_TEST_PROVIDER_FAMILIES = {
    "codex": "openai",
    "claude": "anthropic",
    "grok": "xai",
}


class _ProviderAttestationFixture:
    def __init__(self) -> None:
        safe_parent = Path(os.path.realpath(Path.home()))
        self._temporary = tempfile.TemporaryDirectory(
            prefix=".aas-provider-fixture-", dir=safe_parent
        )
        self.root = Path(self._temporary.name)
        self.paths: dict[str, Path] = {}
        self.environment: dict[str, str] = {}
        python = str(Path(os.path.realpath(sys.executable)))
        for provider, family in _TEST_PROVIDER_FAMILIES.items():
            dependency_root = self.root / "providers" / provider
            dependency_root.mkdir(parents=True, mode=0o700)
            if os.name == "posix":
                (self.root / "providers").chmod(0o700)
                dependency_root.chmod(0o700)
            suffix = ".exe" if os.name == "nt" else ""
            path = dependency_root / f"{provider}{suffix}"
            if os.name == "nt":  # pragma: no cover - Windows CI fixture
                path.write_bytes(Path(python).read_bytes())
            else:
                path.write_text(
                    f"#!/bin/sh\nexec {shlex.quote(python)} \"$@\"\n",
                    encoding="utf-8",
                )
                path.chmod(0o700)
            digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            key = provider.upper()
            self.paths[provider] = path
            self.environment.update(
                {
                    f"AAS_AUTOLOOP_ATTESTED_BIN_{key}": str(path),
                    f"AAS_AUTOLOOP_ATTESTED_SHA256_{key}": digest,
                    f"AAS_AUTOLOOP_ATTESTED_UPSTREAM_{key}": family,
                    f"AAS_AUTOLOOP_ATTESTED_MODEL_{key}": f"{provider}-test-model",
                    f"AAS_AUTOLOOP_ATTESTED_DEPENDENCY_ROOT_{key}": str(
                        dependency_root
                    ),
                }
            )

    def cleanup(self) -> None:
        self._temporary.cleanup()


def _provider_attestation(provider: str, root: Path | None = None) -> dict[str, Any]:
    runtime = HELPER.parent
    if str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
    import panel_parent  # noqa: WPS

    attestation = panel_parent.attest_provider_executable(
        provider,
        required=True,
        forbidden_roots=((root,) if root is not None else ()),
    )
    if attestation is None:  # pragma: no cover - required=True fails instead
        raise AssertionError(f"missing test executable attestation for {provider}")
    return attestation


def _host_execution_attestation(
    candidate_id: str, *, provider: str = "claude"
) -> dict[str, Any]:
    executable = _provider_attestation(provider)
    return {
        "schema_version": "host_execution_attestation.v1",
        "source": "host_dispatch",
        "candidate_id": candidate_id,
        "dispatch_id": f"dispatch-{candidate_id}",
        "evidence_root": f".goal_focus/evidence/{candidate_id}",
        "executor_provider": provider,
        "executor_family": executable["family"],
        "executor_attestation": executable,
    }


def _primary_resource_attestation(*, wall_time_seconds: int = 60) -> dict[str, Any]:
    return {
        "schema_version": "provider_resource_attestation.v1",
        "provider_transport": "trusted-local",
        "role": "primary",
        "resource_gate": "pre-exec-cgroup-rlimit-v1",
        "scope_unit": "aas-arl-primary-1234-feedfacefeed.scope",
        "limits": {
            "wall_time_seconds": wall_time_seconds,
            "runtime_scope_seconds": wall_time_seconds + 15,
            "memory_max_bytes": 4 * 1024 * 1024 * 1024,
            "memory_swap_max_bytes": 0,
            "address_space_bytes": 64 * 1024 * 1024 * 1024,
            "cpu_time_seconds": wall_time_seconds + 60,
            "cpu_quota_percent": 100,
            "tasks_max": 128,
            "open_files_max": 1024,
            "file_size_max_bytes": 4 * 1024 * 1024 * 1024,
            "output_max_bytes": 16_000_000,
            "core_size_max_bytes": 0,
        },
        "output_capture": "bounded-pipe",
        "control_plane_masked": True,
        "cgroup_api_masked": True,
        "cleanup_verified": True,
        "capture_verified": True,
        "timed_out": False,
        "oversized_output": False,
        "sensitive_output_blocked": False,
        "finished_at": "2026-07-29T00:01:00Z",
    }


# Never write __pycache__ into the canonical runtime tree (inventory CI).
sys.dont_write_bytecode = True


def _subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Keep host Telegram/Zulip secrets from auto-notifying (or suppressing watch
    # JSON) during unit tests unless a test explicitly opts in via *extra*.
    # Assigned, not setdefault: an inherited AAS_AUTOLOOP_NOTIFY=zulip would
    # otherwise post every drive/watch event of the suite to a real chat.
    env["AAS_AUTOLOOP_NOTIFY"] = "off"
    if extra:
        env.update(extra)
    return env


def _no_notify(env: dict[str, str]) -> dict[str, str]:
    """Restore the notify guard on an env built by filtering ``AAS_AUTOLOOP_*``.

    Tests that clear the whole prefix to isolate provider-command lookups drop
    the guard by name; without this the run resolves ``--notify auto`` against
    the host secrets and posts to a real chat.
    """
    env["AAS_AUTOLOOP_NOTIFY"] = "off"
    return env


def create_agent_home(root: Path, agent: str) -> None:
    target_for(root, agent).home.mkdir(parents=True, exist_ok=True)


def run_helper(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(HELPER), *args],
        capture_output=True,
        text=True,
        timeout=20,
        check=check,
        env=_subprocess_env(),
    )


def init_loop(run_dir: Path, *, max_iterations: int = 2) -> None:
    run_helper(
        "init",
        "--dir",
        str(run_dir),
        "--goal",
        "integrate autonomous research loop",
        "--success-criteria",
        "ledger validates",
        "--max-iterations",
        str(max_iterations),
        "--goal-focus-mode",
        "off",
    )


def append_iteration(
    run_dir: Path,
    decision: str,
    *,
    objective: str = "record evidence gate result",
    claim_id: str | None = None,
    evidence_id: str | None = None,
    stop_reason: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [
        "append-iteration",
        "--dir",
        str(run_dir),
        "--mode",
        "bounded-research",
        "--objective",
        objective,
        "--decision",
        decision,
        "--source-id",
        "S1",
        "--guard-ref",
        "G1",
        "--remaining-gap",
        "second pass",
    ]
    if claim_id:
        command.extend(["--claim-id", claim_id])
    if evidence_id:
        command.extend(["--evidence-id", evidence_id])
    if stop_reason:
        command.extend(["--stop-reason", stop_reason])
    return run_helper(*command, check=check)


def read_loop_json(run_dir: Path, filename: str) -> dict[str, object]:
    return json.loads((run_dir / filename).read_text(encoding="utf-8"))


def write_loop_json(run_dir: Path, filename: str, payload: dict[str, object]) -> None:
    (run_dir / filename).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_iterations(run_dir: Path, records: list[dict[str, object]]) -> None:
    (run_dir / "iterations.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def write_proof_artifact(
    run_dir: Path,
    evidence_id: str = "proof-artifact-1",
    *,
    checker_status: str = "passed",
    machine_checkable: bool = True,
    proof_path: str = "proofs/proof.txt",
) -> None:
    proof_file = run_dir / proof_path
    proof_file.parent.mkdir(parents=True, exist_ok=True)
    proof_file.write_text("machine-checkable proof fixture\n", encoding="utf-8", newline="\n")
    artifact_dir = run_dir / "proof_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / f"{evidence_id}.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": evidence_id,
                "artifact_type": "python-verifier",
                "machine_checkable": machine_checkable,
                "target": "test theorem",
                "proof_path": proof_path,
                "checker": {
                    "name": "fixture-checker",
                    "status": checker_status,
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def embedded_evidence_artifacts(
    evidence_ids: list[str], *, candidate_id: str
) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    for evidence_id in evidence_ids:
        content = f"complete staged evidence for {evidence_id}\n"
        payload = content.encode("utf-8")
        artifacts.append(
            {
                "schema_version": "goal_focus_evidence.v1",
                "evidence_id": evidence_id,
                "source_path": (
                    f".goal_focus/evidence/{candidate_id}/{evidence_id}"
                ),
                "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
                "content_encoding": "utf-8",
                "content": content,
            }
        )
    return artifacts


def write_text_evidence(
    run_dir: Path, dispatch: dict[str, Any], evidence_id: str
) -> None:
    path = run_dir / str(dispatch["evidence_root"]) / evidence_id
    path.write_text(
        f"complete staged evidence for {evidence_id}\n", encoding="utf-8"
    )


def iteration_record(
    number: int,
    decision: str,
    *,
    evidence_ids: list[str] | None = None,
    stop_reason: str = "",
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "iteration": number,
        "timestamp": "2026-01-01T00:00:00Z",
        "mode": "bounded-research",
        "objective": f"iteration {number}",
        "input_refs": [],
        "evidence_checked": {
            "source_ids": [],
            "claim_ids": [],
            "evidence_ids": evidence_ids or [],
            "guard_refs": [],
        },
        "actions_taken": [],
        "output": "",
        "remaining_gaps": [],
        "budget_delta": {
            "iterations": 1,
            "tokens": 0,
            "usd": 0.0,
            "wall_time_seconds": 0,
        },
        "decision": decision,
        "stop_reason": stop_reason,
    }


class AutonomousResearchLoopTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink semantics")
    def test_force_init_rejects_planted_direct_output_links_before_mutation(self) -> None:
        mod = _load_arl_runtime()
        cases = (
            "loop_state.json",
            "budget.json",
            "iterations.jsonl",
            "recovery.md",
            "goal_priority.json",
            "proof_artifacts",
            "formal",
            "formal/formal_policy.json",
        )
        core_outputs = (
            "loop_state.json",
            "budget.json",
            "iterations.jsonl",
            "recovery.md",
        )
        for planted_name in cases:
            with self.subTest(planted_name=planted_name), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                loop = base / "loop"
                loop.mkdir(mode=0o700)
                args = mod.selftest_init_args(loop, max_iterations=2)
                args.force = True
                if planted_name == "goal_priority.json":
                    args.goal_priority_template = True
                if planted_name.startswith("formal"):
                    args.formal_policy = "on"

                planted = loop / planted_name
                planted.parent.mkdir(parents=True, exist_ok=True)
                if planted_name in {"proof_artifacts", "formal"}:
                    outside = base / f"outside-{planted.name}"
                    outside.mkdir()
                    sentinel = outside / "sentinel.txt"
                    sentinel.write_bytes(b"outside directory bytes stay unchanged\n")
                    planted.symlink_to(outside, target_is_directory=True)
                else:
                    sentinel = base / f"outside-{planted.name}.txt"
                    sentinel.write_bytes(b"outside file bytes stay unchanged\n")
                    planted.symlink_to(sentinel)
                before = sentinel.read_bytes()

                with self.assertRaises(OSError):
                    mod.init_loop(args)

                self.assertEqual(sentinel.read_bytes(), before)
                self.assertTrue(planted.is_symlink())
                for name in core_outputs:
                    candidate = loop / name
                    if candidate != planted:
                        self.assertFalse(candidate.exists(), candidate)

    @unittest.skipUnless(os.name == "posix", "requires POSIX hardlink semantics")
    def test_force_init_rejects_planted_direct_output_hardlinks_before_mutation(self) -> None:
        mod = _load_arl_runtime()
        file_outputs = (
            "loop_state.json",
            "budget.json",
            "iterations.jsonl",
            "recovery.md",
            "goal_priority.json",
            "formal/formal_policy.json",
        )
        core_outputs = (
            "loop_state.json",
            "budget.json",
            "iterations.jsonl",
            "recovery.md",
        )
        for planted_name in file_outputs:
            with self.subTest(planted_name=planted_name), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                loop = base / "loop"
                loop.mkdir(mode=0o700)
                args = mod.selftest_init_args(loop, max_iterations=2)
                args.force = True
                if planted_name == "goal_priority.json":
                    args.goal_priority_template = True
                if planted_name.startswith("formal/"):
                    args.formal_policy = "on"

                planted = loop / planted_name
                planted.parent.mkdir(parents=True, exist_ok=True)
                outside = base / f"outside-{planted.name}.txt"
                outside.write_bytes(b"outside hardlink bytes stay unchanged\n")
                os.link(outside, planted)
                before = outside.read_bytes()

                with self.assertRaises(OSError):
                    mod.init_loop(args)

                self.assertEqual(outside.read_bytes(), before)
                self.assertEqual(planted.read_bytes(), before)
                self.assertEqual(os.lstat(outside).st_nlink, 2)
                for name in core_outputs:
                    candidate = loop / name
                    if candidate != planted:
                        self.assertFalse(candidate.exists(), candidate)

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink semantics")
    def test_force_init_rejects_symlinked_run_directory_before_resolution(self) -> None:
        mod = _load_arl_runtime()
        for link_kind in ("leaf", "intermediate"):
            with self.subTest(link_kind=link_kind), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                if link_kind == "leaf":
                    target_loop = base / "outside-loop"
                    target_loop.mkdir(mode=0o700)
                    requested = base / "loop-link"
                    requested.symlink_to(target_loop, target_is_directory=True)
                    planted_link = requested
                else:
                    outside_parent = base / "outside-parent"
                    target_loop = outside_parent / "nested-loop"
                    target_loop.mkdir(parents=True, mode=0o700)
                    planted_link = base / "parent-link"
                    planted_link.symlink_to(outside_parent, target_is_directory=True)
                    requested = planted_link / "nested-loop"
                sentinel = target_loop / "sentinel.txt"
                sentinel.write_bytes(b"symlinked run target stays unchanged\n")
                before_entries = {
                    path.relative_to(target_loop): path.read_bytes()
                    for path in target_loop.rglob("*")
                    if path.is_file()
                }
                args = mod.selftest_init_args(requested, max_iterations=2)
                args.force = True

                with self.assertRaises(OSError):
                    mod.init_loop(args)

                self.assertTrue(planted_link.is_symlink())
                self.assertEqual(
                    {
                        path.relative_to(target_loop): path.read_bytes()
                        for path in target_loop.rglob("*")
                        if path.is_file()
                    },
                    before_entries,
                )

    def test_runtime_helper_selftest_is_offline_and_validates_ledger(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-B", str(HELPER), "selftest"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
            env=_subprocess_env(),
        )
        payload = json.loads(completed.stdout)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["smoke_mode"], "offline")
        self.assertFalse(payload["network_required"])
        self.assertFalse(payload["live_api_attempted"])
        self.assertFalse(payload["package_install_attempted"])
        self.assertFalse(payload["server_started"])
        self.assertFalse(payload["config_written"])
        self.assertFalse(payload["provider_cli_attempted"])
        self.assertFalse(payload["subagents_spawned"])
        self.assertTrue(payload["run_dir_created"])
        self.assertEqual(payload["validation_status"], "ok")
        self.assertEqual(payload["iterations"], 1)

    def test_runtime_helper_init_append_validate_status_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            init_loop(run_dir)
            append_iteration(run_dir, "continue")
            validate = subprocess.run(
                [sys.executable, "-B", str(HELPER), "validate", "--dir", str(run_dir)],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
                env=_subprocess_env(),
            )
            status = subprocess.run(
                [sys.executable, "-B", str(HELPER), "status", "--dir", str(run_dir)],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
                env=_subprocess_env(),
            )

            validate_payload = json.loads(validate.stdout)
            status_payload = json.loads(status.stdout)
            self.assertEqual(validate_payload["status"], "ok")
            self.assertEqual(validate_payload["checked"]["iterations"], 1)
            self.assertEqual(status_payload["status"], "ok")
            self.assertEqual(status_payload["state_status"], "running")
            self.assertEqual(status_payload["last_decision"], "continue")

    def test_usd_cli_parsers_reject_non_finite_values_without_writes(self) -> None:
        for raw in ("nan", "inf", "-inf"):
            with self.subTest(command="init", raw=raw), tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp) / "loop"
                rejected = run_helper(
                    "init",
                    "--dir",
                    str(run_dir),
                    "--goal",
                    "G",
                    "--success-criteria",
                    "S",
                    f"--max-usd={raw}",
                    check=False,
                )

                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("finite and non-negative", rejected.stderr)
                self.assertFalse(run_dir.exists())

            with self.subTest(command="append", raw=raw), tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp) / "loop"
                init_loop(run_dir)
                before_budget = (run_dir / "budget.json").read_bytes()
                rejected = run_helper(
                    "append-iteration",
                    "--dir",
                    str(run_dir),
                    "--mode",
                    "bounded-research",
                    "--objective",
                    "reject non-finite cost",
                    "--decision",
                    "continue",
                    f"--usd={raw}",
                    check=False,
                )

                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("finite and non-negative", rejected.stderr)
                self.assertEqual((run_dir / "budget.json").read_bytes(), before_budget)
                self.assertEqual((run_dir / "iterations.jsonl").read_text(), "")

    def test_validate_rejects_non_finite_persisted_usd_budget_fields(self) -> None:
        for field in ("max_usd", "spent_usd"):
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as tmp:
                    run_dir = Path(tmp) / "loop"
                    init_loop(run_dir)
                    budget = read_loop_json(run_dir, "budget.json")
                    budget[field] = value
                    write_loop_json(run_dir, "budget.json", budget)

                    rejected = run_helper("validate", "--dir", str(run_dir), check=False)
                    payload = json.loads(rejected.stdout)

                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertEqual(payload["status"], "failed")
                    self.assertIn(
                        f"budget.json {field} must be a finite non-negative number",
                        payload["errors"],
                    )

    def test_runtime_helper_rejects_final_continue_and_preserves_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            init_loop(run_dir, max_iterations=2)
            append_iteration(run_dir, "continue")

            rejected = run_helper(
                "append-iteration",
                "--dir",
                str(run_dir),
                "--mode",
                "bounded-research",
                "--objective",
                "invalid final continue",
                "--decision",
                "continue",
                check=False,
            )

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("final allowed iteration", rejected.stdout)
            self.assertEqual(len((run_dir / "iterations.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_runtime_helper_rejects_early_stop_without_success_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            init_loop(run_dir, max_iterations=3)

            rejected_reason = append_iteration(
                run_dir,
                "stop",
                objective="premature non-proof stop",
                stop_reason="budget_exhausted",
                evidence_id="E1",
                check=False,
            )
            rejected_evidence = append_iteration(
                run_dir,
                "stop",
                objective="premature proof stop without evidence",
                stop_reason="proof_found",
                check=False,
            )
            rejected_artifact = append_iteration(
                run_dir,
                "stop",
                objective="premature proof stop without proof artifact",
                stop_reason="proof_found",
                evidence_id="missing-proof",
                check=False,
            )

            self.assertNotEqual(rejected_reason.returncode, 0)
            self.assertIn("success/proof stop_reason", rejected_reason.stdout)
            self.assertNotEqual(rejected_evidence.returncode, 0)
            self.assertIn("proof artifact evidence_id", rejected_evidence.stdout)
            self.assertNotEqual(rejected_artifact.returncode, 0)
            self.assertIn("valid proof artifact", rejected_artifact.stdout)
            self.assertEqual((run_dir / "iterations.jsonl").read_text(encoding="utf-8"), "")

    def test_runtime_helper_allows_early_success_stop_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            init_loop(run_dir, max_iterations=3)
            write_proof_artifact(run_dir)

            append_iteration(
                run_dir,
                "stop",
                objective="proof found",
                stop_reason="proof_found",
                evidence_id="proof-artifact-1",
            )

            status = json.loads(run_helper("status", "--dir", str(run_dir)).stdout)
            self.assertEqual(status["state_status"], "stopped")
            self.assertEqual(status["remaining_iterations"], 2)
            validation = json.loads(
                run_helper("validate", "--dir", str(run_dir)).stdout
            )
            self.assertEqual(validation["status"], "ok", validation)

    def test_runtime_helper_rejects_early_blocked_bailout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            init_loop(run_dir, max_iterations=3)

            # An agent must not be able to end the loop early by self-marking it
            # blocked: under the enforcement policy a recorded blocker continues
            # the loop, it does not stop it.
            rejected = append_iteration(
                run_dir,
                "blocked",
                objective="give up midway",
                check=False,
            )

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("early blocked", rejected.stdout)
            self.assertEqual((run_dir / "iterations.jsonl").read_text(encoding="utf-8"), "")

    def test_runtime_helper_allows_blocked_only_at_final_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            init_loop(run_dir, max_iterations=1)

            # blocked is reserved for the final allowed iteration (budget
            # exhausted without success).
            append_iteration(run_dir, "blocked", objective="budget exhausted without success")

            status = json.loads(run_helper("status", "--dir", str(run_dir)).stdout)
            self.assertEqual(status["state_status"], "blocked")

    def test_runtime_helper_rejects_unsafe_proof_evidence_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            init_loop(run_dir, max_iterations=3)

            rejected = append_iteration(
                run_dir,
                "stop",
                objective="proof found",
                stop_reason="proof_found",
                evidence_id="../proof",
                check=False,
            )

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("proof evidence_id", rejected.stdout)

    def test_runtime_helper_allows_final_terminal_stop_and_rejects_later_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            init_loop(run_dir, max_iterations=2)
            append_iteration(run_dir, "continue")
            append_iteration(run_dir, "stop", objective="budget exhausted")

            rejected = append_iteration(run_dir, "continue", objective="over budget", check=False)

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("loop status is stopped", rejected.stdout)
            status = json.loads(run_helper("status", "--dir", str(run_dir)).stdout)
            self.assertEqual(status["state_status"], "stopped")
            self.assertEqual(status["remaining_iterations"], 0)

    def test_runtime_helper_validate_fails_for_early_stop_without_success_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            init_loop(run_dir, max_iterations=3)
            state = read_loop_json(run_dir, "loop_state.json")
            budget = read_loop_json(run_dir, "budget.json")
            state["status"] = "stopped"
            state["last_iteration"] = 1
            budget["spent_iterations"] = 1
            write_loop_json(run_dir, "loop_state.json", state)
            write_loop_json(run_dir, "budget.json", budget)
            write_iterations(run_dir, [iteration_record(1, "stop")])

            validate = run_helper("validate", "--dir", str(run_dir), check=False)

            self.assertNotEqual(validate.returncode, 0)
            payload = json.loads(validate.stdout)
            self.assertIn(
                "iteration 1 early stop before max_iterations must use a success/proof stop_reason",
                payload["errors"],
            )
            self.assertIn(
                "iteration 1 early stop before max_iterations must cite proof artifact evidence_ids",
                payload["errors"],
            )

    def test_runtime_helper_validate_fails_for_early_stop_with_invalid_proof_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            init_loop(run_dir, max_iterations=3)
            write_proof_artifact(run_dir, checker_status="failed")
            state = read_loop_json(run_dir, "loop_state.json")
            budget = read_loop_json(run_dir, "budget.json")
            state["status"] = "stopped"
            state["last_iteration"] = 1
            budget["spent_iterations"] = 1
            write_loop_json(run_dir, "loop_state.json", state)
            write_loop_json(run_dir, "budget.json", budget)
            write_iterations(
                run_dir,
                [
                    iteration_record(
                        1,
                        "stop",
                        evidence_ids=["proof-artifact-1"],
                        stop_reason="proof_found",
                    )
                ],
            )

            validate = run_helper("validate", "--dir", str(run_dir), check=False)

            self.assertNotEqual(validate.returncode, 0)
            payload = json.loads(validate.stdout)
            self.assertIn(
                "iteration 1 early stop before max_iterations must cite a valid proof artifact",
                payload["errors"],
            )
            self.assertIn(
                "iteration 1: proof artifact 'proof-artifact-1' checker.status must be 'passed'",
                payload["errors"],
            )

    def test_runtime_helper_validate_fails_when_iteration_count_exceeds_max(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            init_loop(run_dir, max_iterations=1)
            state = read_loop_json(run_dir, "loop_state.json")
            budget = read_loop_json(run_dir, "budget.json")
            state["status"] = "stopped"
            state["last_iteration"] = 2
            budget["spent_iterations"] = 2
            write_loop_json(run_dir, "loop_state.json", state)
            write_loop_json(run_dir, "budget.json", budget)
            write_iterations(run_dir, [iteration_record(1, "continue"), iteration_record(2, "stop")])

            validate = run_helper("validate", "--dir", str(run_dir), check=False)

            self.assertNotEqual(validate.returncode, 0)
            payload = json.loads(validate.stdout)
            self.assertIn("iterations.jsonl exceeds budget.json max_iterations", payload["errors"])

    def test_runtime_helper_validate_fails_when_spent_iterations_desyncs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            init_loop(run_dir, max_iterations=3)
            state = read_loop_json(run_dir, "loop_state.json")
            state["status"] = "running"
            state["last_iteration"] = 1
            write_loop_json(run_dir, "loop_state.json", state)
            write_iterations(run_dir, [iteration_record(1, "continue")])

            validate = run_helper("validate", "--dir", str(run_dir), check=False)

            self.assertNotEqual(validate.returncode, 0)
            payload = json.loads(validate.stdout)
            self.assertIn("budget.json spent_iterations must equal iterations.jsonl record count", payload["errors"])

    def test_runtime_helper_validate_fails_for_running_exhausted_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "loop"
            init_loop(run_dir, max_iterations=1)
            state = read_loop_json(run_dir, "loop_state.json")
            budget = read_loop_json(run_dir, "budget.json")
            state["status"] = "running"
            state["last_iteration"] = 1
            budget["spent_iterations"] = 1
            write_loop_json(run_dir, "loop_state.json", state)
            write_loop_json(run_dir, "budget.json", budget)
            write_iterations(run_dir, [iteration_record(1, "continue")])

            validate = run_helper("validate", "--dir", str(run_dir), check=False)
            status = run_helper("status", "--dir", str(run_dir), check=False)

            self.assertNotEqual(validate.returncode, 0)
            validate_payload = json.loads(validate.stdout)
            status_payload = json.loads(status.stdout)
            self.assertIn(
                "loop_state.json status cannot be running when iteration budget is exhausted",
                validate_payload["errors"],
            )
            self.assertEqual(status_payload["status"], "failed")
            self.assertEqual(status_payload["remaining_iterations"], 0)

    def test_canonical_skill_installs_to_openclaw_without_runtime_or_support_files(self) -> None:
        manifests = load_manifests()
        for platform in ("linux", "macos", "windows", "wsl"):
            with self.subTest(platform=platform):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    create_agent_home(root, "openclaw")
                    plan = build_plan(
                        root,
                        manifests,
                        ["autonomous-research-loop", "autonomous-research-loop-runtime"],
                        detect_agents(root, ["openclaw"]),
                        platform=platform,
                    )
                    canonical_actions = [
                        item
                        for item in plan["actions"]
                        if item.get("artifact_type") == "skill-file"
                        and item.get("skill") == "autonomous-research-loop"
                    ]
                    runtime_companion_actions = [
                        item
                        for item in plan["actions"]
                        if item.get("skill") == "autonomous-research-loop-runtime"
                        and item.get("artifact_type") == "skill-file"
                    ]
                    runtime_actions = [
                        item for item in plan["actions"] if item.get("artifact_type") == "runtime-file"
                    ]

                    self.assertEqual(len(canonical_actions), 1)
                    self.assertNotEqual(canonical_actions[0]["operation"], "skip")
                    self.assertEqual(len(runtime_companion_actions), 1)
                    self.assertEqual(runtime_companion_actions[0]["classification"], "blocked")
                    self.assertEqual(runtime_companion_actions[0]["operation"], "skip")
                    self.assertEqual(
                        runtime_companion_actions[0]["reason"],
                        "OpenClaw runtime-backed skills require neutral runtime evidence",
                    )
                    self.assertEqual(runtime_actions, [])

                    apply_plan(root, plan, dry_run=False)
                    self.assertEqual(verify(root)["status"], "ok")
                    self.assertTrue(
                        (root / ".openclaw" / "skills" / "autonomous-research-loop" / "SKILL.md").is_file()
                    )
                    self.assertFalse((root / ".codex" / "runtime").exists())

    def test_runtime_companion_installs_for_supported_agents_on_all_platforms(self) -> None:
        manifests = load_manifests()
        for agent in ("codex", "claude", "deepseek", "copilot", "antigravity"):
            for platform in ("linux", "macos", "windows", "wsl"):
                with self.subTest(agent=agent, platform=platform):
                    with tempfile.TemporaryDirectory() as tmp:
                        root = Path(tmp)
                        create_agent_home(root, agent)
                        plan = build_plan(
                            root,
                            manifests,
                            ["autonomous-research-loop-runtime"],
                            detect_agents(root, [agent]),
                            platform=platform,
                        )
                        skill_actions = [
                            item
                            for item in plan["actions"]
                            if item.get("artifact_type") == "skill-file"
                            and item.get("skill") == "autonomous-research-loop-runtime"
                        ]
                        runtime_actions = [
                            item for item in plan["actions"] if item.get("artifact_type") == "runtime-file"
                        ]
                        target_relpaths = {item["target_relpath"] for item in runtime_actions}

                        self.assertEqual(len(skill_actions), 1)
                        self.assertNotEqual(skill_actions[0]["operation"], "skip")
                        self.assertIn(
                            "workspace/skills/autonomous-research-loop-runtime/autonomous_research_loop_runtime.py",
                            target_relpaths,
                        )
                        if platform == "windows":
                            self.assertIn("run_skill.ps1", target_relpaths)
                            self.assertIn("run_skill.bat", target_relpaths)
                            self.assertIn("run_python.bat", target_relpaths)
                            self.assertIn(
                                "workspace/skills/autonomous-research-loop-runtime/run_autonomous_research_loop.ps1",
                                target_relpaths,
                            )
                            self.assertIn(
                                "workspace/skills/autonomous-research-loop-runtime/run_autonomous_research_loop.bat",
                                target_relpaths,
                            )
                            self.assertNotIn(
                                "workspace/skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh",
                                target_relpaths,
                            )
                        else:
                            self.assertIn("run_skill.sh", target_relpaths)
                            self.assertIn(
                                "workspace/skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh",
                                target_relpaths,
                            )
                            self.assertNotIn(
                                "workspace/skills/autonomous-research-loop-runtime/run_autonomous_research_loop.bat",
                                target_relpaths,
                            )

    def test_runtime_companion_smoke_contract_and_validator_are_explicit(self) -> None:
        manifests = load_manifests()
        self.assertIn("autonomous-research-loop-runtime", selected_runtime_skills(manifests, None))
        self.assertEqual(
            runtime_command_target(manifests, "autonomous-research-loop-runtime", "linux"),
            "skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "autonomous-research-loop-runtime", "windows", "run_skill.ps1"),
            "skills/autonomous-research-loop-runtime/run_autonomous_research_loop.ps1",
        )
        self.assertEqual(
            runtime_command_target(manifests, "autonomous-research-loop-runtime", "windows", "run_skill.bat"),
            "skills/autonomous-research-loop-runtime/run_autonomous_research_loop.bat",
        )

        completed = subprocess.run(
            [sys.executable, "-B", str(HELPER), "selftest"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
            env=_subprocess_env(),
        )
        checks = validate_smoke_output(
            manifests,
            "autonomous-research-loop-runtime",
            completed,
            ["selftest"],
        )
        self.assertTrue(all(check["ok"] for check in checks), checks)


class AutonomousLoopEnforcementTests(unittest.TestCase):
    """Force-management: arm/disarm/active/done/hook-check with a fail-open Stop hook."""

    def _run(self, *args: str, registry: Path, env_extra: dict[str, str] | None = None):
        env = _subprocess_env({"AAS_AUTOLOOP_REGISTRY": str(registry)})
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, "-B", str(HELPER), *args],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
            check=False,
        )

    def _init(self, run_dir: Path, registry: Path, *extra: str) -> None:
        self._run(
            "init",
            "--dir",
            str(run_dir),
            "--goal",
            "g",
            "--success-criteria",
            "sc",
            "--max-iterations",
            "3",
            "--goal-focus-mode",
            "off",
            *extra,
            registry=registry,
        )

    @unittest.skipUnless(os.name == "nt", "Windows-specific PID probe")
    def test_windows_pid_probe_does_not_send_a_signal(self) -> None:
        spec = importlib.util.spec_from_file_location("autonomous_research_loop_runtime", HELPER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)

        with mock.patch.object(runtime.os, "kill", side_effect=AssertionError("os.kill was called")):
            self.assertTrue(runtime.pid_alive(os.getpid()))

    def test_arm_hook_block_then_kill_switches_allow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg, loop, proj = base / "reg", base / "loop", base / "proj"
            proj.mkdir()
            self._init(loop, reg)
            self.assertEqual(
                self._run("arm", "--dir", str(loop), "--root", str(proj), registry=reg).returncode, 0
            )
            done = json.loads(self._run("done", "--dir", str(loop), registry=reg).stdout)
            self.assertFalse(done["done"])
            # active + not done -> hook blocks turn-end (exit 2)
            self.assertEqual(self._run("hook-check", "--root", str(proj), registry=reg).returncode, 2)
            # kill switch 1: AUTOLOOP_DISABLE env
            self.assertEqual(
                self._run(
                    "hook-check", "--root", str(proj), registry=reg, env_extra={"AUTOLOOP_DISABLE": "1"}
                ).returncode,
                0,
            )
            # kill switch 2: STOP_REQUESTED sentinel -> done + hook allows
            (loop / "STOP_REQUESTED").write_text("", encoding="utf-8")
            done2 = json.loads(self._run("done", "--dir", str(loop), registry=reg).stdout)
            self.assertTrue(done2["done"])
            self.assertEqual(done2["reason"], "user_stop_requested")
            self.assertEqual(self._run("hook-check", "--root", str(proj), registry=reg).returncode, 0)
            (loop / "STOP_REQUESTED").unlink()
            # kill switch 3: disarm
            self._run("disarm", "--dir", str(loop), registry=reg)
            self.assertEqual(json.loads(self._run("active", registry=reg).stdout)["count"], 0)
            self.assertEqual(self._run("hook-check", "--root", str(proj), registry=reg).returncode, 0)

    def test_hook_check_stands_down_for_live_driver_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg, loop, proj = base / "reg", base / "loop", base / "proj"
            proj.mkdir()
            self._init(loop, reg)
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            driver = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            try:
                # arm as the headless driver does: driver flag + a live pid
                self._run(
                    "arm", "--dir", str(loop), "--root", str(proj),
                    "--pid", str(driver.pid), "--driver", registry=reg,
                )
                self.assertFalse(
                    json.loads(self._run("done", "--dir", str(loop), registry=reg).stdout)["done"]
                )
                # not done, but a live driver owns the loop -> interactive hook stands down
                self.assertEqual(self._run("hook-check", "--root", str(proj), registry=reg).returncode, 0)
                self.assertIsNone(driver.poll())
                # same live pid without the driver flag is not driver proof -> hook blocks
                self._run(
                    "arm", "--dir", str(loop), "--root", str(proj),
                    "--pid", str(driver.pid), "--force", registry=reg,
                )
                self.assertEqual(self._run("hook-check", "--root", str(proj), registry=reg).returncode, 2)
                self.assertIsNone(driver.poll())
            finally:
                if driver.poll() is None:
                    driver.terminate()
                try:
                    driver.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    driver.kill()
                    driver.wait(timeout=5)

    def test_watch_reports_iteration_terminal_and_driver_death(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg, loop = base / "reg", base / "loop"
            self._init(loop, reg)
            self._run(
                "append-iteration", "--dir", str(loop), "--mode", "bounded-research",
                "--objective", "o1", "--evidence-id", "e1", "--action-taken", "a1",
                "--output", "out1", "--decision", "continue", registry=reg,
            )
            # baseline 0 -> the appended iteration is reported once
            res = self._run("watch", "--dir", str(loop), "--once", "--from-iteration", "0", registry=reg)
            events = [json.loads(ln) for ln in res.stdout.splitlines() if '"AUTOLOOP_EVENT"' in ln]
            self.assertEqual([e["AUTOLOOP_EVENT"] for e in events], ["iteration"])
            self.assertEqual(events[0]["AUTOLOOP_ITERATION"], "1")
            # Live status surfaces are always written by watch.
            self.assertTrue((loop / "LIVE_STATUS.md").is_file())
            progress_path = loop / "driver_logs" / "progress.jsonl"
            self.assertTrue(progress_path.is_file())
            progress_events = [
                json.loads(ln) for ln in progress_path.read_text(encoding="utf-8").splitlines() if ln.strip()
            ]
            self.assertTrue(any(e.get("event") == "iteration" for e in progress_events))
            self.assertIn("Progress:", (loop / "LIVE_STATUS.md").read_text(encoding="utf-8"))
            # baseline current -> nothing new to report
            res = self._run("watch", "--dir", str(loop), "--once", registry=reg)
            self.assertNotIn('"AUTOLOOP_EVENT"', res.stdout)
            # a driver-owned entry with a dead pid -> driver_dead alert
            self._run(
                "arm", "--dir", str(loop), "--root", str(loop),
                "--pid", "3999999", "--driver", registry=reg,
            )
            res = self._run("watch", "--dir", str(loop), "--once", registry=reg)
            events = [json.loads(ln) for ln in res.stdout.splitlines() if '"AUTOLOOP_EVENT"' in ln]
            self.assertEqual([e["AUTOLOOP_EVENT"] for e in events], ["driver_dead"])
            # STOP_REQUESTED -> terminal event and watch exits on its own
            (loop / "STOP_REQUESTED").write_text("", encoding="utf-8")
            res = self._run("watch", "--dir", str(loop), "--once", registry=reg)
            events = [json.loads(ln) for ln in res.stdout.splitlines() if '"AUTOLOOP_EVENT"' in ln]
            self.assertEqual([e["AUTOLOOP_EVENT"] for e in events], ["terminal"])

    def test_drive_writes_live_status_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg, loop = base / "reg", base / "loop"
            self._init(loop, reg, "--max-iterations", "1")
            # Final-iteration stop via sentinel so drive exits cleanly after one no-op.
            (loop / "STOP_REQUESTED").write_text("", encoding="utf-8")
            res = self._run(
                "drive",
                "--dir",
                str(loop),
                "--root",
                str(loop),
                "--cmd",
                "echo should-not-run",
                "--max-failures",
                "1",
                registry=reg,
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            live = loop / "LIVE_STATUS.md"
            progress = loop / "driver_logs" / "progress.jsonl"
            self.assertTrue(live.is_file(), live)
            self.assertTrue(progress.is_file(), progress)
            events = [
                json.loads(ln) for ln in progress.read_text(encoding="utf-8").splitlines() if ln.strip()
            ]
            names = [e.get("event") for e in events]
            self.assertIn("drive_start", names)
            self.assertIn("drive_stop", names)
            body = live.read_text(encoding="utf-8")
            self.assertIn("Autonomous loop live status", body)
            self.assertIn("drive_stop", body)


    def test_progress_event_failure_labels_next_after_banked(self) -> None:
        """iteration_failed must not look like the last banked iteration failed."""
        import sys
        runtime = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "autonomous-research-loop-runtime"
        )
        sys.path.insert(0, str(runtime))
        import autonomous_research_loop_runtime as arl  # noqa: WPS

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg, loop = base / "reg", base / "loop"
            self._init(loop, reg, "--max-iterations", "10")
            self._run(
                "append-iteration",
                "--dir",
                str(loop),
                "--mode",
                "bounded-research",
                "--objective",
                "banked-work",
                "--evidence-id",
                "e1",
                "--action-taken",
                "a1",
                "--output",
                "PROVED something banked",
                "--decision",
                "continue",
                registry=reg,
            )
            # Mark next path so attempt events prefer it over last objective.
            state = json.loads((loop / "loop_state.json").read_text(encoding="utf-8"))
            state["next_preferred_path"] = "SINGLE PATH: do next work"
            (loop / "loop_state.json").write_text(json.dumps(state), encoding="utf-8")

            failed = arl.build_progress_event(loop, "iteration_failed")
            self.assertEqual(failed["last_completed_iteration"], 1)
            self.assertEqual(failed["next_iteration"], 2)
            self.assertEqual(failed["iteration"], 2)
            self.assertIn("attempting", failed["text"].lower())
            self.assertIn("banked", failed["text"].lower())
            self.assertIn("Failed starting next after banked 1", failed["text"])
            self.assertIn("SINGLE PATH: do next work", failed["text"])
            # Must not present banked PROVED as the failure result body only.
            self.assertNotIn("PROVED something banked", failed.get("output_preview") or "")

            started = arl.build_progress_event(loop, "iteration_start")
            self.assertEqual(started["iteration"], 2)
            self.assertIn("Iteration 2 running", started["text"])
            self.assertIn("**Plan**", started["text"])

            ok = arl.build_progress_event(loop, "iteration_ok")
            self.assertEqual(ok["iteration"], 1)
            self.assertIn("PROVED something banked", ok.get("output_preview") or "")

    def test_progress_result_prefers_contribution_over_certificate_path(self) -> None:
        """Bare certificate paths must not become the notify Result body."""
        import sys

        runtime = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "autonomous-research-loop-runtime"
        )
        sys.path.insert(0, str(runtime))
        import autonomous_research_loop_runtime as arl  # noqa: WPS

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg, loop = base / "reg", base / "loop"
            self._init(loop, reg, "--max-iterations", "10")
            self._run(
                "append-iteration",
                "--dir",
                str(loop),
                "--mode",
                "bounded-research",
                "--objective",
                "Kill residual Case B multi",
                "--evidence-id",
                "e1",
                "--action-taken",
                "proved multi empty",
                "--output",
                "research/autonomous-kge3/iterations/iteration-0093/a11-caseB/certificate.json",
                "--goal-contribution",
                "eliminate: Case B multi empty under beta=2",
                "--campaign-id",
                "a11-deficit-two-residual",
                "--decision",
                "revise",
                registry=reg,
            )
            (loop / "goal_priority.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "discipline_mode": "advise",
                        "primary_campaign": "a11-deficit-two-residual",
                        "primary_objective": "Close residual e=1 Case B under beta=2",
                        "campaign_registry": {
                            "a11-deficit-two-residual": {
                                "objective": "Close residual e=1 Case B under beta=2"
                            }
                        },
                        "next_campaigns_ordered": ["a11-deficit-two-residual"],
                    }
                ),
                encoding="utf-8",
            )
            (loop / "recovery.md").write_text(
                "\n".join(
                    [
                        "# Recovery",
                        "",
                        "| Field | Current state |",
                        "|---|---|",
                        "| Next safe action | Attack pure wt2 at D>=25 |",
                        "| Last valid node | I100 multi kills banked |",
                        "| Remaining gaps | pure wt2 open; e=0 ray |",
                        "",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            ok = arl.build_progress_event(loop, "iteration_ok")
            preview = ok.get("output_preview") or ""
            self.assertIn("eliminate: Case B multi empty", preview)
            self.assertIn("certificate.json", preview)
            self.assertNotEqual(
                preview.strip(),
                "research/autonomous-kge3/iterations/iteration-0093/a11-caseB/certificate.json",
            )
            self.assertIn("**Completed**", ok["text"])
            self.assertIn("**Current**", ok["text"])
            self.assertIn("**Plan**", ok["text"])
            self.assertIn("a11-deficit-two-residual", ok["text"])
            self.assertIn("I100 multi kills banked", ok["text"])
            self.assertIn("pure wt2 open", ok["text"])

            self.assertTrue(
                arl.looks_like_artifact_path(
                    "research/autonomous-kge3/iterations/iteration-0093/a11/certificate.json"
                )
            )
            self.assertFalse(
                arl.looks_like_artifact_path(
                    "Case B empty; floor D>=15; multi still open"
                )
            )

            # Unicode math + itemize list formatting
            mathy = arl.normalize_math_unicode(
                "beta=2; D>=25; delta=8; residual empty"
            )
            self.assertIn("β=2", mathy)
            self.assertIn("D≥25", mathy)
            self.assertIn("δ=8", mathy)
            listed = arl.format_notify_body_block(
                "First clause here is long enough; Second clause also long enough; "
                "Third clause also long enough",
                style="markdown",
            )
            self.assertIn("• ", listed)
            self.assertGreaterEqual(listed.count("•"), 2)

    def test_hook_check_allows_unrelated_root_and_missing_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg, loop, proj = base / "reg", base / "loop", base / "proj"
            proj.mkdir()
            self._init(loop, reg)
            self._run("arm", "--dir", str(loop), "--root", str(proj), registry=reg)
            self.assertEqual(self._run("hook-check", "--root", str(base / "other"), registry=reg).returncode, 0)
            self.assertEqual(
                self._run("hook-check", "--root", str(proj), registry=base / "nope").returncode, 0
            )

    def test_hook_check_fails_open_on_corrupt_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg, loop, proj = base / "reg", base / "loop", base / "proj"
            proj.mkdir()
            self._init(loop, reg)
            self._run("arm", "--dir", str(loop), "--root", str(proj), registry=reg)
            (loop / "loop_state.json").write_text("{ this is not json", encoding="utf-8")
            # corrupt state must NOT trap the session: the hook fails open (exit 0)
            self.assertEqual(self._run("hook-check", "--root", str(proj), registry=reg).returncode, 0)

    def test_require_user_stop_only_overrides_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg, loop = base / "reg", base / "loop"
            self._init(loop, reg, "--require-user-stop-only")
            done = json.loads(self._run("done", "--dir", str(loop), registry=reg).stdout)
            self.assertFalse(done["done"])
            self.assertEqual(done["reason"], "awaiting_user_stop")

    def test_require_user_stop_only_ignores_self_marked_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg, loop = base / "reg", base / "loop"
            self._init(loop, reg, "--require-user-stop-only")
            # The policed agent writes a terminal status straight into the
            # ledger, bypassing append-iteration's guards. Under
            # require-user-stop-only this must NOT release the session.
            state_path = loop / "loop_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["status"] = "stopped"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            done = json.loads(self._run("done", "--dir", str(loop), registry=reg).stdout)
            self.assertFalse(done["done"])
            self.assertEqual(done["reason"], "awaiting_user_stop")

    def test_self_marked_terminal_releases_without_require_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg, loop = base / "reg", base / "loop"
            self._init(loop, reg)
            state_path = loop / "loop_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["status"] = "stopped"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            done = json.loads(self._run("done", "--dir", str(loop), registry=reg).stdout)
            self.assertTrue(done["done"])
            self.assertEqual(done["reason"], "terminal_status:stopped")

    def test_pause_sentinel_allows_turn_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg, loop, proj = base / "reg", base / "loop", base / "proj"
            proj.mkdir()
            self._init(loop, reg)
            self._run("arm", "--dir", str(loop), "--root", str(proj), registry=reg)
            (loop / "PAUSE").write_text("", encoding="utf-8")
            self.assertEqual(self._run("hook-check", "--root", str(proj), registry=reg).returncode, 0)


def _arm_loop(run_dir: Path, registry: Path, root: Path) -> None:
    env = _subprocess_env({"AAS_AUTOLOOP_REGISTRY": str(registry)})
    subprocess.run(
        [sys.executable, "-B", str(HELPER), "arm", "--dir", str(run_dir), "--root", str(root)],
        capture_output=True, text=True, timeout=20, env=env, check=False,
    )


def _init_loop(run_dir: Path, registry: Path, *extra: str, max_iterations: int = 3) -> None:
    env = _subprocess_env({"AAS_AUTOLOOP_REGISTRY": str(registry)})
    subprocess.run(
        [sys.executable, "-B", str(HELPER), "init", "--dir", str(run_dir), "--goal", "g",
         "--success-criteria", "sc", "--max-iterations", str(max_iterations),
         "--goal-focus-mode", "off", *extra],
        capture_output=True, text=True, timeout=20, env=env, check=False,
    )


def _py_iteration(script: str) -> str:
    """A cross-platform iteration command: a python one-liner run through the platform
    shell. The script must use single quotes only, so the ``"<python>" -c "<script>"``
    string parses identically under /bin/sh and cmd.exe."""
    return f'"{sys.executable}" -c "{script}"'


class RuntimeHookCheckTests(unittest.TestCase):
    """The runtime's fail-open Stop-hook check, invoked directly (cross-platform).

    The installer wires this as ``python <runtime.py> hook-check``: the runtime reads
    the hook JSON on stdin, honors the kill switches and the stop_hook_active
    re-entrancy payload, and resolves the project root from CLAUDE_PROJECT_DIR, so
    there is no shell wrapper and the behavior is identical on every OS."""

    def _hook(self, *, registry: Path, root: Path, payload: str = "", env_extra: dict[str, str] | None = None):
        env = _subprocess_env(
            {"AAS_AUTOLOOP_REGISTRY": str(registry), "CLAUDE_PROJECT_DIR": str(root)}
        )
        env.pop("AUTOLOOP_DISABLE", None)
        env.pop("AUTOLOOP_DRIVER", None)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, "-B", str(HELPER), "hook-check"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            check=False,
        )

    def _armed(self, base: Path):
        reg, loop, proj = base / "reg", base / "loop", base / "proj"
        proj.mkdir()
        _init_loop(loop, reg)
        _arm_loop(loop, reg, proj)
        return reg, loop, proj

    def test_allows_when_no_active_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertEqual(self._hook(registry=base / "reg", root=base / "proj").returncode, 0)

    def test_blocks_when_active_loop_not_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg, _loop, proj = self._armed(Path(tmp))
            self.assertEqual(self._hook(registry=reg, root=proj).returncode, 2)

    def test_kill_switch_env_allows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg, _loop, proj = self._armed(Path(tmp))
            self.assertEqual(self._hook(registry=reg, root=proj, env_extra={"AUTOLOOP_DISABLE": "1"}).returncode, 0)

    def test_driver_env_allows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg, _loop, proj = self._armed(Path(tmp))
            self.assertEqual(self._hook(registry=reg, root=proj, env_extra={"AUTOLOOP_DRIVER": "1"}).returncode, 0)

    def test_reentrancy_payload_allows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg, _loop, proj = self._armed(Path(tmp))
            res = self._hook(registry=reg, root=proj, payload='{"stop_hook_active": true}')
            self.assertEqual(res.returncode, 0)

    def test_stop_requested_sentinel_allows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg, loop, proj = self._armed(Path(tmp))
            (loop / "STOP_REQUESTED").write_text("", encoding="utf-8")
            self.assertEqual(self._hook(registry=reg, root=proj).returncode, 0)


@unittest.skipUnless(os.name == "posix", "the .sh shim is shipped for POSIX manual use only")
class AutoloopStopHookShimTests(unittest.TestCase):
    """Smoke: the POSIX .sh convenience shim delegates to the runtime hook-check."""

    STOP_HOOK = HELPER.parent / "autoloop_stop_hook.sh"

    def _shim(self, *, registry: Path, root: Path, payload: str = ""):
        env = _subprocess_env(
            {"AAS_AUTOLOOP_REGISTRY": str(registry), "CLAUDE_PROJECT_DIR": str(root)}
        )
        env.pop("AUTOLOOP_DISABLE", None)
        env.pop("AUTOLOOP_DRIVER", None)
        return subprocess.run(
            ["bash", str(self.STOP_HOOK)],
            input=payload, capture_output=True, text=True, timeout=30, env=env, check=False,
        )

    def _armed(self, base: Path):
        reg, loop, proj = base / "reg", base / "loop", base / "proj"
        proj.mkdir()
        _init_loop(loop, reg)
        _arm_loop(loop, reg, proj)
        return reg, loop, proj

    def test_shim_allows_when_no_active_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertEqual(self._shim(registry=base / "reg", root=base / "proj").returncode, 0)

    def test_shim_blocks_when_active_loop_not_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reg, _loop, proj = self._armed(Path(tmp))
            self.assertEqual(self._shim(registry=reg, root=proj).returncode, 2)


class RuntimeDriveTests(unittest.TestCase):
    """The cross-platform headless driver subcommand: derives done from the runtime,
    fails safe. Replaces the bash driver; the POSIX .sh shim delegates here."""

    def _drive(self, run_dir: Path, registry: Path, cmd: str, *extra: str, timeout: int = 40):
        env = _subprocess_env({"AAS_AUTOLOOP_REGISTRY": str(registry)})
        return subprocess.run(
            [sys.executable, "-B", str(HELPER), "drive", "--dir", str(run_dir), "--root", str(run_dir),
             "--cmd", cmd, *extra],
            capture_output=True, text=True, timeout=timeout, env=env, check=False,
        )

    def test_stops_immediately_when_already_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); reg, loop = base / "reg", base / "loop"
            _init_loop(loop, reg, max_iterations=5)
            (loop / "STOP_REQUESTED").write_text("", encoding="utf-8")
            cmd = _py_iteration(
                "import os,pathlib; pathlib.Path(os.environ['AUTOLOOP_DIR'],'ran').write_text('x')"
            )
            res = self._drive(loop, reg, cmd)
            self.assertEqual(res.returncode, 0)
            self.assertFalse((loop / "ran").exists())  # iteration command never ran

    def test_runs_iterations_until_user_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); reg, loop = base / "reg", base / "loop"
            _init_loop(loop, reg, max_iterations=5)
            cmd = _py_iteration(
                "import os,pathlib; d=pathlib.Path(os.environ['AUTOLOOP_DIR']); p=d/'c'; "
                "c=(int(p.read_text()) if p.exists() else 0)+1; p.write_text(str(c)); "
                "(c>=3 and (d/'STOP_REQUESTED').write_text('x'))"
            )
            res = self._drive(loop, reg, cmd)
            self.assertEqual(res.returncode, 0)
            self.assertEqual((loop / "c").read_text(), "3")

    def test_stops_after_max_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); reg, loop = base / "reg", base / "loop"
            _init_loop(loop, reg, max_iterations=5)
            cmd = _py_iteration(
                "import os,pathlib,sys; d=pathlib.Path(os.environ['AUTOLOOP_DIR']); p=d/'c'; "
                "c=(int(p.read_text()) if p.exists() else 0)+1; p.write_text(str(c)); sys.exit(1)"
            )
            res = self._drive(loop, reg, cmd, "--max-failures", "3")
            self.assertEqual(res.returncode, 3)
            self.assertEqual((loop / "c").read_text(), "3")

    def test_grok_402_balance_exhaustion_uses_three_quota_tries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); reg, loop = base / "reg", base / "loop"
            _init_loop(loop, reg, max_iterations=5)
            cmd = _py_iteration(
                "import os,pathlib,sys; d=pathlib.Path(os.environ['AUTOLOOP_DIR']); "
                "p=d/'c'; c=(int(p.read_text()) if p.exists() else 0)+1; "
                "p.write_text(str(c)); "
                "print('API error (status 402 Payment Required): Grok Build usage balance exhausted'); "
                "sys.exit(1)"
            )
            res = self._drive(
                loop,
                reg,
                cmd,
                "--max-quota-waits",
                "3",
                "--quota-backoff",
                "1",
            )
            self.assertEqual(res.returncode, 5, res.stderr)
            self.assertEqual((loop / "c").read_text(), "3")

    def test_failover_defaults_cap_driver_at_three(self) -> None:
        config = json.loads(
            (HELPER.parent / "failover.example.json").read_text(encoding="utf-8")
        )
        self.assertEqual(config["drive_defaults"]["max_failures"], 3)
        supervisor = (HELPER.parent / "arl_drive_supervisor.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("dd.get('max_failures', 3)", supervisor)
        self.assertNotIn("dd.get('max_failures', 10)", supervisor)

    def test_exports_driver_env_so_hook_stands_down(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); reg, loop = base / "reg", base / "loop"
            _init_loop(loop, reg, max_iterations=5)
            cmd = _py_iteration(
                "import os,pathlib; d=pathlib.Path(os.environ['AUTOLOOP_DIR']); "
                "(d/'env').write_text(os.environ.get('AUTOLOOP_DRIVER','')); "
                "(d/'STOP_REQUESTED').write_text('x')"
            )
            res = self._drive(loop, reg, cmd)
            self.assertEqual(res.returncode, 0)
            self.assertEqual((loop / "env").read_text(), "1")

    @unittest.skipUnless(os.name == "posix", "POSIX umask behavior")
    def test_provider_overrides_fail_before_execution_and_custom_cmd_keeps_umask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for mode in ("grok", "claude", "cmd"):
                reg = base / f"reg-{mode}"
                loop = base / f"loop-{mode}"
                _init_loop(loop, reg, max_iterations=5)
                cache_name = f"{mode}-cache"
                command = _py_iteration(
                    "import os,pathlib; d=pathlib.Path(os.environ['AUTOLOOP_DIR']); "
                    f"(d/'{cache_name}').write_text('x'); "
                    "(d/'STOP_REQUESTED').write_text('x')"
                )
                env = _no_notify(
                    {
                        key: value
                        for key, value in os.environ.items()
                        if not key.startswith("AAS_AUTOLOOP_")
                        and not key.startswith("AAS_GROK")
                    }
                )
                env["PYTHONDONTWRITEBYTECODE"] = "1"
                env["AAS_AUTOLOOP_REGISTRY"] = str(reg)
                argv = [
                    sys.executable,
                    "-B",
                    str(HELPER),
                    "drive",
                    "--dir",
                    str(loop),
                    "--root",
                    str(loop),
                ]
                if mode == "cmd":
                    argv.extend(["--cmd", command])
                else:
                    env[f"AAS_AUTOLOOP_CMD_{mode.upper()}"] = command
                    argv.extend(["--provider", mode])
                previous_umask = os.umask(0o002)
                try:
                    completed = subprocess.run(
                        argv,
                        capture_output=True,
                        text=True,
                        timeout=40,
                        env=env,
                        check=False,
                    )
                finally:
                    os.umask(previous_umask)
                cache = loop / cache_name
                if mode == "cmd":
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(cache.stat().st_mode & 0o777, 0o664)
                else:
                    self.assertEqual(completed.returncode, 2, completed.stderr)
                    self.assertFalse(cache.exists())
                    self.assertIn("primary prompt transport", completed.stderr)

    def test_pause_blocks_iterations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); reg, loop = base / "reg", base / "loop"
            _init_loop(loop, reg, max_iterations=5)
            (loop / "PAUSE").write_text("", encoding="utf-8")
            cmd = _py_iteration(
                "import os,pathlib; pathlib.Path(os.environ['AUTOLOOP_DIR'],'ran').write_text('x')"
            )
            with self.assertRaises(subprocess.TimeoutExpired):
                self._drive(loop, reg, cmd, "--poll", "5", timeout=3)
            self.assertFalse((loop / "ran").exists())  # paused -> no iteration ran


class RuntimeGoalFocusIntegrationTests(unittest.TestCase):
    """Cross-module contracts that must hold at the driver boundary."""

    def setUp(self) -> None:
        super().setUp()
        self.provider_fixture = _ProviderAttestationFixture()
        self.addCleanup(self.provider_fixture.cleanup)
        patcher = mock.patch.dict(
            os.environ, self.provider_fixture.environment, clear=False
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _runtime_modules():
        runtime = HELPER.parent
        if str(runtime) not in sys.path:
            sys.path.insert(0, str(runtime))
        import autonomous_research_loop_runtime as arl  # noqa: WPS
        import goal_focus as gf  # noqa: WPS

        return arl, gf

    def _trusted_registry_root(self, label: str) -> Path:
        root = self.provider_fixture.root / f"registry-{label}"
        root.mkdir(mode=0o700)
        if os.name == "posix":
            root.chmod(0o700)
        return root

    @staticmethod
    def _activate_single_direction(arl: Any, gf: Any, loop: Path) -> None:
        """Install the smallest valid reviewed plan used by drive regressions."""
        registry = gf.load_approach_registry(loop)
        registry["registry_revision"] = 2
        registry["campaigns"] = {
            "campaign-a": {
                "id": "campaign-a",
                "status": "eligible",
                "approaches": {
                    "approach-a": {
                        "id": "approach-a",
                        "campaign_id": "campaign-a",
                        "status": "eligible",
                        "objective": "Run one bounded check.",
                        "next_action": "Execute the single test command.",
                        "scope_lock": "goal",
                        "target_obligation_ids": [],
                        "dependencies": [],
                        "diversity_tags": ["direct"],
                        "estimates": {
                            "goal_resolution": {"lower": 2, "upper": 3}
                        },
                    }
                },
            }
        }
        (loop / "approach_registry.json").write_text(
            json.dumps(registry, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        import panel_parent  # noqa: WPS

        snapshot = gf.strategy_authority_snapshot(loop)
        provider_advice = {
            "schema_version": "strategy_advice.v1",
            "decision": "select",
            "recommended_approach_id": "approach-a",
            "candidates": [
                {
                    "approach_id": "approach-a",
                    "campaign_id": "campaign-a",
                    "rank": 1,
                    "estimates": {
                        factor: {"lower": 2, "upper": 3}
                        for factor in panel_parent.ESTIMATE_FACTORS
                    },
                    "evidence_refs": [],
                    "missing_evidence": [],
                    "falsifier": "The bounded check fails.",
                    "strongest_objection": "The terminal bridge remains open.",
                    "next_action": "Execute the single test command.",
                }
            ],
            "inspected_evidence": [],
            "uninspected_evidence": [],
            "reasoning_summary": "The only eligible approach is selected.",
        }
        strategy = arl._strategy_selection_from_panel(
            loop,
            {
                "authority_snapshot": snapshot,
                "primary_execution_attestation": _provider_attestation(
                    "claude", loop
                ),
                "provider_execution_attestations": {
                    "codex": _provider_attestation("codex", loop)
                },
                "structured_synthesis": {
                    "required_schema": "strategy_advice.v1",
                    "primary_provider": "claude",
                    "primary_family": _provider_attestation("claude", loop)[
                        "family"
                    ],
                    "valid_providers": ["codex"],
                    "different_family_valid_providers": ["codex"],
                    "recommendation_counts": {"approach-a": 1},
                    "decision_counts": {"select": 1},
                    "dissent": False,
                },
                "results": {
                    "codex": {
                        "structured_valid": True,
                        "structured_payload": provider_advice,
                    }
                },
            },
        )
        if strategy.get("status") != "ready":
            raise AssertionError(f"invalid strategy fixture: {strategy}")
        gf.commit_selected_direction(
            loop,
            strategy["selection"],
            strategy["review"],
            "test",
            expected_plan_revision=1,
        )

    def test_compute_done_recovers_goal_focus_transaction_before_terminal_check(self) -> None:
        arl, _ = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            arl.init_loop(arl.selftest_init_args(loop, max_iterations=1))
            transaction_dir = loop / ".goal_focus_transactions"
            transaction_dir.mkdir()

            def recover_before_read(run_dir: Path) -> list[dict[str, Any]]:
                self.assertEqual(run_dir, loop)
                budget = arl.read_json(loop / "budget.json")
                budget["spent_iterations"] = 1
                arl.write_json(loop / "budget.json", budget)
                transaction_dir.rmdir()
                return [{"status": "completed"}]

            with mock.patch.object(
                arl.goal_focus_v2,
                "recover_transactions",
                side_effect=recover_before_read,
            ) as recovered:
                verdict = arl.compute_done(loop)

            recovered.assert_called_once_with(loop)
            self.assertTrue(verdict["done"])
            self.assertEqual(verdict["reason"], "loops_reached")

    def test_enforce_append_and_stage_require_live_exact_dispatch_without_mutation(self) -> None:
        arl, gf = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            init_args = arl.selftest_init_args(loop, max_iterations=2)
            init_args.goal_focus_mode = "enforce"
            arl.init_loop(init_args)
            self._activate_single_direction(arl, gf, loop)
            material_names = (
                "iterations.jsonl",
                "loop_state.json",
                "budget.json",
                "goal_contract.json",
                "approach_registry.json",
                "current_plan.json",
                "direction_decisions.jsonl",
                "recovery.md",
            )

            def material() -> dict[str, bytes]:
                return {
                    name: (loop / name).read_bytes()
                    for name in material_names
                    if (loop / name).exists()
                }

            pristine = material()
            with self.assertRaises(gf.RevisionConflict):
                gf.stage_iteration_candidate(
                    loop,
                    {
                        "candidate_id": "unattested-candidate",
                        "claim_ids": ["dispatch-bound-claim"],
                        "evidence_checked": {
                            "claim_ids": ["dispatch-bound-claim"],
                            "evidence_ids": ["dispatch-bound-evidence"],
                        },
                    },
                    expected_plan_revision=2,
                )
            self.assertEqual(material(), pristine)
            self.assertFalse((loop / "iteration_candidate.json").exists())

            append_args = [
                sys.executable,
                "-B",
                str(HELPER),
                "append-iteration",
                "--dir",
                str(loop),
                "--mode",
                "bounded-research",
                "--objective",
                "must be host dispatched",
                "--decision",
                "revise",
                "--claim-id",
                "dispatch-bound-claim",
                "--evidence-id",
                "dispatch-bound-evidence",
                "--compute-none",
            ]
            no_dispatch = subprocess.run(
                append_args,
                capture_output=True,
                text=True,
                check=False,
                env=_subprocess_env(
                    {"AAS_AUTOLOOP_PRIMARY_PROVIDER": "claude"}
                ),
            )
            self.assertNotEqual(no_dispatch.returncode, 0)
            self.assertIn("live host dispatch", no_dispatch.stdout + no_dispatch.stderr)
            self.assertEqual(material(), pristine)

            dispatch = gf.prepare_iteration_dispatch(
                loop,
                executor_provider="claude",
                executor_family="anthropic",
                executor_attestation=_provider_attestation("claude", loop),
                started_at="2026-07-29T12:00:00Z",
            )["dispatch"]
            write_text_evidence(loop, dispatch, "dispatch-bound-evidence")
            with_dispatch = material()
            dispatch_bytes = (loop / "iteration_dispatch.json").read_bytes()
            with self.assertRaises(gf.RevisionConflict):
                gf.stage_iteration_candidate(
                    loop,
                    {
                        "candidate_id": dispatch["candidate_id"],
                        "claim_ids": ["dispatch-bound-claim"],
                        "evidence_checked": {
                            "claim_ids": ["dispatch-bound-claim"],
                            "evidence_ids": ["dispatch-bound-evidence"],
                        },
                    },
                    expected_plan_revision=2,
                    expected_dispatch_id="stale-dispatch-id",
                )
            stale_env = subprocess.run(
                append_args,
                capture_output=True,
                text=True,
                check=False,
                env=_subprocess_env(
                    {
                        "AAS_AUTOLOOP_PRIMARY_PROVIDER": "claude",
                        "AAS_AUTOLOOP_DISPATCH_ID": "stale-dispatch-id",
                        "AAS_AUTOLOOP_CANDIDATE_ID": dispatch["candidate_id"],
                    }
                ),
            )
            self.assertNotEqual(stale_env.returncode, 0)
            self.assertIn("dispatch id", stale_env.stdout + stale_env.stderr)
            self.assertEqual(material(), with_dispatch)
            self.assertEqual(
                (loop / "iteration_dispatch.json").read_bytes(),
                dispatch_bytes,
            )
            self.assertFalse((loop / "iteration_candidate.json").exists())

    def test_panel_goal_resolution_factor_reaches_core_scorer(self) -> None:
        arl, gf = self._runtime_modules()
        registry = gf.default_approach_registry()
        registry["campaigns"] = {
            "c1": {
                "id": "c1",
                "status": "eligible",
                "approaches": {
                    "a1": {
                        "id": "a1",
                        "campaign_id": "c1",
                        "status": "eligible",
                        "next_action": "Run experiment A1.",
                        "estimates": {},
                    }
                },
            }
        }
        adjusted, mentioned = arl._panel_adjusted_registry(
            registry,
            {
                "claude": {
                    "candidates": [
                        {
                            "campaign_id": "c1",
                            "approach_id": "a1",
                            "estimates": {
                                "goal_resolution_contribution": {
                                    "lower": 3,
                                    "upper": 4,
                                }
                            },
                        }
                    ]
                }
            },
        )
        self.assertEqual(mentioned, {"a1"})
        approach = adjusted["campaigns"]["c1"]["approaches"]["a1"]
        self.assertEqual(
            approach["estimates"]["goal_resolution"],
            {"lower": 3.0, "upper": 4.0},
        )
        score = gf.score_approach(approach)
        self.assertEqual(score["components"]["goal_resolution"]["lower"], 3.0)
        self.assertEqual(score["conservative"], 15.0)

    def test_monitor_prompt_is_observational_and_preserves_legacy_steering(self) -> None:
        arl, _ = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            init_args = arl.selftest_init_args(loop, max_iterations=2)
            init_args.goal_focus_mode = "monitor"
            arl.init_loop(init_args)
            (loop / "goal_priority.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "discipline_mode": "hard",
                        "primary_campaign": "legacy-a",
                        "primary_objective": "Follow the legacy selected route.",
                        "campaign_registry": {
                            "legacy-a": {
                                "objective": "Follow the legacy selected route."
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            prompt = arl.iteration_prompt(loop)
            self.assertIn("MONITOR (observational only)", prompt)
            self.assertIn("goal_priority.v1 — active", prompt)
            self.assertIn("hard path steering active", prompt)
            self.assertNotIn("Stage the result for independent review", prompt)

    def test_monitor_drive_banks_legacy_iteration_without_mutating_goal_focus_authority(self) -> None:
        arl, _ = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            registry_dir = self._trusted_registry_root(base.name)
            init_args = arl.selftest_init_args(loop, max_iterations=1)
            init_args.goal_focus_mode = "monitor"
            arl.init_loop(init_args)
            authority_names = (
                "goal_contract.json",
                "approach_registry.json",
                "current_plan.json",
                "direction_decisions.jsonl",
            )
            for name in authority_names:
                self.assertTrue((loop / name).is_file(), name)
            before = {
                name: (loop / name).read_bytes()
                for name in authority_names
            }
            command = " ".join(
                [
                    f'"{sys.executable}"',
                    "-B",
                    f'"{HELPER}"',
                    "append-iteration",
                    "--dir",
                    f'"{loop}"',
                    "--mode bounded-research",
                    '--objective "legacy monitor attempt"',
                    "--decision blocked",
                    '--output "legacy result banked directly"',
                    "--source-id S1",
                    "--guard-ref G1",
                    '--remaining-gap "none"',
                    '--stop-reason "monitor fixture complete"',
                ]
            )
            result = arl.drive_command(
                arl.selftest_drive_args(loop, registry_dir, command)
            )

            driver_logs = sorted((loop / "driver_logs").glob("iter_*.log"))
            driver_tail = (
                driver_logs[-1].read_text(encoding="utf-8", errors="replace")
                if driver_logs
                else "no iteration log"
            )
            self.assertEqual(result["status"], "ok", f"{result}\n{driver_tail}")
            self.assertEqual(len(arl.read_iterations(loop / "iterations.jsonl")), 1)
            self.assertFalse((loop / "iteration_candidate.json").exists())
            after = {name: (loop / name).read_bytes() for name in before}
            self.assertEqual(after, before)

    def test_monitor_reports_corrupt_goal_focus_authority_but_does_not_block_legacy_drive(self) -> None:
        arl, _ = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            registry_dir = self._trusted_registry_root(base.name)
            init_args = arl.selftest_init_args(loop, max_iterations=1)
            init_args.goal_focus_mode = "monitor"
            arl.init_loop(init_args)
            registry_path = loop / "approach_registry.json"
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["registry_revision"] = 99
            registry_path.write_text(
                json.dumps(registry, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            authority_names = (
                "goal_contract.json",
                "approach_registry.json",
                "current_plan.json",
                "direction_decisions.jsonl",
            )
            corrupt_authority = {
                name: (loop / name).read_bytes() for name in authority_names
            }
            command = " ".join(
                [
                    f'"{sys.executable}"',
                    "-B",
                    f'"{HELPER}"',
                    "append-iteration",
                    "--dir",
                    f'"{loop}"',
                    "--mode bounded-research",
                    '--objective "legacy work despite corrupt monitor state"',
                    "--decision blocked",
                    '--output "legacy result banked while monitor reported drift"',
                    "--source-id S1",
                    "--guard-ref G1",
                    '--stop-reason "monitor corruption fixture complete"',
                ]
            )
            stderr = io.StringIO()
            with mock.patch.object(sys, "stderr", stderr):
                result = arl.drive_command(
                    arl.selftest_drive_args(loop, registry_dir, command)
                )

            driver_logs = sorted((loop / "driver_logs").glob("iter_*.log"))
            driver_tail = (
                driver_logs[-1].read_text(encoding="utf-8", errors="replace")
                if driver_logs
                else "no iteration log"
            )
            self.assertEqual(result["status"], "ok", f"{result}\n{driver_tail}")
            self.assertIn("Goal-Focus monitor finding", stderr.getvalue())
            self.assertIn("registry_revision", stderr.getvalue())
            rows = arl.read_iterations(loop / "iterations.jsonl")
            self.assertEqual(len(rows), 1)
            self.assertIn("legacy result banked", rows[0]["output"])
            self.assertEqual(
                {name: (loop / name).read_bytes() for name in authority_names},
                corrupt_authority,
            )

    def test_strategy_commits_action_from_different_family_reviewer(self) -> None:
        arl, gf = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp)
            init_args = arl.selftest_init_args(loop, max_iterations=2)
            init_args.goal_focus_mode = "enforce"
            arl.init_loop(init_args)
            registry = gf.default_approach_registry()
            registry["campaigns"] = {
                "c1": {
                    "id": "c1",
                    "status": "eligible",
                    "approaches": {
                        "a1": {
                            "id": "a1",
                            "campaign_id": "c1",
                            "status": "eligible",
                            "next_action": "registered action",
                            "estimates": {},
                        }
                    },
                }
            }
            (loop / "approach_registry.json").write_text(
                json.dumps(registry), encoding="utf-8"
            )
            summary = {
                "structured_synthesis": {
                    "primary_provider": "codex",
                    "primary_family": _provider_attestation("codex", loop)[
                        "family"
                    ],
                    "valid_providers": ["codex", "claude"],
                    "different_family_valid_providers": ["claude"],
                    "dissent": True,
                },
                "primary_execution_attestation": _provider_attestation(
                    "codex", loop
                ),
                "provider_execution_attestations": {
                    "codex": _provider_attestation("codex", loop),
                    "claude": _provider_attestation("claude", loop),
                },
                "results": {
                    "codex": {
                        "structured_valid": True,
                        "structured_payload": {
                            "decision": "explore",
                            "candidates": [
                                {
                                    "campaign_id": "c1",
                                    "approach_id": "a1",
                                    "rank": 1,
                                    "next_action": "same-family action",
                                    "estimates": {
                                        "goal_resolution_contribution": {
                                            "lower": 2,
                                            "upper": 3,
                                        }
                                    },
                                }
                            ],
                        },
                    },
                    "claude": {
                        "structured_valid": True,
                        "structured_payload": {
                            "decision": "explore",
                            "candidates": [
                                {
                                    "campaign_id": "c1",
                                    "approach_id": "a1",
                                    "rank": 2,
                                    "next_action": "different-family reviewed action",
                                    "estimates": {
                                        "goal_resolution_contribution": {
                                            "lower": 2,
                                            "upper": 3,
                                        }
                                    },
                                }
                            ],
                        },
                    },
                },
                "authority_snapshot": gf.strategy_authority_snapshot(loop),
            }
            strategy = arl._strategy_selection_from_panel(loop, summary)
            self.assertEqual(strategy["status"], "ready")
            self.assertEqual(
                strategy["selection"]["selected_candidate"]["next_action"],
                "different-family reviewed action",
            )

    def test_goal_focus_cli_status_validate_and_migration_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            initialized = run_helper(
                "init",
                "--dir",
                str(loop),
                "--goal",
                "Resolve the open question.",
                "--success-criteria",
                "Discharge the terminal obligation.",
                "--max-iterations",
                "2",
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            status = run_helper("goal-focus", "status", "--dir", str(loop))
            status_payload = json.loads(status.stdout)
            self.assertEqual(status_payload["status"], "ok")
            self.assertEqual(status_payload["mode"], "enforce")
            self.assertEqual(status_payload["plan"]["state"], "needs_replan")
            validated = run_helper("goal-focus", "validate", "--dir", str(loop))
            self.assertEqual(validated.returncode, 0, validated.stdout)

            legacy = base / "legacy"
            init_loop(legacy, max_iterations=2)
            dry_run = run_helper(
                "goal-focus", "migrate", "--dir", str(legacy), "--dry-run"
            )
            migration = json.loads(dry_run.stdout)
            self.assertEqual(migration["status"], "ok")
            self.assertTrue(migration["dry_run"])
            self.assertFalse(migration["applied"])
            self.assertFalse((legacy / "goal_contract.json").exists())

    def test_goal_focus_migration_apply_refuses_live_driver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "legacy"
            registry_root = self._trusted_registry_root(base.name)
            active = registry_root / "active.d"
            init_loop(loop, max_iterations=2)
            active.mkdir(parents=True, mode=0o700)
            write_loop_json(
                active,
                "live-driver.json",
                {
                    "run_id": "live-driver",
                    "loop_dir": str(loop.resolve()),
                    "project_root": str(base.resolve()),
                    "pid": os.getpid(),
                    "driver": True,
                    "heartbeat": "2026-07-29T00:00:00Z",
                },
            )
            (active / "live-driver.json").chmod(0o600)

            result = run_helper(
                "goal-focus",
                "migrate",
                "--dir",
                str(loop),
                "--registry-dir",
                str(registry_root),
                "--apply",
                check=False,
            )
            payload = json.loads(result.stdout)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(payload["migration_status"], "active_driver")
            self.assertFalse(payload["applied"])
            self.assertEqual(payload["driver_pid"], os.getpid())
            self.assertFalse((loop / "goal_contract.json").exists())
            self.assertFalse((loop / ".goal_focus_migration.claim").exists())

    def test_goal_focus_migration_checks_every_matching_driver_entry(self) -> None:
        arl, _ = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "legacy"
            registry_root = self._trusted_registry_root(base.name)
            active = registry_root / "active.d"
            arl.init_loop(arl.selftest_init_args(loop, max_iterations=2))
            active.mkdir(parents=True, mode=0o700)
            write_loop_json(
                active,
                "00-nondriver.json",
                {
                    "run_id": "nondriver",
                    "loop_dir": str(loop.resolve()),
                    "project_root": str(base.resolve()),
                    "pid": os.getpid(),
                    "driver": False,
                    "heartbeat": arl.utc_now(),
                },
            )
            (active / "00-nondriver.json").chmod(0o600)
            write_loop_json(
                active,
                "99-live-driver.json",
                {
                    "run_id": "live-driver",
                    "loop_dir": str(loop.resolve()),
                    "project_root": str(base.resolve()),
                    "pid": os.getpid(),
                    "driver": True,
                    "heartbeat": arl.utc_now(),
                },
            )
            (active / "99-live-driver.json").chmod(0o600)
            args = argparse.Namespace(
                dir=str(loop),
                registry_dir=str(registry_root),
                apply=True,
                active_campaign="",
            )

            result = arl.goal_focus_migrate_command(args)

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["migration_status"], "active_driver")
            self.assertEqual(result["driver_pids"], [os.getpid()])
            self.assertFalse((loop / arl.MIGRATION_CLAIM_FILE).exists())

    def test_live_driver_is_not_collected_only_because_heartbeat_is_stale(self) -> None:
        arl, _ = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            registry_root = self._trusted_registry_root(base.name)
            active = registry_root / "active.d"
            arl.init_loop(arl.selftest_init_args(loop, max_iterations=2))
            active.mkdir(parents=True, mode=0o700)
            entry_path = active / "live-driver.json"
            write_loop_json(
                active,
                entry_path.name,
                {
                    "run_id": "live-driver",
                    "loop_dir": str(loop.resolve()),
                    "project_root": str(base.resolve()),
                    "pid": os.getpid(),
                    "driver": True,
                    "heartbeat": "2000-01-01T00:00:00Z",
                },
            )
            entry_path.chmod(0o600)

            removed = arl.gc_registry(active)

            self.assertEqual(removed, 0)
            self.assertTrue(entry_path.exists())

    def test_migration_claim_blocks_driver_and_releases_after_apply(self) -> None:
        arl, _ = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "legacy"
            registry_root = self._trusted_registry_root(base.name)
            arl.init_loop(arl.selftest_init_args(loop, max_iterations=2))
            marker = base / "driver-ran"
            command = (
                '"/usr/bin/python3" -c "from pathlib import Path; '
                f"Path(r'{marker}').write_text('ran')\""
            )

            def migrate_while_claimed(
                run_dir: Path,
                *,
                apply: bool,
                active_campaign: str | None,
                migration_claim: dict[str, Any] | None,
            ) -> dict[str, Any]:
                self.assertEqual(run_dir, loop)
                self.assertTrue(apply)
                self.assertIsNone(active_campaign)
                self.assertIsNotNone(migration_claim)
                assert migration_claim is not None
                self.assertEqual(
                    migration_claim["schema_version"],
                    "goal_focus_migration_guard.v1",
                )
                self.assertEqual(migration_claim["run_dir"], str(loop.resolve()))
                self.assertEqual(migration_claim["claim_pid"], os.getpid())
                self.assertEqual(migration_claim["live_driver_count"], 0)
                self.assertTrue(arl.migration_claim_active(loop))
                blocked = arl.drive_command(
                    arl.selftest_drive_args(loop, registry_root, command)
                )
                self.assertEqual(blocked["status"], "failed")
                self.assertEqual(blocked["reason"], "migration_in_progress")
                self.assertFalse(marker.exists())
                return {"status": "migrated", "applied": True}

            args = argparse.Namespace(
                dir=str(loop),
                registry_dir=str(registry_root),
                apply=True,
                active_campaign="",
            )
            with mock.patch.object(
                arl.goal_focus_v2, "migrate_v1", side_effect=migrate_while_claimed
            ):
                result = arl.goal_focus_migrate_command(args)

            self.assertEqual(result["status"], "ok")
            self.assertFalse((loop / arl.MIGRATION_CLAIM_FILE).exists())

    def test_driver_registration_race_removes_own_entry_and_fails(self) -> None:
        arl, _ = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            registry_root = self._trusted_registry_root(base.name)
            arl.init_loop(arl.selftest_init_args(loop, max_iterations=2))
            args = argparse.Namespace(
                dir=str(loop),
                root=str(loop),
                force=False,
                pid=os.getpid(),
                driver=True,
                notify="off",
                registry_dir=str(registry_root),
            )

            with mock.patch.object(
                arl, "migration_claim_active", side_effect=[False, True]
            ):
                with self.assertRaises(arl.MigrationClaimError):
                    arl.arm_loop(args)

            active = registry_root / "active.d"
            self.assertEqual(list(active.glob("*.json")), [])

    def test_drive_fails_closed_when_driver_registration_fails(self) -> None:
        arl, _ = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            registry_root = self._trusted_registry_root(base.name)
            marker = base / "driver-ran"
            arl.init_loop(arl.selftest_init_args(loop, max_iterations=2))
            command = (
                '"/usr/bin/python3" -c "from pathlib import Path; '
                f"Path(r'{marker}').write_text('ran')\""
            )

            with mock.patch.object(
                arl, "arm_loop", side_effect=OSError("registry unavailable")
            ):
                result = arl.drive_command(
                    arl.selftest_drive_args(loop, registry_root, command)
                )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "driver_registration_failed")
            self.assertEqual(result["exit_code"], arl.DRIVE_EXIT_CODES["runtime_error"])
            self.assertFalse(marker.exists())

    def test_migration_releases_claim_when_apply_raises(self) -> None:
        arl, _ = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "legacy"
            registry_root = self._trusted_registry_root(base.name)
            arl.init_loop(arl.selftest_init_args(loop, max_iterations=2))
            args = argparse.Namespace(
                dir=str(loop),
                registry_dir=str(registry_root),
                apply=True,
                active_campaign="",
            )

            with mock.patch.object(
                arl.goal_focus_v2,
                "migrate_v1",
                side_effect=RuntimeError("simulated migration failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated migration failure"):
                    arl.goal_focus_migrate_command(args)

            self.assertFalse((loop / arl.MIGRATION_CLAIM_FILE).exists())

    def test_dead_migration_claim_is_reclaimed_but_malformed_claim_is_not(self) -> None:
        arl, _ = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "legacy"
            registry_root = self._trusted_registry_root(base.name)
            arl.init_loop(arl.selftest_init_args(loop, max_iterations=2))
            claim = loop / arl.MIGRATION_CLAIM_FILE
            claim.write_text(
                json.dumps({"pid": 987654321, "claimed_at": arl.utc_now()}) + "\n",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                dir=str(loop),
                registry_dir=str(registry_root),
                apply=True,
                active_campaign="",
            )
            with mock.patch.object(arl, "pid_alive", return_value=False), mock.patch.object(
                arl.goal_focus_v2,
                "migrate_v1",
                return_value={"status": "migrated", "applied": True},
            ) as migrated:
                result = arl.goal_focus_migrate_command(args)

            self.assertEqual(result["status"], "ok")
            migrated.assert_called_once()
            self.assertFalse(claim.exists())

            claim.write_text("not-json\n", encoding="utf-8")
            with mock.patch.object(arl.goal_focus_v2, "migrate_v1") as migrated:
                blocked = arl.goal_focus_migrate_command(args)
            self.assertEqual(blocked["status"], "failed")
            self.assertEqual(blocked["migration_status"], "migration_in_progress")
            migrated.assert_not_called()
            self.assertTrue(claim.exists())

    @unittest.skipUnless(os.name == "posix", "process ownership test uses POSIX inode replacement")
    def test_migration_claim_real_process_contention_reclaim_and_replacement_ownership(self) -> None:
        arl, _ = self._runtime_modules()
        owner_code = "\n".join(
            [
                "import json, os, sys, time",
                "from pathlib import Path",
                "import autonomous_research_loop_runtime as arl",
                "loop = Path(sys.argv[1])",
                "release = Path(sys.argv[2])",
                "identity = arl.acquire_migration_claim(loop)",
                "print(json.dumps({'pid': os.getpid(), 'identity': identity}), flush=True)",
                "deadline = time.monotonic() + 10",
                "while not release.exists() and time.monotonic() < deadline:",
                "    time.sleep(0.01)",
                "if not release.exists():",
                "    raise SystemExit('release signal timed out')",
                "try:",
                "    arl.release_migration_claim(loop, identity)",
                "except Exception as exc:",
                "    print(json.dumps({'released': False, 'error': str(exc)}), flush=True)",
                "else:",
                "    print(json.dumps({'released': True}), flush=True)",
            ]
        )
        dead_owner_code = "\n".join(
            [
                "import json, os, sys",
                "from pathlib import Path",
                "import autonomous_research_loop_runtime as arl",
                "identity = arl.acquire_migration_claim(Path(sys.argv[1]))",
                "print(json.dumps({'pid': os.getpid(), 'identity': identity}))",
            ]
        )
        child_env = _subprocess_env({"PYTHONPATH": str(HELPER.parent)})

        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "legacy"
            loop.mkdir()
            claim = loop / arl.MIGRATION_CLAIM_FILE

            def start_owner(signal: Path) -> tuple[subprocess.Popen[str], dict[str, Any]]:
                process = subprocess.Popen(
                    [sys.executable, "-B", "-u", "-c", owner_code, str(loop), str(signal)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=child_env,
                )
                assert process.stdout is not None
                first_line = process.stdout.readline()
                if not first_line:
                    _stdout, stderr = process.communicate(timeout=10)
                    self.fail(f"claim owner exited before readiness: {stderr}")
                return process, json.loads(first_line)

            release_first = Path(tmp) / "release-first"
            first_owner, first_ready = start_owner(release_first)
            try:
                self.assertNotEqual(first_ready["pid"], os.getpid())
                self.assertTrue(claim.is_file())
                with self.assertRaises(FileExistsError):
                    arl.acquire_migration_claim(loop)
                release_first.write_text("release\n", encoding="utf-8")
                stdout, stderr = first_owner.communicate(timeout=10)
                self.assertEqual(first_owner.returncode, 0, stderr)
                self.assertTrue(json.loads(stdout.strip())["released"])
                self.assertFalse(claim.exists())
            finally:
                if first_owner.poll() is None:
                    release_first.write_text("release\n", encoding="utf-8")
                    first_owner.communicate(timeout=10)

            dead_owner = subprocess.run(
                [sys.executable, "-B", "-c", dead_owner_code, str(loop)],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
                env=child_env,
            )
            dead_ready = json.loads(dead_owner.stdout)
            self.assertFalse(arl.pid_alive(dead_ready["pid"]))
            self.assertTrue(claim.is_file())
            self.assertTrue(arl.reclaim_dead_migration_claim(loop))
            self.assertFalse(claim.exists())

            release_replaced = Path(tmp) / "release-replaced"
            replacement_owner, replacement_ready = start_owner(release_replaced)
            try:
                old_identity = tuple(replacement_ready["identity"])
                replacement = loop / ".replacement-claim"
                replacement_payload = {
                    "pid": os.getpid(),
                    "claimed_at": arl.utc_now(),
                    "owner": "replacement",
                }
                replacement.write_text(
                    json.dumps(replacement_payload, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.replace(replacement, claim)
                replacement_info = os.lstat(claim)
                replacement_identity = (
                    int(replacement_info.st_dev),
                    int(replacement_info.st_ino),
                )
                self.assertNotEqual(replacement_identity, old_identity)

                release_replaced.write_text("release\n", encoding="utf-8")
                stdout, stderr = replacement_owner.communicate(timeout=10)
                self.assertEqual(replacement_owner.returncode, 0, stderr)
                release_result = json.loads(stdout.strip())
                self.assertFalse(release_result["released"])
                self.assertIn("identity changed", release_result["error"])
                observed, observed_identity = arl.migration_claim_snapshot(loop)
                self.assertEqual(observed["owner"], "replacement")
                self.assertEqual(observed_identity, replacement_identity)

                arl.release_migration_claim(loop, replacement_identity)
                self.assertFalse(claim.exists())
            finally:
                if replacement_owner.poll() is None:
                    release_replaced.write_text("release\n", encoding="utf-8")
                    replacement_owner.communicate(timeout=10)

    def test_enforce_driver_fails_closed_before_primary_or_authority_mutation(self) -> None:
        arl, gf = self._runtime_modules()
        with tempfile.TemporaryDirectory() as off_home_tmp:
            base = self.provider_fixture.root / "enforce-preflight"
            project = base / "project"
            loop = project / ".autoloop" / "loop"
            project.mkdir(parents=True, mode=0o700)
            (project / ".git").mkdir(mode=0o700)
            protected_files = {
                project / ".env": b"PRIVATE_ENV_SENTINEL\n",
                project / "project-secret": b"PRIVATE_PROJECT_SENTINEL\n",
                project / ".git" / "config": b"PRIVATE_VCS_SENTINEL\n",
                Path(off_home_tmp) / "off-home-secret": b"PRIVATE_EXTERNAL_SENTINEL\n",
            }
            for path, payload in protected_files.items():
                path.write_bytes(payload)
                if os.name == "posix":
                    path.chmod(0o600)
            (project / "reviewed-source.txt").write_text(
                "bounded public input\n", encoding="utf-8"
            )

            registry_dir = self._trusted_registry_root("enforce-preflight")
            init_args = arl.selftest_init_args(loop, max_iterations=1)
            init_args.goal_focus_mode = "enforce"
            arl.init_loop(init_args)
            self._activate_single_direction(arl, gf, loop)

            authority_paths = [
                loop / gf.GOAL_CONTRACT_FILE,
                loop / gf.APPROACH_REGISTRY_FILE,
                loop / gf.CURRENT_PLAN_FILE,
                loop / gf.DIRECTION_DECISIONS_FILE,
                loop / "iterations.jsonl",
                loop / "budget.json",
            ]
            authority_before = {path: path.read_bytes() for path in authority_paths}
            protected_before = {
                path: path.read_bytes() for path in protected_files
            }
            evidence_root = loop / Path(*gf.EVIDENCE_ROOT_PARTS)
            self.assertFalse(evidence_root.exists())

            drive_args = arl.selftest_drive_args(loop, registry_dir, "unused")
            drive_args.root = str(project)
            drive_args.cmd = None
            drive_args.provider = "claude"
            with mock.patch.object(
                gf, "prepare_iteration_dispatch"
            ) as prepare_dispatch, mock.patch.object(
                arl, "run_primary_subprocess"
            ) as primary, mock.patch.object(
                arl.subprocess, "Popen"
            ) as popen, mock.patch.object(
                arl, "consume_iteration_submission"
            ) as consume_submission, mock.patch.object(
                arl, "run_panel_phase_for_drive"
            ) as reviewer:
                result = arl.drive_command(drive_args)

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["action"], "drive")
            self.assertEqual(
                result["reason"], "secure_primary_transport_unavailable"
            )
            self.assertEqual(result["exit_code"], 4)
            prepare_dispatch.assert_not_called()
            primary.assert_not_called()
            popen.assert_not_called()
            consume_submission.assert_not_called()
            reviewer.assert_not_called()
            self.assertEqual(
                {path: path.read_bytes() for path in authority_paths},
                authority_before,
            )
            self.assertEqual(
                {path: path.read_bytes() for path in protected_files},
                protected_before,
            )
            self.assertFalse((loop / gf.ITERATION_DISPATCH_FILE).exists())
            self.assertFalse((loop / gf.PENDING_CANDIDATE_FILE).exists())
            self.assertFalse(evidence_root.exists())
            self.assertFalse((loop / "driver_logs").exists())
            self.assertEqual(list(registry_dir.iterdir()), [])

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "trusted-local transport requires Linux",
    )
    def test_trusted_local_primary_subprocess_enforces_limits_and_stdin(self) -> None:
        arl, _gf = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            arl,
            "trusted_local_containment_command",
            wraps=arl.trusted_local_containment_command,
        ) as containment:
            output = io.StringIO()
            resource_metadata: dict[str, object] = {}
            rc, timed_out, cleanup_error = arl.run_primary_subprocess(
                [
                    "/bin/sh",
                    "-c",
                    "IFS= read -r line; printf 'seen:%s\\n' \"$line\"",
                ],
                use_shell=False,
                child_env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin"},
                cwd=Path(tmp),
                timeout_s=30,
                output=output,
                provider="claude",
                enforce_mode=True,
                trusted_local=True,
                stdin_text="bounded-primary-input\n",
                resource_metadata=resource_metadata,
            )
        self.assertEqual(rc, 0, output.getvalue())
        self.assertFalse(timed_out)
        self.assertIsNone(cleanup_error)
        self.assertEqual(output.getvalue(), "seen:bounded-primary-input\n")
        self.assertEqual(
            resource_metadata["schema_version"],
            "provider_resource_attestation.v1",
        )
        self.assertEqual(resource_metadata["role"], "primary")
        self.assertTrue(resource_metadata["cleanup_verified"])
        self.assertEqual(resource_metadata["output_capture"], "bounded-pipe")
        self.assertIn("memory_max_bytes", resource_metadata["limits"])
        containment.assert_called_once()

    def test_trusted_local_invalid_primary_limits_deny_before_spawn(self) -> None:
        arl, _gf = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {"AAS_AUTOLOOP_RESOURCE_MEMORY_MIB": "invalid"},
            clear=False,
        ), mock.patch.object(arl.subprocess, "Popen") as popen:
            with self.assertRaises(OSError):
                arl.run_primary_subprocess(
                    ["/bin/true"],
                    use_shell=False,
                    child_env={"HOME": str(Path.home())},
                    cwd=Path(tmp),
                    timeout_s=30,
                    output=io.StringIO(),
                    provider="claude",
                    enforce_mode=True,
                    trusted_local=True,
                )
        popen.assert_not_called()

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "trusted-local transport requires Linux",
    )
    def test_trusted_local_primary_cpu_limit_terminates_before_wall_timeout(self) -> None:
        arl, _gf = self._runtime_modules()
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {"AAS_AUTOLOOP_RESOURCE_CPU_SECONDS": "1"},
            clear=False,
        ):
            output = io.StringIO()
            rc, timed_out, cleanup_error = arl.run_primary_subprocess(
                ["/bin/sh", "-c", "while :; do :; done"],
                use_shell=False,
                child_env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin"},
                cwd=Path(tmp),
                timeout_s=10,
                output=output,
                provider="claude",
                enforce_mode=True,
                trusted_local=True,
            )
        self.assertNotEqual(rc, 0)
        self.assertFalse(timed_out)
        self.assertIsNone(cleanup_error)
        self.assertLess(time.monotonic() - started, 8)

    def test_trusted_local_enforce_driver_passes_transport_preflight(self) -> None:
        arl, gf = self._runtime_modules()
        base = self.provider_fixture.root / "trusted-local-preflight"
        project = base / "project"
        loop = project / ".autoloop" / "loop"
        project.mkdir(parents=True, mode=0o700)
        registry_dir = self._trusted_registry_root("trusted-local-preflight")
        init_args = arl.selftest_init_args(loop, max_iterations=1)
        init_args.goal_focus_mode = "enforce"
        arl.init_loop(init_args)
        self._activate_single_direction(arl, gf, loop)
        (loop / "STOP_REQUESTED").write_text("test\n", encoding="utf-8")
        args = arl.selftest_drive_args(loop, registry_dir, "unused")
        args.cmd = None
        args.provider = "claude"
        profile = arl.provider_resource_limits(60, role="primary")
        with mock.patch.dict(
            os.environ,
            {"AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local"},
            clear=False,
        ), mock.patch.object(
            arl, "preflight_resource_backend", return_value=profile
        ) as preflight, mock.patch.object(
            arl, "run_primary_subprocess"
        ) as primary:
            result = arl.drive_command(args)
        preflight.assert_called_once_with(60, role="primary")
        primary.assert_not_called()
        self.assertNotEqual(
            result.get("reason"), "secure_primary_transport_unavailable", result
        )
        self.assertEqual(result.get("exit_code"), 0, result)

    def test_trusted_local_resources_apply_independently_of_goal_focus_mode(self) -> None:
        arl, _gf = self._runtime_modules()
        for mode in ("off", "monitor"):
            with self.subTest(mode=mode):
                base = self.provider_fixture.root / f"resource-axis-{mode}"
                project = base / "project"
                loop = project / ".autoloop" / "loop"
                project.mkdir(parents=True, mode=0o700)
                registry_dir = self._trusted_registry_root(
                    f"resource-axis-{mode}"
                )
                init_args = arl.selftest_init_args(loop, max_iterations=1)
                init_args.goal_focus_mode = mode
                arl.init_loop(init_args)
                args = arl.selftest_drive_args(loop, registry_dir, "/bin/false")
                args.root = str(project)
                args.max_failures = 1
                profile = arl.provider_resource_limits(60, role="primary")
                with mock.patch.dict(
                    os.environ,
                    {"AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local"},
                    clear=False,
                ), mock.patch.object(
                    arl, "preflight_resource_backend", return_value=profile
                ) as preflight, mock.patch.object(
                    arl,
                    "run_primary_subprocess",
                    return_value=(1, False, None),
                ) as primary, mock.patch.object(
                    sys, "stdout", io.StringIO()
                ), mock.patch.object(
                    sys, "stderr", io.StringIO()
                ):
                    arl.drive_command(args)

                preflight.assert_called_once_with(60, role="primary")
                primary.assert_called_once()
                self.assertTrue(primary.call_args.kwargs["trusted_local"])

    def test_trusted_local_invalid_limits_fail_before_legacy_registration(self) -> None:
        arl, _gf = self._runtime_modules()
        base = self.provider_fixture.root / "resource-axis-invalid"
        project = base / "project"
        loop = project / ".autoloop" / "loop"
        project.mkdir(parents=True, mode=0o700)
        registry_dir = self._trusted_registry_root("resource-axis-invalid")
        init_args = arl.selftest_init_args(loop, max_iterations=1)
        init_args.goal_focus_mode = "off"
        arl.init_loop(init_args)
        args = arl.selftest_drive_args(loop, registry_dir, "/bin/false")
        args.root = str(project)
        with mock.patch.dict(
            os.environ,
            {
                "AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local",
                "AAS_AUTOLOOP_RESOURCE_MEMORY_MIB": "1",
            },
            clear=False,
        ), mock.patch.object(arl, "arm_loop") as arm, mock.patch.object(
            arl, "run_primary_subprocess"
        ) as primary:
            result = arl.drive_command(args)

        self.assertEqual(result["reason"], "resource_limits_unavailable", result)
        arm.assert_not_called()
        primary.assert_not_called()

    def test_trusted_local_cleanup_failure_is_terminal_in_every_goal_focus_mode(self) -> None:
        arl, _gf = self._runtime_modules()
        for mode in ("off", "monitor"):
            with self.subTest(mode=mode):
                base = self.provider_fixture.root / f"cleanup-axis-{mode}"
                project = base / "project"
                loop = project / ".autoloop" / "loop"
                project.mkdir(parents=True, mode=0o700)
                registry_dir = self._trusted_registry_root(
                    f"cleanup-axis-{mode}"
                )
                init_args = arl.selftest_init_args(loop, max_iterations=4)
                init_args.goal_focus_mode = mode
                arl.init_loop(init_args)
                args = arl.selftest_drive_args(loop, registry_dir, "/bin/false")
                args.root = str(project)
                args.max_failures = 3
                profile = arl.provider_resource_limits(60, role="primary")
                with mock.patch.dict(
                    os.environ,
                    {"AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local"},
                    clear=False,
                ), mock.patch.object(
                    arl, "preflight_resource_backend", return_value=profile
                ), mock.patch.object(
                    arl,
                    "run_primary_subprocess",
                    return_value=(126, False, "synthetic descendants survived"),
                ) as primary, mock.patch.object(
                    sys, "stdout", io.StringIO()
                ), mock.patch.object(
                    sys, "stderr", io.StringIO()
                ):
                    result = arl.drive_command(args)

                primary.assert_called_once()
                self.assertEqual(
                    result["reason"], "resource_cleanup_unverified", result
                )
                self.assertEqual(
                    result["exit_code"],
                    arl.DRIVE_EXIT_CODES["resource_cleanup_unverified"],
                )

    def test_strict_isolated_monitor_retains_legacy_nonresource_path(self) -> None:
        arl, _gf = self._runtime_modules()
        base = self.provider_fixture.root / "resource-axis-strict-monitor"
        project = base / "project"
        loop = project / ".autoloop" / "loop"
        project.mkdir(parents=True, mode=0o700)
        registry_dir = self._trusted_registry_root(
            "resource-axis-strict-monitor"
        )
        init_args = arl.selftest_init_args(loop, max_iterations=1)
        init_args.goal_focus_mode = "monitor"
        arl.init_loop(init_args)
        args = arl.selftest_drive_args(loop, registry_dir, "/bin/false")
        args.root = str(project)
        args.max_failures = 1
        with mock.patch.dict(
            os.environ,
            {"AAS_AUTOLOOP_PROVIDER_TRANSPORT": "strict-isolated"},
            clear=False,
        ), mock.patch.object(
            arl, "preflight_resource_backend"
        ) as preflight, mock.patch.object(
            arl,
            "run_primary_subprocess",
            return_value=(1, False, None),
        ) as primary, mock.patch.object(
            sys, "stdout", io.StringIO()
        ), mock.patch.object(
            sys, "stderr", io.StringIO()
        ):
            arl.drive_command(args)

        preflight.assert_not_called()
        primary.assert_called_once()
        self.assertFalse(primary.call_args.kwargs["trusted_local"])

    def test_trusted_local_resource_preflight_failure_precedes_registration(self) -> None:
        arl, gf = self._runtime_modules()
        base = self.provider_fixture.root / "resource-preflight-failure"
        project = base / "project"
        loop = project / ".autoloop" / "loop"
        project.mkdir(parents=True, mode=0o700)
        registry_dir = self._trusted_registry_root("resource-preflight-failure")
        init_args = arl.selftest_init_args(loop, max_iterations=1)
        init_args.goal_focus_mode = "enforce"
        arl.init_loop(init_args)
        self._activate_single_direction(arl, gf, loop)
        args = arl.selftest_drive_args(loop, registry_dir, "unused")
        args.cmd = None
        args.provider = "claude"
        with mock.patch.dict(
            os.environ,
            {"AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local"},
            clear=False,
        ), mock.patch.object(
            arl,
            "preflight_resource_backend",
            side_effect=arl.ProviderResourceError("bounded fixture failure"),
        ), mock.patch.object(arl, "arm_loop") as arm:
            result = arl.drive_command(args)
        arm.assert_not_called()
        self.assertEqual(result["reason"], "resource_limits_unavailable", result)
        self.assertEqual(result["exit_code"], 4)

    def test_resource_preflight_cleanup_failure_is_nonretryable(self) -> None:
        arl, gf = self._runtime_modules()
        base = self.provider_fixture.root / "resource-preflight-cleanup-failure"
        project = base / "project"
        loop = project / ".autoloop" / "loop"
        project.mkdir(parents=True, mode=0o700)
        registry_dir = self._trusted_registry_root(
            "resource-preflight-cleanup-failure"
        )
        init_args = arl.selftest_init_args(loop, max_iterations=1)
        init_args.goal_focus_mode = "enforce"
        arl.init_loop(init_args)
        self._activate_single_direction(arl, gf, loop)
        args = arl.selftest_drive_args(loop, registry_dir, "unused")
        args.cmd = None
        args.provider = "claude"
        with mock.patch.dict(
            os.environ,
            {"AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local"},
            clear=False,
        ), mock.patch.object(
            arl,
            "preflight_resource_backend",
            side_effect=arl.ProviderResourceCleanupError(
                "synthetic scope survived cleanup"
            ),
        ), mock.patch.object(arl, "arm_loop") as arm:
            result = arl.drive_command(args)
        arm.assert_not_called()
        self.assertEqual(result["reason"], "resource_cleanup_unverified", result)
        self.assertEqual(result["exit_code"], 8)

    def test_panel_cleanup_failure_crosses_drive_as_nonretryable(self) -> None:
        arl, _gf = self._runtime_modules()
        base = self.provider_fixture.root / "panel-cleanup-fatal-drive"
        project = base / "project"
        loop = project / ".autoloop" / "loop"
        project.mkdir(parents=True, mode=0o700)
        registry_dir = self._trusted_registry_root("panel-cleanup-fatal-drive")
        init_args = arl.selftest_init_args(loop, max_iterations=1)
        init_args.goal_focus_mode = "enforce"
        arl.init_loop(init_args)
        args = arl.selftest_drive_args(loop, registry_dir, "unused")
        args.cmd = None
        args.provider = "claude"
        profile = arl.provider_resource_limits(60, role="primary")
        fatal_panel = {
            "fatal_resource_cleanup_failure": True,
            "resource_cleanup_verified": False,
            "panel_content_pass": False,
        }
        with mock.patch.dict(
            os.environ,
            {"AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local"},
            clear=False,
        ), mock.patch.object(
            arl, "preflight_resource_backend", return_value=profile
        ), mock.patch.object(
            arl, "run_panel_phase_for_drive", return_value=fatal_panel
        ) as panel, mock.patch.object(
            arl, "run_primary_subprocess"
        ) as primary:
            result = arl.drive_command(args)
        panel.assert_called_once()
        primary.assert_not_called()
        self.assertEqual(result["reason"], "resource_cleanup_unverified", result)
        self.assertEqual(result["exit_code"], 8)

    def test_cleanup_error_prevents_submission_stage_and_result_review(self) -> None:
        arl, gf = self._runtime_modules()
        base = self.provider_fixture.root / "cleanup-error-bank-gate"
        project = base / "project"
        loop = project / ".autoloop" / "loop"
        project.mkdir(parents=True, mode=0o700)
        registry_dir = self._trusted_registry_root("cleanup-error-bank-gate")
        init_args = arl.selftest_init_args(loop, max_iterations=1)
        init_args.goal_focus_mode = "enforce"
        arl.init_loop(init_args)
        self._activate_single_direction(arl, gf, loop)

        args = arl.selftest_drive_args(loop, registry_dir, "unused")
        args.root = str(project)
        args.cmd = None
        args.provider = "claude"
        profile = arl.provider_resource_limits(60, role="primary")
        with mock.patch.dict(
            os.environ,
            {"AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local"},
            clear=False,
        ), mock.patch.object(
            arl, "preflight_resource_backend", return_value=profile
        ), mock.patch.object(
            arl,
            "run_primary_subprocess",
            return_value=(0, False, "provider resource scope cleanup failed"),
        ) as primary, mock.patch.object(
            arl, "consume_iteration_submission"
        ) as consume_submission, mock.patch.object(
            gf, "stage_iteration_candidate"
        ) as stage_candidate, mock.patch.object(
            arl, "run_panel_phase_for_drive"
        ) as reviewer:
            result = arl.drive_command(args)

        primary.assert_called_once()
        consume_submission.assert_not_called()
        stage_candidate.assert_not_called()
        reviewer.assert_not_called()
        self.assertEqual(result["reason"], "resource_cleanup_unverified", result)
        self.assertEqual(result["exit_code"], 8)
        self.assertIsNone(gf.load_pending_candidate(loop))
        self.assertIsNone(gf.load_iteration_dispatch(loop))
        quarantine = gf.load_candidate_quarantine(loop)
        self.assertIsNotNone(quarantine)
        self.assertEqual(quarantine["object_kind"], "dispatch")
        self.assertEqual(
            gf.pre_dispatch_gate(loop)["action"], "candidate_quarantined"
        )
        self.assertEqual(arl.read_iterations(loop / "iterations.jsonl"), [])

    def test_early_submission_after_incomplete_prompt_transport_is_not_staged(self) -> None:
        arl, gf = self._runtime_modules()
        base = self.provider_fixture.root / "early-incomplete-submission"
        project = base / "project"
        loop = project / ".autoloop" / "loop"
        project.mkdir(parents=True, mode=0o700)
        registry_dir = self._trusted_registry_root("early-incomplete-submission")
        init_args = arl.selftest_init_args(loop, max_iterations=2)
        init_args.goal_focus_mode = "enforce"
        arl.init_loop(init_args)
        self._activate_single_direction(arl, gf, loop)

        args = arl.selftest_drive_args(loop, registry_dir, "unused")
        args.root = str(project)
        args.cmd = None
        args.provider = "claude"
        profile = arl.provider_resource_limits(60, role="primary")

        def write_early_submission(*_args, **kwargs):  # noqa: ANN002, ANN003
            evidence_dir = Path(kwargs["evidence_dir"])
            child_env = kwargs["child_env"]
            evidence_id = "early-evidence.txt"
            evidence = evidence_dir / evidence_id
            evidence.write_text(
                "complete evidence emitted before prompt transport failed\n",
                encoding="utf-8",
            )
            if os.name == "posix":
                evidence.chmod(0o600)

            request: dict[str, object] = {}
            for field in arl.ITERATION_SUBMISSION_ARG_FIELDS:
                if field in arl.ITERATION_SUBMISSION_LIST_FIELDS:
                    request[field] = []
                elif field in arl.ITERATION_SUBMISSION_BOOL_FIELDS:
                    request[field] = False
                elif field in arl.ITERATION_SUBMISSION_INT_FIELDS:
                    request[field] = 0
                elif field == "usd":
                    request[field] = 0.0
                else:
                    request[field] = ""
            request.update(
                {
                    "mode": "bounded-research",
                    "objective": "attempt an early bounded result",
                    "decision": "continue",
                    "claim_id": ["CLAIM-EARLY"],
                    "evidence_id": [evidence_id],
                    "output": "early candidate must not cross the host gate",
                    "compute_none": True,
                    "executor_provider": "claude",
                }
            )
            submission = {
                "schema_version": arl.ITERATION_SUBMISSION_SCHEMA,
                "run_id": child_env["AAS_AUTOLOOP_RUN_ID"],
                "dispatch_id": child_env["AAS_AUTOLOOP_DISPATCH_ID"],
                "candidate_id": child_env["AAS_AUTOLOOP_CANDIDATE_ID"],
                "executor_provider": "claude",
                "request": request,
            }
            submission_path = evidence_dir / arl.ITERATION_SUBMISSION_FILENAME
            submission_path.write_text(
                json.dumps(submission, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if os.name == "posix":
                submission_path.chmod(0o600)

            metadata = kwargs["resource_metadata"]
            metadata.update(
                {
                    "schema_version": "provider_resource_attestation.v1",
                    "provider_transport": "trusted-local",
                    "role": "primary",
                    "resource_gate": "pre-exec-cgroup-rlimit-v1",
                    "scope_unit": "aas-arl-primary-1234-feedfacefeed.scope",
                    "limits": arl.public_resource_limits(profile),
                    "output_capture": "bounded-pipe",
                    "control_plane_masked": True,
                    "cgroup_api_masked": True,
                    "cleanup_verified": True,
                    "capture_verified": False,
                    "timed_out": False,
                    "oversized_output": False,
                    "sensitive_output_blocked": False,
                    "finished_at": "2026-07-29T00:01:00Z",
                }
            )
            # A nonzero transport result plus capture_verified=False models a
            # provider that wrote a candidate before stdin delivery completed.
            return 17, False, None

        with mock.patch.dict(
            os.environ,
            {"AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local"},
            clear=False,
        ), mock.patch.object(
            arl, "preflight_resource_backend", return_value=profile
        ), mock.patch.object(
            arl,
            "run_primary_subprocess",
            side_effect=write_early_submission,
        ) as primary, mock.patch.object(
            arl,
            "consume_iteration_submission",
            wraps=arl.consume_iteration_submission,
        ) as consume_submission, mock.patch.object(
            arl, "run_panel_phase_for_drive"
        ) as reviewer:
            arl.drive_command(args)

        primary.assert_called_once()
        consume_submission.assert_not_called()
        reviewer.assert_not_called()
        self.assertIsNone(gf.load_pending_candidate(loop))
        self.assertEqual(arl.read_iterations(loop / "iterations.jsonl"), [])
        self.assertEqual(
            arl.read_json(loop / "budget.json")["spent_iterations"], 0
        )

    def test_timed_out_primary_submission_has_specific_failure_class(self) -> None:
        arl, gf = self._runtime_modules()
        base = self.provider_fixture.root / "timed-out-primary-submission"
        project = base / "project"
        loop = project / ".autoloop" / "loop"
        project.mkdir(parents=True, mode=0o700)
        registry_dir = self._trusted_registry_root("timed-out-primary-submission")
        init_args = arl.selftest_init_args(loop, max_iterations=1)
        init_args.goal_focus_mode = "enforce"
        arl.init_loop(init_args)
        self._activate_single_direction(arl, gf, loop)

        args = arl.selftest_drive_args(loop, registry_dir, "unused")
        args.root = str(project)
        args.cmd = None
        args.provider = "claude"
        args.max_failures = 1
        profile = arl.provider_resource_limits(60, role="primary")
        submission_paths: list[Path] = []

        def write_submission_then_timeout(*_args, **kwargs):  # noqa: ANN002, ANN003
            evidence_dir = Path(kwargs["evidence_dir"])
            child_env = kwargs["child_env"]
            request: dict[str, object] = {}
            for field in arl.ITERATION_SUBMISSION_ARG_FIELDS:
                if field in arl.ITERATION_SUBMISSION_LIST_FIELDS:
                    request[field] = []
                elif field in arl.ITERATION_SUBMISSION_BOOL_FIELDS:
                    request[field] = False
                elif field in arl.ITERATION_SUBMISSION_INT_FIELDS:
                    request[field] = 0
                elif field == "usd":
                    request[field] = 0.0
                else:
                    request[field] = ""
            request.update(
                {
                    "mode": "bounded-research",
                    "objective": "emit a candidate before the host wall timeout",
                    "decision": "continue",
                    "output": "timed-out candidate must not cross the host gate",
                    "compute_none": True,
                    "executor_provider": "claude",
                }
            )
            submission_path = evidence_dir / arl.ITERATION_SUBMISSION_FILENAME
            submission_path.write_text(
                json.dumps(
                    {
                        "schema_version": arl.ITERATION_SUBMISSION_SCHEMA,
                        "run_id": child_env["AAS_AUTOLOOP_RUN_ID"],
                        "dispatch_id": child_env["AAS_AUTOLOOP_DISPATCH_ID"],
                        "candidate_id": child_env["AAS_AUTOLOOP_CANDIDATE_ID"],
                        "executor_provider": "claude",
                        "request": request,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            if os.name == "posix":
                submission_path.chmod(0o600)
            submission_paths.append(submission_path)

            metadata = kwargs["resource_metadata"]
            metadata.update(_primary_resource_attestation())
            metadata["limits"] = arl.public_resource_limits(profile)
            metadata["timed_out"] = True
            return 126, True, None

        stderr = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {"AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local"},
            clear=False,
        ), mock.patch.object(
            arl, "preflight_resource_backend", return_value=profile
        ), mock.patch.object(
            arl,
            "run_primary_subprocess",
            side_effect=write_submission_then_timeout,
        ) as primary, mock.patch.object(
            arl, "consume_iteration_submission"
        ) as consume_submission, mock.patch.object(
            arl, "run_panel_phase_for_drive"
        ) as reviewer, mock.patch.object(sys, "stderr", stderr):
            result = arl.drive_command(args)

        primary.assert_called_once()
        consume_submission.assert_not_called()
        reviewer.assert_not_called()
        self.assertEqual(result["reason"], "max_failures", result)
        self.assertEqual(len(submission_paths), 1)
        self.assertTrue(submission_paths[0].is_file())
        self.assertIn(
            "primary exceeded its enforced wall-time limit", stderr.getvalue()
        )
        self.assertNotIn(
            "host provider resource attestation is invalid", stderr.getvalue()
        )
        self.assertIsNone(gf.load_pending_candidate(loop))
        self.assertEqual(arl.read_iterations(loop / "iterations.jsonl"), [])
        progress = [
            json.loads(line)
            for line in (loop / "driver_logs" / "progress.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        failed = [event for event in progress if event.get("event") == "iteration_failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["failure_class"], "timeout")
        self.assertIs(failed[0]["timed_out"], True)

    def test_direct_staged_candidate_is_quarantined_after_failed_host_gate(self) -> None:
        cases = (
            (
                "timeout",
                {"timed_out": True},
                True,
                None,
                "primary exceeded its enforced wall-time limit",
                "candidate_quarantined",
                "",
            ),
            (
                "capture",
                {"capture_verified": False},
                False,
                None,
                "primary output capture was not verified",
                "candidate_quarantined",
                "",
            ),
            (
                "oversized",
                {"oversized_output": True},
                False,
                None,
                "primary exceeded its bounded output limit",
                "candidate_quarantined",
                "",
            ),
            (
                "sensitive",
                {"sensitive_output_blocked": True},
                False,
                None,
                "primary emitted blocked sensitive output",
                "candidate_quarantined",
                "",
            ),
            (
                "nonzero",
                {},
                False,
                None,
                "candidate existed after the host submission gate failed",
                "candidate_quarantined",
                "",
            ),
            (
                "auth-tail",
                {},
                False,
                None,
                "candidate existed after the host submission gate failed",
                "candidate_quarantined",
                "authentication failed; please log in again",
            ),
            (
                "quota-tail",
                {},
                False,
                None,
                "candidate existed after the host submission gate failed",
                "candidate_quarantined",
                "usage limit reached; quota exhausted",
            ),
            (
                "cleanup",
                {},
                False,
                "descendant cleanup could not be verified",
                "primary resource cleanup was not verified",
                "resource_cleanup_unverified",
                "",
            ),
        )
        for (
            label,
            final_overrides,
            timed_out,
            cleanup_error,
            expected_reason,
            expected_drive_reason,
            log_text,
        ) in cases:
            with self.subTest(label=label):
                arl, gf = self._runtime_modules()
                base = self.provider_fixture.root / f"direct-stage-{label}"
                project = base / "project"
                loop = project / ".autoloop" / "loop"
                project.mkdir(parents=True, mode=0o700)
                registry_dir = self._trusted_registry_root(f"direct-stage-{label}")
                init_args = arl.selftest_init_args(loop, max_iterations=1)
                init_args.goal_focus_mode = "enforce"
                arl.init_loop(init_args)
                self._activate_single_direction(arl, gf, loop)

                args = arl.selftest_drive_args(loop, registry_dir, "unused")
                args.root = str(project)
                args.cmd = None
                args.provider = "claude"
                args.max_failures = 1
                profile = arl.provider_resource_limits(60, role="primary")
                staged_candidates: list[dict[str, Any]] = []

                def direct_stage_then_fail(*_args, **kwargs):  # noqa: ANN002, ANN003
                    dispatch = gf.load_iteration_dispatch(loop)
                    self.assertIsNotNone(dispatch)
                    write_text_evidence(loop, dispatch, "direct-stage-evidence")
                    clean_attestation = _primary_resource_attestation()
                    clean_attestation["limits"] = arl.public_resource_limits(profile)
                    staged = gf.stage_iteration_candidate(
                        loop,
                        {
                            "schema_version": "1.0",
                            "candidate_id": dispatch["candidate_id"],
                            "iteration": 1,
                            "mode": "bounded-research",
                            "objective": "attempt direct staging before a failed host gate",
                            "decision": "continue",
                            "claim_ids": ["direct-stage-claim"],
                            "evidence_checked": {
                                "claim_ids": ["direct-stage-claim"],
                                "evidence_ids": ["direct-stage-evidence"],
                            },
                            "budget_delta": {"iterations": 1},
                            "execution": {
                                "executor_provider": "claude",
                                "compute": {
                                    "recording_status": "explicit",
                                    "usage": "none",
                                    "services": [],
                                },
                            },
                            "goal_focus": {
                                "plan_revision": dispatch["plan_revision"],
                                "campaign_id": dispatch["campaign_id"],
                                "approach_id": dispatch["approach_id"],
                            },
                        },
                        expected_plan_revision=dispatch["plan_revision"],
                        expected_dispatch_id=dispatch["dispatch_id"],
                        host_resource_attestation=clean_attestation,
                    )["candidate"]
                    staged_candidates.append(staged)
                    metadata = kwargs["resource_metadata"]
                    metadata.update(clean_attestation)
                    metadata.update(final_overrides)
                    if log_text:
                        kwargs["output"].write(log_text + "\n")
                        kwargs["output"].flush()
                    return 126 if timed_out else 17, timed_out, cleanup_error

                with mock.patch.dict(
                    os.environ,
                    {"AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local"},
                    clear=False,
                ), mock.patch.object(
                    arl, "preflight_resource_backend", return_value=profile
                ), mock.patch.object(
                    arl,
                    "run_primary_subprocess",
                    side_effect=direct_stage_then_fail,
                ) as primary, mock.patch.object(
                    arl,
                    "consume_iteration_submission",
                    wraps=arl.consume_iteration_submission,
                ), mock.patch.object(
                    arl, "run_panel_phase_for_drive"
                ) as reviewer, mock.patch.object(
                    gf, "finalize_candidate", wraps=gf.finalize_candidate
                ) as finalizer:
                    result = arl.drive_command(args)

                primary.assert_called_once()
                reviewer.assert_not_called()
                finalizer.assert_not_called()
                self.assertEqual(result["reason"], expected_drive_reason, result)
                self.assertEqual(
                    result["exit_code"], arl.DRIVE_EXIT_CODES[expected_drive_reason]
                )
                self.assertEqual(len(staged_candidates), 1)
                self.assertIsNone(gf.load_pending_candidate(loop))
                self.assertIsNone(gf.load_iteration_dispatch(loop))
                quarantine = gf.load_candidate_quarantine(loop)
                self.assertIsNotNone(quarantine)
                self.assertEqual(quarantine["candidate"], staged_candidates[0])
                self.assertEqual(
                    quarantine["candidate_fingerprint"],
                    gf.candidate_fingerprint(staged_candidates[0]),
                )
                self.assertIn(expected_reason, quarantine["reason"])
                self.assertEqual(arl.read_iterations(loop / "iterations.jsonl"), [])
                self.assertEqual(
                    arl.read_json(loop / "budget.json")["spent_iterations"], 0
                )
                self.assertEqual(
                    gf.pre_dispatch_gate(loop)["action"], "candidate_quarantined"
                )
                progress = [
                    json.loads(line)
                    for line in (loop / "driver_logs" / "progress.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line.strip()
                ]
                self.assertTrue(
                    any(
                        event.get("review_status") == "error"
                        and event.get("candidate_id") == quarantine["candidate_id"]
                        for event in progress
                    ),
                    progress,
                )
                if label in {"auth-tail", "quota-tail"}:
                    self.assertFalse(
                        any(
                            event.get("event") in {"auth_failure", "quota_wait"}
                            for event in progress
                        ),
                        progress,
                    )

                if cleanup_error is not None:
                    with mock.patch.dict(
                        os.environ,
                        {"AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local"},
                        clear=False,
                    ), mock.patch.object(
                        arl, "preflight_resource_backend", return_value=profile
                    ), mock.patch.object(
                        arl, "run_primary_subprocess"
                    ) as restarted_primary, mock.patch.object(
                        arl, "run_panel_phase_for_drive"
                    ) as restarted_reviewer, mock.patch.object(
                        gf, "finalize_candidate", wraps=gf.finalize_candidate
                    ) as restarted_finalizer:
                        restarted = arl.drive_command(args)
                    self.assertEqual(
                        restarted["reason"], "candidate_quarantined", restarted
                    )
                    restarted_primary.assert_not_called()
                    restarted_reviewer.assert_not_called()
                    restarted_finalizer.assert_not_called()
                    self.assertEqual(arl.read_iterations(loop / "iterations.jsonl"), [])
                    self.assertEqual(
                        arl.read_json(loop / "budget.json")["spent_iterations"], 0
                    )

                repeated = gf.quarantine_pending_candidate(
                    loop, reason="must remain idempotently quarantined"
                )
                self.assertEqual(repeated["status"], "already_quarantined")
                if label == "timeout":
                    fingerprint = quarantine["candidate_fingerprint"]
                    status = arl.goal_focus_status_command(
                        argparse.Namespace(dir=str(loop))
                    )
                    self.assertEqual(
                        status["candidate_quarantine"]["candidate_fingerprint"],
                        fingerprint,
                    )
                    visible = arl.goal_focus_recover_quarantine_command(
                        argparse.Namespace(
                            dir=str(loop),
                            release=False,
                            candidate_fingerprint="",
                        )
                    )
                    self.assertEqual(
                        visible["recovery_status"], "awaiting_explicit_release"
                    )
                    mismatch = arl.goal_focus_recover_quarantine_command(
                        argparse.Namespace(
                            dir=str(loop),
                            release=True,
                            candidate_fingerprint="sha256:wrong",
                        )
                    )
                    self.assertEqual(mismatch["status"], "failed")
                    released = arl.goal_focus_recover_quarantine_command(
                        argparse.Namespace(
                            dir=str(loop),
                            release=True,
                            candidate_fingerprint=fingerprint,
                        )
                    )
                    self.assertEqual(released["recovery_status"], "released")
                    self.assertIsNone(gf.load_candidate_quarantine(loop))
                    archived = arl.read_json(
                        loop / released["result"]["archive"]
                    )
                    self.assertEqual(archived["candidate"], staged_candidates[0])
                    self.assertEqual(
                        gf.release_candidate_quarantine(
                            loop, expected_candidate_fingerprint=fingerprint
                        )["status"],
                        "absent",
                    )

    def test_cleanup_tombstone_serializes_with_candidate_staging(self) -> None:
        arl, gf = self._runtime_modules()

        def make_record(dispatch: dict[str, Any], evidence_id: str) -> dict[str, Any]:
            return {
                "schema_version": "1.0",
                "candidate_id": dispatch["candidate_id"],
                "iteration": 1,
                "mode": "bounded-research",
                "objective": "serialize cleanup tombstone with staging",
                "decision": "continue",
                "claim_ids": [f"claim-{evidence_id}"],
                "evidence_checked": {
                    "claim_ids": [f"claim-{evidence_id}"],
                    "evidence_ids": [evidence_id],
                },
                "budget_delta": {"iterations": 1},
                "execution": {
                    "executor_provider": "claude",
                    "compute": {
                        "recording_status": "explicit",
                        "usage": "none",
                        "services": [],
                    },
                },
                "goal_focus": {
                    "plan_revision": dispatch["plan_revision"],
                    "campaign_id": dispatch["campaign_id"],
                    "approach_id": dispatch["approach_id"],
                },
            }

        for outcome in ("tombstone-wins", "stage-wins"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                loop = Path(tmp) / "loop"
                init_args = arl.selftest_init_args(loop, max_iterations=2)
                init_args.goal_focus_mode = "enforce"
                arl.init_loop(init_args)
                self._activate_single_direction(arl, gf, loop)
                dispatch = gf.prepare_iteration_dispatch(
                    loop,
                    executor_provider="claude",
                    executor_family="anthropic",
                    executor_attestation=_provider_attestation("claude", loop),
                    started_at="2026-07-29T12:00:00Z",
                    driver_pid=12345,
                )["dispatch"]
                evidence_id = f"race-{outcome}"
                write_text_evidence(loop, dispatch, evidence_id)
                record = make_record(dispatch, evidence_id)
                attestation = _primary_resource_attestation()

                if outcome == "tombstone-wins":
                    result = gf.quarantine_failed_completion(
                        loop,
                        reason="primary cleanup could not be verified",
                        fallback_dispatch=dispatch,
                    )
                    self.assertEqual(result["status"], "quarantined")
                    self.assertEqual(
                        result["quarantine"]["object_kind"], "dispatch"
                    )
                    with self.assertRaises(gf.RevisionConflict):
                        gf.stage_iteration_candidate(
                            loop,
                            record,
                            expected_plan_revision=dispatch["plan_revision"],
                            expected_dispatch_id=dispatch["dispatch_id"],
                            host_resource_attestation=attestation,
                        )
                else:
                    real_commit = gf.commit_transaction
                    injected = {"done": False}

                    def commit_with_stage_race(*args, **kwargs):  # noqa: ANN002, ANN003
                        transaction_id = str(kwargs.get("transaction_id") or "")
                        if transaction_id.startswith("quarantine-") and not injected["done"]:
                            injected["done"] = True
                            gf.stage_iteration_candidate(
                                loop,
                                record,
                                expected_plan_revision=dispatch["plan_revision"],
                                expected_dispatch_id=dispatch["dispatch_id"],
                                host_resource_attestation=attestation,
                            )
                        return real_commit(*args, **kwargs)

                    with mock.patch.object(
                        gf, "commit_transaction", side_effect=commit_with_stage_race
                    ):
                        result = gf.quarantine_failed_completion(
                            loop,
                            reason="primary cleanup could not be verified",
                            fallback_dispatch=dispatch,
                        )
                    self.assertTrue(injected["done"])
                    self.assertEqual(result["status"], "quarantined")
                    self.assertEqual(
                        result["quarantine"]["object_kind"], "candidate"
                    )
                    self.assertEqual(result["quarantine"]["candidate"]["record"]["claim_ids"], [f"claim-{evidence_id}"])

                self.assertIsNone(gf.load_pending_candidate(loop))
                self.assertIsNone(gf.load_iteration_dispatch(loop))
                self.assertEqual(
                    gf.pre_dispatch_gate(loop)["action"], "candidate_quarantined"
                )
                self.assertEqual(arl.read_iterations(loop / "iterations.jsonl"), [])
                self.assertEqual(
                    arl.read_json(loop / "budget.json")["spent_iterations"], 0
                )

    def test_quarantine_cas_exhaustion_stops_without_retry_or_review(self) -> None:
        arl, gf = self._runtime_modules()
        base = self.provider_fixture.root / "quarantine-cas-exhaustion"
        project = base / "project"
        loop = project / ".autoloop" / "loop"
        project.mkdir(parents=True, mode=0o700)
        registry_dir = self._trusted_registry_root("quarantine-cas-exhaustion")
        init_args = arl.selftest_init_args(loop, max_iterations=2)
        init_args.goal_focus_mode = "enforce"
        arl.init_loop(init_args)
        self._activate_single_direction(arl, gf, loop)
        args = arl.selftest_drive_args(loop, registry_dir, "unused")
        args.root = str(project)
        args.cmd = None
        args.provider = "claude"
        args.max_failures = 3
        profile = arl.provider_resource_limits(60, role="primary")

        def direct_stage_then_fail(*_args, **kwargs):  # noqa: ANN002, ANN003
            dispatch = gf.load_iteration_dispatch(loop)
            write_text_evidence(loop, dispatch, "cas-exhaustion-evidence")
            attestation = _primary_resource_attestation()
            attestation["limits"] = arl.public_resource_limits(profile)
            gf.stage_iteration_candidate(
                loop,
                {
                    "schema_version": "1.0",
                    "candidate_id": dispatch["candidate_id"],
                    "mode": "bounded-research",
                    "objective": "force quarantine CAS exhaustion",
                    "decision": "continue",
                    "claim_ids": ["cas-exhaustion-claim"],
                    "evidence_checked": {
                        "claim_ids": ["cas-exhaustion-claim"],
                        "evidence_ids": ["cas-exhaustion-evidence"],
                    },
                    "budget_delta": {"iterations": 1},
                    "execution": {
                        "executor_provider": "claude",
                        "compute": {
                            "recording_status": "explicit",
                            "usage": "none",
                            "services": [],
                        },
                    },
                    "goal_focus": {
                        "plan_revision": dispatch["plan_revision"],
                        "campaign_id": dispatch["campaign_id"],
                        "approach_id": dispatch["approach_id"],
                    },
                },
                expected_plan_revision=dispatch["plan_revision"],
                expected_dispatch_id=dispatch["dispatch_id"],
                host_resource_attestation=attestation,
            )
            kwargs["resource_metadata"].update(attestation)
            return 17, False, None

        with mock.patch.dict(
            os.environ,
            {"AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local"},
            clear=False,
        ), mock.patch.object(
            arl, "preflight_resource_backend", return_value=profile
        ), mock.patch.object(
            arl, "run_primary_subprocess", side_effect=direct_stage_then_fail
        ) as primary, mock.patch.object(
            gf,
            "quarantine_failed_completion",
            side_effect=gf.RevisionConflict("injected quarantine CAS exhaustion"),
        ), mock.patch.object(
            arl, "run_panel_phase_for_drive"
        ) as reviewer, mock.patch.object(
            gf, "finalize_candidate", wraps=gf.finalize_candidate
        ) as finalizer:
            result = arl.drive_command(args)

        primary.assert_called_once()
        reviewer.assert_not_called()
        finalizer.assert_not_called()
        self.assertEqual(
            result["reason"], "quarantine_persistence_unverified", result
        )
        self.assertEqual(
            result["exit_code"],
            arl.DRIVE_EXIT_CODES["quarantine_persistence_unverified"],
        )
        self.assertIsNotNone(gf.load_pending_candidate(loop))
        self.assertEqual(arl.read_iterations(loop / "iterations.jsonl"), [])
        self.assertEqual(arl.read_json(loop / "budget.json")["spent_iterations"], 0)

    def test_enforce_preflight_preserves_existing_pending_candidate_without_review(self) -> None:
        arl, gf = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            registry_dir = self._trusted_registry_root(base.name)
            init_args = arl.selftest_init_args(loop, max_iterations=2)
            init_args.goal_focus_mode = "enforce"
            arl.init_loop(init_args)
            self._activate_single_direction(arl, gf, loop)
            plan = gf.load_current_plan(loop)
            prepared = gf.prepare_iteration_dispatch(
                loop,
                executor_provider="claude",
                executor_family="anthropic",
                executor_attestation=_provider_attestation("claude", loop),
                started_at="2026-07-29T12:00:00Z",
                driver_pid=12345,
            )["dispatch"]
            write_text_evidence(loop, prepared, "resume-evidence")
            staged = gf.stage_iteration_candidate(
                loop,
                {
                    "schema_version": "1.0",
                    "candidate_id": prepared["candidate_id"],
                    "iteration": 1,
                    "mode": "bounded-research",
                    "objective": "resume exact pending candidate",
                    "decision": "continue",
                    "claim_ids": ["resume-claim"],
                    "evidence_checked": {
                        "claim_ids": ["resume-claim"],
                        "evidence_ids": ["resume-evidence"],
                    },
                    "budget_delta": {"iterations": 1},
                    "execution": {
                        "executor_provider": "claude",
                        "compute": {
                            "recording_status": "explicit",
                            "usage": "none",
                            "services": [],
                        },
                    },
                    "goal_focus": {
                        "plan_revision": plan["plan_revision"],
                        "campaign_id": plan["campaign_id"],
                        "approach_id": plan["approach_id"],
                    },
                },
                expected_plan_revision=2,
                expected_dispatch_id=prepared["dispatch_id"],
                host_resource_attestation=_primary_resource_attestation(),
            )["candidate"]

            args = arl.selftest_drive_args(
                loop,
                registry_dir,
                "unused",
            )
            args.cmd = None
            args.provider = "claude"
            with mock.patch.object(
                arl,
                "run_panel_phase_for_drive",
                side_effect=RuntimeError("all reviewers unavailable"),
            ) as review, mock.patch.object(
                arl, "interruptible_sleep"
            ) as sleeper, mock.patch.object(
                arl, "run_primary_subprocess"
            ) as primary, mock.patch.object(
                arl, "consume_iteration_submission"
            ) as consume_submission, mock.patch.object(
                gf, "prepare_iteration_dispatch"
            ) as prepare_dispatch, mock.patch.object(
                arl.subprocess, "Popen"
            ) as popen, mock.patch.object(arl.subprocess, "run") as worker:
                result = arl.drive_command(args)

            self.assertEqual(result["status"], "failed", result)
            self.assertEqual(
                result["reason"], "secure_primary_transport_unavailable"
            )
            review.assert_not_called()
            sleeper.assert_not_called()
            primary.assert_not_called()
            consume_submission.assert_not_called()
            prepare_dispatch.assert_not_called()
            popen.assert_not_called()
            worker.assert_not_called()
            pending = gf.load_pending_candidate(loop)
            self.assertEqual(pending["candidate_id"], staged["candidate_id"])
            self.assertEqual(arl.read_iterations(loop / "iterations.jsonl"), [])

    def test_enforce_dispatch_rejects_mode_downgrade_stale_plan_and_family_failover(self) -> None:
        arl, gf = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            init_args = arl.selftest_init_args(loop, max_iterations=2)
            init_args.goal_focus_mode = "enforce"
            arl.init_loop(init_args)
            self._activate_single_direction(arl, gf, loop)

            with self.assertRaises(gf.RevisionConflict):
                gf.prepare_iteration_dispatch(
                    loop,
                    executor_provider="codex",
                    executor_family="openai",
                    executor_attestation=_provider_attestation("codex", loop),
                    started_at="2026-07-29T12:00:00Z",
                )
            self.assertIsNone(gf.load_iteration_dispatch(loop))

            dispatch = gf.prepare_iteration_dispatch(
                loop,
                executor_provider="claude",
                executor_family="anthropic",
                executor_attestation=_provider_attestation("claude", loop),
                started_at="2026-07-29T12:00:00Z",
            )["dispatch"]
            plan_path = loop / "current_plan.json"
            original = plan_path.read_bytes()
            downgraded = json.loads(original)
            downgraded["enforcement_mode"] = "monitor"
            plan_path.write_text(json.dumps(downgraded), encoding="utf-8")
            with self.assertRaises(gf.RevisionConflict):
                gf.validate_iteration_dispatch(loop, dispatch)

            plan_path.write_bytes(original)
            stale = json.loads(original)
            stale["plan_revision"] += 1
            plan_path.write_text(json.dumps(stale), encoding="utf-8")
            with self.assertRaises(gf.RevisionConflict):
                gf.validate_iteration_dispatch(loop, dispatch)

    def test_driver_family_failover_requires_schema_valid_host_strategy_review(self) -> None:
        arl, gf = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            init_args = arl.selftest_init_args(loop, max_iterations=2)
            init_args.goal_focus_mode = "enforce"
            arl.init_loop(init_args)
            self._activate_single_direction(arl, gf, loop)
            factors = (
                "goal_resolution_contribution",
                "information_gain",
                "option_value",
                "diversity",
                "execution_cost",
                "verification_cost",
                "bridge_debt",
                "dependency_risk",
                "redundancy",
            )
            payload = {
                "schema_version": "strategy_advice.v1",
                "decision": "explore",
                "recommended_approach_id": "approach-a",
                "candidates": [
                    {
                        "campaign_id": "campaign-a",
                        "approach_id": "approach-a",
                        "rank": 1,
                        "estimates": {
                            factor: {"lower": 2, "upper": 3}
                            for factor in factors
                        },
                        "evidence_refs": ["current_plan.json"],
                        "missing_evidence": [],
                        "falsifier": "The bounded check contradicts the route.",
                        "strongest_objection": "The driver family changed.",
                        "next_action": "Run the re-reviewed bounded check with Codex.",
                    }
                ],
                "inspected_evidence": ["current_plan.json"],
                "uninspected_evidence": [],
                "reasoning_summary": "Claude independently reviewed the Codex failover.",
            }
            import panel_parent  # noqa: WPS

            self.assertEqual(panel_parent.validate_strategy_advice(payload), [])
            summary = {
                "structured_synthesis": {
                    "primary_provider": "codex",
                    "primary_family": _provider_attestation("codex", loop)[
                        "family"
                    ],
                    "valid_providers": ["claude"],
                    "different_family_valid_providers": ["claude"],
                    "dissent": False,
                },
                "primary_execution_attestation": _provider_attestation(
                    "codex", loop
                ),
                "provider_execution_attestations": {
                    "claude": _provider_attestation("claude", loop)
                },
                "results": {
                    "claude": {
                        "structured_valid": True,
                        "structured_payload": payload,
                    }
                },
                "authority_snapshot": gf.strategy_authority_snapshot(loop),
            }
            reviewed = arl._strategy_selection_from_panel(loop, summary)
            self.assertEqual(reviewed["status"], "ready", reviewed)
            gf.commit_selected_direction(
                loop,
                reviewed["selection"],
                reviewed["review"],
                "driver_family_change",
            )
            plan = gf.load_current_plan(loop)
            self.assertEqual(plan["dispatch_provider_family"], "openai")
            self.assertEqual(
                plan["next_action"],
                "Run the re-reviewed bounded check with Codex.",
            )
            self.assertIsNone(gf.load_iteration_dispatch(loop))

    def test_dispatch_crash_is_visible_and_requires_exact_id_to_cancel(self) -> None:
        arl, gf = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            init_args = arl.selftest_init_args(loop, max_iterations=2)
            init_args.goal_focus_mode = "enforce"
            arl.init_loop(init_args)
            self._activate_single_direction(arl, gf, loop)
            dispatch = gf.prepare_iteration_dispatch(
                loop,
                executor_provider="claude",
                executor_family="anthropic",
                executor_attestation=_provider_attestation("claude", loop),
                started_at="2026-07-29T12:00:00Z",
                driver_pid=999999,
            )["dispatch"]

            status = arl.goal_focus_status_command(
                argparse.Namespace(dir=str(loop))
            )
            self.assertEqual(
                status["inflight_dispatch"]["dispatch_id"],
                dispatch["dispatch_id"],
            )
            visible = arl.goal_focus_recover_dispatch_command(
                argparse.Namespace(
                    dir=str(loop),
                    cancel=False,
                    dispatch_id="",
                    reason="",
                )
            )
            self.assertEqual(visible["recovery_status"], "awaiting_explicit_cancel")
            mismatch = arl.goal_focus_recover_dispatch_command(
                argparse.Namespace(
                    dir=str(loop),
                    cancel=True,
                    dispatch_id="wrong-id",
                    reason="fixture",
                )
            )
            self.assertEqual(mismatch["status"], "failed")
            self.assertIsNotNone(gf.load_iteration_dispatch(loop))
            cancelled = arl.goal_focus_recover_dispatch_command(
                argparse.Namespace(
                    dir=str(loop),
                    cancel=True,
                    dispatch_id=dispatch["dispatch_id"],
                    reason="confirmed worker crash",
                )
            )
            self.assertTrue(cancelled["applied"])
            self.assertEqual(cancelled["recovery_status"], "cancelled")
            self.assertIsNone(gf.load_iteration_dispatch(loop))

    def test_enforce_compute_allowlist_rejects_unreported_and_unlisted_services(self) -> None:
        arl, gf = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            init_args = arl.selftest_init_args(loop, max_iterations=2)
            init_args.goal_focus_mode = "enforce"
            arl.init_loop(init_args)
            (loop / "compute_policy.json").write_text(
                json.dumps({"policy": {"backends": ["hetzner", "kaggle"]}}),
                encoding="utf-8",
            )
            self._activate_single_direction(arl, gf, loop)
            plan = gf.load_current_plan(loop)
            self.assertEqual(
                set(plan["compute_policy"]["allowed_services"]),
                {"hetzner", "kaggle"},
            )
            accepted = gf.validate_compute_execution(
                loop,
                {
                    "recording_status": "explicit",
                    "usage": "hetzner",
                    "services": [{"service": "hetzner", "status": "succeeded"}],
                },
            )
            self.assertEqual(accepted["used_services"], ["hetzner"])
            for compute in (
                {
                    "recording_status": "unreported",
                    "usage": "unknown",
                    "services": [],
                },
                {
                    "recording_status": "explicit",
                    "usage": "local",
                    "services": [{"service": "local", "status": "succeeded"}],
                },
            ):
                with self.subTest(compute=compute), self.assertRaises(ValueError):
                    gf.validate_compute_execution(loop, compute)

    def test_primary_compute_credentials_follow_host_pinned_effective_policy(self) -> None:
        arl, gf = self._runtime_modules()
        lane_variables = {
            "hetzner": {"HCLOUD_TOKEN", "HCLOUD_SSH_KEYS"},
            "kaggle": {"KAGGLE_API_TOKEN", "KAGGLE_CONFIG_DIR"},
        }
        source_env = {
            "PATH": "/usr/bin:/bin",
            "ANTHROPIC_API_KEY": "fixture-provider-secret",
            "HCLOUD_TOKEN": "fixture-hetzner-token",
            "HCLOUD_SSH_KEYS": "fixture-ssh-keys",
            "KAGGLE_API_TOKEN": "fixture-kaggle-token",
            "KAGGLE_CONFIG_DIR": "/fixture/kaggle-config",
            "ZULIP_BOT_API_KEY": "fixture-notify-token",
            "GITHUB_TOKEN": "fixture-github-token",
            "MODAL_TOKEN_SECRET": "fixture-modal-token",
        }
        cases = (
            (
                "hetzner-only",
                {"policy": {"backends": ["hetzner"]}},
                {"hetzner"},
                False,
            ),
            (
                "kaggle-only",
                {"policy": {"backends": ["kaggle"]}},
                {"kaggle"},
                False,
            ),
            (
                "both",
                {"policy": {"backends": ["hetzner", "kaggle"]}},
                {"hetzner", "kaggle"},
                False,
            ),
            (
                "hetzner-forbidden",
                {
                    "policy": {
                        "backends": ["hetzner", "kaggle"],
                        "forbidden_services": ["hetzner"],
                    }
                },
                {"kaggle"},
                False,
            ),
            ("no-structured-policy", None, set(), False),
            ("model-proposed-only", None, set(), True),
        )
        for label, policy_document, allowed_lanes, inject_model_selection in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                loop = Path(tmp) / "loop"
                init_args = arl.selftest_init_args(loop, max_iterations=2)
                init_args.goal_focus_mode = "enforce"
                arl.init_loop(init_args)
                if policy_document is not None:
                    (loop / "compute_policy.json").write_text(
                        json.dumps(policy_document), encoding="utf-8"
                    )
                self._activate_single_direction(arl, gf, loop)
                if inject_model_selection:
                    plan = gf.load_current_plan(loop)
                    plan["compute_policy"] = {
                        "allowed_services": ["hetzner", "kaggle"],
                        "forbidden_services": [],
                    }
                    (loop / "current_plan.json").write_text(
                        json.dumps(plan, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )

                child_env = arl.build_primary_child_env(
                    "claude",
                    executable_attestation=_provider_attestation("claude", loop),
                    control={"AUTOLOOP_DIR": str(loop)},
                    environ=source_env,
                    include_provider_credentials=True,
                    include_compute_credentials=True,
                    compute_policy_run_dir=loop,
                )

                expected_lane_variables = set().union(
                    *(lane_variables[lane] for lane in allowed_lanes)
                )
                for lane, variable_names in lane_variables.items():
                    for variable_name in variable_names:
                        with self.subTest(label=label, variable=variable_name):
                            self.assertEqual(
                                variable_name in child_env,
                                variable_name in expected_lane_variables,
                            )
                self.assertEqual(
                    child_env.get("ANTHROPIC_API_KEY"),
                    "fixture-provider-secret",
                )
                for excluded in (
                    "ZULIP_BOT_API_KEY",
                    "GITHUB_TOKEN",
                    "MODAL_TOKEN_SECRET",
                ):
                    self.assertNotIn(excluded, child_env)
                if label == "both":
                    unattested = arl.build_primary_child_env(
                        "claude",
                        executable_attestation=None,
                        control={"AUTOLOOP_DIR": str(loop)},
                        environ=source_env,
                        include_provider_credentials=True,
                        include_compute_credentials=True,
                        compute_policy_run_dir=loop,
                    )
                    for variable_names in lane_variables.values():
                        for variable_name in variable_names:
                            self.assertNotIn(variable_name, unattested)

    def test_trusted_local_drive_passes_only_allowed_lane_credentials_to_primary(self) -> None:
        arl, gf = self._runtime_modules()
        base = self.provider_fixture.root / "trusted-local-compute-credentials"
        project = base / "project"
        loop = project / ".autoloop" / "loop"
        project.mkdir(parents=True, mode=0o700)
        registry_dir = self._trusted_registry_root(
            "trusted-local-compute-credentials"
        )
        init_args = arl.selftest_init_args(loop, max_iterations=1)
        init_args.goal_focus_mode = "enforce"
        arl.init_loop(init_args)
        (loop / "compute_policy.json").write_text(
            json.dumps({"policy": {"backends": ["hetzner"]}}),
            encoding="utf-8",
        )
        self._activate_single_direction(arl, gf, loop)
        args = arl.selftest_drive_args(loop, registry_dir, "unused")
        args.root = str(project)
        args.cmd = None
        args.provider = "claude"
        profile = arl.provider_resource_limits(60, role="primary")

        def capture_primary_env(*_args, **kwargs):  # noqa: ANN002, ANN003
            metadata = kwargs["resource_metadata"]
            metadata.update(_primary_resource_attestation())
            metadata["limits"] = arl.public_resource_limits(profile)
            return 17, False, None

        credential_env = {
            "AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local",
            "HCLOUD_TOKEN": "fixture-live-hetzner-token",
            "HCLOUD_SSH_KEYS": "fixture-live-ssh-keys",
            "KAGGLE_API_TOKEN": "fixture-live-kaggle-token",
            "KAGGLE_CONFIG_DIR": "/fixture/live-kaggle-config",
            "ZULIP_BOT_API_KEY": "fixture-live-notify-token",
            "GITHUB_TOKEN": "fixture-live-github-token",
        }
        with mock.patch.dict(
            os.environ, credential_env, clear=False
        ), mock.patch.object(
            arl, "preflight_resource_backend", return_value=profile
        ), mock.patch.object(
            arl, "run_primary_subprocess", side_effect=capture_primary_env
        ) as primary, mock.patch.object(
            sys, "stdout", io.StringIO()
        ), mock.patch.object(
            sys, "stderr", io.StringIO()
        ):
            arl.drive_command(args)

        primary.assert_called_once()
        child_env = primary.call_args.kwargs["child_env"]
        self.assertEqual(
            child_env.get("HCLOUD_TOKEN"), "fixture-live-hetzner-token"
        )
        self.assertEqual(
            child_env.get("HCLOUD_SSH_KEYS"), "fixture-live-ssh-keys"
        )
        for excluded in (
            "KAGGLE_API_TOKEN",
            "KAGGLE_CONFIG_DIR",
            "ZULIP_BOT_API_KEY",
            "GITHUB_TOKEN",
        ):
            self.assertNotIn(excluded, child_env)

        persisted = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in loop.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        for secret in (
            "fixture-live-hetzner-token",
            "fixture-live-ssh-keys",
            "fixture-live-kaggle-token",
            "fixture-live-notify-token",
            "fixture-live-github-token",
        ):
            self.assertNotIn(secret, persisted)

    def test_enforce_preflight_precedes_custom_or_unknown_provider_resolution(self) -> None:
        arl, gf = self._runtime_modules()
        for label, provider, command in (
            ("custom-command", None, "true"),
            ("unknown-provider", "mystery-gateway", None),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                loop = base / "loop"
                registry_dir = self._trusted_registry_root(base.name)
                init_args = arl.selftest_init_args(loop, max_iterations=2)
                init_args.goal_focus_mode = "enforce"
                arl.init_loop(init_args)
                self._activate_single_direction(arl, gf, loop)
                args = arl.selftest_drive_args(loop, registry_dir, command or "unused")
                args.cmd = command
                args.provider = provider
                with mock.patch.object(
                    arl, "resolve_provider_command"
                ) as resolve, mock.patch.object(
                    arl, "run_primary_subprocess"
                ) as primary, mock.patch.object(
                    arl.subprocess, "run"
                ) as worker:
                    result = arl.drive_command(args)
                self.assertEqual(
                    result["reason"], "secure_primary_transport_unavailable", result
                )
                self.assertEqual(result["exit_code"], 4)
                resolve.assert_not_called()
                primary.assert_not_called()
                worker.assert_not_called()
                self.assertIsNone(gf.load_iteration_dispatch(loop))

    def test_enforce_preflight_precedes_provider_identity_override_handling(self) -> None:
        arl, gf = self._runtime_modules()
        override_values = {
            "AAS_AUTOLOOP_CMD_CLAUDE": "codex exec substituted-family",
            "AAS_AUTOLOOP_ARGS_CLAUDE": "-p substituted-arguments",
            "AAS_AUTOLOOP_BIN_CLAUDE": "/tmp/substituted-binary",
            "AAS_CLAUDE": "/tmp/substituted-short-alias",
        }
        clean_env = dict(os.environ)
        for key in override_values:
            clean_env.pop(key, None)
        for key, value in override_values.items():
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                loop = base / "loop"
                registry_dir = self._trusted_registry_root(base.name)
                init_args = arl.selftest_init_args(loop, max_iterations=2)
                init_args.goal_focus_mode = "enforce"
                arl.init_loop(init_args)
                self._activate_single_direction(arl, gf, loop)
                args = arl.selftest_drive_args(loop, registry_dir, "unused")
                args.cmd = None
                args.provider = "claude"
                patched_env = {**clean_env, key: value}
                with mock.patch.dict(
                    os.environ, patched_env, clear=True
                ), mock.patch.object(
                    arl, "resolve_provider_command"
                ) as resolve, mock.patch.object(
                    arl, "run_primary_subprocess"
                ) as primary, mock.patch.object(
                    arl.subprocess, "run"
                ) as worker:
                    result = arl.drive_command(args)
                self.assertEqual(
                    result["reason"], "secure_primary_transport_unavailable", result
                )
                self.assertEqual(result["exit_code"], 4)
                resolve.assert_not_called()
                primary.assert_not_called()
                worker.assert_not_called()
                self.assertIsNone(gf.load_iteration_dispatch(loop))

    def test_provider_endpoint_overrides_invalidate_family_attribution(self) -> None:
        arl, _gf = self._runtime_modules()
        cases = (
            ("claude", "ANTHROPIC_BASE_URL"),
            ("codex", "OPENAI_BASE_URL"),
            ("deepseek", "DEEPSEEK_BASE_URL"),
            ("deepseek", "CODEWHALE_PROVIDER"),
            ("grok", "XAI_BASE_URL"),
            ("antigravity", "GOOGLE_GEMINI_BASE_URL"),
            ("antigravity", "GOOGLE_VERTEX_BASE_URL"),
            ("antigravity", "GEMINI_NEXT_GEN_API_BASE_URL"),
        )
        for provider, variable in cases:
            with self.subTest(provider=provider, variable=variable):
                found = arl.provider_identity_overrides(
                    provider, {variable: "https://router.invalid"}
                )
                self.assertIn(variable, found)

    def test_attested_primary_model_is_pinned_without_prompt_in_argv_or_env(self) -> None:
        arl, _gf = self._runtime_modules()
        prompt = "PRIMARY_PRIVATE_PROMPT_SENTINEL"
        contracts = {
            "claude": ("--model", "claude-test-model", "AAS_CLAUDE_LATEST_MODEL"),
            "codex": ("--model", "codex-test-model", "AAS_CODEX_LATEST_MODEL"),
            "grok": ("-m", "grok-test-model", "AAS_GROK_LATEST_MODEL"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            loop.mkdir()
            for provider, (flag, model, configured_name) in contracts.items():
                with self.subTest(provider=provider), mock.patch.object(
                    arl, "iteration_prompt", return_value=prompt
                ):
                    spec = arl.resolve_provider_command(
                        provider, loop, environ=dict(os.environ)
                    )
                    argv = list(spec["argv"])
                    self.assertEqual(argv.count(flag), 1)
                    self.assertEqual(argv[argv.index(flag) + 1], model)
                    self.assertNotIn(prompt, argv)
                    self.assertEqual(spec["prompt"], prompt)

                    child_env = arl.build_primary_child_env(
                        provider,
                        executable_attestation=spec["executable_attestation"],
                        control={
                            "AUTOLOOP_DIR": str(loop),
                            "AUTOLOOP_PROMPT": prompt,
                        },
                        environ=dict(os.environ),
                        include_provider_credentials=False,
                    )
                    self.assertNotIn("AUTOLOOP_PROMPT", child_env)
                    self.assertNotIn(prompt, child_env.values())

                    conflicting_env = {
                        **os.environ,
                        configured_name: f"conflicting-{model}",
                    }
                    with self.assertRaises(ValueError):
                        arl.resolve_provider_command(
                            provider, loop, environ=conflicting_env
                        )

    def test_panel_prompt_file_is_contained_and_read_without_following_links(self) -> None:
        arl, _gf = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            loop = root / "research" / "loop"
            loop.mkdir(parents=True)
            inside = loop / "prompt.md"
            inside.write_text("sealed prompt", encoding="utf-8")
            args = argparse.Namespace(
                root=str(root),
                dir=str(loop),
                providers="codex",
                smoke=False,
                phase="target_advice",
                prompt=None,
                prompt_file="prompt.md",
                iter_dir=None,
                timeout=5,
            )
            with mock.patch.object(
                arl,
                "run_panel_phase_for_drive",
                return_value={
                    "panel_content_pass": True,
                    "usable_providers": ["codex"],
                    "iter_dir": str(loop / "iterations" / "iter001"),
                },
            ) as dispatch:
                result = arl.panel_command(args)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(dispatch.call_args.kwargs["prompt"], "sealed prompt")

            outside = base / "outside.md"
            outside.write_text("outside sentinel", encoding="utf-8")
            args.prompt_file = str(outside)
            with self.assertRaises(OSError):
                arl.panel_command(args)

            if os.name == "posix":
                linked = loop / "linked.md"
                linked.symlink_to(outside)
                args.prompt_file = str(linked)
                with self.assertRaises(OSError):
                    arl.panel_command(args)

    def test_failed_exclusive_log_create_is_never_reopened(self) -> None:
        arl, _gf = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            registry_dir = self._trusted_registry_root(base.name)
            arl.init_loop(arl.selftest_init_args(loop, max_iterations=2))
            args = arl.selftest_drive_args(loop, registry_dir, "true")
            with mock.patch.object(
                arl,
                "_open_exclusive_driver_log",
                side_effect=FileExistsError("planted log"),
            ), mock.patch.object(arl, "read_log_tail") as read_tail, mock.patch.object(
                arl.subprocess, "run"
            ) as worker:
                result = arl.drive_command(args)
            self.assertEqual(result["reason"], "runtime_error", result)
            read_tail.assert_not_called()
            worker.assert_not_called()

    def test_successful_driver_log_is_classified_from_original_descriptor(self) -> None:
        arl, _gf = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            loop = base / "loop"
            registry_dir = self._trusted_registry_root(base.name)
            arl.init_loop(arl.selftest_init_args(loop, max_iterations=2))
            command = (
                f'"{sys.executable}" -c '
                '"import sys; print(\'ordinary failure\'); sys.exit(1)"'
            )
            args = arl.selftest_drive_args(loop, registry_dir, command)
            with mock.patch.object(arl, "read_log_tail") as path_reader:
                result = arl.drive_command(args)
            self.assertEqual(result["reason"], "max_failures", result)
            path_reader.assert_not_called()
            logs = list((loop / "driver_logs").glob("iter_*.log"))
            self.assertEqual(len(logs), 1)
            self.assertIn("ordinary failure", logs[0].read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink semantics")
    def test_exclusive_driver_log_refuses_precreated_symlink(self) -> None:
        arl, _gf = self._runtime_modules()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "driver_logs"
            logs.mkdir()
            victim = root / "victim.txt"
            victim.write_text("keep", encoding="utf-8")
            planted = logs / "iter_planted.log"
            planted.symlink_to(victim)
            with self.assertRaises(FileExistsError):
                arl._open_exclusive_driver_log(planted)
            self.assertEqual(victim.read_text(encoding="utf-8"), "keep")

    def test_fixed_family_provider_commands_pin_configurable_upstreams(self) -> None:
        arl, _gf = self._runtime_modules()
        root = Path("/tmp/provider-command-fixture")
        cases = {
            "codex": ("/usr/bin/codex", ["--ignore-user-config", 'model_provider="openai"']),
            "deepseek": ("/usr/bin/codewhale", ["--provider", "deepseek"]),
        }
        for provider, (binary, required) in cases.items():
            with self.subTest(provider=provider), mock.patch.object(
                arl,
                "resolve_provider_binary_details",
                return_value=(binary, True, [binary], None),
            ):
                spec = arl.resolve_provider_command(
                    provider,
                    root,
                    environ={
                        "PATH": "/usr/bin",
                        "CODEWHALE_PROVIDER": "anthropic",
                    },
                )
            argv = spec["argv"]
            for token in required:
                self.assertIn(token, argv)

    def test_result_review_requires_exact_claim_coverage(self) -> None:
        arl, gf = self._runtime_modules()
        evidence_id = "exact-claims.txt"
        pending = {
            "candidate_id": "candidate-exact-claims",
            "host_execution_attestation": _host_execution_attestation(
                "candidate-exact-claims"
            ),
            "record": {
                "claim_ids": ["claim-a", "claim-b"],
                "evidence_checked": {
                    "claim_ids": ["claim-a", "claim-b"],
                    "evidence_ids": [evidence_id],
                },
                "evidence_artifacts": embedded_evidence_artifacts(
                    [evidence_id], candidate_id="candidate-exact-claims"
                ),
                "execution": {"executor_provider": "claude"},
                "progress_assessment": {"obligation_ids": []},
            },
        }

        def summary(claim_ids: list[str]) -> dict[str, Any]:
            payload = {
                "schema_version": "result_review.v1",
                "candidate_id": "candidate-exact-claims",
                "candidate_fingerprint": gf.candidate_fingerprint(pending),
                "verdict": "pass",
                "safe_to_bank": True,
                "inspected_paths": [evidence_id],
                "uninspected_paths": [],
                "invalidation_conditions": [],
                "summary": "Exact staged claims were inspected.",
                "claim_reviews": [
                    {
                        "claim_id": claim_id,
                        "status": "supported",
                        "evidence_refs": [evidence_id],
                        "reason": "The staged record supports this exact claim.",
                    }
                    for claim_id in claim_ids
                ],
                "obligation_reviews": [],
                "machine_checks": [],
            }
            import panel_parent  # noqa: WPS

            self.assertEqual(panel_parent.validate_result_review(payload), [])
            return {
                "structured_synthesis": {
                    "candidate_ids": ["candidate-exact-claims"],
                    "candidate_fingerprints": [
                        gf.candidate_fingerprint(pending)
                    ],
                    "primary_family": _provider_attestation("claude")["family"],
                    "conservative_verdict": "pass",
                },
                "provider_execution_attestations": {
                    "codex": _provider_attestation("codex")
                },
                "results": {
                    "codex": {
                        "structured_valid": True,
                        "structured_payload": payload,
                    }
                },
            }

        missing = arl._result_review_from_panel(pending, summary(["claim-a"]))
        self.assertEqual(missing["status"], "pending")
        self.assertEqual(missing["missing_claim_ids"], ["claim-b"])
        unexpected = arl._result_review_from_panel(
            pending, summary(["claim-a", "claim-b", "claim-extra"])
        )
        self.assertEqual(unexpected["status"], "pending")
        self.assertEqual(unexpected["unexpected_claim_ids"], ["claim-extra"])
        exact = arl._result_review_from_panel(
            pending, summary(["claim-a", "claim-b"])
        )
        self.assertEqual(exact["status"], "accepted")

    def test_result_review_rejects_split_coverage_across_reviewers(self) -> None:
        arl, gf = self._runtime_modules()
        evidence_a = "evidence-a.txt"
        evidence_b = "evidence-b.txt"
        pending = {
            "candidate_id": "candidate-split-coverage",
            "host_execution_attestation": _host_execution_attestation(
                "candidate-split-coverage"
            ),
            "record": {
                "claim_ids": ["claim-a", "claim-b"],
                "evidence_checked": {
                    "claim_ids": ["claim-a", "claim-b"],
                    "evidence_ids": [evidence_a, evidence_b],
                },
                "evidence_artifacts": embedded_evidence_artifacts(
                    [evidence_a, evidence_b],
                    candidate_id="candidate-split-coverage",
                ),
                "execution": {"executor_provider": "claude"},
                "progress_assessment": {
                    "obligation_ids": ["O1", "O2"],
                    "global_delta": "partial",
                },
                "obligation_transitions": [
                    {"obligation_id": "O1", "to": "partial"},
                    {"obligation_id": "O2", "to": "partial"},
                ],
            },
        }
        fingerprint = gf.candidate_fingerprint(pending)

        def payload(claim_id: str, obligation_id: str, evidence_id: str) -> dict[str, Any]:
            return {
                "schema_version": "result_review.v1",
                "candidate_id": pending["candidate_id"],
                "candidate_fingerprint": fingerprint,
                "verdict": "pass",
                "safe_to_bank": True,
                "inspected_paths": [evidence_id],
                "uninspected_paths": [],
                "invalidation_conditions": [],
                "summary": "One subset of the candidate was reviewed.",
                "claim_reviews": [
                    {
                        "claim_id": claim_id,
                        "status": "supported",
                        "evidence_refs": [evidence_id],
                        "reason": "This subset is supported.",
                    }
                ],
                "obligation_reviews": [
                    {
                        "obligation_id": obligation_id,
                        "target_status": "partial",
                        "verdict": "accept",
                        "evidence_refs": [evidence_id],
                        "reason": "This subset transition is supported.",
                    }
                ],
                "machine_checks": [],
            }

        codex_payload = payload("claim-a", "O1", evidence_a)
        grok_payload = payload("claim-b", "O2", evidence_b)
        import panel_parent  # noqa: WPS

        self.assertEqual(panel_parent.validate_result_review(codex_payload), [])
        self.assertEqual(panel_parent.validate_result_review(grok_payload), [])
        outcome = arl._result_review_from_panel(
            pending,
            {
                "structured_synthesis": {
                    "candidate_ids": [pending["candidate_id"]],
                    "candidate_fingerprints": [fingerprint],
                    "conservative_verdict": "pass",
                },
                "provider_execution_attestations": {
                    "codex": _provider_attestation("codex"),
                    "grok": _provider_attestation("grok"),
                },
                "results": {
                    "codex": {
                        "structured_valid": True,
                        "structured_payload": codex_payload,
                    },
                    "grok": {
                        "structured_valid": True,
                        "structured_payload": grok_payload,
                    },
                },
            },
        )
        self.assertEqual(outcome["status"], "pending", outcome)
        self.assertEqual(
            outcome["reviewer_coverage_errors"]["codex"]["missing_claim_ids"],
            ["claim-b"],
        )
        self.assertEqual(
            outcome["reviewer_coverage_errors"]["grok"]["missing_obligation_ids"],
            ["O1"],
        )

    def test_result_review_binds_candidate_fingerprint_and_exact_obligation_evidence(self) -> None:
        arl, gf = self._runtime_modules()
        evidence_id = "staged-evidence-1.txt"
        pending = {
            "schema_version": "iteration_candidate.v1",
            "candidate_id": "candidate-obligation-binding",
            "host_execution_attestation": _host_execution_attestation(
                "candidate-obligation-binding"
            ),
            "record": {
                "claim_ids": ["claim-obligation"],
                "evidence_checked": {
                    "claim_ids": ["claim-obligation"],
                    "evidence_ids": [evidence_id],
                },
                "evidence_artifacts": embedded_evidence_artifacts(
                    [evidence_id],
                    candidate_id="candidate-obligation-binding",
                ),
                "execution": {"executor_provider": "claude"},
                "progress_assessment": {
                    "obligation_ids": ["GOAL-SC-1"],
                    "global_delta": "satisfied",
                },
                "obligation_transitions": [
                    {"obligation_id": "GOAL-SC-1", "to": "satisfied"}
                ],
            },
        }
        exact_fingerprint = gf.candidate_fingerprint(pending)

        def summary(
            *,
            fingerprint: str | None = exact_fingerprint,
            target_status: str = "satisfied",
            evidence_refs: list[str] | None = None,
        ) -> dict[str, Any]:
            payload = {
                "schema_version": "result_review.v1",
                "candidate_id": pending["candidate_id"],
                "verdict": "pass",
                "safe_to_bank": True,
                "inspected_paths": [evidence_id],
                "uninspected_paths": [],
                "invalidation_conditions": [],
                "summary": "The exact candidate and transition were inspected.",
                "claim_reviews": [
                    {
                        "claim_id": "claim-obligation",
                        "status": "supported",
                        "evidence_refs": [evidence_id],
                        "reason": "The staged evidence supports the claim.",
                    }
                ],
                "obligation_reviews": [
                    {
                        "obligation_id": "GOAL-SC-1",
                        "target_status": target_status,
                        "verdict": "accept",
                        "evidence_refs": (
                            [evidence_id]
                            if evidence_refs is None
                            else evidence_refs
                        ),
                        "reason": "The evidence supports this exact transition.",
                    }
                ],
                "machine_checks": [],
            }
            if fingerprint is not None:
                payload["candidate_fingerprint"] = fingerprint
            return {
                "structured_synthesis": {
                    "candidate_ids": [pending["candidate_id"]],
                    "candidate_fingerprints": [
                        (
                            fingerprint
                            if fingerprint is not None
                            else ""
                        )
                    ],
                    "conservative_verdict": "pass",
                },
                "provider_execution_attestations": {
                    "codex": _provider_attestation("codex")
                },
                "results": {
                    "codex": {
                        "structured_valid": True,
                        "structured_payload": payload,
                    }
                },
            }

        cases = (
            ("missing-fingerprint", {"fingerprint": None}),
            ("wrong-fingerprint", {"fingerprint": "sha256:" + "0" * 64}),
            ("wrong-target", {"target_status": "partial"}),
            ("empty-evidence", {"evidence_refs": []}),
            ("mismatched-evidence", {"evidence_refs": ["other-evidence"]}),
        )
        for label, kwargs in cases:
            with self.subTest(label=label):
                outcome = arl._result_review_from_panel(
                    pending,
                    summary(**kwargs),
                )
                self.assertEqual(outcome["status"], "pending", outcome)

        exact_summary = summary()
        import panel_parent  # noqa: WPS

        exact_payload = exact_summary["results"]["codex"]["structured_payload"]
        self.assertEqual(panel_parent.validate_result_review(exact_payload), [])
        exact = arl._result_review_from_panel(pending, exact_summary)
        self.assertEqual(exact["status"], "accepted", exact)
        review = exact["review"]
        self.assertEqual(review["candidate_fingerprint"], exact_fingerprint)
        self.assertEqual(
            review["obligation_reviews"][0]["target_status"],
            "satisfied",
        )
        self.assertEqual(
            review["obligation_reviews"][0]["evidence_refs"],
            [evidence_id],
        )

    def test_reviewer_side_mutation_prevents_finalization_and_preserves_pending_budget(self) -> None:
        arl, gf = self._runtime_modules()
        for mutation in ("candidate", "current_plan", "quarantine"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                loop = Path(tmp) / "loop"
                init_args = arl.selftest_init_args(loop, max_iterations=2)
                init_args.goal_focus_mode = "enforce"
                arl.init_loop(init_args)
                self._activate_single_direction(arl, gf, loop)
                plan = gf.load_current_plan(loop)
                dispatch = gf.prepare_iteration_dispatch(
                    loop,
                    executor_provider="claude",
                    executor_family="anthropic",
                    executor_attestation=_provider_attestation("claude", loop),
                    started_at="2026-07-29T12:00:00Z",
                )["dispatch"]
                write_text_evidence(loop, dispatch, "staged-evidence-1")
                candidate = gf.stage_iteration_candidate(
                    loop,
                    {
                        "candidate_id": dispatch["candidate_id"],
                        "iteration": 1,
                        "mode": "bounded-research",
                        "objective": "test reviewer mutation isolation",
                        "claim_ids": ["claim-before-mutation"],
                        "evidence_checked": {
                            "claim_ids": ["claim-before-mutation"],
                            "evidence_ids": ["staged-evidence-1"],
                        },
                        "execution": {
                            "executor_provider": "claude",
                            "compute": {
                                "recording_status": "explicit",
                                "usage": "none",
                                "services": [],
                            },
                        },
                        "goal_focus": {
                            "plan_revision": plan["plan_revision"],
                            "campaign_id": plan["campaign_id"],
                            "approach_id": plan["approach_id"],
                        },
                        "progress_assessment": {
                            "campaign_delta": "substantial",
                            "global_delta": "satisfied",
                            "obligation_ids": ["GOAL-SC-1"],
                        },
                        "obligation_transitions": [
                            {"obligation_id": "GOAL-SC-1", "to": "satisfied"}
                        ],
                        "budget_delta": {"iterations": 1},
                        "decision": "stop",
                        "stop_reason": "proof_found",
                    },
                    expected_plan_revision=plan["plan_revision"],
                    expected_dispatch_id=dispatch["dispatch_id"],
                    host_resource_attestation=_primary_resource_attestation(),
                )["candidate"]
                fingerprint = gf.candidate_fingerprint(candidate)
                provider_review = {
                    "schema_version": "result_review.v1",
                    "candidate_id": candidate["candidate_id"],
                    "candidate_fingerprint": fingerprint,
                    "verdict": "pass",
                    "safe_to_bank": True,
                    "inspected_paths": ["staged-evidence-1"],
                    "uninspected_paths": [],
                    "invalidation_conditions": [],
                    "summary": "The exact staged candidate is supported.",
                    "claim_reviews": [
                        {
                            "claim_id": "claim-before-mutation",
                            "status": "supported",
                            "evidence_refs": ["staged-evidence-1"],
                            "reason": "The staged evidence supports the claim.",
                        }
                    ],
                    "obligation_reviews": [
                        {
                            "obligation_id": "GOAL-SC-1",
                            "target_status": "satisfied",
                            "verdict": "accept",
                            "evidence_refs": ["staged-evidence-1"],
                            "reason": "The exact transition is supported.",
                        }
                    ],
                    "machine_checks": [],
                }
                review_result = arl._result_review_from_panel(
                    candidate,
                    {
                        "structured_synthesis": {
                            "candidate_ids": [candidate["candidate_id"]],
                            "candidate_fingerprints": [fingerprint],
                            "conservative_verdict": "pass",
                        },
                        "provider_execution_attestations": {
                            "codex": _provider_attestation("codex", loop)
                        },
                        "results": {
                            "codex": {
                                "structured_valid": True,
                                "structured_payload": provider_review,
                            }
                        },
                    },
                )
                self.assertEqual(review_result["status"], "accepted", review_result)
                review = review_result["review"]
                if mutation == "candidate":
                    candidate_path = loop / "iteration_candidate.json"
                    mutated = json.loads(candidate_path.read_text(encoding="utf-8"))
                    mutated["reviewer_side_mutation"] = True
                    candidate_path.write_text(
                        json.dumps(mutated, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                elif mutation == "current_plan":
                    plan_path = loop / "current_plan.json"
                    mutated = json.loads(plan_path.read_text(encoding="utf-8"))
                    mutated["next_action"] = "reviewer attempted to replace the plan"
                    plan_path.write_text(
                        json.dumps(mutated, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                else:
                    quarantine = {
                        "schema_version": gf.CANDIDATE_QUARANTINE_SCHEMA,
                        "quarantine_id": "candidate-quarantine-review-race",
                        "object_kind": "candidate",
                        "candidate_id": candidate["candidate_id"],
                        "candidate_fingerprint": fingerprint,
                        "reason": "injected finalization race",
                        "source": "test_fault_injection",
                        "quarantined_at": "2026-07-29T12:01:00Z",
                        "candidate": candidate,
                    }
                    (loop / gf.CANDIDATE_QUARANTINE_FILE).write_text(
                        json.dumps(quarantine, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )

                with self.assertRaises((ValueError, gf.RevisionConflict)):
                    gf.finalize_candidate(
                        loop,
                        accepted=True,
                        review=review,
                        expected_plan_revision=plan["plan_revision"],
                    )
                self.assertTrue((loop / "iteration_candidate.json").exists())
                self.assertEqual(
                    arl.read_json(loop / "budget.json")["spent_iterations"],
                    0,
                )
                self.assertEqual(
                    arl.read_iterations(loop / "iterations.jsonl"),
                    [],
                )


@unittest.skipUnless(os.name == "posix", "the .sh shim is shipped for POSIX manual use only")
class AutoloopDriverShimTests(unittest.TestCase):
    """Smoke: the POSIX .sh convenience shim delegates to the runtime drive subcommand."""

    DRIVER = HELPER.parent / "autoloop_driver.sh"

    def test_shim_stops_immediately_when_already_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); reg, loop = base / "reg", base / "loop"
            _init_loop(loop, reg, max_iterations=5)
            (loop / "STOP_REQUESTED").write_text("", encoding="utf-8")
            env = _subprocess_env({"AAS_AUTOLOOP_REGISTRY": str(reg)})
            res = subprocess.run(
                ["bash", str(self.DRIVER), "--dir", str(loop), "--root", str(loop),
                 "--cmd", ': > "$AUTOLOOP_DIR/ran"'],
                capture_output=True, text=True, timeout=40, env=env, check=False,
            )
            self.assertEqual(res.returncode, 0)
            self.assertFalse((loop / "ran").exists())  # iteration command never ran


def _load_arl_runtime():
    # The runtime imports its sibling panel_parent / goal_priority helpers by
    # bare name (they are co-located at run time). Put the runtime dir on
    # sys.path so those imports resolve when loaded as a standalone module.
    helper_dir = str(HELPER.parent)
    added = helper_dir not in sys.path
    if added:
        sys.path.insert(0, helper_dir)
    try:
        spec = importlib.util.spec_from_file_location("arl_runtime_under_test", HELPER)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if added:
            sys.path.remove(helper_dir)
    return mod


def _fake_cli(bindir: Path, name: str, body: str | None = None) -> Path:
    """Create a host-runnable stub CLI (POSIX script or Windows .cmd)."""
    if os.name == "nt":
        path = bindir / f"{name}.cmd"
        if body is None:
            path.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
        else:
            path.write_text(body, encoding="utf-8")
        return path
    path = bindir / name
    if body is None:
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    else:
        path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _fake_grok_models_cli(bindir: Path, model: str) -> Path:
    if os.name == "nt":
        body = (
            "@echo off\r\n"
            "if \"%~1\"==\"models\" (\r\n"
            f"  echo Default model: {model}\r\n"
            "  echo Available models:\r\n"
            f"  echo * {model} ^(default^)\r\n"
            ")\r\n"
        )
    else:
        body = (
            "#!/bin/sh\n"
            "if [ \"$1\" = models ]; then\n"
            f"  printf '%s\\n' 'Default model: {model}' 'Available models:' '* {model} (default)'\n"
            "fi\n"
        )
    return _fake_cli(bindir, "grok", body)


def _grok_profile_payload(status: str = "ready", model_id: str = "grok-4.5") -> dict[str, Any]:
    configured = status in {"ready", "degraded"}
    return {
        "schema_version": "grok-remote.profile-status.v1",
        "status": status,
        "profile_name": "default" if configured else None,
        "profile_sha256": "a" * 64 if configured else None,
        "release_id": "b" * 64 if configured else None,
        "grok_release_id": "sha256:" + "c" * 64 if configured else None,
        "model_id": model_id if configured else None,
        "eligible_rungs": ["vpn"] if configured else [],
        "missing_rungs": ["home:windows"] if status == "degraded" else [],
        "reason_code": {
            "ready": "ready",
            "degraded": "ready_with_missing_optional_rungs",
            "blocked": "active_profile_invalid",
            "unconfigured": "no_active_profile",
        }[status],
    }


def _fake_grok_remote_profile_cli(bindir: Path) -> Path:
    if os.name == "nt":
        body = (
            "@echo off\r\n"
            "if \"%~1\"==\"--help\" (\r\n"
            "  echo   grok-remote doctor --json   report managed profile readiness\r\n"
            "  exit /b 0\r\n"
            ")\r\n"
            "if \"%~1\"==\"doctor\" if \"%~2\"==\"--json\" (\r\n"
            "  echo %AAS_TEST_GROK_PROFILE_JSON%\r\n"
            "  exit /b %AAS_TEST_GROK_PROFILE_EXIT%\r\n"
            ")\r\n"
            "exit /b 97\r\n"
        )
    else:
        body = (
            "#!/bin/sh\n"
            "if [ \"$1\" = --help ]; then\n"
            "  printf '%s\\n' '  grok-remote doctor --json   report managed profile readiness'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"$1\" = doctor ] && [ \"$2\" = --json ]; then\n"
            "  printf '%s\\n' \"$AAS_TEST_GROK_PROFILE_JSON\"\n"
            "  exit \"$AAS_TEST_GROK_PROFILE_EXIT\"\n"
            "fi\n"
            "exit 97\n"
        )
    return _fake_cli(bindir, "grok-remote", body)


class GrokProviderResolveTests(unittest.TestCase):
    """Platform-aware grok binary resolution (provider id always 'grok')."""

    def setUp(self) -> None:
        self.mod = _load_arl_runtime()
        self.plat = self.mod.runtime_platform_name()

    def test_grok_in_provider_specs_not_grok_remote(self) -> None:
        self.assertIn("grok", self.mod.PROVIDER_SPECS)
        self.assertNotIn("grok-remote", self.mod.PROVIDER_SPECS)

    def test_prefers_bare_grok_without_resolved_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            remote = _fake_cli(bindir, "grok-remote")
            bare = _fake_cli(bindir, "grok")
            env = {
                "PATH": str(bindir),
                "HOME": str(bindir),
                "USERPROFILE": str(bindir),
            }
            with mock.patch.object(self.mod, "probe_grok_remote_profile") as remote_probe:
                binary, found, tried = self.mod.resolve_provider_binary(
                    "grok", environ=env, platform=self.plat
                )
            self.assertTrue(found, tried)
            self.assertEqual(Path(binary).resolve(), bare.resolve())
            self.assertFalse(any(t.startswith("grok-remote") for t in tried), tried)
            self.assertTrue(remote.exists() and bare.exists())
            remote_probe.assert_not_called()

    def test_prefers_bare_grok_when_resolved_model_is_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            _fake_cli(bindir, "grok-remote")
            bare = _fake_grok_models_cli(bindir, "grok-4.5")
            env = {
                "PATH": str(bindir),
                "HOME": str(bindir),
                "USERPROFILE": str(bindir),
                "AAS_GROK_LATEST_MODEL": "grok-4.5",
            }
            with mock.patch.object(self.mod, "probe_grok_remote_profile") as remote_probe:
                binary, found, tried = self.mod.resolve_provider_binary(
                    "grok", environ=env, platform=self.plat
                )
            self.assertTrue(found, tried)
            self.assertEqual(Path(binary).resolve(), bare.resolve())
            self.assertFalse(any(t.startswith("grok-remote") for t in tried), tried)
            remote_probe.assert_not_called()

    def test_uses_remote_only_after_bare_model_nonconfirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            remote = _fake_grok_remote_profile_cli(bindir)
            _fake_grok_models_cli(bindir, "grok-4.4")
            env = {
                "PATH": str(bindir),
                "HOME": str(bindir),
                "USERPROFILE": str(bindir),
                "AAS_GROK_LATEST_MODEL": "grok-4.5",
                "AAS_TEST_GROK_PROFILE_JSON": json.dumps(_grok_profile_payload()),
                "AAS_TEST_GROK_PROFILE_EXIT": "0",
            }
            binary, found, tried, selection = self.mod.resolve_provider_binary_details(
                "grok", environ=env, platform=self.plat
            )
            self.assertTrue(found, tried)
            self.assertEqual(Path(binary).resolve(), remote.resolve())
            self.assertTrue(any(t.startswith("grok-remote") for t in tried), tried)
            self.assertEqual(selection["grok_profile_status"]["model_id"], "grok-4.5")

    def test_does_not_authorize_remote_without_resolved_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            _fake_cli(bindir, "grok-remote")
            env = {
                "PATH": str(bindir),
                "HOME": str(bindir),
                "USERPROFILE": str(bindir),
            }
            _binary, found, tried = self.mod.resolve_provider_binary(
                "grok", environ=env, platform=self.plat
            )
            self.assertFalse(found, tried)
            self.assertFalse(any(t.startswith("grok-remote") for t in tried), tried)

    def test_deduplicates_resolved_bare_executable_before_model_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            _fake_cli(bindir, "grok")
            remote = _fake_grok_remote_profile_cli(bindir)
            env = {
                "PATH": str(bindir),
                "HOME": str(bindir),
                "USERPROFILE": str(bindir),
                "AAS_GROK_LATEST_MODEL": "grok-4.5",
                "AAS_TEST_GROK_PROFILE_JSON": json.dumps(_grok_profile_payload()),
                "AAS_TEST_GROK_PROFILE_EXIT": "0",
            }
            not_confirmed = {
                "schema_version": self.mod.GROK_MODEL_PROBE_SCHEMA,
                "status": "not-confirmed",
                "resolved_model": "grok-4.5",
                "available_models": ["grok-4.4"],
                "reason_code": "resolved_model_not_listed",
            }
            platform_candidates = {
                **self.mod.GROK_BARE_BINARY_CANDIDATES,
                self.plat: ["grok", "grok"],
            }
            with (
                mock.patch.object(self.mod, "GROK_BARE_BINARY_CANDIDATES", platform_candidates),
                mock.patch.object(
                    self.mod,
                    "probe_grok_model_membership",
                    return_value=not_confirmed,
                ) as probe,
            ):
                binary, found, tried = self.mod.resolve_provider_binary(
                    "grok", environ=env, platform=self.plat
                )
            self.assertTrue(found, tried)
            self.assertEqual(Path(binary).resolve(), remote.resolve())
            probe.assert_called_once()

    def test_invalid_model_blocks_before_bare_or_remote_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            bare = _fake_grok_models_cli(bindir, "grok-4.5")
            _fake_grok_remote_profile_cli(bindir)
            env = {
                "PATH": str(bindir),
                "HOME": str(bindir),
                "USERPROFILE": str(bindir),
                "AAS_GROK_LATEST_MODEL": "_invalid",
                "AAS_AUTOLOOP_BIN_GROK": str(bare),
            }
            _binary, found, tried, selection = self.mod.resolve_provider_binary_details(
                "grok", environ=env, platform=self.plat
            )
            self.assertFalse(found)
            self.assertEqual(tried, [])
            self.assertEqual(selection["reason_code"], "resolved_model_invalid")

    @unittest.skipUnless(os.name == "posix", "POSIX umask behavior")
    def test_bare_model_probe_uses_private_posix_umask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            home.mkdir()
            bare = _fake_cli(
                root,
                "grok",
                (
                    "#!/bin/sh\n"
                    "mkdir -p \"$HOME/.grok\"\n"
                    ": > \"$HOME/.grok/models_cache.json\"\n"
                    "printf '%s\\n' '* grok-4.5 (default)'\n"
                ),
            )
            previous_umask = os.umask(0o002)
            try:
                probe = self.mod.probe_grok_model_membership(
                    str(bare),
                    "grok-4.5",
                    {**os.environ, "HOME": str(home)},
                )
            finally:
                os.umask(previous_umask)
            cache = home / ".grok" / "models_cache.json"
            self.assertEqual(probe["status"], "confirmed")
            self.assertEqual(cache.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX umask behavior")
    def test_remote_help_and_doctor_use_private_posix_umask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / "cache"
            cache_dir.mkdir()
            remote = _fake_cli(
                root,
                "grok-remote",
                (
                    "#!/bin/sh\n"
                    "if [ \"$1\" = --help ]; then\n"
                    "  : > \"$AAS_TEST_CACHE_DIR/help-cache\"\n"
                    "  printf '%s\\n' 'grok-remote doctor --json'\n"
                    "  exit 0\n"
                    "fi\n"
                    "if [ \"$1\" = doctor ] && [ \"$2\" = --json ]; then\n"
                    "  : > \"$AAS_TEST_CACHE_DIR/doctor-cache\"\n"
                    "  printf '%s\\n' \"$AAS_TEST_GROK_PROFILE_JSON\"\n"
                    "  exit 0\n"
                    "fi\n"
                    "exit 97\n"
                ),
            )
            payload = _grok_profile_payload()
            env = {
                **os.environ,
                "AAS_TEST_CACHE_DIR": str(cache_dir),
                "AAS_TEST_GROK_PROFILE_JSON": json.dumps(payload),
            }
            previous_umask = os.umask(0o002)
            try:
                observed, error = self.mod.probe_grok_remote_profile(
                    str(remote),
                    "grok-4.5",
                    env,
                )
            finally:
                os.umask(previous_umask)
            self.assertIsNone(error)
            self.assertEqual(observed, payload)
            self.assertEqual((cache_dir / "help-cache").stat().st_mode & 0o777, 0o600)
            self.assertEqual((cache_dir / "doctor-cache").stat().st_mode & 0o777, 0o600)

    def test_invalid_model_blocks_full_command_override_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            loop.mkdir()
            env = {
                "AAS_GROK_LATEST_MODEL": "_invalid",
                "AAS_AUTOLOOP_CMD_GROK": "printf should-not-run",
            }
            entry = self.mod.resolve_provider_command("grok", loop, environ=env)
            self.assertEqual(entry["mode"], "argv")
            self.assertFalse(entry["binary_found"])
            self.assertEqual(entry["tried"], [])
            self.assertEqual(entry["grok_selection"]["reason_code"], "resolved_model_invalid")

    def test_remote_fallback_rejects_nonready_mismatch_and_invalid_output(self) -> None:
        cases = []
        cases.append(("blocked", _grok_profile_payload("blocked"), "2", "managed_profile_not_ready"))
        cases.append(
            (
                "mismatch",
                _grok_profile_payload(model_id="grok-4.6"),
                "0",
                "managed_profile_model_mismatch",
            )
        )
        invalid = _grok_profile_payload()
        invalid["endpoint"] = "private.example"
        cases.append(("invalid", invalid, "0", "managed_profile_output_invalid"))
        for name, payload, exit_code, expected_reason in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                bindir = Path(tmp)
                _fake_grok_models_cli(bindir, "grok-4.4")
                _fake_grok_remote_profile_cli(bindir)
                env = {
                    "PATH": str(bindir),
                    "HOME": str(bindir),
                    "USERPROFILE": str(bindir),
                    "AAS_GROK_LATEST_MODEL": "grok-4.5",
                    "AAS_TEST_GROK_PROFILE_JSON": json.dumps(payload),
                    "AAS_TEST_GROK_PROFILE_EXIT": exit_code,
                }
                _binary, found, _tried, selection = self.mod.resolve_provider_binary_details(
                    "grok", environ=env, platform=self.plat
                )
                self.assertFalse(found, selection)
                self.assertTrue(selection["reason_code"].startswith(expected_reason), selection)

    def test_falls_back_to_grok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            bare = _fake_cli(bindir, "grok")
            env = {
                "PATH": str(bindir),
                "HOME": str(bindir),
                "USERPROFILE": str(bindir),
            }
            binary, found, _tried = self.mod.resolve_provider_binary(
                "grok", environ=env, platform=self.plat
            )
            self.assertTrue(found, _tried)
            self.assertTrue(Path(binary).name.startswith("grok"), binary)
            self.assertFalse(Path(binary).name.startswith("grok-remote"), binary)
            self.assertTrue(bare.exists())

    def test_aas_autoloop_bin_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            forced = _fake_cli(bindir, "custom-grok")
            _fake_cli(bindir, "grok-remote")
            env = {
                "PATH": str(bindir),
                "HOME": str(bindir),
                "USERPROFILE": str(bindir),
                "AAS_AUTOLOOP_BIN_GROK": str(forced),
            }
            binary, found, _ = self.mod.resolve_provider_binary(
                "grok", environ=env, platform=self.plat
            )
            self.assertTrue(found)
            self.assertEqual(Path(binary).resolve(), forced.resolve())

    def test_aas_grok_override_when_no_autoloop_bin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            forced = _fake_cli(bindir, "via-aas-grok")
            _fake_cli(bindir, "grok-remote")
            env = {
                "PATH": str(bindir),
                "HOME": str(bindir),
                "USERPROFILE": str(bindir),
                "AAS_GROK": str(forced),
            }
            binary, found, _ = self.mod.resolve_provider_binary(
                "grok", environ=env, platform=self.plat
            )
            self.assertTrue(found)
            self.assertEqual(Path(binary).resolve(), forced.resolve())

    def test_windows_candidates_include_cmd_and_exe(self) -> None:
        cands = self.mod.provider_binary_candidates("grok", platform="windows")
        self.assertIn("grok-remote.cmd", cands)
        self.assertIn("grok.exe", cands)
        self.assertEqual(cands[0], "%USERPROFILE%\\.grok\\bin\\grok.exe")

    def test_provider_subprocess_options_preserve_windows_behavior(self) -> None:
        self.assertEqual(self.mod.provider_subprocess_options("claude"), {})
        with mock.patch.object(self.mod.os, "name", "nt"):
            self.assertEqual(self.mod.provider_subprocess_options("grok"), {})

    def test_macos_candidates_include_homebrew(self) -> None:
        cands = self.mod.provider_binary_candidates("grok", platform="macos")
        self.assertIn("/opt/homebrew/bin/grok", cands)
        self.assertEqual(cands[0], "grok")

    def test_resolve_provider_command_grok_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            _fake_cli(bindir, "grok")
            loop = Path(tmp) / "loop"
            loop.mkdir()
            env = {
                "PATH": str(bindir),
                "HOME": str(bindir),
                "USERPROFILE": str(bindir),
            }
            cleaned = {
                k: v
                for k, v in os.environ.items()
                if not k.startswith("AAS_AUTOLOOP_") and k != "AAS_GROK"
            }
            cleaned.update(env)
            entry = self.mod.resolve_provider_command("grok", loop, environ=cleaned)
            self.assertTrue(entry["binary_found"], entry)
            self.assertEqual(entry["mode"], "argv")
            self.assertIn("-p", entry["argv"])
            self.assertIn("--yolo", entry["argv"])
            self.assertEqual(entry["grok_selection"]["status"], "not-performed")

    def test_resolve_provider_command_pins_resolved_grok_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            _fake_grok_models_cli(bindir, "grok-4.5")
            loop = bindir / "loop"
            loop.mkdir()
            env = {
                "PATH": str(bindir),
                "HOME": str(bindir),
                "USERPROFILE": str(bindir),
                "AAS_GROK_LATEST_MODEL": "grok-4.5",
            }
            entry = self.mod.resolve_provider_command("grok", loop, environ=env)
            self.assertTrue(entry["binary_found"], entry)
            self.assertEqual(entry["grok_selection"]["source"], "bare-model-confirmed")
            self.assertEqual(entry["argv"][-2:], ["-m", "grok-4.5"])


class AntigravityProviderResolveTests(unittest.TestCase):
    """Antigravity resolves the Google Antigravity CLI `agy` (agy -p ...
    --dangerously-skip-permissions), with the standalone `gemini` CLI as a
    per-binary-args fallback (`gemini --yolo -p ...`)."""

    def setUp(self) -> None:
        self.mod = _load_arl_runtime()

    def _resolve(self, bindir: Path, loop: Path) -> dict[str, Any]:
        cleaned = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("AAS_AUTOLOOP_") and not k.startswith("AAS_ANTIGRAVITY")
        }
        cleaned.update({"PATH": str(bindir), "HOME": str(bindir), "USERPROFILE": str(bindir)})
        return self.mod.resolve_provider_command("antigravity", loop, environ=cleaned)

    def test_resolves_agy_with_skip_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            _fake_cli(bindir, "agy")
            loop = bindir / "loop"
            loop.mkdir()
            entry = self._resolve(bindir, loop)
            self.assertTrue(entry["binary_found"], entry)
            self.assertIn("agy", os.path.basename(entry["binary"]).lower())
            self.assertIn("-p", entry["argv"])
            self.assertIn("--dangerously-skip-permissions", entry["argv"])
            self.assertNotIn("--yolo", entry["argv"])
            # Order lock: -p, prompt, then --dangerously-skip-permissions
            # (flags between -p and prompt become the prompt text).
            p_idx = entry["argv"].index("-p")
            skip_idx = entry["argv"].index("--dangerously-skip-permissions")
            self.assertEqual(skip_idx, p_idx + 2, entry["argv"])
            self.assertNotEqual(entry["argv"][p_idx + 1], "--dangerously-skip-permissions")

    def test_falls_back_to_gemini_with_yolo_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            _fake_cli(bindir, "gemini")  # no agy on PATH
            loop = bindir / "loop"
            loop.mkdir()
            entry = self._resolve(bindir, loop)
            self.assertTrue(entry["binary_found"], entry)
            self.assertIn("gemini", os.path.basename(entry["binary"]).lower())
            self.assertIn("--yolo", entry["argv"])
            self.assertNotIn("--dangerously-skip-permissions", entry["argv"])

    def test_prefers_agy_over_gemini_when_both_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp)
            _fake_cli(bindir, "agy")
            _fake_cli(bindir, "gemini")
            loop = bindir / "loop"
            loop.mkdir()
            entry = self._resolve(bindir, loop)
            self.assertIn("agy", os.path.basename(entry["binary"]).lower())
            self.assertIn("--dangerously-skip-permissions", entry["argv"])


class HookCheckWorkspaceRootTests(unittest.TestCase):
    def test_prefers_grok_workspace_root_over_claude_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg = base / "reg"
            root_a = base / "projA"
            root_b = base / "projB"
            loop = base / "loop"
            root_a.mkdir()
            root_b.mkdir()
            loop.mkdir()
            # init minimal loop state so compute_done works if matched
            run_helper(
                "init",
                "--dir",
                str(loop),
                "--goal",
                "g",
                "--success-criteria",
                "s",
                "--max-iterations",
                "5",
                "--goal-focus-mode",
                "off",
            )
            env = _subprocess_env(
                {
                    "AAS_AUTOLOOP_REGISTRY": str(reg),
                    "GROK_WORKSPACE_ROOT": str(root_a),
                    "CLAUDE_PROJECT_DIR": str(root_b),
                }
            )
            subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(HELPER),
                    "arm",
                    "--dir",
                    str(loop),
                    "--root",
                    str(root_a),
                    "--pid",
                    str(os.getpid()),
                    "--registry-dir",
                    str(reg),
                ],
                check=True,
                capture_output=True,
                env=env,
            )
            # hook-check with no --root uses env
            res = subprocess.run(
                [sys.executable, "-B", str(HELPER), "hook-check", "--registry-dir", str(reg)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            # active unfinished loop for root_a -> exit 2 (block); no JSON on stdout
            self.assertEqual(res.returncode, 2, res.stderr)
            self.assertIn("Autoloop", res.stderr)

            # Preferring GROK_WORKSPACE_ROOT: if only CLAUDE points at armed root,
            # but GROK points elsewhere, do not match armed root_a.
            env_wrong = _subprocess_env(
                {
                    "AAS_AUTOLOOP_REGISTRY": str(reg),
                    "GROK_WORKSPACE_ROOT": str(root_b),
                    "CLAUDE_PROJECT_DIR": str(root_a),
                }
            )
            res2 = subprocess.run(
                [sys.executable, "-B", str(HELPER), "hook-check", "--registry-dir", str(reg)],
                capture_output=True,
                text=True,
                env=env_wrong,
                check=False,
            )
            self.assertEqual(res2.returncode, 0, res2.stderr)


class DriveCwdTests(unittest.TestCase):
    def test_drive_sets_child_cwd_to_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg = base / "reg"
            loop = base / "loop"
            root = base / "project root with spaces"
            root.mkdir()
            _init_loop(loop, reg, max_iterations=5)
            # Record cwd then stop
            cmd = _py_iteration(
                "import os,pathlib; d=pathlib.Path(os.environ['AUTOLOOP_DIR']); "
                "d.joinpath('cwd').write_text(os.getcwd()); "
                "pathlib.Path(os.environ['AUTOLOOP_DIR'],'STOP_REQUESTED').write_text('x')"
            )
            env = _subprocess_env({"AAS_AUTOLOOP_REGISTRY": str(reg)})
            res = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(HELPER),
                    "drive",
                    "--dir",
                    str(loop),
                    "--root",
                    str(root),
                    "--cmd",
                    cmd,
                ],
                capture_output=True,
                text=True,
                timeout=40,
                env=env,
                check=False,
                cwd=str(base),  # driver started outside root
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            recorded = (loop / "cwd").read_text()
            self.assertEqual(Path(recorded).resolve(), root.resolve())


class DriveProviderGrokTests(unittest.TestCase):
    def test_drive_provider_grok_fails_before_unprivate_prompt_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg = base / "reg"
            loop = base / "loop"
            root = base / "proj"
            bindir = base / "bin"
            root.mkdir()
            bindir.mkdir()
            # Fake grok via host Python so Windows and POSIX both can exec it.
            fake = bindir / ("fake_grok.cmd" if os.name == "nt" else "fake_grok")
            if os.name == "nt":
                fake.write_text(
                    "@echo off\r\n"
                    f"\"{sys.executable}\" -c "
                    "\"import os,pathlib; d=pathlib.Path(os.environ['AUTOLOOP_DIR']); "
                    "c=(int((d/'c').read_text()) if (d/'c').exists() else 0)+1; "
                    "(d/'c').write_text(str(c)); "
                    "(d/f'cwd_{c}').write_text(os.getcwd()); "
                    "(c>=2 and (d/'STOP_REQUESTED').write_text('x'))\"\r\n",
                    encoding="utf-8",
                )
            else:
                fake.write_text(
                    "#!/bin/sh\n"
                    f"exec \"{sys.executable}\" -c "
                    "\"import os,pathlib; d=pathlib.Path(os.environ['AUTOLOOP_DIR']); "
                    "c=(int((d/'c').read_text()) if (d/'c').exists() else 0)+1; "
                    "(d/'c').write_text(str(c)); "
                    "(d/f'cwd_{c}').write_text(os.getcwd()); "
                    "(c>=2 and (d/'STOP_REQUESTED').write_text('x'))\"\n",
                    encoding="utf-8",
                )
                fake.chmod(0o755)
            _init_loop(loop, reg, max_iterations=10)
            env = _subprocess_env(
                {
                    "AAS_AUTOLOOP_REGISTRY": str(reg),
                    "AAS_AUTOLOOP_BIN_GROK": str(fake),
                }
            )
            env.pop("AAS_AUTOLOOP_CMD_GROK", None)
            env.pop("AAS_GROK", None)
            res = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(HELPER),
                    "drive",
                    "--dir",
                    str(loop),
                    "--root",
                    str(root),
                    "--provider",
                    "grok",
                    "--max-failures",
                    "2",
                    "--iteration-timeout",
                    "10",
                ],
                capture_output=True,
                text=True,
                timeout=45,
                env=env,
                check=False,
                cwd=str(base),
            )
            self.assertEqual(res.returncode, 2, res.stderr + res.stdout)
            self.assertIn("primary prompt transport failed", res.stderr)
            self.assertFalse((loop / "c").exists())
            self.assertFalse((loop / "cwd_1").exists())


@unittest.skipUnless(os.name == "posix", "the supervisor is a POSIX shell runtime")
class SupervisorBehaviorTests(unittest.TestCase):
    def _write_runtime_stub(self, root: Path) -> Path:
        stub = root / "runtime_stub.py"
        stub.write_text(
            """from __future__ import annotations

import json
import sys
from pathlib import Path


def option(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


args = sys.argv[1:]
command = args[0]
loop = Path(option(args, "--dir"))
if command == "done":
    print(json.dumps({"done": (loop / "stub.done").exists()}))
    raise SystemExit(0)
if command == "notify-event":
    with (loop / "stub-notify.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(args, ensure_ascii=False) + "\\n")
    raise SystemExit(0)
if command == "drive":
    provider = option(args, "--provider")
    with (loop / "stub-drive.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"provider": provider, "args": args}) + "\\n")
    if (loop / "stub.exit8").exists():
        raise SystemExit(8)
    if (loop / "stub.exit9").exists():
        raise SystemExit(9)
    if (loop / "stub.exit10").exists():
        raise SystemExit(10)
    if provider == "claude":
        raise SystemExit(5)
    (loop / "stub.done").write_text("done\\n", encoding="utf-8")
    raise SystemExit(0)
raise SystemExit(2)
""",
            encoding="utf-8",
        )
        return stub

    @staticmethod
    def _config(**overrides: object) -> dict[str, object]:
        config: dict[str, object] = {
            "schema_version": "failover.v1",
            "research_title": "Supervisor behavioral test",
            "primary_order": ["claude", "codex"],
            "max_quota_waits_per_primary": 1,
            "max_restarts": 4,
            "retry_sleep_s": 0,
            "rotate_cooldown_s": 0,
            "sync_panel_exclude_until_credit": False,
            "drive_defaults": {
                "panel": "off",
                "notify": "off",
                "iteration_timeout": 5,
                "max_failures": 1,
            },
        }
        config.update(overrides)
        return config

    def _run(
        self,
        root: Path,
        loop: Path,
        config: dict[str, object],
    ) -> subprocess.CompletedProcess[str]:
        project = root / "project"
        project.mkdir(exist_ok=True)
        config_path = loop / "failover.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        stub = self._write_runtime_stub(root)
        env = _subprocess_env(
            {
                "LOOP_DIR": str(loop),
                "PROJECT_ROOT": str(project),
                "FAILOVER_JSON": str(config_path),
                "RUNTIME_PY": str(stub),
                "SYNC_PANEL_PY": str(root / "missing-sync-panel.py"),
            }
        )
        return subprocess.run(
            ["bash", str(SUPERVISOR)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=env,
        )

    def test_supervisor_rotates_and_persists_private_state_with_structured_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            loop.mkdir(mode=0o700)

            result = self._run(root, loop, self._config())

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((loop / "driver" / "PRIMARY").read_text(), "codex")
            self.assertEqual((loop / "driver" / "EXCLUDED").read_text(), "claude\n")
            self.assertEqual(
                stat.S_IMODE((loop / "driver" / "PRIMARY").stat().st_mode), 0o600
            )
            self.assertEqual(
                stat.S_IMODE((loop / "driver" / "EXCLUDED").stat().st_mode), 0o600
            )
            drive_rows = [
                json.loads(line)
                for line in (loop / "stub-drive.jsonl").read_text().splitlines()
            ]
            self.assertEqual(
                [row["provider"] for row in drive_rows], ["claude", "codex"]
            )
            notify_rows = [
                json.loads(line)
                for line in (loop / "stub-notify.jsonl").read_text().splitlines()
            ]
            self.assertGreaterEqual(len(notify_rows), 3)
            self.assertTrue(all(row[0] == "notify-event" for row in notify_rows))
            self.assertTrue(all("--completed" in row for row in notify_rows))
            self.assertNotIn("Supervisor behavioral test", result.stderr)
            self.assertIn("structured notification emitted", result.stderr)
            self.assertEqual(list((loop / "driver").glob(".*.tmp")), [])

    def test_supervisor_refuses_hostile_state_paths_and_links_without_running_driver(self) -> None:
        attacks = (
            "config-primary",
            "config-excluded",
            "driver-symlink",
            "primary-symlink",
            "primary-hardlink",
            "excluded-symlink",
            "excluded-hardlink",
        )
        for attack in attacks:
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                loop = root / "loop"
                loop.mkdir(mode=0o700)
                victim = root / "victim.txt"
                victim.write_bytes(b"victim bytes stay unchanged\n")
                outside = root / "outside-driver"
                outside.mkdir(mode=0o700)
                (outside / "sentinel").write_bytes(b"outside directory unchanged\n")
                config = self._config()

                if attack == "config-primary":
                    config["write_active_primary_path"] = "driver/OTHER"
                elif attack == "config-excluded":
                    config["session_exclude_path"] = "../victim.txt"
                elif attack == "driver-symlink":
                    (loop / "driver").symlink_to(outside, target_is_directory=True)
                else:
                    driver = loop / "driver"
                    driver.mkdir(mode=0o700)
                    leaf = (
                        driver / "EXCLUDED"
                        if attack.startswith("excluded-")
                        else driver / "PRIMARY"
                    )
                    if attack.endswith("symlink"):
                        leaf.symlink_to(victim)
                    else:
                        os.link(victim, leaf)

                before_victim = victim.read_bytes()
                before_outside = (outside / "sentinel").read_bytes()
                result = self._run(root, loop, config)

                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertFalse((loop / "stub-drive.jsonl").exists())
                self.assertEqual(victim.read_bytes(), before_victim)
                self.assertEqual((outside / "sentinel").read_bytes(), before_outside)

    def test_supervisor_never_retries_unverified_resource_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            loop.mkdir(mode=0o700)
            (loop / "stub.exit8").write_text("stop without retry\n", encoding="utf-8")

            result = self._run(root, loop, self._config(max_restarts=50))

            self.assertEqual(result.returncode, 13, result.stdout + result.stderr)
            drive_rows = [
                json.loads(line)
                for line in (loop / "stub-drive.jsonl").read_text().splitlines()
            ]
            self.assertEqual([row["provider"] for row in drive_rows], ["claude"])
            excluded = loop / "driver" / "EXCLUDED"
            self.assertTrue(not excluded.exists() or not excluded.read_text().strip())

    def test_supervisor_never_retries_candidate_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            loop.mkdir(mode=0o700)
            (loop / "stub.exit9").write_text("stop for inspection\n", encoding="utf-8")

            result = self._run(root, loop, self._config(max_restarts=50))

            self.assertEqual(result.returncode, 14, result.stdout + result.stderr)
            drive_rows = [
                json.loads(line)
                for line in (loop / "stub-drive.jsonl").read_text().splitlines()
            ]
            self.assertEqual([row["provider"] for row in drive_rows], ["claude"])
            excluded = loop / "driver" / "EXCLUDED"
            self.assertTrue(not excluded.exists() or not excluded.read_text().strip())

    def test_supervisor_never_retries_unpersisted_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loop = root / "loop"
            loop.mkdir(mode=0o700)
            (loop / "stub.exit10").write_text(
                "stop because tombstone persistence is unverified\n",
                encoding="utf-8",
            )

            result = self._run(root, loop, self._config(max_restarts=50))

            self.assertEqual(result.returncode, 15, result.stdout + result.stderr)
            drive_rows = [
                json.loads(line)
                for line in (loop / "stub-drive.jsonl").read_text().splitlines()
            ]
            self.assertEqual([row["provider"] for row in drive_rows], ["claude"])


class NotifyPolicyTests(unittest.TestCase):
    def _mod(self):
        return _load_arl_runtime()

    def test_normalize_and_resolve_off(self) -> None:
        mod = self._mod()
        self.assertEqual(mod.normalize_notify_token("OFF"), "off")
        self.assertEqual(mod.normalize_notify_token("both"), "both")
        with mock.patch.dict(os.environ, {"AAS_AUTOLOOP_NOTIFY": "off"}, clear=False):
            self.assertIsNone(
                mod.resolve_notify_channel(explicit=None, run_dir=None, default_auto=True)
            )

    def test_notify_off_has_zero_remote_transport_side_effects(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            mod.init_loop(mod.selftest_init_args(loop, max_iterations=1))
            with mock.patch.object(mod, "resolve_remote_notify_argv") as resolve, mock.patch.object(
                mod.subprocess, "run"
            ) as transport:
                payload = mod.emit_loop_progress(
                    loop,
                    "iteration_ok",
                    notify_channel="off",
                    to_stderr=False,
                    extra={
                        "iteration_status": "success",
                        "review_status": "not_required",
                        "finished_at": "2026-07-29T12:00:00Z",
                        "completed_summary": "The local result was recorded.",
                        "current_summary": "The loop remains local-only.",
                        "next_action": "Continue without external notification.",
                        "compute": [],
                    },
                )
            self.assertEqual(payload["event"], "iteration_ok")
            resolve.assert_not_called()
            transport.assert_not_called()

    def test_enforce_notify_without_egress_consent_spawns_no_transport_or_raw_hook(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            init_args = mod.selftest_init_args(loop, max_iterations=1)
            init_args.goal_focus_mode = "enforce"
            mod.init_loop(init_args)
            mod._LAST_REMOTE_NOTIFY.clear()
            with mock.patch.dict(
                os.environ,
                {
                    "AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS": "",
                    "AAS_ALLOW_RAW_NOTIFY_CMD": "1",
                },
                clear=False,
            ), mock.patch.object(
                mod, "resolve_remote_notify_argv"
            ) as resolve, mock.patch.object(
                mod.subprocess, "run"
            ) as transport, mock.patch.object(
                mod, "watch_notify"
            ) as raw_hook:
                payload = mod.emit_loop_progress(
                    loop,
                    "supervisor",
                    notify_channel="zulip",
                    notify_cmd="never-run-raw-hook",
                    to_stderr=False,
                    extra={"compute": []},
                )

            self.assertEqual(payload["notification"]["schema"], "aas.autoloop.notify.v2")
            resolve.assert_not_called()
            transport.assert_not_called()
            raw_hook.assert_not_called()

    def test_enforce_notify_with_explicit_consent_reaches_mocked_transports(self) -> None:
        mod = self._mod()
        bridge_result = {
            "ok": True,
            "dry_run": False,
            "delivery": {"delivered": True, "channel": "zulip"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            init_args = mod.selftest_init_args(loop, max_iterations=1)
            init_args.goal_focus_mode = "enforce"
            mod.init_loop(init_args)
            mod._LAST_REMOTE_NOTIFY.clear()
            with mock.patch.dict(
                os.environ,
                {
                    "AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS": "allow",
                    "AAS_ALLOW_RAW_NOTIFY_CMD": "1",
                },
                clear=False,
            ), mock.patch.object(
                mod,
                "resolve_remote_notify_argv",
                return_value=["mock-remote-bridge"],
            ) as resolve, mock.patch.object(
                mod.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    ["mock-remote-bridge"],
                    0,
                    stdout=json.dumps(bridge_result),
                    stderr="",
                ),
            ) as transport, mock.patch.object(
                mod, "watch_notify"
            ) as raw_hook:
                payload = mod.emit_loop_progress(
                    loop,
                    "supervisor",
                    notify_channel="zulip",
                    notify_cmd="mock-raw-hook",
                    to_stderr=False,
                    extra={
                        "completed_summary": "No research result was banked.",
                        "current_summary": "The safe-denial loop remains unchanged.",
                        "next_action": "Wait for a reviewed secure transport.",
                        "compute": [],
                    },
                )

            resolve.assert_called_once()
            self.assertTrue(resolve.call_args.kwargs["event_json_stdin"])
            transport.assert_called_once()
            outbound = json.loads(transport.call_args.kwargs["input"])
            self.assertEqual(outbound, payload["notification"])
            self.assertEqual(outbound["compute"], {"reported": True, "runs": []})
            raw_hook.assert_called_once()

    def test_iteration_key_dedupes_timestamp_changed_body_for_window(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            loop.mkdir()
            mod._LAST_REMOTE_NOTIFY.clear()
            mod._remote_notify_remember(
                loop,
                fp="first-body",
                iter_key="result_review_error:2",
                now=100.0,
            )
            self.assertTrue(
                mod._remote_notify_is_duplicate(
                    loop,
                    fp="body-with-new-timestamp",
                    iter_key="result_review_error:2",
                    now=150.0,
                )
            )
            self.assertFalse(
                mod._remote_notify_is_duplicate(
                    loop,
                    fp="body-after-window",
                    iter_key="result_review_error:2",
                    now=221.0,
                )
            )

    @unittest.skipUnless(os.name == "posix", "requires POSIX link semantics")
    def test_supervisor_progress_live_status_links_leave_targets_unchanged(self) -> None:
        mod = self._mod()
        for link_kind in ("symlink", "hardlink"):
            with self.subTest(link_kind=link_kind), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                loop = base / "loop"
                mod.init_loop(mod.selftest_init_args(loop, max_iterations=1))
                target = base / f"outside-live-{link_kind}.md"
                target.write_bytes(b"outside live-status bytes stay unchanged\n")
                planted = loop / "LIVE_STATUS.md"
                if link_kind == "symlink":
                    planted.symlink_to(target)
                else:
                    os.link(target, planted)
                before = target.read_bytes()

                payload = mod.emit_loop_progress(
                    loop,
                    "supervisor",
                    notify_channel="off",
                    to_stderr=False,
                    to_stdout_json=False,
                    extra={"source": "supervisor"},
                )

                self.assertEqual(payload["event"], "supervisor")
                self.assertEqual(target.read_bytes(), before)
                self.assertEqual(planted.read_bytes(), before)
                self.assertEqual(
                    list(loop.glob(".runtime-write-*-LIVE_STATUS.md")), []
                )

    @unittest.skipUnless(os.name == "posix", "requires POSIX link semantics")
    def test_notify_dedupe_links_leave_targets_unchanged_without_temp_residue(self) -> None:
        mod = self._mod()
        for link_kind in ("symlink", "hardlink"):
            with self.subTest(link_kind=link_kind), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                loop = base / "loop"
                mod.init_loop(mod.selftest_init_args(loop, max_iterations=1))
                logs = loop / "driver_logs"
                logs.mkdir(mode=0o700)
                target = base / f"outside-dedupe-{link_kind}.json"
                target.write_bytes(b"outside dedupe bytes stay unchanged\n")
                planted = mod._remote_notify_dedupe_path(loop)
                if link_kind == "symlink":
                    planted.symlink_to(target)
                else:
                    os.link(target, planted)
                before = target.read_bytes()
                mod._LAST_REMOTE_NOTIFY.clear()

                mod._remote_notify_remember(
                    loop,
                    fp="synthetic-fingerprint",
                    iter_key="supervisor:1:synthetic",
                    now=100.0,
                )

                self.assertEqual(target.read_bytes(), before)
                self.assertEqual(planted.read_bytes(), before)
                self.assertEqual(
                    list(logs.glob(".runtime-write-*.remote_notify_dedupe.json")),
                    [],
                )
                self.assertEqual(mod._remote_notify_load_disk(loop), {})

    def test_result_review_error_is_next_attempt_without_prior_compute(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            mod.init_loop(mod.selftest_init_args(loop, max_iterations=3))
            record = iteration_record(1, "continue")
            record["execution"] = {
                "executor_provider": "claude",
                "compute": {
                    "recording_status": "explicit",
                    "usage": "hetzner",
                    "services": [{"service": "hetzner", "status": "succeeded"}],
                },
            }
            write_iterations(loop, [record])
            budget = json.loads((loop / "budget.json").read_text(encoding="utf-8"))
            budget["spent_iterations"] = 1
            write_loop_json(loop, "budget.json", budget)
            payload = mod.build_progress_event(
                loop,
                "result_review_error",
                extra={
                    "candidate_id": "candidate-2",
                    "iteration_status": "error",
                    "review_status": "error",
                },
            )
        self.assertEqual(payload["iteration"], 2)
        notification = payload["notification"]
        self.assertEqual(notification["iteration"]["number"], 2)
        self.assertFalse(notification["compute"]["reported"])
        self.assertEqual(notification["compute"]["runs"], [])

    def test_watch_rejected_row_reports_failure_and_persisted_panel_agents(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            mod.init_loop(mod.selftest_init_args(loop, max_iterations=3))
            record = iteration_record(1, "revise")
            record.update(
                {
                    "bank_status": "rejected",
                    "candidate_id": "candidate-rejected-watch",
                    "result_review": {
                        "status": "failed",
                        "providers": ["claude"],
                        "usable_providers": ["codewhale"],
                        "reviewer_families": ["anthropic", "deepseek"],
                    },
                }
            )
            write_iterations(loop, [record])
            budget = mod.read_json(loop / "budget.json")
            budget["spent_iterations"] = 1
            mod.write_json(loop / "budget.json", budget)

            payload = mod.build_progress_event(
                loop,
                "iteration",
                extra={"source": "watch"},
            )

        notification = payload["notification"]
        self.assertEqual(notification["iteration"]["status"], "failure")
        self.assertEqual(notification["review"]["status"], "failed")
        self.assertEqual(
            notification["review"]["families"], ["anthropic", "deepseek"]
        )
        panel = notification["agents"]["panel"]
        self.assertTrue(panel["reported"])
        self.assertEqual(
            {agent["name"] for agent in panel["agents"]},
            {"claude", "codewhale"},
        )

    def test_notify_identity_precedence_config_env_contract_state_directory(self) -> None:
        mod = self._mod()
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "research_loop"
            loop.mkdir()
            (loop / "loop_state.json").write_text(
                json.dumps({"goal": "State goal should be fourth."}),
                encoding="utf-8",
            )
            (loop / "goal_contract.json").write_text(
                json.dumps({"goal": "Contract goal should be third."}),
                encoding="utf-8",
            )
            config_path = loop / "failover.json"
            config_path.write_text(
                json.dumps(
                    {
                        "research_title": "Configured research title",
                        "job_slug": "configured-topic",
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "AAS_AUTOLOOP_RESEARCH_TITLE": "Environment research title",
                "AAS_REMOTE_JOB_ID": "environment-topic",
            }
            with mock.patch.dict(os.environ, env, clear=True):
                configured = mod.resolve_loop_notify_identity(loop)
            self.assertEqual(configured["title"], "Configured research title")
            self.assertEqual(configured["slug"], "configured-topic")

            config_path.unlink()
            with mock.patch.dict(os.environ, env, clear=True):
                from_env = mod.resolve_loop_notify_identity(loop)
            self.assertEqual(from_env["title"], "Environment research title")
            self.assertEqual(from_env["slug"], "environment-topic")

            with mock.patch.dict(os.environ, {}, clear=True):
                from_contract = mod.resolve_loop_notify_identity(loop)
            self.assertEqual(from_contract["title"], "Contract goal should be third.")

            (loop / "goal_contract.json").unlink()
            with mock.patch.dict(os.environ, {}, clear=True):
                from_state = mod.resolve_loop_notify_identity(loop)
            self.assertEqual(from_state["title"], "State goal should be fourth.")

            (loop / "loop_state.json").write_text(
                json.dumps({"goal": ""}), encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {}, clear=True):
                generic = mod.resolve_loop_notify_identity(loop)
            self.assertNotEqual(generic["title"], "research_loop")

    def test_legacy_notify_placeholder_falls_through_to_goal_identity(self) -> None:
        mod = self._mod()
        variants = (
            "optional-stable-zulip-topic-id",
            "OPTIONAL_STABLE_ZULIP_TOPIC_ID",
            "Optional Stable Zulip Topic Id",
        )
        for placeholder in variants:
            with self.subTest(placeholder=placeholder), tempfile.TemporaryDirectory() as tmp:
                loop = Path(tmp) / "research_loop"
                loop.mkdir()
                goal = "Resolve the sample bounded open question"
                (loop / "goal_contract.json").write_text(
                    json.dumps({"goal": goal}), encoding="utf-8"
                )
                (loop / "loop_state.json").write_text(
                    json.dumps(
                        {
                            "goal": "State fallback should remain below the contract.",
                            "standing_orders": {
                                "notify": {
                                    "research_title": placeholder,
                                    "job_slug": placeholder,
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                (loop / "failover.json").write_text(
                    json.dumps(
                        {
                            "research_title": placeholder,
                            "job_slug": placeholder,
                        }
                    ),
                    encoding="utf-8",
                )
                env = {
                    "AAS_AUTOLOOP_RESEARCH_TITLE": placeholder,
                    "AAS_REMOTE_JOB_TITLE": placeholder,
                    "AAS_REMOTE_JOB_ID": placeholder,
                }
                with mock.patch.dict(os.environ, env, clear=True):
                    identity = mod.resolve_loop_notify_identity(loop)
                    remote_job = mod.resolve_remote_job_id(loop)

                self.assertEqual(identity["title"], goal)
                self.assertEqual(identity["slug"], "resolve-the-sample-bounded-open-question")
                self.assertEqual(remote_job, identity["slug"])
                self.assertNotIn("optional-stable", json.dumps(identity).lower())

    def test_explicit_beats_env(self) -> None:
        mod = self._mod()
        with mock.patch.dict(os.environ, {"AAS_AUTOLOOP_NOTIFY": "telegram"}, clear=False):
            self.assertEqual(
                mod.resolve_notify_channel(explicit="zulip", run_dir=None, default_auto=True),
                "zulip",
            )
            self.assertIsNone(
                mod.resolve_notify_channel(explicit="off", run_dir=None, default_auto=True)
            )

    def test_auto_defers_to_env_and_loop_state(self) -> None:
        """``auto`` must not outrank the sources documented below it.

        drive/watch default ``--notify`` to ``auto``, so treating that token as
        decisive silently strands the env and loop_state levels: a suite that
        exports AAS_AUTOLOOP_NOTIFY=off would still post every event to a real
        chat whenever host secrets happened to be configured.
        """
        mod = self._mod()
        with mock.patch.object(mod, "auto_notify_channel_from_secrets", return_value="zulip"):
            with mock.patch.dict(os.environ, {"AAS_AUTOLOOP_NOTIFY": "off"}, clear=False):
                self.assertIsNone(
                    mod.resolve_notify_channel(explicit="auto", run_dir=None, default_auto=True)
                )
            with mock.patch.dict(os.environ, {"AAS_AUTOLOOP_NOTIFY": "telegram"}, clear=False):
                self.assertEqual(
                    mod.resolve_notify_channel(explicit="auto", run_dir=None, default_auto=True),
                    "telegram",
                )
            # loop_state is below env but still above the secrets fallback.
            with tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp) / "loop"
                run_dir.mkdir()
                (run_dir / "loop_state.json").write_text(
                    json.dumps({"notify_channel": "off"}), encoding="utf-8"
                )
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("AAS_AUTOLOOP_NOTIFY", None)
                    os.environ.pop("AAS_REMOTE_NOTIFY", None)
                    self.assertIsNone(
                        mod.resolve_notify_channel(
                            explicit="auto", run_dir=run_dir, default_auto=True
                        )
                    )

    def test_auto_uses_secrets_when_configured(self) -> None:
        mod = self._mod()
        # Default secrets pick is Zulip-primary (not dual "both").
        with mock.patch.object(mod, "auto_notify_channel_from_secrets", return_value="zulip"):
            self.assertEqual(
                mod.resolve_notify_channel(explicit="auto", run_dir=None, default_auto=True),
                "zulip",
            )
            self.assertEqual(
                mod.resolve_notify_channel(explicit=None, run_dir=None, default_auto=True),
                "zulip",
            )

    def test_auto_notify_prefers_zulip_over_telegram(self) -> None:
        mod = self._mod()
        with mock.patch.object(
            mod, "detect_configured_notify_channels", return_value=["zulip", "telegram"]
        ):
            self.assertEqual(mod.auto_notify_channel_from_secrets(), "zulip")
        with mock.patch.object(mod, "detect_configured_notify_channels", return_value=["telegram"]):
            self.assertEqual(mod.auto_notify_channel_from_secrets(), "telegram")

    def test_auto_none_when_unconfigured(self) -> None:
        mod = self._mod()
        with mock.patch.object(mod, "auto_notify_channel_from_secrets", return_value=None):
            self.assertIsNone(
                mod.resolve_notify_channel(explicit="auto", run_dir=None, default_auto=True)
            )

    def test_arm_persists_notify_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            reg, loop = base / "reg", base / "loop"
            init_loop(loop, max_iterations=3)
            res = run_helper(
                "arm",
                "--dir",
                str(loop),
                "--root",
                str(loop),
                "--notify",
                "off",
                "--registry-dir",
                str(reg),
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            payload = json.loads(res.stdout)
            self.assertEqual(payload.get("notify_channel"), "off")
            state = json.loads((loop / "loop_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state.get("notify_channel"), "off")
            # registry files live under active.d/
            entries = list((reg / "active.d").glob("*.json"))
            self.assertTrue(entries)
            entry = json.loads(entries[0].read_text(encoding="utf-8"))
            self.assertEqual(entry.get("notify_channel"), "off")


class ResearchNotifyTextTests(unittest.TestCase):
    """Notify summaries must carry the research finding, not agent status."""

    @staticmethod
    def _runtime():
        runtime = HELPER.parent
        if str(runtime) not in sys.path:
            sys.path.insert(0, str(runtime))
        import autonomous_research_loop_runtime as arl  # noqa: WPS

        return arl

    def test_research_result_text_prefers_objective_and_outcome(self) -> None:
        arl = self._runtime()
        record = {
            "label": "A2-SAMPLE-370",
            "objective": "Design the sample gadget construction",
            "outcome": "sample-construction-designed;no-regressions",
            "primary_independent_agree": True,
            "goal_contribution": "construct",
            "campaign_id": "A2",
        }
        text = arl.research_result_text(record)
        self.assertIn("Design the sample gadget construction", text)
        self.assertIn("sample-construction-designed", text)
        self.assertIn("verification agree", text)
        self.assertIn("construct", text)
        self.assertIn("A2", text)

    def test_research_result_text_prefers_worker_plain_summary(self) -> None:
        arl = self._runtime()
        record = {
            "completed_summary": "The gadget enforces both constraint types.",
            "objective": "unused when a plain summary exists",
            "primary_independent_agree": False,
        }
        text = arl.research_result_text(record)
        self.assertIn("The gadget enforces both constraint types.", text)
        self.assertNotIn("unused when a plain summary exists", text)
        self.assertIn("verification disagree", text)

    def test_research_position_text_lists_bounded_gaps(self) -> None:
        arl = self._runtime()
        record = {"evidence_gaps": ["g1", "g2", "g3", "g4", "g5", "g6"]}
        text = arl.research_position_text(record, "Banked.")
        self.assertIn("Banked.", text)
        self.assertIn("g1", text)
        self.assertIn("g4", text)
        self.assertNotIn("g5", text)
        self.assertIn("(+2 more)", text)
        self.assertEqual(
            arl.research_position_text({}, "fallback only"), "fallback only"
        )
