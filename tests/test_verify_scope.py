from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.ai_agents_skills.agents import detect_agents, target_for
from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.planner import build_plan
from installer.ai_agents_skills.verify import verify


def create_agent_homes(root: Path, *agents: str) -> None:
    for agent in agents:
        target_for(root, agent).home.mkdir(parents=True, exist_ok=True)


def _install(root: Path, skills: list[str], agents: list[str], runtime_profile: str = "auto") -> None:
    create_agent_homes(root, *agents)
    plan = build_plan(
        root, load_manifests(), skills, detect_agents(root, agents),
        platform="linux", requested_agents=agents, runtime_profile=runtime_profile,
    )
    apply_plan(root, plan, dry_run=False)


def _result_for(report: dict, artifact_type: str) -> dict:
    return next(r for r in report["results"] if r["artifact_type"] == artifact_type)


def _check(result: dict, name: str) -> dict | None:
    return next((c for c in result["checks"] if c["name"] == name), None)


class VerifyScopeTests(unittest.TestCase):
    """verify must hold each artifact to the part of the file the installer owns."""

    def test_a_user_edit_elsewhere_in_a_merged_config_is_not_drift(self) -> None:
        # The installer owns one block of ~/.grok/config.toml and never the file,
        # so an ordinary user edit must not report the install as damaged.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "home"
            root.mkdir()
            _install(root, ["autonomous-research-loop"], ["grok"])
            self.assertEqual(verify(root)["status"], "ok")

            config = root / ".grok" / "config.toml"
            config.write_text(config.read_text(encoding="utf-8") + '\nmodel = "grok-4"\n', encoding="utf-8")
            self.assertEqual(verify(root)["status"], "ok")

    def test_damage_inside_the_managed_block_is_still_caught(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "home"
            root.mkdir()
            _install(root, ["autonomous-research-loop"], ["grok"])
            config = root / ".grok" / "config.toml"
            config.write_text(
                config.read_text(encoding="utf-8").replace("ai-agents-skills:compat-claude:start", "gone"),
                encoding="utf-8",
            )
            report = verify(root)
            self.assertEqual(report["status"], "failed")
            result = _result_for(report, "settings-compat-merge")
            self.assertFalse(_check(result, "managed-block-present")["ok"])

    def test_a_user_edit_elsewhere_in_a_merged_settings_file_is_not_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "home"
            root.mkdir()
            _install(root, ["autonomous-research-loop"], ["claude"], runtime_profile="full")
            settings = root / ".claude" / "settings.json"
            self.assertEqual(verify(root)["status"], "ok")

            data = json.loads(settings.read_text(encoding="utf-8"))
            data.setdefault("permissions", {})["allow"] = ["Bash(git status)"]
            settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            self.assertEqual(verify(root)["status"], "ok")

    def test_damage_inside_the_managed_hook_entry_is_still_caught(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "home"
            root.mkdir()
            _install(root, ["autonomous-research-loop"], ["claude"], runtime_profile="full")
            settings = root / ".claude" / "settings.json"
            data = json.loads(settings.read_text(encoding="utf-8"))
            for entries in data.get("hooks", {}).values():
                for entry in entries:
                    if entry.get("_managedBy") == "ai-agents-skills":
                        entry["hooks"][0]["command"] = "rm -rf /"
            settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            report = verify(root)
            self.assertEqual(report["status"], "failed")
            result = _result_for(report, "settings-hook-merge")
            self.assertFalse(_check(result, "managed-entry-match")["ok"])

    def test_one_unreadable_artifact_does_not_abort_the_run(self) -> None:
        # An interrupted sync or a mistaken mkdir can leave a directory where a
        # managed file was. That is one failed artifact, not a failed run.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "home"
            root.mkdir()
            _install(root, ["graph-verifier"], ["codex"])
            baseline = verify(root)
            self.assertEqual(baseline["status"], "ok")

            victim = root / ".codex" / "skills" / "graph-verifier" / "SKILL.md"
            victim.unlink()
            victim.mkdir()

            report = verify(root)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["checked"], baseline["checked"])
            failed = [r for r in report["results"] if r["status"] != "ok"]
            self.assertEqual(len(failed), 1, failed)
            self.assertFalse(_check(failed[0], "artifact-readable")["ok"])

    def test_a_marker_less_support_file_verifies_clean_when_undamaged(self) -> None:
        # render only marks comment-bearing formats, so demanding a marker on a
        # .json support file fails a fresh install that has nothing wrong with it.
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "home"
            root.mkdir()
            references = Path("canonical/skills/graph-verifier/references")
            created_dir = not references.exists()
            references.mkdir(parents=True, exist_ok=True)
            schema = references / "schema.json"
            schema.write_text('{"type": "object"}\n', encoding="utf-8")
            try:
                create_agent_homes(root, "codex")
                plan = build_plan(
                    root, manifests, ["graph-verifier"], detect_agents(root, ["codex"]),
                    platform="linux", requested_agents=["codex"],
                )
                apply_plan(root, plan, dry_run=False)
                report = verify(root)
            finally:
                schema.unlink()
                if created_dir:
                    references.rmdir()
            self.assertEqual(report["status"], "ok", [r for r in report["results"] if r["status"] != "ok"])

    def test_agent_visible_fails_when_the_agent_skills_directory_moves(self) -> None:
        # Antigravity resolves its skills directory from its own config, so a
        # migrated home leaves correctly named files where the agent no longer looks.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "home"
            root.mkdir()
            _install(root, ["graph-verifier"], ["antigravity"])
            self.assertEqual(verify(root)["status"], "ok")

            moved = root / ".gemini" / "config" / "skills"
            moved.mkdir(parents=True, exist_ok=True)
            with patch(
                "installer.ai_agents_skills.agents.antigravity_skills_dir",
                lambda r: Path(r) / ".gemini" / "config" / "skills",
            ):
                report = verify(root)
                result = _result_for(report, "skill-file")
            self.assertTrue(Path(result["artifact"]).is_file())
            self.assertFalse(_check(result, "agent-visible")["ok"])
            self.assertEqual(report["status"], "failed")


if __name__ == "__main__":
    unittest.main()
