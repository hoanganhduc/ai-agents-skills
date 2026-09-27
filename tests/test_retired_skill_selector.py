import argparse
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from installer.ai_agents_skills.cli import resolve_skill_filter


class RetiredSelectorTests(unittest.TestCase):
    def args(self, command="uninstall", name="lean-research-library"):
        return argparse.Namespace(command=command, skill=name, skills=None,
                                  root=Path("/unused"), agents="codex", run_id=None)

    def manifests(self):
        return {"skills": {"skills": {}, "aliases": {}}}

    def record(self, **changes):
        return {"managed": True, "key": "codex:old", "artifact": "old",
                "artifact_type": "skill", "skill": "lean-research-library", "agent": "codex", **changes}

    def test_recorded_retired_name_is_selectable_for_uninstall_only(self):
        with patch("installer.ai_agents_skills.cli.load_state", return_value={"artifacts": [self.record()]}):
            self.assertEqual(resolve_skill_filter(self.args(), self.manifests()), {"lean-research-library"})
            for command in ["plan", "install", "verify"]:
                with self.assertRaises(ValueError): resolve_skill_filter(self.args(command), self.manifests())

    def test_unknown_unmanaged_and_wrong_agent_do_not_resolve(self):
        for records in [[], [self.record(managed=False)], [self.record(agent="claude")]]:
            with patch("installer.ai_agents_skills.cli.load_state", return_value={"artifacts": records}):
                with self.assertRaises(ValueError): resolve_skill_filter(self.args(), self.manifests())

    def test_uninstall_journal_supports_rollback(self):
        with patch("installer.ai_agents_skills.cli.load_state", return_value={"uninstall_records": [self.record()]}):
            self.assertEqual(resolve_skill_filter(self.args("rollback"), self.manifests()), {"lean-research-library"})

    @unittest.skipIf(os.name == "nt", "native Windows installer mutation is intentionally disabled")
    def test_real_journal_survives_catalog_removal_and_preserves_edits(self):
        import tempfile
        from installer.ai_agents_skills.manifest import load_manifests
        from installer.ai_agents_skills.agents import detect_agents
        from installer.ai_agents_skills.planner import build_plan
        from installer.ai_agents_skills.apply import apply_plan
        from installer.ai_agents_skills.lifecycle import uninstall, rollback
        # Install a currently available skill, then simulate a future catalog
        # that removed it. Lifecycle must use actual installer state, not names.
        for changed in [False, True]:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); (root / '.codex').mkdir()
                manifests = load_manifests()
                plan = build_plan(root, manifests, ['zenodo-artifact'], detect_agents(root), runtime_profile='none', install_mode='copy')
                applied = apply_plan(root, plan, dry_run=False)
                target = root / '.codex/skills/zenodo-artifact/SKILL.md'
                self.assertTrue(target.exists())
                if changed: target.write_text('user modified skill\n', encoding="utf-8")
                final_catalog = self.manifests()
                args = self.args(name='zenodo-artifact'); args.root = root
                selected = resolve_skill_filter(args, final_catalog)
                preview = uninstall(root, skills=selected, dry_run=True)
                self.assertTrue(target.exists())
                uninstall(root, skills=selected, dry_run=False)
                self.assertEqual(target.exists(), changed)
                if changed: self.assertEqual(target.read_text(encoding="utf-8"), 'user modified skill\n')
                args.command = 'rollback'; args.run = applied['run_id']
                self.assertEqual(resolve_skill_filter(args, final_catalog), selected)
                rollback(root, skills=selected, run_id=applied['run_id'], dry_run=True)


if __name__ == "__main__": unittest.main()
