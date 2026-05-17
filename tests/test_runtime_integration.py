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
from unittest.mock import patch

from installer.ai_agents_skills.agents import detect_agents
from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.cli import INSTALL_CONFIRMATION_PHRASE, main
from installer.ai_agents_skills.lifecycle import uninstall
from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.planner import build_plan
from installer.ai_agents_skills.runtime import RUNTIME_SOURCE_ROOT, replace_with_runtime_file, runtime_inventory
from installer.ai_agents_skills.runtime_smoke import runtime_command_target, selected_runtime_skills
from installer.ai_agents_skills.sanitize import has_sensitive_material
from installer.ai_agents_skills.state import load_state
from installer.ai_agents_skills.verify import verify


def create_agent_home(root: Path, agent: str = "codex") -> None:
    (root / f".{agent}").mkdir(parents=True)


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

            result = runtime_inventory(root)
            blocked = {item["path"]: item["classification"] for item in result["entries"]}
            reasons = {item["path"]: item["reason"] for item in result["entries"]}

            self.assertEqual(blocked["workspace/skills/zotero/config.json"], "denied")
            self.assertEqual(blocked["workspace/config/research-compute.toml"], "denied")
            self.assertEqual(blocked["workspace/config/research-compute.example.toml"], "candidate")
            self.assertEqual(blocked["workspace/skills/zotero/__pycache__/x.pyc"], "denied")
            self.assertEqual(blocked["workspace/data/calibre/cache/metadata.db"], "denied")
            self.assertEqual(blocked["workspace/skills/zotero/docker-compose.yml"], "blocked")
            self.assertIn("persistent execution marker", reasons["workspace/skills/zotero/docker-compose.yml"])

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
            runtime_command_target(manifests, "graph-verifier", "linux"),
            "skills/graph-verifier/run_graph_verifier.sh",
        )
        self.assertEqual(
            runtime_command_target(manifests, "formal-skeleton-helper", "macos"),
            "skills/formal-skeleton-helper/run_formal_skeleton.sh",
        )

    def test_runtime_smoke_rejects_non_runtime_skill_scope(self) -> None:
        manifests = load_manifests()
        with self.assertRaises(ValueError):
            selected_runtime_skills(manifests, {"agent-group-discuss"})

    def test_runtime_smoke_rejects_runtime_skill_without_smoke_contract(self) -> None:
        manifests = load_manifests()
        with self.assertRaisesRegex(ValueError, "zotero"):
            selected_runtime_skills(manifests, {"zotero"})

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
