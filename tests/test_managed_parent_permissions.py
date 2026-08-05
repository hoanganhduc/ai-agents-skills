from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.ai_agents_skills.agents import target_for
from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.lifecycle import uninstall
from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.managed_permissions import plan_managed_parent_chain
from installer.ai_agents_skills.planner import build_plan, classify_file_action
from installer.ai_agents_skills.runtime import runtime_file_action


REPO_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == "nt", "POSIX mode normalization")
class ManagedParentPermissionTests(unittest.TestCase):
    def _skill_plan(self, root: Path) -> dict:
        return build_plan(
            root,
            load_manifests(),
            ["graph-verifier"],
            [target_for(root, "codex")],
            install_mode="copy",
            runtime_profile="none",
        )

    def test_skill_noop_repairs_parent_mode_drift_but_not_user_owned_ancestors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex = root / ".codex"
            codex.mkdir(mode=0o755)
            root.chmod(0o755)
            apply_plan(root, self._skill_plan(root), dry_run=False)
            skills = codex / "skills"
            skill = skills / "graph-verifier"
            skills.chmod(0o775)
            skill.chmod(0o775)

            result = apply_plan(root, self._skill_plan(root), dry_run=False)

            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(codex.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(skills.stat().st_mode) & 0o022, 0)
            self.assertEqual(stat.S_IMODE(skill.stat().st_mode) & 0o022, 0)
            repaired = [
                action for action in result["actions"]
                if action.get("artifact_type") == "skill-file"
            ][0]
            self.assertTrue(repaired.get("normalized_parent_modes"))

            uninstall(root, dry_run=False)

            self.assertTrue(codex.is_dir())
            self.assertEqual(stat.S_IMODE(codex.stat().st_mode), 0o755)

    def test_fresh_root_creates_unmanaged_agent_prefix_without_chmodding_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o755)

            result = apply_plan(root, self._skill_plan(root), dry_run=False)

            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o755)
            self.assertTrue((root / ".codex" / "skills" / "graph-verifier").is_dir())
            self.assertEqual(
                stat.S_IMODE((root / ".codex" / "skills").stat().st_mode) & 0o022,
                0,
            )
            created = {
                item
                for action in result["actions"]
                for item in action.get("created_parent_dirs", [])
            }
            self.assertIn(".codex", created)

            uninstall(root, dry_run=False)

            self.assertFalse((root / ".codex").exists())

    def test_failed_apply_removes_only_new_empty_parent_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o755)

            with mock.patch(
                "installer.ai_agents_skills.apply.apply_file_action",
                side_effect=RuntimeError("injected apply failure"),
            ), self.assertRaisesRegex(RuntimeError, "injected apply failure"):
                apply_plan(root, self._skill_plan(root), dry_run=False)

            self.assertFalse((root / ".codex").exists())

    def test_runtime_noop_repairs_entire_managed_chain_and_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".codex").mkdir(mode=0o755)
            runtime_root = root / ".codex" / "runtime"
            entry = {
                "source": "runners/load_secret_env.py",
                "target": "workspace/skills/probe/load_secret_env.py",
                "type": "text",
                "mode": "0644",
                "newline": "lf",
            }

            def plan() -> dict:
                action = runtime_file_action(
                    root=root,
                    runtime_root=runtime_root,
                    entry=entry,
                    skill="probe",
                    artifact_name="probe-loader",
                    backup_replace=False,
                    seen_targets={},
                )
                return {"actions": [action], "root": str(root), "skipped_agents": []}

            apply_plan(root, plan(), dry_run=False)
            target = runtime_root / entry["target"]
            managed_dirs = [
                runtime_root,
                runtime_root / "workspace",
                runtime_root / "workspace" / "skills",
                runtime_root / "workspace" / "skills" / "probe",
            ]
            for directory in managed_dirs:
                directory.chmod(0o775)
            target.chmod(0o664)

            result = apply_plan(root, plan(), dry_run=False)

            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) & 0o022 == 0 for path in managed_dirs))
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
            self.assertTrue(result["actions"][0].get("normalized_parent_modes"))

    def test_credential_runtime_ancestor_modes_converge_and_restore_for_codex_and_shared_roots(
        self,
    ) -> None:
        source = REPO_ROOT / "canonical/runtime/skills/zotero/run_zot.sh"
        entry = {
            "source": "skills/zotero/run_zot.sh",
            "target": "workspace/skills/zotero/run_zot.sh",
            "type": "text",
            "mode": "0755",
            "newline": "lf",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_roots = (
                root / ".codex" / "runtime",
                root / ".local" / "share" / "ai-agents-skills" / "runtime",
            )
            for runtime_root in runtime_roots:
                with self.subTest(runtime_root=str(runtime_root)):
                    target = runtime_root / entry["target"]
                    target.parent.mkdir(parents=True)
                    target.write_bytes(source.read_bytes())
                    target.chmod(0o755)
                    managed_dirs = (
                        runtime_root,
                        runtime_root / "workspace",
                        runtime_root / "workspace" / "skills",
                        runtime_root / "workspace" / "skills" / "zotero",
                    )
                    for directory in managed_dirs:
                        directory.chmod(0o775)

                    action = runtime_file_action(
                        root=root,
                        runtime_root=runtime_root,
                        entry=entry,
                        skill="zotero",
                        artifact_name="workspace/skills/zotero/run_zot.sh",
                        backup_replace=False,
                        seen_targets={},
                    )
                    self.assertEqual(action["operation"], "noop")
                    planned = plan_managed_parent_chain(root, action)
                    action["planned_parent_mode_changes"] = planned
                    self.assertEqual(
                        {Path(item["path"]) for item in planned},
                        set(managed_dirs),
                    )

                    result = apply_plan(
                        root,
                        {"actions": [action], "root": str(root), "skipped_agents": []},
                        dry_run=False,
                    )

                    self.assertTrue(
                        all(stat.S_IMODE(path.stat().st_mode) & 0o022 == 0 for path in managed_dirs)
                    )
                    self.assertEqual(
                        {Path(item["path"]) for item in result["actions"][0]["permission_origin"]},
                        set(managed_dirs),
                    )

                    uninstall(root, dry_run=False)

                    self.assertTrue(target.is_file())
                    self.assertTrue(
                        all(stat.S_IMODE(path.stat().st_mode) == 0o775 for path in managed_dirs)
                    )

    def test_openclaw_credential_skill_update_converges_and_restores_ancestor_modes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = REPO_ROOT / "canonical/skills/zotero/SKILL.md"
            content = source.read_text(encoding="utf-8")
            target = root / ".openclaw" / "skills" / "zotero" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text("Managed by ai-agents-skills\nstale\n", encoding="utf-8")
            managed_dirs = (root / ".openclaw" / "skills", target.parent)
            for directory in managed_dirs:
                directory.chmod(0o775)
            action = classify_file_action(
                "openclaw",
                "zotero",
                target,
                content,
                "skill-file",
                False,
                False,
                install_mode="copy",
                source_path=source,
            )
            self.assertEqual(action["operation"], "update")
            planned = plan_managed_parent_chain(root, action)
            action["planned_parent_mode_changes"] = planned
            self.assertEqual(
                {Path(item["path"]) for item in planned},
                set(managed_dirs),
            )

            result = apply_plan(
                root,
                {"actions": [action], "root": str(root), "skipped_agents": []},
                dry_run=False,
            )

            self.assertEqual(target.read_text(encoding="utf-8"), content)
            self.assertTrue(
                all(stat.S_IMODE(path.stat().st_mode) & 0o022 == 0 for path in managed_dirs)
            )
            self.assertTrue(result["actions"][0].get("permission_origin"))

            uninstall(root, dry_run=False)

            self.assertTrue(target.is_file())
            self.assertTrue(
                all(stat.S_IMODE(path.stat().st_mode) == 0o775 for path in managed_dirs)
            )


if __name__ == "__main__":
    unittest.main()
