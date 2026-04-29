from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.cli import main
from installer.ai_agents_skills.docs import generate_docs
from installer.ai_agents_skills.lifecycle import rollback, uninstall
from installer.ai_agents_skills.manifest import REPO_ROOT, load_manifests
from installer.ai_agents_skills.planner import build_plan
from installer.ai_agents_skills.selectors import resolve_skills
from installer.ai_agents_skills.verify import verify


class Args:
    skill = None
    skills = None
    profile = None
    exclude = None


def fake_root() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory()


def create_agent_homes(root: Path, *agents: str) -> None:
    for agent in agents:
        (root / f".{agent}").mkdir(parents=True, exist_ok=True)


class ManifestTests(unittest.TestCase):
    def test_default_profile_resolves_research_core(self) -> None:
        manifests = load_manifests()
        args = Args()
        selected = resolve_skills(args, manifests)
        self.assertIn("deep-research-workflow", selected)
        self.assertIn("research-briefing", selected)
        self.assertNotIn("tikz-draw", selected)

    def test_legacy_alias_resolves_to_canonical(self) -> None:
        manifests = load_manifests()
        args = Args()
        args.skills = "deep-research,research_digest_wrapper"
        selected = resolve_skills(args, manifests)
        self.assertEqual(selected, ["deep-research-workflow", "research-digest-wrapper"])


class PlanInstallVerifyTests(unittest.TestCase):
    def test_partial_install_to_fake_root_only_installs_selected_skill(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude", "codex")
            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            from installer.ai_agents_skills.agents import detect_agents

            plan = build_plan(root, manifests, selected, detect_agents(root))
            apply_result = apply_plan(root, plan, dry_run=False)
            self.assertFalse(apply_result["dry_run"])

            self.assertTrue((root / ".claude" / "skills" / "zotero" / "SKILL.md").exists())
            self.assertTrue((root / ".agents" / "skills" / "zotero" / "SKILL.md").exists())
            self.assertFalse((root / ".claude" / "skills" / "tikz-draw" / "SKILL.md").exists())

            result = verify(root)
            self.assertEqual(result["status"], "ok")
            checked_skills = {item["skill"] for item in result["results"]}
            self.assertEqual(checked_skills, {"zotero"})

    def test_no_detected_agent_means_no_actions(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, [])
            self.assertEqual(plan["actions"], [])
            self.assertEqual(len(plan["skipped_agents"]), 3)

    def test_unmanaged_existing_file_is_skipped_by_default(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            existing = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            existing.parent.mkdir(parents=True)
            existing.write_text("user file\n", encoding="utf-8")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            file_actions = [a for a in plan["actions"] if a["kind"] == "file"]
            self.assertEqual(file_actions[0]["classification"], "unmanaged")
            self.assertEqual(file_actions[0]["operation"], "skip")

    def test_codex_legacy_skill_dir_is_skipped_by_default(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex")
            legacy = root / ".codex" / "skills" / "zotero" / "SKILL.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("legacy codex skill\n", encoding="utf-8")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            file_actions = [a for a in plan["actions"] if a["kind"] == "file"]
            self.assertEqual(file_actions[0]["classification"], "legacy")
            self.assertEqual(file_actions[0]["operation"], "skip")
            self.assertEqual(file_actions[0]["legacy_path"], str(legacy))

    def test_target_dir_legacy_alias_is_skipped_by_default(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            legacy = root / ".claude" / "skills" / "deep-research" / "SKILL.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("legacy alias skill\n", encoding="utf-8")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "deep-research-workflow"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            file_actions = [a for a in plan["actions"] if a["kind"] == "file"]
            block_actions = [a for a in plan["actions"] if a["kind"] == "managed-block"]
            self.assertEqual(file_actions[0]["classification"], "legacy")
            self.assertEqual(file_actions[0]["operation"], "skip")
            self.assertEqual(file_actions[0]["legacy_path"], str(legacy))
            self.assertEqual(block_actions[0]["operation"], "skip")

    def test_codex_legacy_alias_can_be_migrated_to_canonical_target(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex")
            legacy = root / ".codex" / "skills" / "research_digest_wrapper" / "SKILL.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("legacy alias skill\n", encoding="utf-8")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "research-digest-wrapper"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root), migrate=True)
            file_actions = [a for a in plan["actions"] if a["kind"] == "file"]
            self.assertEqual(file_actions[0]["classification"], "legacy")
            self.assertEqual(file_actions[0]["operation"], "migrate-copy")
            self.assertEqual(file_actions[0]["legacy_path"], str(legacy))

            apply_plan(root, plan, dry_run=False)
            target = root / ".agents" / "skills" / "research-digest-wrapper" / "SKILL.md"
            self.assertTrue(target.exists())
            self.assertTrue(legacy.exists())

    def test_uninstall_is_dry_run_by_default_then_apply_removes(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            apply_plan(root, plan, dry_run=False)

            target = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            self.assertTrue(target.exists())
            dry = uninstall(root, skills={"zotero"}, dry_run=True)
            self.assertTrue(dry["dry_run"])
            self.assertTrue(target.exists())
            applied = uninstall(root, skills={"zotero"}, dry_run=False)
            self.assertFalse(applied["dry_run"])
            self.assertFalse(target.exists())

    def test_rollback_dry_run_and_apply(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            result = apply_plan(root, plan, dry_run=False)
            target = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            self.assertTrue(target.exists())
            dry = rollback(root, run_id=result["run_id"], dry_run=True)
            self.assertTrue(dry["dry_run"])
            applied = rollback(root, run_id=result["run_id"], dry_run=False)
            self.assertFalse(applied["dry_run"])
            self.assertFalse(target.exists())


class DocsAndLauncherTests(unittest.TestCase):
    def test_generated_docs_include_manifest_skills(self) -> None:
        manifests = load_manifests()
        written = generate_docs(manifests)
        self.assertIn(REPO_ROOT / "README.md", written)
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for skill in ("deep-research-workflow", "zotero", "vnthuquan"):
            self.assertIn(f"`{skill}`", readme)
        self.assertIn("docs/system-profile.md", readme)

    def test_make_bat_prefers_pwsh_and_forwards_all_args(self) -> None:
        text = (REPO_ROOT / "make.bat").read_text(encoding="utf-8")
        self.assertIn("where pwsh", text)
        self.assertIn("%*", text)
        self.assertLess(text.index("where pwsh"), text.index("powershell.exe"))

    def test_cli_install_refuses_real_system_without_flag(self) -> None:
        code = main(["--json", "install", "--skill", "zotero", "--apply"])
        self.assertEqual(code, 1)

    def test_cli_install_dry_run_flag_previews_without_writes(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            code = main([
                "--json",
                "--root",
                str(root),
                "install",
                "--skill",
                "zotero",
                "--dry-run",
            ])
            self.assertEqual(code, 0)
            self.assertFalse((root / ".claude" / "skills" / "zotero" / "SKILL.md").exists())

    def test_cli_install_rejects_dry_run_with_apply(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            code = main([
                "--json",
                "--root",
                str(root),
                "install",
                "--skill",
                "zotero",
                "--dry-run",
                "--apply",
            ])
            self.assertEqual(code, 1)

    def test_cli_install_fake_root_succeeds(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            code = main([
                "--json",
                "--root",
                str(root),
                "install",
                "--skill",
                "zotero",
                "--apply",
            ])
            self.assertEqual(code, 0)

    def test_cli_accepts_global_options_after_subcommand(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            code = main([
                "plan",
                "--root",
                str(root),
                "--skill",
                "zotero",
                "--json",
            ])
            self.assertEqual(code, 0)

    def test_plan_uses_canonical_skill_body_when_available(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "deep-research-workflow"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            file_actions = [a for a in plan["actions"] if a["kind"] == "file"]
            self.assertIn("Phased deep research", file_actions[0]["content"])
            self.assertIn("Managed by ai-agents-skills", file_actions[0]["content"])


if __name__ == "__main__":
    unittest.main()
