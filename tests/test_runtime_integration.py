from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from installer.ai_agents_skills.agents import detect_agents
from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.cli import INSTALL_CONFIRMATION_PHRASE, main
from installer.ai_agents_skills.discovery import current_platform
from installer.ai_agents_skills.lifecycle import uninstall
from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.planner import build_plan
from installer.ai_agents_skills.runtime import RUNTIME_SOURCE_ROOT, replace_with_runtime_file, runtime_inventory
from installer.ai_agents_skills.runtime_smoke import (
    run_installed_runtime_smoke,
    runtime_command_target,
    runtime_smoke_coverage_rows,
    selected_runtime_skills,
)
from installer.ai_agents_skills.sanitize import has_sensitive_material
from installer.ai_agents_skills.state import load_state
from installer.ai_agents_skills.verify import verify


def create_agent_home(root: Path, agent: str = "codex") -> None:
    (root / f".{agent}").mkdir(parents=True)


def create_fake_tool(root: Path, name: str, args_path: Path, *, cwd_path: Path | None = None) -> Path:
    recorder = root / f"{name}_recorder.py"
    lines = [
        "from pathlib import Path",
        "import sys",
        f"Path({str(args_path)!r}).write_text('\\n'.join(sys.argv[1:]) + ('\\n' if len(sys.argv) > 1 else ''), encoding='utf-8')",
    ]
    if cwd_path is not None:
        lines.insert(2, "import pathlib")
        lines.append(f"Path({str(cwd_path)!r}).write_text(str(pathlib.Path.cwd()) + '\\n', encoding='utf-8')")
    recorder.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name == "nt":
        wrapper = root / f"{name}.cmd"
        wrapper.write_text(
            f"@echo off\r\n\"{sys.executable}\" \"{recorder}\" %*\r\nexit /b %ERRORLEVEL%\r\n",
            encoding="utf-8",
        )
        return wrapper
    wrapper = root / name
    wrapper.write_text(
        f"#!/usr/bin/env sh\nexec \"{sys.executable}\" \"{recorder}\" \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


class RuntimeIntegrationTests(unittest.TestCase):
    def test_runtime_files_are_root_scoped_and_installed_with_runtime_backed_skill(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                detect_agents(root, ["codex"]),
                platform="linux",
            )
            runtime_actions = [item for item in plan["actions"] if item["artifact_type"] == "runtime-file"]

            self.assertTrue(runtime_actions)
            self.assertEqual({item["agent"] for item in runtime_actions}, {"runtime"})
            self.assertTrue(all(item["owner"] == "runtime" for item in runtime_actions))
            target_relpaths = {item["target_relpath"] for item in runtime_actions}
            self.assertIn("run_skill.sh", target_relpaths)
            self.assertIn("workspace/skills/graph-verifier/run_graph_verifier.sh", target_relpaths)
            self.assertIn("workspace/skills/graph-verifier/graph_verifier.py", target_relpaths)
            self.assertNotIn("run_skill.ps1", target_relpaths)
            self.assertNotIn("run_skill.bat", target_relpaths)
            self.assertNotIn("run_python.bat", target_relpaths)
            self.assertNotIn("workspace/skills/graph-verifier/run_graph_verifier.bat", target_relpaths)

            result = apply_plan(root, plan, dry_run=False)
            self.assertTrue(any(item["artifact_type"] == "runtime-file" for item in result["actions"]))
            self.assertEqual(verify(root)["status"], "ok")
            self.assertTrue((root / ".codex" / "runtime" / "run_skill.sh").is_file())

            uninstall_result = uninstall(root, skills={"graph-verifier"}, dry_run=False)
            self.assertTrue(any(item["artifact_type"] == "runtime-file" for item in uninstall_result["removed"]))
            self.assertFalse((root / ".codex" / "runtime" / "workspace" / "skills" / "graph-verifier" / "graph_verifier.py").exists())

    def test_windows_runtime_plan_filters_posix_runtime_files(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                detect_agents(root, ["codex"]),
                platform="windows",
            )
            runtime_actions = [item for item in plan["actions"] if item["artifact_type"] == "runtime-file"]
            target_relpaths = {item["target_relpath"] for item in runtime_actions}

            self.assertIn("run_skill.ps1", target_relpaths)
            self.assertIn("run_skill.bat", target_relpaths)
            self.assertIn("run_python.bat", target_relpaths)
            self.assertIn("workspace/skills/graph-verifier/graph_verifier.py", target_relpaths)
            self.assertIn("workspace/skills/graph-verifier/run_graph_verifier.bat", target_relpaths)
            self.assertNotIn("run_skill.sh", target_relpaths)
            self.assertNotIn("workspace/skills/graph-verifier/run_graph_verifier.sh", target_relpaths)

    def test_submission_venue_selector_installs_runtime_files_for_supported_agents(self) -> None:
        manifests = load_manifests()
        for agent in ("codex", "claude", "deepseek", "copilot"):
            for platform in ("linux", "macos", "wsl", "windows"):
                with self.subTest(agent=agent, platform=platform):
                    with tempfile.TemporaryDirectory() as tmp:
                        root = Path(tmp)
                        create_agent_home(root, agent)
                        plan = build_plan(
                            root,
                            manifests,
                            ["submission-venue-selector"],
                            detect_agents(root, [agent]),
                            platform=platform,
                        )
                        skill_actions = [
                            item
                            for item in plan["actions"]
                            if item.get("artifact_type") == "skill-file" and item.get("skill") == "submission-venue-selector"
                        ]
                        runtime_actions = [item for item in plan["actions"] if item.get("artifact_type") == "runtime-file"]
                        self.assertEqual(len(skill_actions), 1)
                        self.assertNotEqual(skill_actions[0]["operation"], "skip")
                        target_relpaths = {item["target_relpath"] for item in runtime_actions}
                        self.assertIn(
                            "workspace/skills/submission-venue-selector/submission_venue_selector.py",
                            target_relpaths,
                        )
                        if platform == "windows":
                            self.assertIn("run_skill.ps1", target_relpaths)
                            self.assertIn("run_skill.bat", target_relpaths)
                            self.assertIn(
                                "workspace/skills/submission-venue-selector/run_submission_venue_selector.ps1",
                                target_relpaths,
                            )
                            self.assertIn(
                                "workspace/skills/submission-venue-selector/run_submission_venue_selector.bat",
                                target_relpaths,
                            )
                        else:
                            self.assertIn("run_skill.sh", target_relpaths)
                            self.assertIn(
                                "workspace/skills/submission-venue-selector/run_submission_venue_selector.sh",
                                target_relpaths,
                            )

    def test_openclaw_submission_venue_selector_runtime_backed_skill_is_expected_blocked(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "openclaw")
            plan = build_plan(
                root,
                manifests,
                ["submission-venue-selector"],
                detect_agents(root, ["openclaw"]),
            )
            file_action = next(action for action in plan["actions"] if action["artifact_type"] == "skill-file")

            self.assertEqual(file_action["classification"], "blocked")
            self.assertEqual(file_action["operation"], "skip")
            self.assertEqual(file_action["reason"], "OpenClaw runtime-backed skills require neutral runtime evidence")
            self.assertFalse([action for action in plan["actions"] if action["artifact_type"] == "runtime-file"])

    def test_no_runtime_disables_runtime_actions(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                detect_agents(root, ["codex"]),
                runtime_profile="none",
            )
            self.assertFalse([item for item in plan["actions"] if item["artifact_type"] == "runtime-file"])

    def test_canonical_runtime_inventory_has_only_candidate_sources(self) -> None:
        result = runtime_inventory(RUNTIME_SOURCE_ROOT)
        self.assertEqual(result["status"], "ok")
        offenders = [
            item
            for item in result["entries"]
            if item["classification"] != "candidate"
        ]
        self.assertEqual(offenders, [])

    def test_runtime_manifest_covers_canonical_runtime_candidates(self) -> None:
        manifests = load_manifests()
        result = runtime_inventory(RUNTIME_SOURCE_ROOT)
        candidates = {
            item["path"]
            for item in result["entries"]
            if item["classification"] == "candidate"
        }
        declared = {
            entry["source"]
            for entry in manifests["runtime"].get("runners", [])
        }
        for spec in manifests["runtime"].get("skills", {}).values():
            declared.update(entry["source"] for entry in spec.get("files", []))

        self.assertEqual(sorted(candidates - declared), [])
        self.assertEqual(sorted(declared - candidates), [])

    def test_runtime_inventory_output_uses_sanitized_relative_entries(self) -> None:
        result = runtime_inventory(RUNTIME_SOURCE_ROOT)
        serialized = json.dumps(result)

        self.assertFalse(has_sensitive_material(serialized))
        self.assertNotIn(str(RUNTIME_SOURCE_ROOT), serialized)
        for item in result["entries"]:
            self.assertFalse(Path(item["path"]).is_absolute())
            self.assertNotIn("content", item)

    def test_full_runtime_windows_profile_has_native_launcher_per_skill(self) -> None:
        manifests = load_manifests()
        runtime = manifests["runtime"]
        full_profile_skills = runtime["runtime_profiles"]["full"]["skills"]
        missing = []
        for skill in full_profile_skills:
            launchers = [
                entry["target"]
                for entry in runtime["skills"][skill].get("files", [])
                if (
                    "windows" in entry.get("platforms", [])
                    and entry["target"].lower().endswith((".bat", ".ps1"))
                )
            ]
            if not launchers:
                missing.append(skill)

        self.assertEqual(missing, [])

    def test_full_runtime_posix_profile_has_native_launcher_per_skill(self) -> None:
        manifests = load_manifests()
        runtime = manifests["runtime"]
        full_profile_skills = runtime["runtime_profiles"]["full"]["skills"]
        missing = []
        for skill in full_profile_skills:
            launchers = [
                entry["target"]
                for entry in runtime["skills"][skill].get("files", [])
                if (
                    set(entry.get("platforms", [])).intersection({"linux", "macos", "wsl"})
                    and entry["target"].lower().endswith(".sh")
                )
            ]
            if not launchers:
                missing.append(skill)

        self.assertEqual(missing, [])

    def test_windows_runtime_commands_are_documented_for_full_profile_skills(self) -> None:
        manifests = load_manifests()
        runtime = manifests["runtime"]
        canonical_skills = Path(__file__).resolve().parents[1] / "canonical" / "skills"
        missing = []
        for skill in runtime["runtime_profiles"]["full"]["skills"]:
            text = (canonical_skills / skill / "SKILL.md").read_text(encoding="utf-8")
            windows_targets = [
                entry["target"].removeprefix("workspace/")
                for entry in runtime["skills"][skill].get("files", [])
                if (
                    "windows" in entry.get("platforms", [])
                    and entry["target"].lower().endswith((".bat", ".ps1"))
                )
            ]
            for target in windows_targets:
                if "run_skill.bat" not in text or target not in text:
                    missing.append(f"{skill}:{target}")

        self.assertEqual(missing, [])

    def test_formal_runtime_smoke_skills_are_supported_and_use_platform_launchers(self) -> None:
        manifests = load_manifests()
        selected = set(selected_runtime_skills(
            manifests,
            {"lean-formalization-intake", "lean-strict-verification-gate"},
        ))
        self.assertEqual(selected, {"lean-formalization-intake", "lean-strict-verification-gate"})
        self.assertEqual(
            runtime_command_target(manifests, "lean-strict-verification-gate", "linux"),
            "skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "lean-strict-verification-gate", "windows", "run_skill.bat"),
            "skills/lean-strict-verification-gate/run_lean_strict_verification_gate.bat",
        )
        self.assertEqual(
            runtime_command_target(manifests, "lean-strict-verification-gate", "windows", "run_skill.ps1"),
            "skills/lean-strict-verification-gate/run_lean_strict_verification_gate.ps1",
        )

    def test_axiom_axle_runtime_smoke_skill_is_supported_and_uses_platform_launchers(self) -> None:
        manifests = load_manifests()
        selected = set(selected_runtime_skills(manifests, {"axiom-axle-mcp"}))

        self.assertEqual(selected, {"axiom-axle-mcp"})
        self.assertEqual(
            runtime_command_target(manifests, "axiom-axle-mcp", "linux"),
            "skills/axiom-axle-mcp/run_axiom_axle_mcp.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "axiom-axle-mcp", "macos"),
            "skills/axiom-axle-mcp/run_axiom_axle_mcp.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "axiom-axle-mcp", "wsl"),
            "skills/axiom-axle-mcp/run_axiom_axle_mcp.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "axiom-axle-mcp", "windows", "run_skill.bat"),
            "skills/axiom-axle-mcp/run_axiom_axle_mcp.bat",
        )
        self.assertEqual(
            runtime_command_target(manifests, "axiom-axle-mcp", "windows", "run_skill.ps1"),
            "skills/axiom-axle-mcp/run_axiom_axle_mcp.ps1",
        )

    def test_lean_explore_mcp_runtime_smoke_skill_is_supported_and_uses_platform_launchers(self) -> None:
        manifests = load_manifests()
        selected = set(selected_runtime_skills(manifests, {"lean-explore-mcp"}))

        self.assertEqual(selected, {"lean-explore-mcp"})
        self.assertEqual(
            runtime_command_target(manifests, "lean-explore-mcp", "linux"),
            "skills/lean-explore-mcp/run_lean_explore_mcp.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "lean-explore-mcp", "macos"),
            "skills/lean-explore-mcp/run_lean_explore_mcp.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "lean-explore-mcp", "wsl"),
            "skills/lean-explore-mcp/run_lean_explore_mcp.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "lean-explore-mcp", "windows", "run_skill.bat"),
            "skills/lean-explore-mcp/run_lean_explore_mcp.bat",
        )
        self.assertEqual(
            runtime_command_target(manifests, "lean-explore-mcp", "windows", "run_skill.ps1"),
            "skills/lean-explore-mcp/run_lean_explore_mcp.ps1",
        )

    def test_installed_runtime_smoke_uses_scratch_workspace(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            plan = build_plan(
                root,
                manifests,
                ["formal-skeleton-helper"],
                detect_agents(root, ["codex"]),
                platform=platform,
            )
            apply_plan(root, plan, dry_run=False)

            result = run_installed_runtime_smoke(
                root,
                manifests,
                skills={"formal-skeleton-helper"},
                platform=platform,
                timeout=30,
            )

            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["mode"], "installed")
            self.assertFalse((root / ".codex" / "runtime" / "workspace" / "runtime-smoke").exists())

    def test_axiom_axle_helper_does_not_execute_install_or_leak_secret(self) -> None:
        helper = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "axiom-axle-mcp"
            / "axiom_axle_mcp.py"
        )
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            marker = Path(tmp) / "executed"
            for name in ("uvx", "uvx.exe", "pip", "npx", "axle-mcp-server"):
                fake = fake_bin / name
                fake.write_text(f"#!/usr/bin/env sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
                fake.chmod(0o755)
            env = {
                **os.environ,
                "PATH": str(fake_bin),
                "AXLE_API_KEY": "AXLE-SMOKE-CANARY",
                "PYTHONDONTWRITEBYTECODE": "1",
            }

            for command in ("doctor", "smoke", "config-snippet"):
                completed = subprocess.run(
                    [sys.executable, str(helper), command],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                payload = json.loads(completed.stdout)
                serialized = json.dumps(payload, sort_keys=True)
                self.assertTrue(payload["no_auto_install"])
                self.assertFalse(payload["installs_attempted"])
                self.assertFalse(payload["live_api_attempted"])
                self.assertFalse(payload["config_written"])
                self.assertFalse(payload["server_started"])
                self.assertNotIn("AXLE-SMOKE-CANARY", serialized)
                if command == "doctor":
                    self.assertEqual(payload["auth_status"], "present")

            self.assertFalse(marker.exists())

    def test_axiom_axle_helper_does_not_write_config_or_state(self) -> None:
        helper = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "axiom-axle-mcp"
            / "axiom_axle_mcp.py"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

            for command in ("doctor", "smoke", "config-snippet"):
                completed = subprocess.run(
                    [sys.executable, str(helper), command],
                    capture_output=True,
                    text=True,
                    cwd=root,
                    env=env,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

            self.assertEqual(list(root.rglob("*")), [])

    def test_lean_explore_mcp_helper_does_not_execute_install_start_server_or_leak_secret(self) -> None:
        helper = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "lean-explore-mcp"
            / "lean_explore_mcp.py"
        )
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            marker = Path(tmp) / "executed"
            for name in ("lean-explore", "lean-explore.exe", "pip", "pip.exe", "python -m pip"):
                fake = fake_bin / name
                fake.write_text(f"#!/usr/bin/env sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
                fake.chmod(0o755)
            env = {
                **os.environ,
                "PATH": str(fake_bin),
                "LEANEXPLORE_API_KEY": "LEANEXPLORE-SMOKE-CANARY",
                "PYTHONDONTWRITEBYTECODE": "1",
            }

            commands = (
                ("doctor",),
                ("smoke",),
                ("config-snippet", "--backend", "api"),
                ("config-snippet", "--backend", "local"),
            )
            for command in commands:
                completed = subprocess.run(
                    [sys.executable, str(helper), *command],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                payload = json.loads(completed.stdout)
                serialized = json.dumps(payload, sort_keys=True)
                self.assertTrue(payload["no_auto_install"])
                self.assertFalse(payload["installs_attempted"])
                self.assertFalse(payload["live_api_attempted"])
                self.assertFalse(payload["config_written"])
                self.assertFalse(payload["server_started"])
                self.assertFalse(payload["downloads_attempted"])
                self.assertNotIn("LEANEXPLORE-SMOKE-CANARY", serialized)
                if command == ("doctor",):
                    self.assertEqual(payload["auth_status"], "present")
                if command == ("config-snippet", "--backend", "api"):
                    command_payload = payload["local_stdio_mcp_config"]["mcpServers"]["lean-explore"]
                    self.assertEqual(command_payload["args"], ["mcp", "serve", "--backend", "api"])
                    self.assertEqual(command_payload["env"]["LEANEXPLORE_API_KEY"], "<LEANEXPLORE_API_KEY>")
                if command == ("config-snippet", "--backend", "local"):
                    command_payload = payload["local_stdio_mcp_config"]["mcpServers"]["lean-explore"]
                    self.assertEqual(command_payload["args"], ["mcp", "serve", "--backend", "local"])
                    self.assertNotIn("env", command_payload)

            self.assertFalse(marker.exists())

    def test_lean_explore_mcp_helper_does_not_write_config_or_state(self) -> None:
        helper = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "lean-explore-mcp"
            / "lean_explore_mcp.py"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

            commands = (
                ("doctor",),
                ("smoke",),
                ("config-snippet", "--backend", "api"),
                ("config-snippet", "--backend", "local"),
            )
            for command in commands:
                completed = subprocess.run(
                    [sys.executable, str(helper), *command],
                    capture_output=True,
                    text=True,
                    cwd=root,
                    env=env,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

            self.assertEqual(list(root.rglob("*")), [])

    def test_formal_runtime_doctor_does_not_execute_or_install_toolchain_commands(self) -> None:
        helper_paths = [
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "lean-formalization-intake"
            / "lean_formalization_intake.py",
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "lean-strict-verification-gate"
            / "lean_strict_verification_gate.py",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            marker = Path(tmp) / "executed"
            for name in ("lean", "lake", "elan", "npm", "npx", "pip"):
                fake = fake_bin / name
                fake.write_text(f"#!/usr/bin/env sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
                fake.chmod(0o755)
            env = {**os.environ, "PATH": str(fake_bin)}
            for helper in helper_paths:
                completed = subprocess.run(
                    [sys.executable, str(helper), "doctor"],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                payload = json.loads(completed.stdout)
                self.assertTrue(payload["no_auto_install"])
                self.assertFalse(payload["installs_attempted"])
            self.assertFalse(marker.exists())

    def test_formal_helpers_doctor_honors_explicit_tool_env_without_executing(self) -> None:
        helper_paths = [
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "lean-formalization-intake"
            / "lean_formalization_intake.py",
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "lean-strict-verification-gate"
            / "lean_strict_verification_gate.py",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_lean = root / "fake-lean"
            fake_lake = root / "fake-lake"
            marker = root / "executed"
            for path in (fake_lean, fake_lake):
                path.write_text(f"#!/usr/bin/env sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
                path.chmod(0o755)
            env = {**os.environ, "AAS_LEAN": str(fake_lean), "AAS_LAKE": str(fake_lake)}
            for helper in helper_paths:
                completed = subprocess.run(
                    [sys.executable, str(helper), "doctor"],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                payload = json.loads(completed.stdout)
                self.assertEqual(payload["tool_status"]["lean"]["path"], str(fake_lean))
                self.assertEqual(payload["tool_status"]["lean"]["source"], "env")
                self.assertEqual(payload["tool_status"]["lake"]["path"], str(fake_lake))
                self.assertEqual(payload["tool_status"]["lake"]["source"], "env")
            self.assertFalse(marker.exists())

    def test_lean_strict_gate_direct_runner_uses_explicit_lean(self) -> None:
        helper = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "lean-strict-verification-gate"
            / "lean_strict_verification_gate.py"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lean_args = root / "lean-args.txt"
            fake_lean = create_fake_tool(root, "lean", lean_args)
            lean_file = root / "proof.lean"
            lean_file.write_text("theorem demo : True := by\n  trivial\n", encoding="utf-8")
            env = {**os.environ, "AAS_LEAN": str(fake_lean)}

            payload = self.run_json_helper(
                [
                    sys.executable,
                    str(helper),
                    "verify",
                    "--input",
                    str(lean_file),
                    "--artifact-stage",
                    "final_candidate",
                    "--typecheck",
                ],
                env=env,
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["lean_check_status"], "typechecked")
            self.assertEqual(payload["runner"], "direct-lean")
            self.assertEqual(payload["tool_status"]["lean"]["source"], "env")
            self.assertEqual(lean_args.read_text(encoding="utf-8").strip(), str(lean_file))

    def test_lean_strict_gate_lake_env_runner_requires_lake_project_and_uses_project_cwd(self) -> None:
        helper = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "lean-strict-verification-gate"
            / "lean_strict_verification_gate.py"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            (project / "lakefile.toml").write_text("name = \"formal\"\n", encoding="utf-8")
            lake_args = root / "lake-args.txt"
            lake_cwd = root / "lake-cwd.txt"
            fake_lake = create_fake_tool(root, "lake", lake_args, cwd_path=lake_cwd)
            lean_file = root / "proof.lean"
            lean_file.write_text("theorem demo : True := by\n  trivial\n", encoding="utf-8")
            env = {**os.environ, "AAS_LAKE": str(fake_lake)}

            payload = self.run_json_helper(
                [
                    sys.executable,
                    str(helper),
                    "verify",
                    "--input",
                    str(lean_file),
                    "--artifact-stage",
                    "final_candidate",
                    "--typecheck",
                    "--runner",
                    "lake-env-lean",
                    "--project-root",
                    str(project),
                ],
                env=env,
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["lean_check_status"], "typechecked")
            self.assertEqual(payload["runner"], "lake-env-lean")
            self.assertEqual(payload["typecheck_cwd"], str(project.resolve()))
            self.assertEqual(lake_cwd.read_text(encoding="utf-8").strip(), str(project.resolve()))
            self.assertEqual(
                lake_args.read_text(encoding="utf-8").splitlines(),
                ["env", "lean", str(lean_file.resolve())],
            )

            missing_project = root / "missing-project"
            missing_project.mkdir()
            failed_payload = self.run_json_helper(
                [
                    sys.executable,
                    str(helper),
                    "verify",
                    "--input",
                    str(lean_file),
                    "--artifact-stage",
                    "final_candidate",
                    "--typecheck",
                    "--runner",
                    "lake-env-lean",
                    "--project-root",
                    str(missing_project),
                ],
                env=env,
                expected_returncode=1,
            )
            self.assertEqual(failed_payload["lean_check_status"], "command_failed")
            self.assertIn("lakefile", failed_payload["typecheck_stderr"])

    def test_lean_strict_gate_scan_blocks_placeholders_unsafe_constructs_and_bad_encoding(self) -> None:
        helper = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "lean-strict-verification-gate"
            / "lean_strict_verification_gate.py"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final_candidate = root / "final.lean"
            final_candidate.write_text("theorem demo : True := by\n  sorry\n", encoding="utf-8")
            stub = root / "stub.lean"
            stub.write_text("theorem demo : True := by\n  sorry\n", encoding="utf-8")
            unsafe = root / "unsafe.lean"
            unsafe.write_text("import Evil.Provider\n#eval IO.println \"x\"\n", encoding="utf-8")
            bad = root / "bad.lean"
            bad.write_bytes(b"\xff")

            final_payload = self.run_json_helper(
                [sys.executable, str(helper), "scan", "--input", str(final_candidate), "--artifact-stage", "final_candidate"],
                expected_returncode=1,
            )
            self.assertFalse(final_payload["ok"])
            self.assertIn("active_placeholder", {item["kind"] for item in final_payload["findings"]})

            stub_payload = self.run_json_helper(
                [sys.executable, str(helper), "scan", "--input", str(stub), "--artifact-stage", "stub"],
            )
            self.assertTrue(stub_payload["ok"])
            self.assertEqual(stub_payload["placeholder_status"], "placeholders_allowed_for_stub")

            unsafe_payload = self.run_json_helper(
                [sys.executable, str(helper), "scan", "--input", str(unsafe)],
                expected_returncode=1,
            )
            self.assertFalse(unsafe_payload["ok"])
            self.assertTrue({"unsafe_construct", "non_allowlisted_import"}.issubset(
                {item["kind"] for item in unsafe_payload["findings"]}
            ))

            bad_payload = self.run_json_helper(
                [sys.executable, str(helper), "scan", "--input", str(bad)],
                expected_returncode=1,
            )
            self.assertEqual(bad_payload["findings"][0]["kind"], "invalid_utf8")

    def run_json_helper(self, argv: list[str], *, expected_returncode: int = 0, env: dict[str, str] | None = None) -> dict[str, Any]:
        completed = subprocess.run(argv, capture_output=True, text=True, check=False, env=env)
        self.assertEqual(completed.returncode, expected_returncode, completed.stderr)
        return json.loads(completed.stdout)

    def test_full_runtime_profile_filters_platform_files(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                detect_agents(root, ["codex"]),
                runtime_profile="full",
                platform="windows",
            )
            runtime_actions = [item for item in plan["actions"] if item["artifact_type"] == "runtime-file"]
            blocked = [item for item in runtime_actions if item["classification"] == "blocked"]
            target_relpaths = {item["target_relpath"] for item in runtime_actions}

            self.assertEqual(blocked, [])
            self.assertTrue(runtime_actions)
            self.assertTrue(all(not item["target_relpath"].endswith(".sh") for item in runtime_actions))
            self.assertIn("run_skill.bat", target_relpaths)
            self.assertIn("run_python.bat", target_relpaths)
            self.assertIn("workspace/skills/zotero/run_zot.bat", target_relpaths)
            self.assertIn("workspace/skills/getscipapers_requester/run_gsp_helper.bat", target_relpaths)

        for platform in ("linux", "macos", "wsl"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                create_agent_home(root, "codex")
                plan = build_plan(
                    root,
                    manifests,
                    ["graph-verifier"],
                    detect_agents(root, ["codex"]),
                    runtime_profile="full",
                    platform=platform,
                )
                runtime_actions = [item for item in plan["actions"] if item["artifact_type"] == "runtime-file"]
                blocked = [item for item in runtime_actions if item["classification"] == "blocked"]

                self.assertEqual(blocked, [])
                self.assertTrue(runtime_actions)
                self.assertTrue(all(not item["target_relpath"].endswith((".bat", ".ps1")) for item in runtime_actions))
                self.assertTrue(any(item["target_relpath"] == "run_skill.sh" for item in runtime_actions))

    def test_runtime_inventory_blocks_state_and_live_config_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace" / "skills" / "zotero").mkdir(parents=True)
            (root / "workspace" / "skills" / "zotero" / "config.json").write_text("{}", encoding="utf-8")
            (root / "workspace" / "config").mkdir(parents=True)
            (root / "workspace" / "config" / "research-compute.toml").write_text("token = 'x'", encoding="utf-8")
            (root / "workspace" / "config" / "research-compute.example.toml").write_text(
                "# example only\n",
                encoding="utf-8",
            )
            (root / "workspace" / "skills" / "zotero" / "__pycache__").mkdir()
            (root / "workspace" / "skills" / "zotero" / "__pycache__" / "x.pyc").write_bytes(b"x")
            (root / "workspace" / "data" / "calibre" / "cache").mkdir(parents=True)
            (root / "workspace" / "data" / "calibre" / "cache" / "metadata.db").write_bytes(b"db")
            (root / "workspace" / "skills" / "zotero" / "docker-compose.yml").write_text(
                "services:\n  zotero:\n    restart: unless-stopped\n",
                encoding="utf-8",
            )
            (root / "workspace" / "skills" / "zotero" / "run_service.sh").write_text(
                "#!/usr/bin/env sh\n# --restart=unless-stopped\n",
                encoding="utf-8",
            )
            (root / "workspace" / ".env").write_text("TOKEN=x\n", encoding="utf-8")
            (root / "workspace" / ".mcp").mkdir()
            (root / "workspace" / ".mcp" / "servers.json").write_text("{}", encoding="utf-8")
            (root / "workspace" / "skills" / "lean" / "mcp-config.json").parent.mkdir(parents=True)
            (root / "workspace" / "skills" / "lean" / "mcp-config.json").write_text("{}", encoding="utf-8")
            (root / "workspace" / "skills" / "lean" / "provider-config.toml").write_text(
                "provider = 'example'\n",
                encoding="utf-8",
            )
            (root / "workspace" / "skills" / "lean" / "provider-config.example.toml").write_text(
                "# example only\n",
                encoding="utf-8",
            )
            (root / "workspace" / "skills" / "lean" / "axle.toml").write_text("enabled = true\n", encoding="utf-8")
            (root / "workspace" / "skills" / "lean" / "package.json").write_text(
                '{"scripts":{"start":"node server.js"}}\n',
                encoding="utf-8",
            )
            (root / "workspace" / "skills" / "lean" / "Dockerfile").write_text("FROM python:3\n", encoding="utf-8")
            (root / "workspace" / "skills" / "lean" / "Procfile").write_text("web: python app.py\n", encoding="utf-8")
            (root / "workspace" / "skills" / "lean" / "formal.service").write_text(
                "[Service]\nExecStart=/bin/true\n",
                encoding="utf-8",
            )

            result = runtime_inventory(root)
            blocked = {item["path"]: item["classification"] for item in result["entries"]}
            reasons = {item["path"]: item["reason"] for item in result["entries"]}

            self.assertEqual(blocked["workspace/skills/zotero/config.json"], "denied")
            self.assertEqual(blocked["workspace/config/research-compute.toml"], "denied")
            self.assertEqual(blocked["workspace/config/research-compute.example.toml"], "candidate")
            self.assertEqual(blocked["workspace/skills/zotero/__pycache__/x.pyc"], "denied")
            self.assertEqual(blocked["workspace/data/calibre/cache/metadata.db"], "denied")
            self.assertEqual(blocked["workspace/skills/zotero/docker-compose.yml"], "denied")
            self.assertEqual(blocked["workspace/skills/zotero/run_service.sh"], "blocked")
            self.assertIn("persistent execution marker", reasons["workspace/skills/zotero/run_service.sh"])
            self.assertEqual(blocked["workspace/.env"], "denied")
            self.assertEqual(blocked["workspace/.mcp/servers.json"], "denied")
            self.assertEqual(blocked["workspace/skills/lean/mcp-config.json"], "denied")
            self.assertEqual(blocked["workspace/skills/lean/provider-config.toml"], "denied")
            self.assertEqual(blocked["workspace/skills/lean/provider-config.example.toml"], "candidate")
            self.assertEqual(blocked["workspace/skills/lean/axle.toml"], "denied")
            self.assertEqual(blocked["workspace/skills/lean/package.json"], "denied")
            self.assertEqual(blocked["workspace/skills/lean/Dockerfile"], "denied")
            self.assertEqual(blocked["workspace/skills/lean/Procfile"], "denied")
            self.assertEqual(blocked["workspace/skills/lean/formal.service"], "denied")

    def test_runtime_existing_drift_is_not_adopted_without_backup_replace(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            existing = root / ".codex" / "runtime" / "workspace" / "skills" / "graph-verifier" / "graph_verifier.py"
            existing.parent.mkdir(parents=True)
            existing.write_text("# locally modified runtime helper\n", encoding="utf-8")

            plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                detect_agents(root, ["codex"]),
                adopt=True,
                platform="linux",
            )
            graph_runtime = [
                item for item in plan["actions"]
                if item.get("target_relpath") == "workspace/skills/graph-verifier/graph_verifier.py"
            ][0]

            self.assertEqual(graph_runtime["classification"], "unmanaged")
            self.assertEqual(graph_runtime["operation"], "skip")
            self.assertIn("differs from runtime source", graph_runtime["reason"])

            replace_plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                detect_agents(root, ["codex"]),
                backup_replace=True,
                platform="linux",
            )
            replacement = [
                item for item in replace_plan["actions"]
                if item.get("target_relpath") == "workspace/skills/graph-verifier/graph_verifier.py"
            ][0]
            self.assertEqual(replacement["classification"], "conflict")
            self.assertEqual(replacement["operation"], "backup-replace")

    def test_runtime_inventory_reports_symlinked_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "outside"
            target.mkdir()
            link = root / "workspace" / "linked"
            link.parent.mkdir(parents=True)
            try:
                link.symlink_to(target, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")

            result = runtime_inventory(root)
            entries = {item["path"]: item for item in result["entries"]}

            self.assertEqual(entries["workspace/linked"]["classification"], "blocked")
            self.assertEqual(entries["workspace/linked"]["reason"], "symlink")

    def test_agent_scoped_uninstall_preserves_shared_runtime_for_other_agents(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            create_agent_home(root, "claude")
            plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                detect_agents(root, ["codex", "claude"]),
                platform="windows",
            )
            apply_plan(root, plan, dry_run=False)

            runtime_file = (
                root
                / "AppData"
                / "Local"
                / "ai-agents-skills"
                / "runtime"
                / "workspace"
                / "skills"
                / "graph-verifier"
                / "graph_verifier.py"
            )
            self.assertTrue(runtime_file.is_file())

            result = uninstall(root, skills={"graph-verifier"}, agents={"claude"}, dry_run=False)
            removed_runtime = [
                item
                for item in result["removed"]
                if item.get("artifact_type") == "runtime-file"
            ]

            self.assertEqual(removed_runtime, [])
            self.assertTrue(runtime_file.is_file())
            self.assertTrue((root / ".codex" / "skills" / "graph-verifier" / "SKILL.md").exists())
            self.assertFalse((root / ".claude" / "skills" / "graph-verifier" / "SKILL.md").exists())
            self.assertEqual(verify(root, agent_filter={"codex"})["status"], "ok")
            self.assertEqual(verify(root, agent_filter={"claude"})["status"], "no-managed-artifacts")

    def test_agent_scoped_uninstall_removes_runtime_when_last_consumer_goes_away(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                detect_agents(root, ["codex"]),
                platform="windows",
            )
            apply_plan(root, plan, dry_run=False)

            result = uninstall(root, skills={"graph-verifier"}, agents={"codex"}, dry_run=False)
            removed_runtime = [
                item for item in result["removed"]
                if item.get("artifact_type") == "runtime-file"
            ]
            remaining_runtime = [
                item for item in load_state(root)["artifacts"]
                if item.get("artifact_type") == "runtime-file"
            ]

            self.assertTrue(removed_runtime)
            self.assertEqual(remaining_runtime, [])
            self.assertFalse((
                root
                / ".codex"
                / "runtime"
                / "workspace"
                / "skills"
                / "graph-verifier"
                / "graph_verifier.py"
            ).exists())

    @unittest.skipUnless(os.name == "nt", "Windows batch wrapper test")
    def test_getscipapers_windows_runner_uses_installed_runtime_workspace(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            plan = build_plan(
                root,
                manifests,
                ["getscipapers-requester"],
                detect_agents(root, ["codex"]),
                platform="windows",
            )
            apply_plan(root, plan, dry_run=False)
            runtime_root = root / ".codex" / "runtime"
            runtime_workspace = runtime_root / "workspace"
            fake_home = root / "home"
            fake_home.mkdir()
            env = os.environ.copy()
            env["USERPROFILE"] = str(fake_home)
            env.pop("GETSCIPAPERS_SKILL_CONFIG", None)
            env.pop("OPENCLAW_WORKSPACE", None)
            env.pop("AAS_RUNTIME_WORKSPACE", None)

            completed = subprocess.run(
                [
                    str(runtime_root / "run_skill.bat"),
                    "skills/getscipapers_requester/run_gsp_helper.bat",
                    "latest-downloads",
                    "--limit",
                    "0",
                ],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["files"], [])
            state_dir = runtime_workspace / "data" / "research" / "getscipapers_bot" / "state"
            self.assertTrue(state_dir.is_dir())
            self.assertFalse((fake_home / ".codex").exists())

    @unittest.skipUnless(os.name == "nt", "Windows batch wrapper test")
    def test_docling_windows_wrapper_forwards_more_than_nine_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "runtime"
            docling_dir = runtime_root / "workspace" / "skills" / "docling"
            docling_dir.mkdir(parents=True)
            source_dir = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "skills" / "docling"
            shutil.copy2(source_dir / "run_docling.bat", docling_dir / "run_docling.bat")
            shutil.copy2(source_dir / "run_docling.ps1", docling_dir / "run_docling.ps1")
            (docling_dir / "docling_convert.py").write_text(
                "import json, sys\nprint(json.dumps(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            args = [f"arg{i}" for i in range(12)]
            env = os.environ.copy()
            env["AAS_RUNTIME_PYTHON"] = sys.executable

            completed = subprocess.run(
                [str(docling_dir / "run_docling.bat"), "convert", *args],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout), args)

    def test_cli_plan_shows_runtime_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "--agent",
                    "codex",
                    "plan",
                    "--skill",
                    "graph-verifier",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            self.assertTrue(any(item["artifact_type"] == "runtime-file" for item in payload["actions"]))

    def test_cli_install_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
                patch("sys.stdin", io.StringIO(f"{INSTALL_CONFIRMATION_PHRASE}\n")),
            ):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "--agent",
                    "codex",
                    "install",
                    "--skill",
                    "graph-verifier",
                    "--apply",
                ])
            self.assertEqual(code, 0)
            self.assertTrue((root / ".codex" / "runtime" / "workspace" / "skills" / "graph-verifier" / "graph_verifier.py").is_file())

    def test_runtime_preflight_rejects_real_system_openclaw_runtime_root(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                detect_agents(root, ["codex"]),
                runtime_root=root / ".openclaw" / "ai-agents-skills" / "runtime",
            )

            with patch("installer.ai_agents_skills.runtime.looks_like_real_system_root", return_value=True):
                with self.assertRaisesRegex(ValueError, "OpenClaw runtime writes"):
                    apply_plan(root, plan, dry_run=True)

    def test_runtime_replace_does_not_follow_predictable_temp_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_tmp:
            root = Path(tmp)
            outside = Path(outside_tmp)
            source = root / "source.txt"
            target = root / "managed.txt"
            victim = outside / "victim.txt"
            predictable_temp = root / ".managed.txt.runtime.tmp"
            source.write_text("managed\n", encoding="utf-8")
            victim.write_text("outside\n", encoding="utf-8")
            try:
                predictable_temp.symlink_to(victim)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"file symlink unavailable: {exc}")

            replace_with_runtime_file(source, target, {"file_type": "text", "mode": "0644"})

            self.assertEqual(victim.read_text(encoding="utf-8"), "outside\n")
            self.assertTrue(predictable_temp.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "managed\n")

    @unittest.skipIf(os.name == "nt", "POSIX runtime runner is not a native Windows runtime target")
    def test_bash_runtime_runner_ignores_external_workspace_without_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            runtime_root = temp / "runtime"
            workspace_script = runtime_root / "workspace" / "skills" / "demo" / "run.sh"
            workspace_script.parent.mkdir(parents=True)
            workspace_script.write_text("#!/usr/bin/env bash\nprintf 'managed-workspace\\n'\n", encoding="utf-8")
            workspace_script.chmod(0o755)
            runner = runtime_root / "run_skill.sh"
            shutil.copy2(Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_skill.sh", runner)
            runner.chmod(0o755)

            external_script = temp / "external" / "skills" / "demo" / "run.sh"
            external_script.parent.mkdir(parents=True)
            marker = temp / "external-ran"
            external_script.write_text(
                f"#!/usr/bin/env bash\nprintf 'external-workspace\\n'\ntouch {marker}\n",
                encoding="utf-8",
            )
            external_script.chmod(0o755)
            env = os.environ.copy()
            env["AAS_RUNTIME_WORKSPACE"] = str(temp / "external")
            env.pop("AAS_ALLOW_EXTERNAL_RUNTIME_WORKSPACE", None)

            completed = subprocess.run(
                ["bash", str(runner), "skills/demo/run.sh"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "managed-workspace\n")
            self.assertFalse(marker.exists())

    @unittest.skipIf(os.name == "nt", "POSIX runtime runner is not a native Windows runtime target")
    def test_bash_runtime_runner_ignores_external_secrets_file_without_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            runtime_root = temp / "runtime"
            workspace_script = runtime_root / "workspace" / "skills" / "demo" / "run.sh"
            workspace_script.parent.mkdir(parents=True)
            workspace_script.write_text(
                "#!/usr/bin/env bash\nprintf '%s|%s\\n' \"$AAS_SECRETS_FILE\" \"$OPENCLAW_SECRETS_FILE\"\n",
                encoding="utf-8",
            )
            workspace_script.chmod(0o755)
            runner = runtime_root / "run_skill.sh"
            shutil.copy2(Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_skill.sh", runner)
            runner.chmod(0o755)
            external = temp / "external-secrets.json"
            env = os.environ.copy()
            env["AAS_SECRETS_FILE"] = str(external)
            env.pop("AAS_ALLOW_EXTERNAL_SECRETS_FILE", None)

            completed = subprocess.run(
                ["bash", str(runner), "skills/demo/run.sh"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
            )

            expected = str((runtime_root / "workspace" / ".secrets.json").resolve())
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, f"{expected}|{expected}\n")

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell runner test")
    def test_powershell_runtime_runner_ignores_external_workspace_without_opt_in(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")

        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            runtime_root = temp / "runtime"
            workspace_script = runtime_root / "workspace" / "skills" / "demo" / "run.bat"
            workspace_script.parent.mkdir(parents=True)
            workspace_script.write_text("@echo off\r\necho managed-workspace\r\n", encoding="utf-8")
            runner = runtime_root / "run_skill.ps1"
            shutil.copy2(Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_skill.ps1", runner)

            external_script = temp / "external" / "skills" / "demo" / "run.bat"
            external_script.parent.mkdir(parents=True)
            marker = temp / "external-ran"
            external_script.write_text(
                f"@echo off\r\necho external-workspace\r\ntype nul > \"{marker}\"\r\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AAS_RUNTIME_WORKSPACE"] = str(temp / "external")
            env.pop("AAS_ALLOW_EXTERNAL_RUNTIME_WORKSPACE", None)

            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(runner),
                    "skills/demo/run.bat",
                ],
                check=False,
                text=True,
                capture_output=True,
                env=env,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.replace("\r\n", "\n"), "managed-workspace\n")
            self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell runner junction test")
    def test_powershell_runtime_runner_rejects_reparse_point_parent(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")

        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            runtime_root = temp / "runtime"
            workspace = runtime_root / "workspace"
            workspace.mkdir(parents=True)
            runner = runtime_root / "run_skill.ps1"
            shutil.copy2(Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_skill.ps1", runner)
            external_script_dir = temp / "external" / "skills" / "demo"
            external_script_dir.mkdir(parents=True)
            marker = temp / "external-ran"
            (external_script_dir / "run.bat").write_text(
                f"@echo off\r\necho external-workspace\r\ntype nul > \"{marker}\"\r\n",
                encoding="utf-8",
            )
            link = workspace / "skills" / "demo"
            link.parent.mkdir(parents=True)
            completed_link = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(external_script_dir)],
                text=True,
                capture_output=True,
            )
            if completed_link.returncode != 0:
                self.skipTest(f"junction unavailable: {completed_link.stderr or completed_link.stdout}")

            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(runner),
                    "skills/demo/run.bat",
                ],
                check=False,
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("symlinked runtime command path", completed.stderr)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "nt", "Windows batch wrapper test")
    def test_rss_summary_wrapper_propagates_digest_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "runtime"
            workspace = runtime_root / "workspace"
            rss_dir = workspace / "skills" / "rss-news-digest"
            rss_dir.mkdir(parents=True)
            shutil.copy2(
                Path(__file__).resolve().parents[1]
                / "canonical"
                / "runtime"
                / "skills"
                / "rss-news-digest"
                / "run_and_summarize.bat",
                rss_dir / "run_and_summarize.bat",
            )
            (rss_dir / "run_rss_news_digest.bat").write_text("@echo off\r\nexit /b 7\r\n", encoding="utf-8")
            env = os.environ.copy()
            env["AAS_RUNTIME_WORKSPACE"] = str(workspace)

            completed = subprocess.run(
                [str(rss_dir / "run_and_summarize.bat")],
                check=False,
                text=True,
                capture_output=True,
                env=env,
            )

            self.assertEqual(completed.returncode, 7)
            self.assertFalse((workspace / "data" / "research" / "rss" / "digests" / "last-summary.md").exists())

    def test_runtime_windows_launchers_are_ascii_clean(self) -> None:
        runtime_root = Path(__file__).resolve().parents[1] / "canonical" / "runtime"
        offenders = []
        for path in runtime_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".bat", ".ps1"}:
                continue
            text = path.read_text(encoding="utf-8")
            if any(ord(char) > 127 for char in text):
                offenders.append(str(path.relative_to(runtime_root)))
        self.assertEqual(offenders, [])

    def test_windows_python_runner_prefers_path_python_before_py_launcher(self) -> None:
        runner = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_python.bat"
        text = runner.read_text(encoding="utf-8")

        py_probe = text.index("\nwhere py >nul")
        self.assertLess(text.index("\nwhere python.exe >nul"), py_probe)
        self.assertLess(text.index("\nwhere python >nul"), py_probe)

    def test_runtime_smoke_selects_native_command_targets(self) -> None:
        manifests = load_manifests()
        self.assertEqual(
            runtime_command_target(manifests, "graph-verifier", "windows"),
            "skills/graph-verifier/run_graph_verifier.bat",
        )
        self.assertEqual(
            runtime_command_target(manifests, "graph-verifier", "windows", "run_skill.ps1"),
            "skills/graph-verifier/run_graph_verifier.bat",
        )
        self.assertEqual(
            runtime_command_target(manifests, "lean-strict-verification-gate", "windows", "run_skill.ps1"),
            "skills/lean-strict-verification-gate/run_lean_strict_verification_gate.ps1",
        )
        self.assertEqual(
            runtime_command_target(manifests, "graph-verifier", "linux"),
            "skills/graph-verifier/run_graph_verifier.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "formal-skeleton-helper", "macos"),
            "skills/formal-skeleton-helper/run_formal_skeleton.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "deep-research-workflow", "linux"),
            "skills/deep-research-workflow/run_deep_research_workflow.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "deep-research-workflow", "windows", "run_skill.ps1"),
            "skills/deep-research-workflow/run_deep_research_workflow.ps1",
        )

    def test_runtime_smoke_rejects_non_runtime_skill_scope(self) -> None:
        manifests = load_manifests()
        with self.assertRaises(ValueError):
            selected_runtime_skills(manifests, {"agent-group-discuss"})

    def test_runtime_smoke_rejects_runtime_skill_without_smoke_contract(self) -> None:
        manifests = load_manifests()
        with self.assertRaisesRegex(ValueError, "zotero"):
            selected_runtime_skills(manifests, {"zotero"})

    def test_runtime_smoke_coverage_classifies_non_offline_skills(self) -> None:
        manifests = load_manifests()
        rows = {row["skill"]: row for row in runtime_smoke_coverage_rows(manifests)}

        self.assertEqual(rows["graph-verifier"]["status"], "offline-smoke")
        self.assertEqual(rows["zotero"]["status"], "manual-native")
        self.assertEqual(rows["docling"]["status"], "doctor-only")
        self.assertNotIn("zotero", selected_runtime_skills(manifests, None))
        self.assertIn("local library", rows["zotero"]["reason"])
        self.assertTrue(all(row["reason"] for row in rows.values()))

    def test_runtime_smoke_contracts_are_offline_and_workspace_relative(self) -> None:
        manifests = load_manifests()
        for skill in selected_runtime_skills(manifests, None):
            with self.subTest(skill=skill):
                smoke = manifests["runtime"]["skills"][skill]["smoke"]
                self.assertEqual(smoke["schema"], "runtime-smoke.v1")
                self.assertEqual(smoke["mode"], "offline")
                self.assertGreater(smoke["timeout_seconds"], 0)
                for value in smoke["command"].values():
                    self.assertTrue(value.startswith("workspace/"))
                    self.assertNotIn("..", Path(value).parts)
                self.assertEqual(smoke["safety"]["network"], "forbidden")
                self.assertEqual(smoke["safety"]["live_api"], "forbidden")
                self.assertEqual(smoke["safety"]["package_install"], "forbidden")
                self.assertEqual(smoke["safety"]["server_start"], "forbidden")
                self.assertEqual(smoke["safety"]["config_write"], "forbidden")
                self.assertEqual(smoke["safety"]["real_secrets"], "forbidden")

        self.assertEqual(
            manifests["runtime"]["skills"]["deep-research-workflow"]["smoke"]["args"],
            ["selftest"],
        )

    def test_gdrive_credential_errors_do_not_embed_raw_secret_values(self) -> None:
        for path in (
            Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "skills" / "zotero" / "lib" / "gdrive.py",
            Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "skills" / "calibre" / "lib" / "gdrive.py",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("'{creds_value}'", text)
            self.assertNotIn('"{creds_value}"', text)


if __name__ == "__main__":
    unittest.main()
