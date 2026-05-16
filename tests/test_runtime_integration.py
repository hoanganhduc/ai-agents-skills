from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
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
from installer.ai_agents_skills.runtime import runtime_inventory
from installer.ai_agents_skills.runtime_smoke import runtime_command_target, selected_runtime_skills
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
            self.assertTrue(any(item["target_relpath"] == "run_skill.sh" for item in runtime_actions))
            self.assertTrue(any("graph-verifier" in item["target_relpath"] for item in runtime_actions))

            result = apply_plan(root, plan, dry_run=False)
            self.assertTrue(any(item["artifact_type"] == "runtime-file" for item in result["actions"]))
            self.assertEqual(verify(root)["status"], "ok")
            self.assertTrue((root / ".codex" / "runtime" / "run_skill.sh").is_file())

            uninstall_result = uninstall(root, skills={"graph-verifier"}, dry_run=False)
            self.assertTrue(any(item["artifact_type"] == "runtime-file" for item in uninstall_result["removed"]))
            self.assertFalse((root / ".codex" / "runtime" / "workspace" / "skills" / "graph-verifier" / "graph_verifier.py").exists())

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

    def test_portable_runtime_text_is_ascii_clean(self) -> None:
        runtime_root = Path(__file__).resolve().parents[1] / "canonical" / "runtime"
        offenders = []
        for path in runtime_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".bat", ".ps1", ".py", ".sh", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8")
            if any(ord(char) > 127 for char in text):
                offenders.append(str(path.relative_to(runtime_root)))
        self.assertEqual(offenders, [])

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
            selected_runtime_skills(manifests, {"zotero"})


if __name__ == "__main__":
    unittest.main()
