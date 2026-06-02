from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.agents import detect_agents
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
    (root / f".{agent}").mkdir(parents=True, exist_ok=True)


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
            subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    "init",
                    "--dir",
                    str(run_dir),
                    "--goal",
                    "integrate autonomous research loop",
                    "--success-criteria",
                    "ledger validates",
                    "--max-iterations",
                    "2",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    "append-iteration",
                    "--dir",
                    str(run_dir),
                    "--mode",
                    "bounded-research",
                    "--objective",
                    "record evidence gate result",
                    "--decision",
                    "continue",
                    "--source-id",
                    "S1",
                    "--guard-ref",
                    "G1",
                    "--remaining-gap",
                    "second pass",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
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
        for agent in ("codex", "claude", "deepseek", "copilot"):
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
