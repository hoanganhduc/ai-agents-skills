from __future__ import annotations

import argparse
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


def create_agent_home(root: Path, agent: str) -> None:
    target_for(root, agent).home.mkdir(parents=True, exist_ok=True)


def run_helper(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HELPER), *args],
        capture_output=True,
        text=True,
        timeout=20,
        check=check,
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
    def test_runtime_helper_selftest_is_offline_and_validates_ledger(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(HELPER), "selftest"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
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
                [sys.executable, str(HELPER), "validate", "--dir", str(run_dir)],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
            status = subprocess.run(
                [sys.executable, str(HELPER), "status", "--dir", str(run_dir)],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )

            validate_payload = json.loads(validate.stdout)
            status_payload = json.loads(status.stdout)
            self.assertEqual(validate_payload["status"], "ok")
            self.assertEqual(validate_payload["checked"]["iterations"], 1)
            self.assertEqual(status_payload["status"], "ok")
            self.assertEqual(status_payload["state_status"], "running")
            self.assertEqual(status_payload["last_decision"], "continue")

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
            [sys.executable, str(HELPER), "selftest"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
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
        env = dict(os.environ, AAS_AUTOLOOP_REGISTRY=str(registry))
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(HELPER), *args],
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
    env = dict(os.environ, AAS_AUTOLOOP_REGISTRY=str(registry))
    subprocess.run(
        [sys.executable, str(HELPER), "arm", "--dir", str(run_dir), "--root", str(root)],
        capture_output=True, text=True, timeout=20, env=env, check=False,
    )


def _init_loop(run_dir: Path, registry: Path, *extra: str, max_iterations: int = 3) -> None:
    env = dict(os.environ, AAS_AUTOLOOP_REGISTRY=str(registry))
    subprocess.run(
        [sys.executable, str(HELPER), "init", "--dir", str(run_dir), "--goal", "g",
         "--success-criteria", "sc", "--max-iterations", str(max_iterations), *extra],
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
        env = dict(os.environ, AAS_AUTOLOOP_REGISTRY=str(registry), CLAUDE_PROJECT_DIR=str(root))
        env.pop("AUTOLOOP_DISABLE", None)
        env.pop("AUTOLOOP_DRIVER", None)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(HELPER), "hook-check"],
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
        env = dict(os.environ, AAS_AUTOLOOP_REGISTRY=str(registry), CLAUDE_PROJECT_DIR=str(root))
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
        env = dict(os.environ, AAS_AUTOLOOP_REGISTRY=str(registry))
        return subprocess.run(
            [sys.executable, str(HELPER), "drive", "--dir", str(run_dir), "--root", str(run_dir),
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
    def test_drive_private_umask_is_grok_provider_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            observed_modes: dict[str, int] = {}
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
                env = {
                    key: value
                    for key, value in os.environ.items()
                    if not key.startswith("AAS_AUTOLOOP_") and not key.startswith("AAS_GROK")
                }
                env["AAS_AUTOLOOP_REGISTRY"] = str(reg)
                argv = [
                    sys.executable,
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
                self.assertEqual(completed.returncode, 0, completed.stderr)
                observed_modes[mode] = (loop / cache_name).stat().st_mode & 0o777
            self.assertEqual(observed_modes, {"grok": 0o600, "claude": 0o664, "cmd": 0o664})

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


@unittest.skipUnless(os.name == "posix", "the .sh shim is shipped for POSIX manual use only")
class AutoloopDriverShimTests(unittest.TestCase):
    """Smoke: the POSIX .sh convenience shim delegates to the runtime drive subcommand."""

    DRIVER = HELPER.parent / "autoloop_driver.sh"

    def test_shim_stops_immediately_when_already_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); reg, loop = base / "reg", base / "loop"
            _init_loop(loop, reg, max_iterations=5)
            (loop / "STOP_REQUESTED").write_text("", encoding="utf-8")
            env = dict(os.environ, AAS_AUTOLOOP_REGISTRY=str(reg))
            res = subprocess.run(
                ["bash", str(self.DRIVER), "--dir", str(loop), "--root", str(loop),
                 "--cmd", ': > "$AUTOLOOP_DIR/ran"'],
                capture_output=True, text=True, timeout=40, env=env, check=False,
            )
            self.assertEqual(res.returncode, 0)
            self.assertFalse((loop / "ran").exists())  # iteration command never ran


def _load_arl_runtime():
    spec = importlib.util.spec_from_file_location("arl_runtime_under_test", HELPER)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
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
            )
            env = dict(
                os.environ,
                AAS_AUTOLOOP_REGISTRY=str(reg),
                GROK_WORKSPACE_ROOT=str(root_a),
                CLAUDE_PROJECT_DIR=str(root_b),
            )
            subprocess.run(
                [
                    sys.executable,
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
                [sys.executable, str(HELPER), "hook-check", "--registry-dir", str(reg)],
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
            env_wrong = dict(
                os.environ,
                AAS_AUTOLOOP_REGISTRY=str(reg),
                GROK_WORKSPACE_ROOT=str(root_b),
                CLAUDE_PROJECT_DIR=str(root_a),
            )
            res2 = subprocess.run(
                [sys.executable, str(HELPER), "hook-check", "--registry-dir", str(reg)],
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
            env = dict(os.environ, AAS_AUTOLOOP_REGISTRY=str(reg))
            res = subprocess.run(
                [
                    sys.executable,
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
    def test_drive_provider_grok_multi_iter_and_stop(self) -> None:
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
            env = dict(os.environ)
            env["AAS_AUTOLOOP_REGISTRY"] = str(reg)
            env["AAS_AUTOLOOP_BIN_GROK"] = str(fake)
            env.pop("AAS_AUTOLOOP_CMD_GROK", None)
            env.pop("AAS_GROK", None)
            res = subprocess.run(
                [
                    sys.executable,
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
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
            self.assertEqual((loop / "c").read_text().strip(), "2")
            self.assertEqual(Path((loop / "cwd_1").read_text().strip()).resolve(), root.resolve())


class NotifyPolicyTests(unittest.TestCase):
    def _mod(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("arl_rt_notify", HELPER)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod

    def test_normalize_and_resolve_off(self) -> None:
        mod = self._mod()
        self.assertEqual(mod.normalize_notify_token("OFF"), "off")
        self.assertEqual(mod.normalize_notify_token("both"), "both")
        with mock.patch.dict(os.environ, {"AAS_AUTOLOOP_NOTIFY": "off"}, clear=False):
            self.assertIsNone(
                mod.resolve_notify_channel(explicit=None, run_dir=None, default_auto=True)
            )

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

    def test_auto_uses_secrets_when_configured(self) -> None:
        mod = self._mod()
        with mock.patch.object(mod, "auto_notify_channel_from_secrets", return_value="both"):
            self.assertEqual(
                mod.resolve_notify_channel(explicit="auto", run_dir=None, default_auto=True),
                "both",
            )
            self.assertEqual(
                mod.resolve_notify_channel(explicit=None, run_dir=None, default_auto=True),
                "both",
            )

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
