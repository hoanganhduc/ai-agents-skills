from __future__ import annotations

import contextlib
import copy
import io
import importlib.util
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from installer.ai_agents_skills.agents import detect_agents, target_for
from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.cli import INSTALL_CONFIRMATION_PHRASE, main
from installer.ai_agents_skills.discovery import current_platform
from installer.ai_agents_skills.lifecycle import rollback, uninstall
from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.planner import build_plan
from installer.ai_agents_skills.runtime import RUNTIME_SOURCE_ROOT, replace_with_runtime_file, runtime_denied_patterns, runtime_inventory
from installer.ai_agents_skills.runtime_smoke import (
    run_installed_runtime_smoke,
    run_runtime_smoke,
    run_smoke_case,
    runtime_command_target,
    runtime_smoke_coverage_rows,
    runtime_smoke_skill_names,
    selected_runtime_skills,
)
from installer.ai_agents_skills.sanitize import has_sensitive_material
from installer.ai_agents_skills.state import artifact_signature, load_state, save_state, sha256_file
from installer.ai_agents_skills.verify import verify


def create_agent_home(root: Path, agent: str = "codex") -> None:
    target_for(root, agent).home.mkdir(parents=True)


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
    def test_runtime_denied_patterns_match_manifest(self) -> None:
        manifests = load_manifests()

        self.assertEqual(tuple(manifests["runtime"]["denied_patterns"]), runtime_denied_patterns())

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

    def test_rollback_preserves_runtime_runner_when_other_runtime_skill_remains(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            agents = detect_agents(root, ["codex"])
            graph_plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                agents,
                platform="linux",
            )
            graph_result = apply_plan(root, graph_plan, dry_run=False)
            formal_plan = build_plan(
                root,
                manifests,
                ["formal-skeleton-helper"],
                agents,
                platform="linux",
            )
            apply_plan(root, formal_plan, dry_run=False)

            runtime_root = root / ".codex" / "runtime"
            runner = runtime_root / "run_skill.sh"
            graph_runtime = runtime_root / "workspace" / "skills" / "graph-verifier" / "graph_verifier.py"
            formal_runtime = runtime_root / "workspace" / "skills" / "formal-skeleton-helper" / "run_formal_skeleton.sh"
            self.assertTrue(runner.is_file())
            self.assertTrue(graph_runtime.is_file())
            self.assertTrue(formal_runtime.is_file())

            rollback(root, run_id=graph_result["run_id"], dry_run=False)

            self.assertTrue(runner.is_file())
            self.assertFalse(graph_runtime.exists())
            self.assertTrue(formal_runtime.is_file())
            self.assertEqual(verify(root)["status"], "ok")

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
            self.assertIn("run_python.ps1", target_relpaths)
            self.assertIn("workspace/skills/graph-verifier/graph_verifier.py", target_relpaths)
            self.assertFalse(any(path.endswith((".bat", ".cmd")) for path in target_relpaths))
            self.assertNotIn("run_skill.sh", target_relpaths)
            self.assertNotIn("workspace/skills/graph-verifier/run_graph_verifier.sh", target_relpaths)

    def test_windows_upgrade_removes_obsolete_managed_runtime_file_and_rollback_restores_it(self) -> None:
        manifests = load_manifests()
        old_manifests = copy.deepcopy(manifests)
        old_manifests["runtime"]["skills"]["graph-verifier"]["files"].append(
            {
                "source": "runners/run_skill.ps1",
                "target": "workspace/skills/graph-verifier/run_graph_verifier.bat",
                "type": "text",
                "platforms": ["windows"],
                "newline": "crlf",
                "mode": "0644",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            agents = detect_agents(root, ["codex"])
            apply_plan(
                root,
                build_plan(root, old_manifests, ["graph-verifier"], agents, platform="windows"),
                dry_run=False,
            )
            obsolete = (
                root
                / ".codex"
                / "runtime"
                / "workspace"
                / "skills"
                / "graph-verifier"
                / "run_graph_verifier.bat"
            )
            self.assertTrue(obsolete.is_file())

            upgrade_plan = build_plan(
                root, manifests, ["graph-verifier"], agents, platform="windows"
            )
            removals = [
                action
                for action in upgrade_plan["actions"]
                if action.get("path") == str(obsolete)
            ]
            self.assertEqual(len(removals), 1)
            self.assertEqual(removals[0]["operation"], "remove-obsolete")

            result = apply_plan(root, upgrade_plan, dry_run=False)
            self.assertFalse(obsolete.exists())
            self.assertFalse(
                any(item.get("artifact") == str(obsolete) for item in load_state(root)["artifacts"])
            )

            rollback(root, run_id=result["run_id"], dry_run=False)
            self.assertTrue(obsolete.is_file())
            self.assertTrue(
                any(item.get("artifact") == str(obsolete) for item in load_state(root)["artifacts"])
            )

    def test_windows_upgrade_preserves_modified_obsolete_runtime_file(self) -> None:
        manifests = load_manifests()
        old_manifests = copy.deepcopy(manifests)
        old_manifests["runtime"]["skills"]["graph-verifier"]["files"].append(
            {
                "source": "runners/run_skill.ps1",
                "target": "workspace/skills/graph-verifier/run_graph_verifier.bat",
                "type": "text",
                "platforms": ["windows"],
                "newline": "crlf",
                "mode": "0644",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            agents = detect_agents(root, ["codex"])
            apply_plan(
                root,
                build_plan(root, old_manifests, ["graph-verifier"], agents, platform="windows"),
                dry_run=False,
            )
            obsolete = (
                root
                / ".codex"
                / "runtime"
                / "workspace"
                / "skills"
                / "graph-verifier"
                / "run_graph_verifier.bat"
            )
            obsolete.write_text("@echo off\r\necho user-edit\r\n", encoding="utf-8")

            upgrade_plan = build_plan(
                root, manifests, ["graph-verifier"], agents, platform="windows"
            )
            conflicts = [
                action
                for action in upgrade_plan["actions"]
                if action.get("path") == str(obsolete)
            ]
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0]["classification"], "conflict")
            self.assertEqual(conflicts[0]["operation"], "skip")

            apply_plan(root, upgrade_plan, dry_run=False)
            self.assertIn("user-edit", obsolete.read_text(encoding="utf-8"))
            self.assertTrue(
                any(item.get("artifact") == str(obsolete) for item in load_state(root)["artifacts"])
            )

    def test_windows_upgrade_removes_obsolete_files_from_all_managed_runtime_roots(self) -> None:
        manifests = load_manifests()
        old_manifests = copy.deepcopy(manifests)
        old_manifests["runtime"]["skills"]["graph-verifier"]["files"].append(
            {
                "source": "runners/run_skill.ps1",
                "target": "workspace/skills/graph-verifier/run_graph_verifier.bat",
                "type": "text",
                "platforms": ["windows"],
                "newline": "crlf",
                "mode": "0644",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            agents = detect_agents(root, ["codex"])
            codex_runtime = root / ".codex" / "runtime"
            shared_runtime = root / "AppData" / "Local" / "ai-agents-skills" / "runtime"
            for runtime_root in (codex_runtime, shared_runtime):
                apply_plan(
                    root,
                    build_plan(
                        root,
                        old_manifests,
                        ["graph-verifier"],
                        agents,
                        platform="windows",
                        runtime_root=runtime_root,
                    ),
                    dry_run=False,
                )

            obsolete_files = [
                runtime_root
                / "workspace"
                / "skills"
                / "graph-verifier"
                / "run_graph_verifier.bat"
                for runtime_root in (codex_runtime, shared_runtime)
            ]
            self.assertTrue(all(path.is_file() for path in obsolete_files))

            upgrade_plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                agents,
                platform="windows",
                runtime_root=shared_runtime,
            )
            removals = {
                action["path"]
                for action in upgrade_plan["actions"]
                if action.get("operation") == "remove-obsolete"
            }
            self.assertTrue({str(path) for path in obsolete_files}.issubset(removals))

            result = apply_plan(root, upgrade_plan, dry_run=False)
            self.assertTrue(all(not path.exists() for path in obsolete_files))
            rollback(root, run_id=result["run_id"], dry_run=False)
            self.assertTrue(all(path.is_file() for path in obsolete_files))

    def test_windows_upgrade_preserves_runtime_files_declared_for_other_platforms(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            agents = detect_agents(root, ["codex"])
            runtime_root = root / "shared-runtime"
            apply_plan(
                root,
                build_plan(
                    root,
                    manifests,
                    ["graph-verifier"],
                    agents,
                    platform="linux",
                    runtime_root=runtime_root,
                ),
                dry_run=False,
            )
            posix_runner = runtime_root / "run_skill.sh"
            graph_runner = (
                runtime_root
                / "workspace"
                / "skills"
                / "graph-verifier"
                / "run_graph_verifier.sh"
            )
            self.assertTrue(posix_runner.is_file() and graph_runner.is_file())

            windows_plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                agents,
                platform="windows",
                runtime_root=runtime_root,
            )
            removal_paths = {
                action["path"]
                for action in windows_plan["actions"]
                if action.get("operation") == "remove-obsolete"
            }
            self.assertNotIn(str(posix_runner), removal_paths)
            self.assertNotIn(str(graph_runner), removal_paths)

    def test_opencode_only_runtime_uses_neutral_shared_root(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "opencode")
            plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                detect_agents(root, ["opencode"]),
                platform="linux",
            )
            runtime_actions = [item for item in plan["actions"] if item["artifact_type"] == "runtime-file"]
            self.assertTrue(runtime_actions)
            self.assertTrue(
                all(
                    str(root / ".local" / "share" / "ai-agents-skills" / "runtime") in item["path"]
                    for item in runtime_actions
                )
            )
            self.assertFalse(any(".config/opencode" in item["path"] for item in runtime_actions))
            self.assertFalse(any(".codex/runtime" in item["path"] for item in runtime_actions))

            apply_plan(root, plan, dry_run=False)
            self.assertTrue((root / ".local" / "share" / "ai-agents-skills" / "runtime" / "run_skill.sh").is_file())
            self.assertTrue((root / ".config" / "opencode" / "skills" / "graph-verifier" / "SKILL.md").is_file())
            self.assertEqual(verify(root)["status"], "ok")

    def test_antigravity_only_runtime_uses_neutral_shared_root(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "antigravity")
            plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                detect_agents(root, ["antigravity"]),
                platform="linux",
            )
            runtime_actions = [item for item in plan["actions"] if item["artifact_type"] == "runtime-file"]
            self.assertTrue(runtime_actions)
            self.assertTrue(
                all(
                    str(root / ".local" / "share" / "ai-agents-skills" / "runtime") in item["path"]
                    for item in runtime_actions
                )
            )
            self.assertFalse(any(".gemini/antigravity-cli" in item["path"] for item in runtime_actions))
            self.assertFalse(any(".codex/runtime" in item["path"] for item in runtime_actions))

            apply_plan(root, plan, dry_run=False)
            self.assertTrue((root / ".local" / "share" / "ai-agents-skills" / "runtime" / "run_skill.sh").is_file())
            self.assertTrue((root / ".gemini" / "antigravity-cli" / "skills" / "graph-verifier.md").is_file())
            self.assertEqual(verify(root)["status"], "ok")

    def test_grok_only_runtime_uses_neutral_shared_root(self) -> None:
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "grok")
            plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                detect_agents(root, ["grok"]),
                platform="linux",
            )
            runtime_actions = [item for item in plan["actions"] if item["artifact_type"] == "runtime-file"]
            self.assertTrue(runtime_actions)
            self.assertTrue(
                all(
                    str(root / ".local" / "share" / "ai-agents-skills" / "runtime") in item["path"]
                    for item in runtime_actions
                )
            )
            self.assertFalse(any(".grok/runtime" in item["path"] for item in runtime_actions))
            self.assertFalse(any(".codex/runtime" in item["path"] for item in runtime_actions))

            apply_plan(root, plan, dry_run=False)
            self.assertTrue((root / ".local" / "share" / "ai-agents-skills" / "runtime" / "run_skill.sh").is_file())
            self.assertTrue((root / ".grok" / "skills" / "graph-verifier" / "SKILL.md").is_file())
            self.assertEqual(verify(root)["status"], "ok")

    def test_vnthuquan_defaults_use_neutral_data_root(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "vnthuquan"
            / "vnthuquan_wrapper.py"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xdg_data = root / "xdg-data"
            with patch.dict(
                os.environ,
                {
                    "HOME": str(root / "home"),
                    "XDG_DATA_HOME": str(xdg_data),
                    "LOCALAPPDATA": str(xdg_data),
                    "VNTHUQUAN_ASSISTANT_HOME": str(root / "runtime"),
                },
                clear=False,
            ):
                for key in ("AAS_DATA_ROOT", "AAS_RUNS_ROOT", "AAS_STATE_ROOT", "VNTHUQUAN_RUN_DIR", "VNTHUQUAN_STATE_DIR"):
                    os.environ.pop(key, None)
                spec = importlib.util.spec_from_file_location("vnthuquan_wrapper_defaults_test", source)
                self.assertIsNotNone(spec)
                self.assertIsNotNone(spec.loader)
                module = importlib.util.module_from_spec(spec)
                previous = sys.dont_write_bytecode
                sys.dont_write_bytecode = True
                try:
                    spec.loader.exec_module(module)
                finally:
                    sys.dont_write_bytecode = previous

            self.assertEqual(module.RUN_DIR, xdg_data / "ai-agents-skills" / "runs" / "vnthuquan")
            self.assertEqual(module.STATE_DIR, xdg_data / "ai-agents-skills" / "state" / "vnthuquan")
            self.assertNotIn(".codex", str(module.RUN_DIR))
            self.assertNotIn(".codex", str(module.STATE_DIR))

            module.CALIBRE_RUNNER = root / "runtime" / "run_skill.bat"
            with (
                patch.object(module.os, "name", "nt"),
                patch.object(module.subprocess, "run") as subprocess_run,
            ):
                result = module.run_calibre(["doctor"])
            self.assertFalse(result["ok"])
            self.assertEqual(result["error_code"], "powershell_runner_unavailable")
            subprocess_run.assert_not_called()

    def test_tikz_direct_mode_defaults_to_neutral_data_root_outside_codex(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "tikz-draw"
            / "tikz_draw.py"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xdg_data = root / "xdg-data"
            with patch.dict(
                os.environ,
                {
                    "HOME": str(root / "home"),
                    "XDG_DATA_HOME": str(xdg_data),
                    "LOCALAPPDATA": str(xdg_data),
                },
                clear=False,
            ):
                for key in ("AAS_DATA_ROOT", "AAS_RUNS_ROOT"):
                    os.environ.pop(key, None)
                sys.path.insert(0, str(source.parent))
                try:
                    spec = importlib.util.spec_from_file_location("tikz_draw_defaults_test", source)
                    self.assertIsNotNone(spec)
                    self.assertIsNotNone(spec.loader)
                    module = importlib.util.module_from_spec(spec)
                    previous = sys.dont_write_bytecode
                    sys.dont_write_bytecode = True
                    try:
                        spec.loader.exec_module(module)
                    finally:
                        sys.dont_write_bytecode = previous
                    run_root = module.default_direct_run_root("run-test")
                finally:
                    sys.path.remove(str(source.parent))

            self.assertEqual(run_root, xdg_data / "ai-agents-skills" / "runs" / "tikz-draw" / "run-test")
            self.assertEqual(module.PLATFORM_NAME, "ai-agents-skills")
            self.assertNotIn(".codex", str(run_root))

    def test_submission_venue_selector_installs_runtime_files_for_supported_agents(self) -> None:
        manifests = load_manifests()
        for agent in ("codex", "claude", "deepseek", "copilot", "opencode", "antigravity", "grok", "kimi"):
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
                            self.assertIn(
                                "workspace/skills/submission-venue-selector/run_submission_venue_selector.ps1",
                                target_relpaths,
                            )
                            self.assertFalse(any(path.endswith((".bat", ".cmd")) for path in target_relpaths))
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

        self.assertEqual(result["source_root"], "<RUNTIME_SOURCE_ROOT>")
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
                    and entry["target"].lower().endswith((".ps1", ".py"))
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
                if "windows" in entry.get("platforms", []) and entry["target"].lower().endswith((".ps1", ".py"))
            ]
            if "run_skill.ps1" not in text or not any(target in text for target in windows_targets):
                missing.append(skill)

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
            runtime_command_target(manifests, "lean-explore-mcp", "windows", "run_skill.ps1"),
            "skills/lean-explore-mcp/run_lean_explore_mcp.ps1",
        )

    def test_installed_runtime_smoke_uses_scratch_workspace(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        # Source-only installer tests do not borrow packages from an ambient
        # system Python. Graph smoke has a separate managed-closure gate below.
        smoke_skills = {"formal-skeleton-helper", "get-available-resources"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            plan = build_plan(
                root,
                manifests,
                sorted(smoke_skills),
                detect_agents(root, ["codex"]),
                platform=platform,
            )
            apply_plan(root, plan, dry_run=False)

            with patch(
                "installer.ai_agents_skills.runtime_smoke.run_smoke_case",
                wraps=run_smoke_case,
            ) as smoke_case:
                result = run_installed_runtime_smoke(
                    root,
                    manifests,
                    skills=smoke_skills,
                    platform=platform,
                    timeout=30,
                )

            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(
                result["schema"],
                "ai-agents-skills.installed-runtime-smoke.v1",
            )
            self.assertEqual(result["schema_version"], 1)
            self.assertEqual(result["unknown_coverage_count"], 0)
            self.assertEqual(result["mode"], "installed")
            if platform == "windows":
                targets = {item["skill"]: item["command_target"] for item in result["results"]}
                self.assertEqual(targets["formal-skeleton-helper"], "skills/formal-skeleton-helper/formal_skeleton_helper.py")
                self.assertEqual(targets["get-available-resources"], "skills/get-available-resources/detect_resources.py")
            installed_runtime = str(root / ".codex" / "runtime")
            self.assertTrue(smoke_case.call_args_list)
            for call in smoke_case.call_args_list:
                runner_path = str(call.kwargs["runner"]["argv"][-1])
                self.assertIn("aas-installed-runtime-smoke-", runner_path)
                self.assertFalse(runner_path.startswith(installed_runtime))
            self.assertFalse((root / ".codex" / "runtime" / "workspace" / "runtime-smoke").exists())

    def test_installed_graph_runtime_smoke_requires_managed_shared_python(self) -> None:
        expected_python = (
            Path.home()
            / ".local"
            / "share"
            / "coding-system"
            / "python-closure"
            / "shared"
            / "bin"
            / "python"
        )
        configured = os.environ.get("AAS_RUNTIME_PYTHON")
        if configured != str(expected_python) or not expected_python.is_file():
            self.skipTest(
                "source-only validation: managed shared Python closure is not installed/selected"
            )
        dependency_probe = subprocess.run(
            [str(expected_python), "-I", "-c", "import networkx"],
            check=False,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(
            dependency_probe.returncode,
            0,
            "selected managed shared Python closure does not provide networkx",
        )

        manifests = load_manifests()
        platform = current_platform(None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                detect_agents(root, ["codex"]),
                platform=platform,
            )
            apply_plan(root, plan, dry_run=False)
            result = run_installed_runtime_smoke(
                root,
                manifests,
                skills={"graph-verifier"},
                platform=platform,
                timeout=30,
            )

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual([item["skill"] for item in result["results"]], ["graph-verifier"])

    def test_installed_runtime_smoke_rejects_null_runtime_root_without_omitting_sibling(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        selected = {"deep-research-workflow", "graph-verifier"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            apply_plan(
                root,
                build_plan(
                    root,
                    manifests,
                    sorted(selected),
                    detect_agents(root, ["codex"]),
                    platform=platform,
                ),
                dry_run=False,
            )
            state = load_state(root)
            malformed = next(
                item
                for item in state["artifacts"]
                if item.get("artifact_type") == "runtime-file"
                and item.get("skill") == "deep-research-workflow"
            )
            malformed["runtime_root"] = None
            save_state(root, state)

            with (
                patch("installer.ai_agents_skills.runtime_smoke.verify") as managed_verify,
                patch("installer.ai_agents_skills.runtime_smoke.run_smoke_case") as smoke_case,
            ):
                result = run_installed_runtime_smoke(
                    root,
                    manifests,
                    skills=selected,
                    platform=platform,
                )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["runtime_state_coverage_status"], "failed")
            self.assertGreater(result["runtime_boundary_violation_count"], 0)
            self.assertIn(
                "runtime-root-missing",
                {item["kind"] for item in result["runtime_boundary_violations"]},
            )
            self.assertEqual({item["skill"] for item in result["results"]}, selected)
            managed_verify.assert_not_called()
            smoke_case.assert_not_called()

    def test_installed_runtime_smoke_recomputes_complete_offline_and_exclusion_closures(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        cases = (
            ("deep-research-workflow", "offline-smoke"),
            ("annotated-review", "manual-native"),
        )
        for skill, coverage in cases:
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                create_agent_home(root, "codex")
                apply_plan(
                    root,
                    build_plan(
                        root,
                        manifests,
                        [skill],
                        detect_agents(root, ["codex"]),
                        platform=platform,
                    ),
                    dry_run=False,
                )
                state = load_state(root)
                removed = next(
                    item
                    for item in state["artifacts"]
                    if item.get("artifact_type") == "runtime-file"
                    and item.get("skill") == skill
                    and str(item.get("target_relpath", "")).endswith(".py")
                )
                Path(removed["artifact"]).unlink()
                state["artifacts"].remove(removed)
                save_state(root, state)

                with (
                    patch("installer.ai_agents_skills.runtime_smoke.verify") as managed_verify,
                    patch("installer.ai_agents_skills.runtime_smoke.run_smoke_case") as smoke_case,
                ):
                    result = run_installed_runtime_smoke(
                        root,
                        manifests,
                        skills={skill},
                        platform=platform,
                    )

                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["runtime_state_missing_count"], 1)
                self.assertEqual(
                    result["runtime_state_missing_records"][0]["target_relpath"],
                    removed["target_relpath"],
                )
                self.assertEqual({item["skill"] for item in result["results"]}, {skill})
                self.assertEqual(
                    manifests["runtime"]["skills"][skill]["smoke_coverage"]["status"],
                    coverage,
                )
                self.assertEqual(result["declared_exclusion_count"], 0)
                managed_verify.assert_not_called()
                smoke_case.assert_not_called()

    def test_installed_runtime_smoke_rejects_forged_runner_hash_before_verify_or_execution(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        runner_name = "run_skill.ps1" if platform == "windows" else "run_skill.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            apply_plan(
                root,
                build_plan(
                    root,
                    manifests,
                    ["graph-verifier"],
                    detect_agents(root, ["codex"]),
                    platform=platform,
                ),
                dry_run=False,
            )
            state = load_state(root)
            runner = next(
                item
                for item in state["artifacts"]
                if item.get("artifact_type") == "runtime-file"
                and item.get("skill") == "runtime-runner"
                and item.get("target_relpath") == runner_name
            )
            runner_path = Path(runner["artifact"])
            runner_path.write_text("forged installed runner\n", encoding="utf-8")
            forged_hash = sha256_file(runner_path)
            runner["source_sha256"] = forged_hash
            runner["canonical_source_sha256"] = forged_hash
            runner["installed_signature"] = artifact_signature(runner_path)
            runner["new_hash"] = forged_hash
            save_state(root, state)

            with (
                patch("installer.ai_agents_skills.runtime_smoke.verify") as managed_verify,
                patch("installer.ai_agents_skills.runtime_smoke.run_smoke_case") as smoke_case,
            ):
                result = run_installed_runtime_smoke(
                    root,
                    manifests,
                    skills={"graph-verifier"},
                    platform=platform,
                )

            self.assertEqual(result["status"], "failed")
            self.assertGreater(result["runtime_state_mismatched_count"], 0)
            runner_mismatches = [
                item
                for item in result["runtime_state_mismatched_records"]
                if item.get("skill") == "runtime-runner"
                and item.get("target_relpath") == runner_name
            ]
            self.assertEqual(len(runner_mismatches), 1)
            self.assertIn("source_sha256", runner_mismatches[0]["fields"])
            managed_verify.assert_not_called()
            smoke_case.assert_not_called()

    def test_installed_runtime_smoke_rejects_extra_and_duplicate_records_before_verify(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        for mutation, count_field in (
            ("extra", "runtime_state_extra_count"),
            ("duplicate", "runtime_state_duplicate_count"),
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                create_agent_home(root, "codex")
                apply_plan(
                    root,
                    build_plan(
                        root,
                        manifests,
                        ["graph-verifier"],
                        detect_agents(root, ["codex"]),
                        platform=platform,
                    ),
                    dry_run=False,
                )
                state = load_state(root)
                record = next(
                    item
                    for item in state["artifacts"]
                    if item.get("artifact_type") == "runtime-file"
                    and item.get("skill") == "graph-verifier"
                )
                forged = copy.deepcopy(record)
                if mutation == "extra":
                    target_relpath = "workspace/skills/graph-verifier/extra.py"
                    forged["target_relpath"] = target_relpath
                    forged["artifact"] = str(Path(forged["runtime_root"]) / target_relpath)
                    forged["artifact_id"] = f"runtime-file:graph-verifier:{target_relpath}"
                    forged["artifact_name"] = target_relpath
                state["artifacts"].append(forged)
                save_state(root, state)

                with (
                    patch("installer.ai_agents_skills.runtime_smoke.verify") as managed_verify,
                    patch("installer.ai_agents_skills.runtime_smoke.run_smoke_case") as smoke_case,
                ):
                    result = run_installed_runtime_smoke(
                        root,
                        manifests,
                        skills={"graph-verifier"},
                        platform=platform,
                    )

                self.assertEqual(result["status"], "failed")
                self.assertEqual(result[count_field], 1)
                managed_verify.assert_not_called()
                smoke_case.assert_not_called()

    def test_installed_runtime_smoke_returns_stable_failure_for_unreadable_state(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        payloads = {
            "malformed-json": "{not-json",
            "unsupported-schema": json.dumps({
                "schema_version": 999,
                "artifacts": [],
                "runs": [],
                "uninstall_records": [],
            }),
        }
        for label, payload in payloads.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                state_path = root / ".ai-agents-skills" / "state.json"
                state_path.parent.mkdir(parents=True)
                state_path.write_text(payload, encoding="utf-8")

                with (
                    patch("installer.ai_agents_skills.runtime_smoke.verify") as managed_verify,
                    patch("installer.ai_agents_skills.runtime_smoke.run_smoke_case") as smoke_case,
                ):
                    result = run_installed_runtime_smoke(
                        root,
                        manifests,
                        skills={"graph-verifier"},
                        platform=platform,
                    )

                self.assertEqual(
                    result["schema"],
                    "ai-agents-skills.installed-runtime-smoke.v1",
                )
                self.assertEqual(result["schema_version"], 1)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["failure_kind"], "invalid-managed-state")
                self.assertEqual(result["reason"], "installer state could not be loaded")
                self.assertEqual(result["managed_state_verify_status"], "not-run-invalid-state")
                self.assertEqual(
                    [(item["skill"], item["failure_kind"]) for item in result["results"]],
                    [("graph-verifier", "invalid-managed-state")],
                )
                managed_verify.assert_not_called()
                smoke_case.assert_not_called()

    def test_installed_runtime_smoke_rejects_mixed_non_runtime_request(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            apply_plan(
                root,
                build_plan(
                    root,
                    manifests,
                    ["graph-verifier"],
                    detect_agents(root, ["codex"]),
                    platform=platform,
                ),
                dry_run=False,
            )

            result = run_installed_runtime_smoke(
                root,
                manifests,
                skills={"graph-verifier", "paper-review"},
                platform=platform,
            )

            self.assertEqual(result["schema"], "ai-agents-skills.installed-runtime-smoke.v1")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(
                {item["skill"] for item in result["results"]},
                {"graph-verifier", "paper-review"},
            )
            paper = next(item for item in result["results"] if item["skill"] == "paper-review")
            self.assertEqual(paper["failure_kind"], "not-runtime-backed")

    def test_installed_runtime_smoke_rejects_malformed_skill_records_at_selected_root(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        cases = ((None, True), ("", True), (7, True), (None, False))
        for malformed_skill, keep_runtime_root in cases:
            with self.subTest(
                skill=malformed_skill,
                keep_runtime_root=keep_runtime_root,
            ), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                create_agent_home(root, "codex")
                apply_plan(
                    root,
                    build_plan(
                        root,
                        manifests,
                        ["graph-verifier"],
                        detect_agents(root, ["codex"]),
                        platform=platform,
                    ),
                    dry_run=False,
                )
                state = load_state(root)
                malformed = copy.deepcopy(next(
                    item
                    for item in state["artifacts"]
                    if item.get("artifact_type") == "runtime-file"
                    and item.get("skill") == "graph-verifier"
                ))
                malformed["skill"] = malformed_skill
                if not keep_runtime_root:
                    malformed["runtime_root"] = None
                state["artifacts"].append(malformed)
                save_state(root, state)

                with patch("installer.ai_agents_skills.runtime_smoke.verify") as managed_verify:
                    result = run_installed_runtime_smoke(
                        root,
                        manifests,
                        platform=platform,
                    )

                self.assertEqual(result["schema"], "ai-agents-skills.installed-runtime-smoke.v1")
                self.assertEqual(result["status"], "failed")
                self.assertGreater(result["runtime_state_mismatched_count"], 0)
                self.assertIn(
                    "runtime-record-identity-invalid",
                    {item["kind"] for item in result["runtime_state_mismatched_records"]},
                )
                managed_verify.assert_not_called()

    def test_installed_runtime_smoke_preflight_faults_preserve_report_schema(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        for patched_name in ("runtime_expected_sha256", "resolved_path_within"):
            with self.subTest(patched=patched_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                create_agent_home(root, "codex")
                apply_plan(
                    root,
                    build_plan(
                        root,
                        manifests,
                        ["graph-verifier"],
                        detect_agents(root, ["codex"]),
                        platform=platform,
                    ),
                    dry_run=False,
                )

                with (
                    patch(
                        f"installer.ai_agents_skills.runtime_smoke.{patched_name}",
                        side_effect=PermissionError("test-only preflight denial"),
                    ),
                    patch("installer.ai_agents_skills.runtime_smoke.verify") as managed_verify,
                ):
                    result = run_installed_runtime_smoke(
                        root,
                        manifests,
                        skills={"graph-verifier"},
                        platform=platform,
                    )

                self.assertEqual(result["schema"], "ai-agents-skills.installed-runtime-smoke.v1")
                self.assertEqual(result["schema_version"], 1)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["runtime_state_coverage_status"], "failed")
                managed_verify.assert_not_called()

    def test_installed_runtime_smoke_timeout_bytes_preserve_report_schema(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            apply_plan(
                root,
                build_plan(
                    root,
                    manifests,
                    ["graph-verifier"],
                    detect_agents(root, ["codex"]),
                    platform=platform,
                ),
                dry_run=False,
            )
            timeout_error = subprocess.TimeoutExpired(
                cmd=["runtime-smoke"],
                timeout=1,
                output=b"partial-stdout-\xff",
                stderr=b"partial-stderr-\xfe",
            )

            with patch(
                "installer.ai_agents_skills.runtime_smoke.run_smoke_process",
                side_effect=timeout_error,
            ):
                result = run_installed_runtime_smoke(
                    root,
                    manifests,
                    skills={"graph-verifier"},
                    platform=platform,
                )

            self.assertEqual(result["schema"], "ai-agents-skills.installed-runtime-smoke.v1")
            self.assertEqual(result["status"], "failed")
            self.assertIsInstance(result["results"][0]["stdout_tail"], str)
            self.assertIsInstance(result["results"][0]["stderr_tail"], str)
            json.dumps(result)

    def test_installed_runtime_smoke_deep_research_fallback_parse_error_preserves_schema(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        runner_name = "run_skill.ps1" if platform == "windows" else "run_skill.sh"
        process_results = [
            subprocess.CompletedProcess(
                args=["runtime-smoke", "selftest"],
                returncode=2,
                stdout="",
                stderr="invalid choice: 'selftest'",
            ),
            subprocess.CompletedProcess(
                args=["runtime-smoke", "init"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=["runtime-smoke", "validate"],
                returncode=0,
                stdout="not-json",
                stderr="",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            apply_plan(
                root,
                build_plan(
                    root,
                    manifests,
                    ["deep-research-workflow"],
                    detect_agents(root, ["codex"]),
                    platform=platform,
                ),
                dry_run=False,
            )
            with (
                patch(
                    "installer.ai_agents_skills.runtime_smoke.runner_invocations",
                    return_value=[{"name": runner_name, "argv": ["fake-runner"]}],
                ),
                patch(
                    "installer.ai_agents_skills.runtime_smoke.run_smoke_process",
                    side_effect=process_results,
                ),
            ):
                result = run_installed_runtime_smoke(
                    root,
                    manifests,
                    skills={"deep-research-workflow"},
                    platform=platform,
                )

        self.assertEqual(result["schema"], "ai-agents-skills.installed-runtime-smoke.v1")
        self.assertEqual(result["status"], "failed")
        self.assertTrue(
            any(
                check["name"] == "output-validation" and not check["ok"]
                for check in result["results"][0]["checks"]
            )
        )
        json.dumps(result)

    def test_installed_runtime_smoke_filter_excludes_unrelated_scratch_files(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            apply_plan(
                root,
                build_plan(
                    root,
                    manifests,
                    ["digest-bridge", "graph-verifier"],
                    detect_agents(root, ["codex"]),
                    platform=platform,
                ),
                dry_run=False,
            )
            state = load_state(root)
            unrelated = next(
                item
                for item in state["artifacts"]
                if item.get("artifact_type") == "runtime-file"
                and item.get("skill") == "digest-bridge"
            )
            Path(unrelated["artifact"]).write_text("tampered unrelated file\n", encoding="utf-8")

            def assert_filtered_scratch(*args: Any, **kwargs: Any) -> dict[str, Any]:
                relative = str(unrelated["target_relpath"]).removeprefix("workspace/")
                self.assertFalse((kwargs["workspace"] / relative).exists())
                return {
                    "status": "ok",
                    "mode": "installed",
                    "runtime_root": str(root / ".codex" / "runtime"),
                    "skill": "graph-verifier",
                    "runner": "run_skill.sh",
                }

            with patch(
                "installer.ai_agents_skills.runtime_smoke.run_smoke_case",
                side_effect=assert_filtered_scratch,
            ) as smoke_case:
                result = run_installed_runtime_smoke(
                    root,
                    manifests,
                    skills={"graph-verifier"},
                    platform=platform,
                )

            self.assertEqual(result["status"], "ok", result)
            self.assertEqual({item["skill"] for item in result["results"]}, {"graph-verifier"})
            self.assertEqual(smoke_case.call_count, 1)

    def test_installed_runtime_smoke_refuses_tampered_code_before_execution(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            apply_plan(
                root,
                build_plan(
                    root,
                    manifests,
                    ["graph-verifier"],
                    detect_agents(root, ["codex"]),
                    platform=platform,
                ),
                dry_run=False,
            )
            state = load_state(root)
            graph_artifact = next(
                item
                for item in state["artifacts"]
                if item.get("artifact_type") == "runtime-file"
                and item.get("skill") == "graph-verifier"
            )
            Path(graph_artifact["artifact"]).write_text("tampered\n", encoding="utf-8")

            with patch("installer.ai_agents_skills.runtime_smoke.run_smoke_case") as smoke_case:
                result = run_installed_runtime_smoke(
                    root,
                    manifests,
                    skills={"graph-verifier"},
                    platform=platform,
                )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["checked"], 0)
            self.assertEqual(result["managed_state_verify_status"], "failed")
            self.assertIn("integrity verification failed", result["reason"])
            smoke_case.assert_not_called()

    @unittest.skipIf(os.name == "nt", "POSIX executable-mode regression")
    def test_installed_runtime_smoke_rejects_non_executable_scoped_runner(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            apply_plan(
                root,
                build_plan(
                    root,
                    manifests,
                    ["graph-verifier"],
                    detect_agents(root, ["codex"]),
                    platform=platform,
                ),
                dry_run=False,
            )
            runner = root / ".codex" / "runtime" / "run_skill.sh"
            runner.chmod(0o644)

            with patch("installer.ai_agents_skills.runtime_smoke.run_smoke_case") as smoke_case:
                result = run_installed_runtime_smoke(
                    root,
                    manifests,
                    skills={"graph-verifier"},
                    platform=platform,
                )

            self.assertEqual(result["schema"], "ai-agents-skills.installed-runtime-smoke.v1")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["managed_state_verify_status"], "failed")
            self.assertIn("integrity verification failed", result["reason"])
            smoke_case.assert_not_called()

    def test_installed_runtime_smoke_descriptor_copy_rechecks_source_hash(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            apply_plan(
                root,
                build_plan(
                    root,
                    manifests,
                    ["graph-verifier"],
                    detect_agents(root, ["codex"]),
                    platform=platform,
                ),
                dry_run=False,
            )
            state = load_state(root)
            graph_artifact = next(
                item
                for item in state["artifacts"]
                if item.get("artifact_type") == "runtime-file"
                and item.get("skill") == "graph-verifier"
            )
            Path(graph_artifact["artifact"]).write_text("tampered-after-verify\n", encoding="utf-8")

            with (
                patch(
                    "installer.ai_agents_skills.runtime_smoke.verify",
                    return_value={"status": "ok", "checked": 1, "results": []},
                ),
                patch(
                    "installer.ai_agents_skills.runtime_smoke.verify_artifact",
                    return_value={"status": "ok", "checks": []},
                ),
                patch("installer.ai_agents_skills.runtime_smoke.run_smoke_case") as smoke_case,
            ):
                result = run_installed_runtime_smoke(
                    root,
                    manifests,
                    skills={"graph-verifier"},
                    platform=platform,
                )

            self.assertEqual(result["status"], "failed")
            smoke_case.assert_not_called()
            copy_checks = [
                check
                for item in result["results"]
                for check in item.get("checks", [])
            ]
            self.assertTrue(any(check["name"].endswith(":source-hash") and not check["ok"] for check in copy_checks))

    @unittest.skipIf(os.name == "nt", "POSIX descriptor-mode regression")
    def test_installed_runtime_smoke_descriptor_copy_rechecks_source_mode(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            apply_plan(
                root,
                build_plan(
                    root,
                    manifests,
                    ["graph-verifier"],
                    detect_agents(root, ["codex"]),
                    platform=platform,
                ),
                dry_run=False,
            )
            runner = root / ".codex" / "runtime" / "run_skill.sh"
            runner.chmod(0o644)

            with (
                patch(
                    "installer.ai_agents_skills.runtime_smoke.verify",
                    return_value={"status": "ok", "checked": 1, "results": []},
                ),
                patch(
                    "installer.ai_agents_skills.runtime_smoke.verify_artifact",
                    return_value={"status": "ok", "checks": []},
                ),
                patch("installer.ai_agents_skills.runtime_smoke.run_smoke_case") as smoke_case,
            ):
                result = run_installed_runtime_smoke(
                    root,
                    manifests,
                    skills={"graph-verifier"},
                    platform=platform,
                )

            self.assertEqual(result["status"], "failed")
            smoke_case.assert_not_called()
            copy_checks = [
                check
                for item in result["results"]
                for check in item.get("checks", [])
            ]
            self.assertTrue(
                any(
                    check["name"].endswith(":descriptor-copy") and not check["ok"]
                    for check in copy_checks
                )
            )

    def test_installed_runtime_smoke_complete_coverage_rejects_omitted_runtime_skill(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            apply_plan(
                root,
                build_plan(
                    root,
                    manifests,
                    ["formal-skeleton-helper"],
                    detect_agents(root, ["codex"]),
                    platform=platform,
                ),
                dry_run=False,
            )
            with patch(
                "installer.ai_agents_skills.runtime_smoke.run_smoke_case",
                return_value={"status": "ok", "skill": "formal-skeleton-helper"},
            ):
                result = run_installed_runtime_smoke(
                    root,
                    manifests,
                    platform=platform,
                    require_complete_coverage=True,
                )

            self.assertEqual(result["status"], "failed")
            self.assertGreater(result["missing_managed_runtime_count"], 0)
            self.assertIn("graph-verifier", result["missing_managed_runtime_skills"])
            missing = {
                item["skill"]
                for item in result["results"]
                if item.get("failure_kind") == "missing-managed-runtime"
            }
            self.assertIn("graph-verifier", missing)

    def test_installed_runtime_smoke_rejects_state_paths_outside_selected_root(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            old_root = base / "old-root"
            new_root = base / "new-root"
            create_agent_home(old_root, "codex")
            apply_plan(
                old_root,
                build_plan(
                    old_root,
                    manifests,
                    ["graph-verifier"],
                    detect_agents(old_root, ["codex"]),
                    platform=platform,
                ),
                dry_run=False,
            )
            (new_root / ".ai-agents-skills").mkdir(parents=True)
            shutil.copy2(
                old_root / ".ai-agents-skills" / "state.json",
                new_root / ".ai-agents-skills" / "state.json",
            )

            with patch("installer.ai_agents_skills.runtime_smoke.run_smoke_case") as smoke_case:
                result = run_installed_runtime_smoke(
                    new_root,
                    manifests,
                    skills={"graph-verifier"},
                    platform=platform,
                )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["checked"], 0)
            self.assertGreater(result["runtime_boundary_violation_count"], 0)
            self.assertIn("escapes the selected root", result["reason"])
            smoke_case.assert_not_called()

    def test_installed_runtime_smoke_launch_error_preserves_report_schema(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            apply_plan(
                root,
                build_plan(
                    root,
                    manifests,
                    ["graph-verifier"],
                    detect_agents(root, ["codex"]),
                    platform=platform,
                ),
                dry_run=False,
            )
            with patch(
                "installer.ai_agents_skills.runtime_smoke.run_smoke_process",
                side_effect=PermissionError("test-only launch denial"),
            ):
                result = run_installed_runtime_smoke(
                    root,
                    manifests,
                    skills={"graph-verifier"},
                    platform=platform,
                )

            self.assertEqual(result["schema"], "ai-agents-skills.installed-runtime-smoke.v1")
            self.assertEqual(result["schema_version"], 1)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["results"][0]["failure_kind"], "launch-error")

    def test_installed_runtime_smoke_runs_all_offline_contracts_and_reports_declared_exclusions(self) -> None:
        manifests = load_manifests()
        platform = current_platform(None)
        offline_skills = set(runtime_smoke_skill_names(manifests))
        declared_exclusion_statuses = {"manual-native", "doctor-only", "static-only"}
        excluded_skills = {
            skill
            for skill, spec in manifests["runtime"]["skills"].items()
            if spec["smoke_coverage"]["status"] in declared_exclusion_statuses
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_agent_home(root, "codex")
            apply_plan(
                root,
                build_plan(
                    root,
                    manifests,
                    [],
                    detect_agents(root, ["codex"]),
                    runtime_profile="full",
                    platform=platform,
                    requested_agents=["codex"],
                ),
                dry_run=False,
            )

            def successful_smoke(*args: Any, **kwargs: Any) -> dict[str, Any]:
                return {
                    "status": "ok",
                    "mode": kwargs["mode"],
                    "runtime_root": str(kwargs["runtime_root"]),
                    "skill": kwargs["skill"],
                    "runner": kwargs["runner"]["name"],
                }

            with patch(
                "installer.ai_agents_skills.runtime_smoke.run_smoke_case",
                side_effect=successful_smoke,
            ) as smoke_case:
                result = run_installed_runtime_smoke(
                    root,
                    manifests,
                    platform=platform,
                )

            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(
                result["schema"],
                "ai-agents-skills.installed-runtime-smoke.v1",
            )
            self.assertEqual(result["schema_version"], 1)
            self.assertEqual(result["unknown_coverage_count"], 0)
            self.assertEqual(
                {call.kwargs["skill"] for call in smoke_case.call_args_list},
                offline_skills,
            )
            self.assertEqual(
                {item["skill"] for item in result["declared_exclusions"]},
                excluded_skills,
            )
            self.assertEqual(result["declared_exclusion_count"], len(excluded_skills))
            self.assertEqual(
                {item["coverage"] for item in result["declared_exclusions"]},
                declared_exclusion_statuses,
            )

            unknown_manifests = copy.deepcopy(manifests)
            unknown_skill = sorted(excluded_skills)[0]
            unknown_manifests["runtime"]["skills"][unknown_skill]["smoke_coverage"] = {
                "status": "future-unknown",
                "reason": "test-only unknown coverage class",
            }
            with patch(
                "installer.ai_agents_skills.runtime_smoke.run_smoke_case",
                side_effect=successful_smoke,
            ):
                unknown_result = run_installed_runtime_smoke(
                    root,
                    unknown_manifests,
                    platform=platform,
                )

            self.assertEqual(unknown_result["status"], "failed")
            self.assertEqual(unknown_result["unknown_coverage_count"], 1)
            unknown_rows = [
                item for item in unknown_result["results"]
                if item["skill"] == unknown_skill
            ]
            self.assertEqual(len(unknown_rows), 1)
            self.assertEqual(unknown_rows[0]["status"], "failed")
            self.assertEqual(unknown_rows[0]["failure_kind"], "unknown-coverage")
            self.assertIn("unknown smoke coverage", unknown_rows[0]["reason"])

            with patch(
                "installer.ai_agents_skills.runtime_smoke.runner_invocations",
                return_value=[],
            ):
                no_runner_result = run_installed_runtime_smoke(
                    root,
                    manifests,
                    platform=platform,
                )

            self.assertEqual(no_runner_result["status"], "failed")
            self.assertEqual(no_runner_result["unknown_coverage_count"], 0)
            no_runner_failures = {
                item["skill"]
                for item in no_runner_result["results"]
                if item["status"] == "failed"
            }
            self.assertEqual(no_runner_failures, offline_skills)
            self.assertEqual(
                {item["skill"] for item in no_runner_result["declared_exclusions"]},
                excluded_skills,
            )

    def test_installed_runtime_smoke_schema_covers_skipped_reports(self) -> None:
        manifests = load_manifests()
        host_platform = current_platform(None)
        other_platform = "windows" if host_platform != "windows" else "linux"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = [
                run_installed_runtime_smoke(root, manifests),
                run_installed_runtime_smoke(
                    root,
                    manifests,
                    platform=other_platform,
                ),
            ]

        for report in reports:
            self.assertEqual(report["status"], "skipped")
            self.assertEqual(
                report["schema"],
                "ai-agents-skills.installed-runtime-smoke.v1",
            )
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["unknown_coverage_count"], 0)

    def test_installed_runtime_smoke_is_a_public_cli_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stream = io.StringIO()
            expected = {
                "schema": "ai-agents-skills.installed-runtime-smoke.v1",
                "schema_version": 1,
                "status": "ok",
                "mode": "installed",
                "platform": current_platform(None),
                "checked": 0,
                "results": [],
            }
            with (
                contextlib.redirect_stdout(stream),
                patch(
                    "installer.ai_agents_skills.cli.run_installed_runtime_smoke",
                    return_value=expected,
                ) as installed_smoke,
            ):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "installed-runtime-smoke",
                    "--skills",
                    "graph-verifier",
                ])

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stream.getvalue()), expected)
            self.assertEqual(installed_smoke.call_args.args[0], root)
            self.assertEqual(installed_smoke.call_args.kwargs["skills"], {"graph-verifier"})

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
                    self.assertTrue(command_payload["command"].endswith("run_lean_explore_mcp.sh"))
                    self.assertEqual(command_payload["args"], ["serve", "--backend", "api"])
                    self.assertEqual(command_payload["env"]["LEANEXPLORE_API_KEY"], "<LEANEXPLORE_API_KEY>")
                    self.assertEqual(
                        command_payload["env"]["AAS_LEANEXPLORE_SITE_PACKAGES"],
                        "<ABSOLUTE_LEANEXPLORE_1_2_1_SITE_PACKAGES>",
                    )
                if command == ("config-snippet", "--backend", "local"):
                    command_payload = payload["local_stdio_mcp_config"]["mcpServers"]["lean-explore"]
                    self.assertEqual(command_payload["args"], ["serve", "--backend", "local"])
                    self.assertNotIn("LEANEXPLORE_API_KEY", command_payload["env"])

            self.assertFalse(marker.exists())

    @unittest.skipUnless(
        os.name == "posix" and Path("/proc/self/cmdline").is_file(),
        "requires POSIX /proc process-argument inspection",
    )
    def test_lean_explore_posix_wrapper_keeps_key_out_of_process_argv(self) -> None:
        source_dir = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "lean-explore-mcp"
        )
        canary = "LEANEXPLORE-PROCESS-ARGV-CANARY"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "runtime" / "workspace" / "skills" / "lean-explore-mcp"
            skill_dir.mkdir(parents=True)
            wrapper = skill_dir / "run_lean_explore_mcp.sh"
            helper = skill_dir / "lean_explore_mcp.py"
            shutil.copy2(source_dir / wrapper.name, wrapper)
            # An ephemeral copy can never be root-owned, so patch the flag the
            # wrapper documents for exactly this case.  The argv and environment
            # claims under test do not depend on the generation gate.
            wrapper_text = wrapper.read_text(encoding="utf-8")
            self.assertIn("lean_explore_exact_generation_enforcement=1", wrapper_text)
            wrapper.write_text(
                wrapper_text.replace(
                    "lean_explore_exact_generation_enforcement=1",
                    "lean_explore_exact_generation_enforcement=0",
                    1,
                ),
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            marker = root / "child-ready"
            capture_marker = root / "key-captured"
            leaf_helper = root / "lean-explore-leaf.py"
            leaf_helper.write_text(
                "import time\ntime.sleep(10)\n",
                encoding="utf-8",
            )
            bridge_helper = root / "lean-explore-bridge.py"
            bridge_helper.write_text(
                "import os, subprocess\n"
                "from pathlib import Path\n"
                f"leaf = subprocess.Popen(['/usr/bin/python3', {str(leaf_helper)!r}])\n"
                f"Path({str(marker)!r}).write_text("
                "str(os.getpid()) + '|' + str(leaf.pid) + '|' + "
                "('present' if os.environ.get('LEANEXPLORE_API_KEY') else 'missing'), "
                "encoding='utf-8')\n"
                "leaf.wait()\n",
                encoding="utf-8",
            )
            helper.write_text(
                "import os, subprocess\n"
                "fd = int(os.environ.pop('AAS_LEANEXPLORE_KEY_FD'))\n"
                "key = os.read(fd, 4098).rstrip(b'\\n')\n"
                "os.close(fd)\n"
                f"open({str(capture_marker)!r}, 'wb').write(b'present' if key else b'missing')\n"
                "key = b''\n"
                f"bridge = subprocess.Popen(['/usr/bin/python3', {str(bridge_helper)!r}])\n"
                "bridge.wait()\n",
                encoding="utf-8",
            )
            helper.chmod(0o600)
            env = {
                "HOME": str(root),
                "PATH": "/usr/bin:/bin",
                "AAS_RUNTIME_PYTHON": "/usr/bin/python3",
                "LEANEXPLORE_API_KEY": canary,
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            process = subprocess.Popen(
                ["/bin/bash", str(wrapper), "doctor"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            descendant_pids: list[int] = []
            try:
                for _ in range(250):
                    if marker.is_file() or process.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(marker.is_file(), "LeanExplore child did not reach its ready state")
                self.assertEqual(capture_marker.read_text(encoding="utf-8"), "present")
                bridge_text, leaf_text, key_status = marker.read_text(
                    encoding="utf-8"
                ).split("|", 2)
                descendant_pids = [int(bridge_text), int(leaf_text)]
                self.assertEqual(key_status, "missing")
                for pid in [process.pid, *descendant_pids]:
                    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
                    self.assertFalse(
                        canary.encode() in cmdline,
                        "LeanExplore credential appeared in descendant process arguments",
                    )
                    self.assertFalse(
                        b"--api-key" in cmdline,
                        "LeanExplore descendant used the forbidden API-key argv flag",
                    )
            finally:
                if process.poll() is None:
                    process.terminate()
                for pid in reversed(descendant_pids):
                    try:
                        os.kill(pid, 15)
                    except ProcessLookupError:
                        pass
                process.communicate(timeout=10)

    @unittest.skipUnless(os.name == "nt", "native Windows process-argument inspection")
    def test_lean_explore_windows_wrapper_keeps_key_out_of_process_argv(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        if importlib.util.find_spec("psutil") is None:
            self.skipTest("psutil is required for native Windows argv inspection")
        import psutil  # type: ignore[import-not-found]

        source_dir = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "lean-explore-mcp"
        )
        canary = "LEANEXPLORE-WINDOWS-ARGV-CANARY"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            skill_dir = runtime / "workspace" / "skills" / "lean-explore-mcp"
            skill_dir.mkdir(parents=True)
            wrapper = skill_dir / "run_lean_explore_mcp.ps1"
            helper = skill_dir / "lean_explore_mcp.py"
            shutil.copy2(source_dir / wrapper.name, wrapper)
            marker = root / "child-ready"
            helper.write_text(
                "import os, time\n"
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text("
                "str(os.getpid()) + '|' + "
                "('present' if os.environ.get('LEANEXPLORE_API_KEY') else 'missing'), "
                "encoding='utf-8')\n"
                "time.sleep(10)\n",
                encoding="utf-8",
            )
            (runtime / "run_python.ps1").write_text(
                "param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)\n"
                "& $env:TEST_AAS_PYTHON $env:AAS_RUNTIME_SCRIPT @Args\n"
                "exit $LASTEXITCODE\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "AAS_RUNTIME_ROOT": str(runtime),
                    "LEANEXPLORE_API_KEY": canary,
                    "TEST_AAS_PYTHON": sys.executable,
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
            )
            process = subprocess.Popen(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(wrapper),
                    "doctor",
                ],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            child_pid: int | None = None
            try:
                for _ in range(500):
                    if marker.is_file() or process.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(marker.is_file(), "LeanExplore Windows child did not become ready")
                pid_text, key_status = marker.read_text(encoding="utf-8").split("|", 1)
                child_pid = int(pid_text)
                self.assertEqual(key_status, "present")
                child_argv = "\0".join(psutil.Process(child_pid).cmdline())
                self.assertFalse(
                    canary in child_argv,
                    "LeanExplore credential appeared in Windows child process arguments",
                )
                self.assertFalse(
                    "--api-key" in child_argv,
                    "LeanExplore Windows child used the forbidden API-key argv flag",
                )
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.communicate(timeout=12)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
                if child_pid is not None:
                    try:
                        child = psutil.Process(child_pid)
                        if child.is_running():
                            child.terminate()
                            child.wait(timeout=5)
                    except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                        pass

    def test_lean_explore_wrappers_scrub_key_before_discovery_and_never_build_key_argv(self) -> None:
        source_dir = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "lean-explore-mcp"
        )
        posix = (source_dir / "run_lean_explore_mcp.sh").read_text(encoding="utf-8")
        capture = 'lean_explore_api_key="${LEANEXPLORE_API_KEY:-}"'
        scrub = "unset LEANEXPLORE_API_KEY"
        discovery = 'script_path="${BASH_SOURCE[0]:-$0}"'
        self.assertLess(posix.index(capture), posix.index(scrub))
        self.assertLess(posix.index(scrub), posix.index(discovery))
        self.assertNotIn('"--api-key",', posix)

        helper = (source_dir / "lean_explore_mcp.py").read_text(encoding="utf-8")
        self.assertLess(
            helper.index('os.environ.pop("LEANEXPLORE_API_KEY", None)'),
            helper.index("def tool_status"),
        )
        self.assertEqual(helper.count('"--api-key"'), 0)
        self.assertNotIn("import subprocess", helper)
        self.assertIn('SUPPORTED_LEAN_EXPLORE_VERSION = "1.2.1"', helper)
        self.assertIn('mcp_app.run(transport="stdio")', helper)

        windows = (source_dir / "run_lean_explore_mcp.ps1").read_text(encoding="utf-8")
        self.assertLess(
            windows.index('$leanExploreApiKey = [Environment]::GetEnvironmentVariable('),
            windows.index('$script = Join-Path $PSScriptRoot "lean_explore_mcp.py"'),
        )
        self.assertIn('if ($SkillArgs.Count -gt 0 -and $SkillArgs[0] -ieq "serve")', windows)
        self.assertLess(
            windows.index(
                '[Environment]::SetEnvironmentVariable(\n'
                '    "LEANEXPLORE_API_KEY",\n'
                '    $null,'
            ),
            windows.index('$script = Join-Path $PSScriptRoot "lean_explore_mcp.py"'),
        )

        python_runner = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "runners"
            / "run_python.ps1"
        ).read_text(encoding="utf-8")
        self.assertLess(
            python_runner.index('$leanExploreApiKey = [Environment]::GetEnvironmentVariable('),
            python_runner.index("function Resolve-ExplicitPython"),
        )
        self.assertIn("$leanExploreHelper", python_runner)
        self.assertNotIn('"--api-key"', python_runner)

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

    @unittest.skipUnless(os.name == "posix", "LeanExplore private-FD adapter is POSIX-only")
    def test_lean_explore_121_adapter_performs_real_offline_mcp_handshake(self) -> None:
        helper = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "skills"
            / "lean-explore-mcp"
            / "lean_explore_mcp.py"
        )
        candidates: list[Path] = []
        configured = os.environ.get("AAS_LEANEXPLORE_TEST_SITE_PACKAGES", "")
        if configured:
            candidates.append(Path(configured))
        closure_root = (
            Path.home()
            / ".local/share/coding-system/python-closure/lean-explore"
        )
        candidates.extend(sorted((closure_root / "lib").glob("python*/site-packages")))
        site_packages = next(
            (
                candidate
                for candidate in candidates
                if candidate.is_dir()
                and any(candidate.glob("lean_explore-1.2.1.dist-info"))
            ),
            None,
        )
        if site_packages is None:
            self.skipTest("an exact lean-explore 1.2.1 site-packages closure is unavailable")

        closure_fd = os.open(closure_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        key_read, key_write = os.pipe()
        canary = b"LEANEXPLORE-OFFLINE-HANDSHAKE-CANARY"
        os.write(key_write, canary)
        os.close(key_write)
        env = {
            "HOME": str(Path.home()),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "AAS_LEANEXPLORE_CLOSURE_FD": str(closure_fd),
            "AAS_LEANEXPLORE_SITE_RELATIVE": str(site_packages.relative_to(closure_root)),
            "AAS_LEANEXPLORE_KEY_FD": str(key_read),
        }
        process = subprocess.Popen(
            ["/usr/bin/python3", "-I", str(helper), "serve", "--backend", "api"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            pass_fds=(closure_fd, key_read),
            bufsize=1,
        )
        os.close(closure_fd)
        os.close(key_read)
        try:
            if Path(f"/proc/{process.pid}/cmdline").is_file():
                self.assertNotIn(canary, Path(f"/proc/{process.pid}/cmdline").read_bytes())
                self.assertNotIn(canary, Path(f"/proc/{process.pid}/environ").read_bytes())
            requests = (
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "aas-offline-test", "version": "1"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            assert process.stdin is not None
            for request in requests:
                process.stdin.write(json.dumps(request) + "\n")
                process.stdin.flush()
            responses: dict[int, dict[str, Any]] = {}
            deadline = time.monotonic() + 15
            assert process.stdout is not None
            while time.monotonic() < deadline and len(responses) < 2:
                ready, _, _ = select.select([process.stdout], [], [], 0.25)
                if not ready:
                    continue
                line = process.stdout.readline()
                if not line:
                    break
                response = json.loads(line)
                if isinstance(response.get("id"), int):
                    responses[response["id"]] = response
            self.assertEqual(responses[1]["result"]["protocolVersion"], "2025-03-26")
            tool_names = {tool["name"] for tool in responses[2]["result"]["tools"]}
            self.assertTrue({"search", "search_summary", "get_source_code"}.issubset(tool_names))
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)

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
            self.assertIn("run_skill.ps1", target_relpaths)
            self.assertIn("run_python.ps1", target_relpaths)
            self.assertIn("workspace/skills/zotero/zot.py", target_relpaths)
            self.assertIn("workspace/skills/getscipapers_requester/gsp_openclaw_helper.py", target_relpaths)
            self.assertFalse(any(path.endswith((".bat", ".cmd")) for path in target_relpaths))

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

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell runner test")
    def test_getscipapers_windows_runner_uses_installed_runtime_workspace(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
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
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(runtime_root / "run_skill.ps1"),
                    "skills/getscipapers_requester/gsp_openclaw_helper.py",
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

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell wrapper test")
    def test_docling_windows_wrapper_forwards_more_than_nine_args(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "runtime"
            docling_dir = runtime_root / "workspace" / "skills" / "docling"
            docling_dir.mkdir(parents=True)
            source_dir = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "skills" / "docling"
            shutil.copy2(source_dir / "run_docling.ps1", docling_dir / "run_docling.ps1")
            (docling_dir / "docling_convert.py").write_text(
                "import json, sys\nprint(json.dumps(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            args = [f"arg{i}" for i in range(12)]
            env = os.environ.copy()
            env["AAS_RUNTIME_PYTHON"] = sys.executable

            completed = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(docling_dir / "run_docling.ps1"), "convert", *args],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout), args)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell wrapper test")
    def test_docling_windows_wrapper_uses_aas_runtime_python(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "runtime"
            docling_dir = runtime_root / "workspace" / "skills" / "docling"
            docling_dir.mkdir(parents=True)
            source_dir = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "skills" / "docling"
            shutil.copy2(source_dir / "run_docling.ps1", docling_dir / "run_docling.ps1")
            (docling_dir / "doctor.py").write_text(
                "import json, sys\nprint(json.dumps({'executable': sys.executable, 'args': sys.argv[1:]}))\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AAS_RUNTIME_PYTHON"] = sys.executable
            env.pop("DOCLING_PYTHON", None)

            completed = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(docling_dir / "run_docling.ps1"), "doctor", "arg1", "arg2"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(Path(payload["executable"]).resolve(), Path(sys.executable).resolve())
            self.assertEqual(payload["args"], ["arg1", "arg2"])

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

            with patch("installer.ai_agents_skills.openclaw_target_gate.looks_like_real_system_root", return_value=True):
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
    def test_bash_runtime_runner_scrubs_legacy_broad_secrets_selectors(self) -> None:
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

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "|\n")

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell runner test")
    def test_powershell_runtime_runner_ignores_external_workspace_without_opt_in(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")

        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            runtime_root = temp / "runtime"
            workspace_script = runtime_root / "workspace" / "skills" / "demo" / "run.ps1"
            workspace_script.parent.mkdir(parents=True)
            workspace_script.write_text("Write-Output 'managed-workspace'\n", encoding="utf-8")
            runner = runtime_root / "run_skill.ps1"
            shutil.copy2(Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_skill.ps1", runner)

            external_script = temp / "external" / "skills" / "demo" / "run.ps1"
            external_script.parent.mkdir(parents=True)
            marker = temp / "external-ran"
            external_script.write_text(
                f"Write-Output 'external-workspace'\nSet-Content -LiteralPath '{marker}' -Value 'ran'\n",
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
                    "skills/demo/run.ps1",
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

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell wrapper test")
    def test_rss_summary_wrapper_propagates_digest_failure(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "runtime"
            workspace = runtime_root / "workspace"
            rss_dir = workspace / "skills" / "rss-news-digest"
            rss_dir.mkdir(parents=True)
            shutil.copy2(
                Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "skills" / "rss-news-digest" / "run_and_summarize.ps1",
                rss_dir / "run_and_summarize.ps1",
            )
            (runtime_root / "run_python.ps1").write_text("exit 7\n", encoding="utf-8")
            (rss_dir / "rss_news_digest.py").write_text("raise SystemExit(99)\n", encoding="utf-8")
            env = os.environ.copy()
            env["AAS_RUNTIME_ROOT"] = str(runtime_root)
            env["AAS_RUNTIME_WORKSPACE"] = str(workspace)

            completed = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(rss_dir / "run_and_summarize.ps1")],
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
        runner = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_python.ps1"
        text = runner.read_text(encoding="utf-8")

        candidates = text.index('@("python.exe", "python", "py")')
        self.assertGreaterEqual(candidates, 0)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell runner test")
    def test_windows_python_runner_uses_only_first_duplicate_path_command(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        runner = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_python.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            child = root / "child.py"
            child.write_text("print('unused')\n", encoding="utf-8")
            first_log = root / "first.log"
            second_log = root / "second.log"
            for directory, log_path in (
                (first_dir, first_log),
                (second_dir, second_log),
            ):
                escaped_log = str(log_path).replace("'", "''")
                (directory / "python.ps1").write_text(
                    "param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args = @())\n"
                    f"Add-Content -LiteralPath '{escaped_log}' -Value ($Args -join '|')\n"
                    "if ($Args.Count -ge 2 -and $Args[0] -eq '-c') { Write-Output '3.11'; exit 0 }\n"
                    "exit 0\n",
                    encoding="utf-8",
                )
            env = os.environ.copy()
            env["PATH"] = str(first_dir) + os.pathsep + str(second_dir)
            env["AAS_RUNTIME_SCRIPT"] = str(child)
            env.pop("AAS_RUNTIME_PYTHON", None)
            env.pop("AAS_PYTHON", None)

            completed = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(len(first_log.read_text(encoding="utf-8").splitlines()), 2)
            self.assertFalse(second_log.exists())

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell runner test")
    def test_windows_python_runner_propagates_child_exit_code(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        runner = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_python.ps1"
        arguments = [
            "space value",
            "bang!value",
            "amp&value",
            "pipe|value",
            "less<value",
            "greater>value",
            "caret^value",
            "percent%value",
            "left(value",
            "right)value",
            'quote"value',
        ]
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fail.py"
            args_path = Path(tmp) / "args.json"
            script.write_text(
                "import json, sys\n"
                f"from pathlib import Path\nPath({str(args_path)!r}).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
                "raise SystemExit(7)\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AAS_RUNTIME_SCRIPT"] = str(script)
            env["AAS_RUNTIME_PYTHON"] = sys.executable
            env["ERRORLEVEL"] = "0"

            completed = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner), *arguments],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )
            recorded_args = json.loads(args_path.read_text(encoding="utf-8"))

        self.assertEqual(completed.returncode, 7, completed.stderr)
        self.assertEqual(recorded_args, arguments)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell runner test")
    def test_windows_python_runner_preserves_explicit_error_codes(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        runner = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_python.ps1"
        env = os.environ.copy()
        env.pop("AAS_RUNTIME_SCRIPT", None)
        missing_script = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
            check=False,
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
        )
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "ok.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            env["AAS_RUNTIME_SCRIPT"] = str(script)
            env["AAS_RUNTIME_PYTHON"] = str(Path(tmp) / "missing-python.exe")
            missing_python = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

        self.assertEqual(missing_script.returncode, 2, missing_script.stderr)
        self.assertEqual(missing_python.returncode, 127, missing_python.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell runner test")
    def test_windows_python_runner_rejects_non_python_and_python_39(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        non_python = shutil.which("cmd.exe") or os.environ.get("COMSPEC")
        if not non_python:
            self.skipTest("cmd.exe is unavailable")
        runner = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_python.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "child.py"
            script.write_text("print('child-ran')\n", encoding="utf-8")
            old_python = root / "old-python.ps1"
            old_python.write_text("Write-Output '3.9'\nexit 0\n", encoding="utf-8")

            for candidate in (non_python, str(old_python)):
                with self.subTest(candidate=candidate):
                    env = os.environ.copy()
                    env["AAS_RUNTIME_SCRIPT"] = str(script)
                    env["AAS_RUNTIME_PYTHON"] = candidate
                    completed = subprocess.run(
                        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
                        check=False,
                        text=True,
                        capture_output=True,
                        env=env,
                        timeout=30,
                    )

                    self.assertEqual(completed.returncode, 127, completed.stderr)
                    self.assertIn("version 3.10 or newer", completed.stderr)
                    self.assertNotIn("child-ran", completed.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell runner test")
    def test_windows_python_runner_explicit_py_uses_python3_launcher_flag(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        runner = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_python.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "child.py"
            script.write_text("print('unused')\n", encoding="utf-8")
            args_path = root / "py-args.txt"
            fake_py = root / "py.ps1"
            escaped_args_path = str(args_path).replace("'", "''")
            fake_py.write_text(
                "param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args = @())\n"
                f"Add-Content -LiteralPath '{escaped_args_path}' -Value ($Args -join '|')\n"
                "if ($Args.Count -ge 3 -and $Args[0] -eq '-3' -and $Args[1] -eq '-c') {\n"
                "  Write-Output '3.11'\n"
                "  exit 0\n"
                "}\n"
                "if ($Args.Count -ge 2 -and $Args[0] -eq '-3') { exit 0 }\n"
                "exit 91\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PATH"] = str(root) + os.pathsep + env.get("PATH", "")
            env["AAS_RUNTIME_SCRIPT"] = str(script)
            env["AAS_RUNTIME_PYTHON"] = "py"

            completed = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            invocations = args_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(invocations), 2)
            self.assertTrue(all(line.startswith("-3|") for line in invocations), invocations)
            self.assertIn("|-c|", invocations[0])

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell runner test")
    def test_windows_python_runner_runtime_override_beats_stale_py_fallback(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        runner = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_python.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "child.py"
            script.write_text("print('runtime-override-won')\n", encoding="utf-8")
            env = os.environ.copy()
            env["AAS_RUNTIME_SCRIPT"] = str(script)
            env["AAS_RUNTIME_PYTHON"] = sys.executable
            env["AAS_PYTHON"] = "py"

            completed = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("runtime-override-won", completed.stdout)

    def test_windows_python_runner_and_manim_helper_are_declared_runtime_files(self) -> None:
        manifests = load_manifests()
        runner_targets = {entry["target"] for entry in manifests["runtime"]["runners"]}
        manim_sources = {
            entry["source"] for entry in manifests["runtime"]["skills"]["manim-math-animation"]["files"]
        }

        self.assertIn("run_python.ps1", runner_targets)
        self.assertIn("skills/manim-math-animation/mma/tools.py", manim_sources)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell runner test")
    def test_windows_python_runner_does_not_execute_injected_environment_path(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        runner = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_python.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "ok.py"
            marker = root / "injected.txt"
            script.write_text("print('ok')\n", encoding="utf-8")
            env = os.environ.copy()
            env["AAS_RUNTIME_SCRIPT"] = str(script)
            env["AAS_RUNTIME_PYTHON"] = f'missing-python" & echo injected>"{marker}" & rem "'

            completed = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

        self.assertEqual(completed.returncode, 127)
        self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell wrapper test")
    def test_windows_python_fallback_wrappers_propagate_child_exit_code(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        runtime_source = Path(__file__).resolve().parents[1] / "canonical" / "runtime"
        runtime_skills = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "skills"
        wrappers = {
            "deep-research-workflow": ("run_deep_research_workflow.ps1", "deep_research_workflow.py"),
            "lean-formalization-intake": ("run_lean_formalization_intake.ps1", "lean_formalization_intake.py"),
            "lean-research-library": ("run_lean_research_library.ps1", "lean_research_library.py"),
            "lean-strict-verification-gate": (
                "run_lean_strict_verification_gate.ps1",
                "lean_strict_verification_gate.py",
            ),
            "self-improving-agent": ("run_self_improving_agent.ps1", "self_improving_agent.py"),
        }
        arguments = ["space value", "bang!value", "amp&value", "pipe|value", "less<value", "greater>value", "caret^value", "percent%value", "left(value", "right)value", 'quote"value']
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy2(runtime_source / "runners" / "run_python.ps1", root / "run_python.ps1")
            env = os.environ.copy()
            env.pop("AAS_RUNTIME_PYTHON", None)
            env["AAS_PYTHON"] = sys.executable
            env["AAS_RUNTIME_ROOT"] = str(root)
            for skill, (wrapper_name, script_name) in wrappers.items():
                with self.subTest(skill=skill):
                    wrapper = root / wrapper_name
                    shutil.copy2(runtime_skills / skill / wrapper_name, wrapper)
                    args_path = root / f"{skill}-args.json"
                    (root / script_name).write_text(
                        "import json, sys\n"
                        f"from pathlib import Path\nPath({str(args_path)!r}).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
                        "raise SystemExit(7)\n",
                        encoding="utf-8",
                    )
                    completed = subprocess.run(
                        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper), *arguments],
                        check=False,
                        text=True,
                        capture_output=True,
                        env=env,
                        timeout=30,
                    )
                    self.assertEqual(completed.returncode, 7, completed.stderr)
                    self.assertEqual(json.loads(args_path.read_text(encoding="utf-8")), arguments)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell wrapper test")
    def test_windows_python_fallback_wrapper_honors_invalid_aas_python(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        runtime_source = Path(__file__).resolve().parents[1] / "canonical" / "runtime"
        source = runtime_source / "skills" / "deep-research-workflow" / "run_deep_research_workflow.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / source.name
            script = root / "deep_research_workflow.py"
            shutil.copy2(source, wrapper)
            shutil.copy2(runtime_source / "runners" / "run_python.ps1", root / "run_python.ps1")
            script.write_text("print('ok')\n", encoding="utf-8")
            env = os.environ.copy()
            env["AAS_RUNTIME_ROOT"] = str(root)
            env.pop("AAS_RUNTIME_PYTHON", None)
            env["AAS_PYTHON"] = str(root / "missing-python.exe")

            completed = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

        self.assertEqual(completed.returncode, 127, completed.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell wrapper test")
    def test_windows_python_wrappers_delegate_to_shared_runner(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        runtime_skills = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "skills"
        wrappers = {
            "deep-research-workflow": ("run_deep_research_workflow.ps1", "deep_research_workflow.py"),
            "lean-formalization-intake": ("run_lean_formalization_intake.ps1", "lean_formalization_intake.py"),
            "lean-research-library": ("run_lean_research_library.ps1", "lean_research_library.py"),
            "lean-strict-verification-gate": ("run_lean_strict_verification_gate.ps1", "lean_strict_verification_gate.py"),
            "self-improving-agent": ("run_self_improving_agent.ps1", "self_improving_agent.py"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = root / "record.json"
            (root / "run_python.ps1").write_text(
                "param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments = @())\n"
                "$payload = @{ script = $env:AAS_RUNTIME_SCRIPT; arguments = [object[]]$Arguments } | ConvertTo-Json -Compress\n"
                "[IO.File]::WriteAllText($env:AAS_TEST_RECORD, $payload)\n"
                "exit 23\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AAS_RUNTIME_ROOT"] = str(root)
            env["AAS_TEST_RECORD"] = str(record)
            for skill, (wrapper_name, script_name) in wrappers.items():
                with self.subTest(skill=skill):
                    wrapper = root / wrapper_name
                    shutil.copy2(runtime_skills / skill / wrapper_name, wrapper)
                    (root / script_name).write_text("raise SystemExit(99)\n", encoding="utf-8")
                    completed = subprocess.run(
                        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper), "space value", "amp&value"],
                        check=False,
                        text=True,
                        capture_output=True,
                        env=env,
                        timeout=30,
                    )
                    payload = json.loads(record.read_text(encoding="utf-8"))
                    self.assertEqual(completed.returncode, 23, completed.stderr)
                    self.assertEqual(Path(payload["script"]).name, script_name)
                    self.assertEqual(payload["arguments"], ["space value", "amp&value"])

    def test_windows_runtime_does_not_publish_cmd_entrypoints(self) -> None:
        runtime = Path(__file__).resolve().parents[1] / "canonical" / "runtime"
        cmd_entrypoints = [
            path.relative_to(runtime).as_posix()
            for path in runtime.rglob("*")
            if path.is_file() and path.suffix.lower() in {".bat", ".cmd"}
        ]
        self.assertEqual(cmd_entrypoints, [])

        manifests = load_manifests()
        published_targets = [
            entry["target"]
            for entry in manifests["runtime"]["runners"]
        ]
        for spec in manifests["runtime"]["skills"].values():
            published_targets.extend(entry["target"] for entry in spec.get("files", []))
        self.assertFalse(any(target.lower().endswith((".bat", ".cmd")) for target in published_targets))
        for skill in (
            "deep-research-workflow",
            "lean-formalization-intake",
            "lean-research-library",
            "lean-strict-verification-gate",
            "self-improving-agent",
        ):
            with self.subTest(active_windows_target=skill):
                self.assertTrue(runtime_command_target(manifests, skill, "windows").endswith(".ps1"))

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell runner test")
    def test_windows_run_skill_preserves_hostile_arguments_and_child_exit(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        source = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_skill.ps1"
        arguments = ["amp&value", "pipe|value", "less<value", "greater>value", "caret^value", "percent%value", "left(value", "right)value", 'quote"value', "bang!value"]
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            workspace = runtime / "workspace"
            workspace.mkdir()
            shutil.copy2(source, runtime / "run_skill.ps1")
            recorder = workspace / "record.ps1"
            args_path = runtime / "args.json"
            recorder.write_text(
                "param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments = @())\n"
                "[IO.File]::WriteAllText($env:AAS_TEST_ARGS_PATH, (ConvertTo-Json -InputObject ([object[]]$Arguments) -Compress))\n"
                "exit 7\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AAS_TEST_ARGS_PATH"] = str(args_path)
            env["ERRORLEVEL"] = "0"
            completed = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runtime / "run_skill.ps1"), "record.ps1", *arguments],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 7, completed.stderr)
            self.assertEqual(json.loads(args_path.read_text(encoding="utf-8")), arguments)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell runner test")
    def test_windows_run_skill_rejects_cmd_target_metacharacters_before_execution(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell executable not found")
        source = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_skill.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            workspace = runtime / "workspace"
            workspace.mkdir()
            shutil.copy2(source, runtime / "run_skill.ps1")
            marker = runtime / "injected.txt"
            target = workspace / "target.bat"
            target.write_text(f'@echo off\r\necho injected>"{marker}"\r\n', encoding="utf-8")
            arguments = ["safe-value", "amp&value", "pipe|value", "less<value", "greater>value", "caret^value", "percent%value", "bang!value", "left(value", "right)value", 'quote"value', "line\rbreak", "line\nbreak"]
            for argument in arguments:
                with self.subTest(argument=argument):
                    completed = subprocess.run(
                        [
                            powershell,
                            "-NoProfile",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(runtime / "run_skill.ps1"),
                            "target.bat",
                            argument,
                        ],
                        check=False,
                        text=True,
                        capture_output=True,
                        timeout=30,
                    )

                    self.assertEqual(completed.returncode, 64, completed.stderr)
                    self.assertFalse(marker.exists())

    def test_runtime_smoke_selects_native_command_targets(self) -> None:
        manifests = load_manifests()
        self.assertEqual(
            runtime_command_target(manifests, "graph-verifier", "windows"),
            "skills/graph-verifier/graph_verifier.py",
        )
        self.assertEqual(
            runtime_command_target(manifests, "graph-verifier", "windows", "run_skill.ps1"),
            "skills/graph-verifier/graph_verifier.py",
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

    def test_runtime_smoke_fails_closed_when_no_native_runner_is_available(self) -> None:
        manifests = load_manifests()
        with patch(
            "installer.ai_agents_skills.runtime_smoke.runner_invocations",
            return_value=[],
        ):
            result = run_runtime_smoke(
                manifests,
                skills={"graph-verifier"},
                platform=current_platform(None),
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["checked"], 0)
        self.assertEqual(result["results"][0]["failure_kind"], "runner-unavailable")

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
