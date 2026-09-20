from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest import mock

from installer.ai_agents_skills.agents import detect_agents, target_for
from installer.ai_agents_skills.apply import apply_json_setting_action, uninstall_origin
from installer.ai_agents_skills.json_merge import merge_json_value, restore_json_value
from installer.ai_agents_skills.lifecycle import (
    apply_uninstall_action,
    load_run_actions,
    plan_uninstall_action,
    rollback_artifact,
)
from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.planner import build_plan
from installer.ai_agents_skills.state import state_for_root
from installer.ai_agents_skills.state import artifact_signature, signatures_match


class KaggleWindowsTargetTests(unittest.TestCase):
    def test_codewhale_is_a_distinct_copy_mode_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = target_for(root, "codewhale")
            self.assertEqual(target.home, root / ".codewhale")
            self.assertEqual(target.skills_dir, root / ".codewhale" / "skills")
            self.assertFalse(target.instruction_blocks_enabled)

    def test_kaggle_manifest_declares_every_windows_target(self) -> None:
        supported = set(load_manifests()["skills"]["skills"]["kaggle-research-compute"]["supported_agents"])
        self.assertTrue(
            {
                "codex",
                "claude",
                "deepseek",
                "codewhale",
                "copilot",
                "opencode",
                "antigravity",
                "grok",
                "kimi",
                "chatgpt-local-coder",
            }.issubset(supported)
        )

    def test_planner_uses_target_owned_skill_roots_and_managed_isolation_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".codewhale").mkdir()
            clc_config = root / "AppData" / "Roaming" / "chatgpt-local-coder" / "config.json"
            clc_config.parent.mkdir(parents=True)
            clc_config.write_text(json.dumps({"workspaceRoots": ["C:/work"]}) + "\n", encoding="utf-8")
            codewhale_config = root / ".codewhale" / "config.toml"
            codewhale_config.write_text("[providers.deepseek]\nmodel = \"demo\"\n", encoding="utf-8")

            agents = detect_agents(
                root,
                ["codewhale", "chatgpt-local-coder"],
                platform="windows",
            )
            plan = build_plan(
                root,
                load_manifests(),
                ["kaggle-research-compute"],
                agents,
                runtime_profile="none",
                platform="windows",
                requested_agents=["codewhale", "chatgpt-local-coder"],
            )
            skill_actions = {
                action["agent"]: action
                for action in plan["actions"]
                if action.get("artifact_type") == "skill-file"
            }
            self.assertEqual(
                Path(skill_actions["codewhale"]["path"]),
                root / ".codewhale" / "skills" / "kaggle-research-compute" / "SKILL.md",
            )
            self.assertEqual(skill_actions["codewhale"]["install_mode"], "copy")
            self.assertEqual(
                Path(skill_actions["chatgpt-local-coder"]["path"]),
                root / ".chatgpt-local-coder" / "skills" / "kaggle-research-compute" / "SKILL.md",
            )

            codewhale_setting = next(
                action
                for action in plan["actions"]
                if action.get("artifact_id") == "settings-compat:codewhale-skill-isolation"
            )
            self.assertEqual(Path(codewhale_setting["path"]), codewhale_config)
            self.assertEqual(codewhale_setting["operation"], "merge")

            clc_setting = next(
                action
                for action in plan["actions"]
                if action.get("artifact_id") == "settings-value:skills.scanHostOnly"
            )
            self.assertEqual(Path(clc_setting["path"]), clc_config)
            self.assertEqual(clc_setting["setting_path"], ["skills", "scanHostOnly"])
            self.assertEqual(clc_setting["operation"], "merge")

    def test_json_setting_merge_and_restore_preserve_unrelated_values(self) -> None:
        source = {"workspaceRoots": ["C:/work"]}
        merged, changed, created, existed, old = merge_json_value(
            source, ["skills", "scanHostOnly"], True
        )
        self.assertTrue(changed)
        self.assertFalse(existed)
        self.assertIsNone(old)
        self.assertEqual(merged["workspaceRoots"], ["C:/work"])
        restored, restored_changed = restore_json_value(
            merged,
            ["skills", "scanHostOnly"],
            installed_value=True,
            original_exists=False,
            original_value=None,
            created_containers=created,
        )
        self.assertTrue(restored_changed)
        self.assertEqual(restored, source)

    def test_json_setting_merge_refuses_existing_null_container(self) -> None:
        source = {"skills": None, "keep": 1}
        with self.assertRaisesRegex(ValueError, "skills.*must be an object"):
            merge_json_value(source, ["skills", "scanHostOnly"], True)
        self.assertEqual(source, {"skills": None, "keep": 1})

    def test_json_setting_action_uninstalls_only_its_owned_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "AppData" / "Roaming" / "chatgpt-local-coder" / "config.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"workspaceRoots": ["C:/work"]}) + "\n", encoding="utf-8")
            action = {
                "kind": "json-setting-merge",
                "agent": "chatgpt-local-coder",
                "skill": "repo-management",
                "path": str(config),
                "artifact_type": "settings-value-merge",
                "artifact_id": "settings-value:skills.scanHostOnly",
                "artifact_name": "skills.scanHostOnly",
                "setting_path": ["skills", "scanHostOnly"],
                "setting_value": True,
                "classification": "managed",
                "operation": "merge",
                "current_signature": artifact_signature(config),
            }
            installed = apply_json_setting_action(root, "test-run", action)
            installed["uninstall"] = uninstall_origin(installed, None)
            data = json.loads(config.read_text(encoding="utf-8"))
            self.assertTrue(data["skills"]["scanHostOnly"])
            data["unrelated"] = "kept"
            config.write_text(json.dumps(data) + "\n", encoding="utf-8")

            uninstall_action = plan_uninstall_action(installed, root)
            self.assertEqual(uninstall_action["operation"], "json-setting-restore")
            result = apply_uninstall_action(uninstall_action, root)
            self.assertTrue(result["completed"])
            final = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(final, {"workspaceRoots": ["C:/work"], "unrelated": "kept"})

    def test_json_setting_rollback_preserves_later_unrelated_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "AppData" / "Roaming" / "chatgpt-local-coder" / "config.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"workspaceRoots": ["C:/work"]}) + "\n", encoding="utf-8")
            action = {
                "kind": "json-setting-merge",
                "agent": "chatgpt-local-coder",
                "skill": "repo-management",
                "path": str(config),
                "artifact_type": "settings-value-merge",
                "artifact_id": "settings-value:skills.scanHostOnly",
                "artifact_name": "skills.scanHostOnly",
                "setting_path": ["skills", "scanHostOnly"],
                "setting_value": True,
                "classification": "managed",
                "operation": "merge",
                "current_signature": artifact_signature(config),
            }
            installed = apply_json_setting_action(root, "test-run", action)
            installed["uninstall"] = uninstall_origin(installed, None)
            data = json.loads(config.read_text(encoding="utf-8"))
            data["later"] = "kept"
            config.write_text(json.dumps(data) + "\n", encoding="utf-8")
            rollback_artifact(installed, root)
            self.assertEqual(
                json.loads(config.read_text(encoding="utf-8")),
                {"workspaceRoots": ["C:/work"], "later": "kept"},
            )

    def test_partial_setting_update_rollback_restores_previous_region(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            settings.write_text(
                json.dumps(
                    {
                        "later": "kept",
                        "hooks": {
                            "Stop": [
                                {
                                    "_managedBy": "ai-agents-skills",
                                    "_id": "demo-hook",
                                    "command": "new-hook",
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            item = {
                "artifact": str(settings),
                "artifact_type": "settings-hook-merge",
                "event": "Stop",
                "managed_id": "demo-hook",
                "uninstall": {
                    "action": "merge-remove",
                    "event": "Stop",
                    "managed_id": "demo-hook",
                    "created_containers": {"hooks": False, "event": False},
                    "created_file": False,
                },
                "previous_state_artifact": {
                    "event": "Stop",
                    "managed_id": "demo-hook",
                    "managed_entry": {
                        "_managedBy": "ai-agents-skills",
                        "_id": "demo-hook",
                        "command": "old-hook",
                    },
                },
            }
            rollback_artifact(item, Path(tmp))
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(data["later"], "kept")
            self.assertEqual(data["hooks"]["Stop"][0]["command"], "old-hook")

    def test_windows_state_paths_translate_to_wsl_root_without_duplicate_identity(self) -> None:
        state = {
            "schema_version": 1,
            "artifacts": [
                {
                    "key": r"codex:kaggle-research-compute:C:\Users\...\.codex\skills\kaggle-research-compute\SKILL.md",
                    "agent": "codex",
                    "skill": "kaggle-research-compute",
                    "artifact": r"C:\Users\...\.codex\skills\kaggle-research-compute\SKILL.md",
                    "artifact_type": "skill-file",
                    "managed": True,
                    "uninstall": {"action": "unmanage-only"},
                }
            ],
            "runs": [],
            "uninstall_records": [],
        }
        translated = state_for_root(state, PurePosixPath("/mnt/c/Users/..."))  # type: ignore[arg-type]
        item = translated["artifacts"][0]
        self.assertEqual(
            item["artifact"],
            "/mnt/c/Users/.../.codex/skills/kaggle-research-compute/SKILL.md",
        )
        self.assertIn("/mnt/c/Users/.../.codex/skills", item["key"])

    def test_wsl_state_paths_translate_back_to_native_windows_root(self) -> None:
        state = {
            "schema_version": 1,
            "artifacts": [
                {
                    "key": "codex:kaggle-research-compute:/mnt/c/Users/.../.codex/skills/kaggle-research-compute/SKILL.md",
                    "agent": "codex",
                    "skill": "kaggle-research-compute",
                    "artifact": "/mnt/c/Users/.../.codex/skills/kaggle-research-compute/SKILL.md",
                    "artifact_type": "skill-file",
                    "managed": True,
                    "installed_signature": {
                        "exists": True,
                        "kind": "symlink",
                        "target": "/mnt/c/Users/.../ai-agents-skills/canonical/skills/kaggle-research-compute/SKILL.md",
                    },
                    "uninstall": {"action": "unmanage-only"},
                }
            ],
            "runs": [],
            "uninstall_records": [],
        }
        translated = state_for_root(state, PureWindowsPath(r"C:\Users\..."))  # type: ignore[arg-type]
        item = translated["artifacts"][0]
        self.assertEqual(
            item["artifact"],
            r"C:\Users\...\.codex\skills\kaggle-research-compute\SKILL.md",
        )
        self.assertIn(r"C:\Users\...\.codex\skills", item["key"])
        self.assertEqual(
            item["installed_signature"]["target"],
            r"C:\Users\...\ai-agents-skills\canonical\skills\kaggle-research-compute\SKILL.md",
        )
        self.assertTrue(
            signatures_match(
                item["installed_signature"],
                {
                    "exists": True,
                    "kind": "symlink",
                    "target": r"\\?\C:\Users\...\ai-agents-skills\canonical\skills\kaggle-research-compute\SKILL.md",
                },
            )
        )

    def test_run_record_actions_translate_for_cross_substrate_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "20260920-000000-deadbeef"
            run_record = Path(tmp) / f"{run_id}.json"
            run_record.write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "actions": [
                            {
                                "artifact": "/mnt/c/Users/.../.codex/skills/kaggle-research-compute/SKILL.md",
                                "key": "codex:kaggle-research-compute:/mnt/c/Users/.../.codex/skills/kaggle-research-compute/SKILL.md",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            state = {"runs": [{"run_id": run_id, "action_count": 1}]}
            with mock.patch(
                "installer.ai_agents_skills.lifecycle.run_record_path",
                return_value=run_record,
            ):
                actions = load_run_actions(PureWindowsPath(r"C:\Users\..."), state, run_id)  # type: ignore[arg-type]
            self.assertEqual(
                actions[0]["artifact"],
                r"C:\Users\...\.codex\skills\kaggle-research-compute\SKILL.md",
            )


if __name__ == "__main__":
    unittest.main()
