from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.cli import main
from installer.ai_agents_skills.docs import generate_docs
from installer.ai_agents_skills.lifecycle import rollback, uninstall
from installer.ai_agents_skills.manifest import REPO_ROOT, load_manifests
from installer.ai_agents_skills.planner import build_plan
from installer.ai_agents_skills.selectors import resolve_artifacts, resolve_skills
from installer.ai_agents_skills.verify import verify


class Args:
    skill = None
    skills = None
    profile = None
    exclude = None
    no_skills = False
    artifact = None
    artifacts = None
    artifact_profile = None
    exclude_artifact = None
    with_deps = False
    install_mode = "auto"


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
        args.skills = "deep-research,research_digest_wrapper,openclaw-research"
        selected = resolve_skills(args, manifests)
        self.assertEqual(selected, ["deep-research-workflow", "research-digest-wrapper", "source-research"])


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
            self.assertTrue((root / ".codex" / "skills" / "zotero" / "SKILL.md").exists())
            self.assertTrue((root / ".claude" / "skills" / "zotero" / "SKILL.md").is_symlink())
            codex_skill = root / ".codex" / "skills" / "zotero" / "SKILL.md"
            self.assertFalse(codex_skill.is_symlink())
            self.assertIn("Install mode: reference", codex_skill.read_text(encoding="utf-8"))
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

    def test_verify_reports_no_managed_artifacts_explicitly(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            result = verify(root)
            self.assertEqual(result["status"], "no-managed-artifacts")
            self.assertEqual(result["checked"], 0)

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
            block_actions = [a for a in plan["actions"] if a["kind"] == "managed-block"]
            self.assertEqual(file_actions[0]["classification"], "unmanaged")
            self.assertEqual(file_actions[0]["operation"], "skip")
            self.assertEqual(block_actions[0]["operation"], "skip")

    def test_codex_optional_agents_skill_dir_is_skipped_by_default(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex")
            legacy = root / ".agents" / "skills" / "zotero" / "SKILL.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("optional workspace codex skill\n", encoding="utf-8")

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
            legacy = root / ".agents" / "skills" / "research_digest_wrapper" / "SKILL.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("legacy alias skill\n", encoding="utf-8")
            legacy_extra = legacy.parent / "notes.md"
            legacy_extra.write_text("legacy support file\n", encoding="utf-8")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "research-digest-wrapper"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root), migrate=True)
            file_actions = [a for a in plan["actions"] if a["kind"] == "file"]
            legacy_removal_actions = [a for a in plan["actions"] if a["operation"] == "remove-legacy"]
            self.assertEqual(file_actions[0]["classification"], "legacy")
            self.assertEqual(file_actions[0]["operation"], "migrate-install")
            self.assertEqual(file_actions[0]["install_mode"], "reference")
            self.assertEqual(file_actions[0]["legacy_path"], str(legacy))
            self.assertEqual(len(legacy_removal_actions), 1)
            self.assertEqual(legacy_removal_actions[0]["path"], str(legacy.parent))

            apply_result = apply_plan(root, plan, dry_run=False)
            target = root / ".codex" / "skills" / "research-digest-wrapper" / "SKILL.md"
            self.assertTrue(target.exists())
            self.assertFalse(target.is_symlink())
            self.assertIn("Install mode: reference", target.read_text(encoding="utf-8"))
            self.assertFalse(legacy.parent.exists())
            remove_results = [a for a in apply_result["actions"] if a["operation"] == "remove-legacy"]
            self.assertEqual(len(remove_results), 1)
            self.assertTrue(remove_results[0]["applied"])
            self.assertFalse(remove_results[0]["managed"])
            self.assertEqual(verify(root)["status"], "ok")

    def test_copy_install_mode_writes_regular_skill_files(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            args.install_mode = "copy"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root), install_mode=args.install_mode)
            apply_plan(root, plan, dry_run=False)

            target = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            self.assertTrue(target.exists())
            self.assertFalse(target.is_symlink())
            self.assertIn("Managed by ai-agents-skills", target.read_text(encoding="utf-8"))
            self.assertEqual(verify(root)["status"], "ok")

    def test_codex_existing_canonical_symlink_is_replaced_with_reference_adapter(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex")
            target = root / ".codex" / "skills" / "zotero" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.symlink_to(REPO_ROOT / "canonical" / "skills" / "zotero" / "SKILL.md")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            file_actions = [a for a in plan["actions"] if a["kind"] == "file" and a["artifact_type"] == "skill-file"]
            self.assertEqual(file_actions[0]["classification"], "managed")
            self.assertEqual(file_actions[0]["operation"], "update")
            self.assertEqual(file_actions[0]["install_mode"], "reference")
            apply_plan(root, plan, dry_run=False)

            self.assertTrue(target.exists())
            self.assertFalse(target.is_symlink())
            self.assertIn("Install mode: reference", target.read_text(encoding="utf-8"))
            self.assertEqual(verify(root)["status"], "ok")

    def test_codex_force_symlink_mode_keeps_canonical_link(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            args.install_mode = "symlink"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root), install_mode=args.install_mode)
            file_actions = [a for a in plan["actions"] if a["kind"] == "file" and a["artifact_type"] == "skill-file"]
            self.assertEqual(file_actions[0]["install_mode"], "symlink")
            apply_plan(root, plan, dry_run=False)

            target = root / ".codex" / "skills" / "zotero" / "SKILL.md"
            self.assertTrue(target.exists())
            self.assertTrue(target.is_symlink())
            self.assertIn("canonical/skills/zotero/SKILL.md", str(target.resolve()))
            self.assertEqual(verify(root)["status"], "ok")

    def test_reference_install_mode_writes_thin_adapter_without_support_files(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "deep-research-workflow"
            args.install_mode = "reference"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root), install_mode=args.install_mode)
            apply_plan(root, plan, dry_run=False)

            target = root / ".claude" / "skills" / "deep-research-workflow" / "SKILL.md"
            self.assertTrue(target.exists())
            self.assertFalse(target.is_symlink())
            text = target.read_text(encoding="utf-8")
            self.assertIn("Install mode: reference", text)
            self.assertIn("Canonical skill source:", text)
            self.assertIn("~/", text)
            self.assertIn("canonical/skills/deep-research-workflow/SKILL.md", text)
            self.assertNotIn("/home/", text)
            self.assertFalse((target.parent / "references").exists())
            self.assertEqual(verify(root)["status"], "ok")

    def test_switching_to_reference_removes_managed_support_files(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "deep-research-workflow"
            selected = resolve_skills(args, manifests)
            initial_plan = build_plan(root, manifests, selected, detect_agents(root), install_mode="symlink")
            apply_plan(root, initial_plan, dry_run=False)
            support = root / ".claude" / "skills" / "deep-research-workflow" / "references" / "output-structure.md"
            self.assertTrue(support.is_symlink())

            reference_plan = build_plan(root, manifests, selected, detect_agents(root), install_mode="reference")
            remove_actions = [a for a in reference_plan["actions"] if a["operation"] == "remove-obsolete"]
            self.assertTrue(remove_actions)
            result = apply_plan(root, reference_plan, dry_run=False)

            target = root / ".claude" / "skills" / "deep-research-workflow" / "SKILL.md"
            self.assertTrue(target.exists())
            self.assertFalse(target.is_symlink())
            self.assertFalse(support.exists())
            state = (root / ".ai-agents-skills" / "state.json").read_text(encoding="utf-8")
            self.assertNotIn("output-structure.md", state)
            self.assertEqual(verify(root)["status"], "ok")

            rollback(root, run_id=result["run_id"], dry_run=False)
            self.assertTrue(target.is_symlink())
            self.assertTrue(support.is_symlink())
            self.assertEqual(verify(root)["status"], "ok")

    def test_deepseek_default_symlink_mode_keeps_canonical_link(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "deepseek")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            file_actions = [a for a in plan["actions"] if a["kind"] == "file" and a["artifact_type"] == "skill-file"]
            self.assertEqual(file_actions[0]["install_mode"], "symlink")
            apply_plan(root, plan, dry_run=False)

            target = root / ".deepseek" / "skills" / "zotero" / "SKILL.md"
            self.assertTrue(target.exists())
            self.assertTrue(target.is_symlink())
            self.assertIn("canonical/skills/zotero/SKILL.md", str(target.resolve()))
            self.assertEqual(verify(root)["status"], "ok")

    def test_symlink_failure_falls_back_to_reference_adapter(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            with patch("installer.ai_agents_skills.apply.replace_with_symlink", side_effect=OSError("symlink disabled")):
                apply_plan(root, plan, dry_run=False)

            target = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            self.assertTrue(target.exists())
            self.assertFalse(target.is_symlink())
            text = target.read_text(encoding="utf-8")
            self.assertIn("Install mode: reference", text)
            self.assertIn("Canonical skill source:", text)
            self.assertNotIn("/home/", text)
            self.assertEqual(verify(root)["status"], "ok")

    def test_migrate_removes_legacy_alias_when_canonical_already_installed(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "research-digest-wrapper"
            selected = resolve_skills(args, manifests)
            initial_plan = build_plan(root, manifests, selected, detect_agents(root), migrate=True)
            apply_plan(root, initial_plan, dry_run=False)

            legacy = root / ".agents" / "skills" / "research_digest_wrapper" / "SKILL.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("legacy alias skill\n", encoding="utf-8")

            migrate_plan = build_plan(root, manifests, selected, detect_agents(root), migrate=True)
            file_actions = [a for a in migrate_plan["actions"] if a["kind"] == "file" and a["artifact_type"] == "skill-file"]
            legacy_removal_actions = [a for a in migrate_plan["actions"] if a["operation"] == "remove-legacy"]
            self.assertEqual(file_actions[0]["operation"], "noop")
            self.assertEqual(len(legacy_removal_actions), 1)

            apply_plan(root, migrate_plan, dry_run=False)
            target = root / ".codex" / "skills" / "research-digest-wrapper" / "SKILL.md"
            self.assertFalse(target.is_symlink())
            self.assertIn("Install mode: reference", target.read_text(encoding="utf-8"))
            self.assertFalse(legacy.parent.exists())
            self.assertEqual(verify(root)["status"], "ok")

    def test_rollback_mode_switch_restores_previous_symlink(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            symlink_plan = build_plan(root, manifests, selected, detect_agents(root), install_mode="symlink")
            apply_plan(root, symlink_plan, dry_run=False)
            target = root / ".codex" / "skills" / "zotero" / "SKILL.md"
            self.assertTrue(target.is_symlink())

            auto_plan = build_plan(root, manifests, selected, detect_agents(root), install_mode="auto")
            result = apply_plan(root, auto_plan, dry_run=False)
            self.assertFalse(target.is_symlink())
            self.assertIn("Install mode: reference", target.read_text(encoding="utf-8"))

            rollback(root, run_id=result["run_id"], dry_run=False)
            self.assertTrue(target.is_symlink())
            self.assertIn("canonical/skills/zotero/SKILL.md", str(target.resolve()))
            self.assertEqual(verify(root)["status"], "ok")

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
            instructions = root / ".claude" / "CLAUDE.md"
            self.assertTrue(target.exists())
            self.assertTrue(instructions.exists())
            dry = uninstall(root, skills={"zotero"}, dry_run=True)
            self.assertTrue(dry["dry_run"])
            self.assertTrue(target.exists())
            applied = uninstall(root, skills={"zotero"}, dry_run=False)
            self.assertFalse(applied["dry_run"])
            self.assertFalse(target.exists())
            self.assertFalse(instructions.exists())

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
            instructions = root / ".claude" / "CLAUDE.md"
            self.assertTrue(target.exists())
            self.assertTrue(instructions.exists())
            dry = rollback(root, run_id=result["run_id"], dry_run=True)
            self.assertTrue(dry["dry_run"])
            applied = rollback(root, run_id=result["run_id"], dry_run=False)
            self.assertFalse(applied["dry_run"])
            self.assertFalse(target.exists())
            self.assertFalse(instructions.exists())

    def test_support_files_are_installed_and_verified(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "deep-research-workflow"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            support_actions = [a for a in plan["actions"] if a["artifact_type"] == "skill-support-file"]
            self.assertTrue(support_actions)
            apply_plan(root, plan, dry_run=False)
            support = root / ".claude" / "skills" / "deep-research-workflow" / "references" / "output-structure.md"
            self.assertTrue(support.exists())
            self.assertTrue(support.is_symlink())
            self.assertIn("canonical/skills/deep-research-workflow/references/output-structure.md", str(support.resolve()))
            result = verify(root)
            self.assertEqual(result["status"], "ok")

    def test_verify_instruction_block_ignores_unmanaged_surrounding_text(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            instructions = root / ".claude" / "CLAUDE.md"
            local_path = "/" + "home" + "/exampleuser/private"
            instructions.write_text(f"User note with {local_path} path.\n", encoding="utf-8")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            apply_plan(root, plan, dry_run=False)
            self.assertEqual(verify(root)["status"], "ok")

    def test_uninstall_can_remove_one_skill_without_touching_another(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "source-research,zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            apply_plan(root, plan, dry_run=False)
            uninstall(root, skills={"zotero"}, dry_run=False)

            self.assertFalse((root / ".claude" / "skills" / "zotero" / "SKILL.md").exists())
            self.assertTrue((root / ".claude" / "skills" / "source-research" / "SKILL.md").exists())
            instructions = (root / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertNotIn("ai-agents-skills:zotero", instructions)
            self.assertIn("ai-agents-skills:source-research", instructions)

    def test_artifact_profile_installs_templates_and_personas(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex", "claude", "deepseek")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.no_skills = True
            args.artifact_profile = "workflow-templates,review-personas"
            selected = resolve_skills(args, manifests)
            artifacts = resolve_artifacts(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root), artifacts=artifacts)
            apply_plan(root, plan, dry_run=False)

            self.assertTrue((root / ".codex" / "templates" / "SPEC.md").exists())
            codex_persona = root / ".codex" / "agents" / "literature-scout.toml"
            claude_persona = root / ".claude" / "agents" / "literature-scout.md"
            deepseek_persona = root / ".deepseek" / "agents" / "literature-scout.md"
            self.assertIn("developer_instructions", codex_persona.read_text(encoding="utf-8"))
            self.assertTrue(claude_persona.read_text(encoding="utf-8").lstrip().startswith("---"))
            self.assertIn("reference document", deepseek_persona.read_text(encoding="utf-8"))
            self.assertEqual(verify(root)["status"], "ok")

    def test_entrypoint_alias_requires_backing_skill(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.no_skills = True
            args.artifact = "entrypoint-alias:deep-research"
            plan = build_plan(
                root,
                manifests,
                resolve_skills(args, manifests),
                detect_agents(root),
                artifacts=resolve_artifacts(args, manifests),
            )
            action = plan["actions"][0]
            self.assertEqual(action["operation"], "skip")
            self.assertEqual(action["classification"], "blocked")

            args.no_skills = False
            args.skills = "deep-research-workflow"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root), artifacts=resolve_artifacts(args, manifests))
            apply_plan(root, plan, dry_run=False)
            command = root / ".claude" / "commands" / "deep-research.md"
            self.assertTrue(command.exists())
            self.assertIn("Backing skill", command.read_text(encoding="utf-8"))

    def test_uninstall_can_remove_one_artifact_without_removing_skill(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "deep-research-workflow"
            args.artifact = "entrypoint-alias:deep-research"
            selected = resolve_skills(args, manifests)
            artifacts = resolve_artifacts(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root), artifacts=artifacts)
            apply_plan(root, plan, dry_run=False)

            uninstall(root, artifacts={"entrypoint-alias:deep-research"}, dry_run=False)
            self.assertTrue((root / ".claude" / "skills" / "deep-research-workflow" / "SKILL.md").exists())
            self.assertFalse((root / ".claude" / "commands" / "deep-research.md").exists())

    def test_management_notice_artifact_adds_and_removes_instruction_block(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.no_skills = True
            args.artifact_profile = "repo-management"
            plan = build_plan(
                root,
                manifests,
                resolve_skills(args, manifests),
                detect_agents(root),
                artifacts=resolve_artifacts(args, manifests),
            )
            self.assertEqual(plan["actions"][0]["artifact_type"], "management-notice")
            apply_plan(root, plan, dry_run=False)
            instructions = root / ".codex" / "AGENTS.md"
            self.assertIn("ai-agents-skills:repo-management", instructions.read_text(encoding="utf-8"))
            self.assertEqual(verify(root)["status"], "ok")

            uninstall(root, artifacts={"management-notice:repo-management"}, dry_run=False)
            self.assertFalse(instructions.exists())


class DocsAndLauncherTests(unittest.TestCase):
    def test_generated_docs_include_manifest_skills(self) -> None:
        manifests = load_manifests()
        written = generate_docs(manifests)
        self.assertIn(REPO_ROOT / "README.md", written)
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for skill in ("deep-research-workflow", "source-research", "zotero", "vnthuquan"):
            self.assertIn(f"`{skill}`", readme)
        self.assertNotIn("`openclaw-research`", readme)
        self.assertIn("docs/workflow-overview.md", readme)
        self.assertIn("docs/multi-agent-examples.md", readme)
        self.assertIn("Graph Reconfiguration Specialist", readme)
        self.assertIn("docs/system-profile.md", readme)
        self.assertIn("docs/agent-locations.md", readme)
        self.assertIn("docs/audit-and-migration.md", readme)
        self.assertTrue((REPO_ROOT / "docs" / "agent-locations.md").exists())
        self.assertTrue((REPO_ROOT / "docs" / "audit-and-migration.md").exists())

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

    def test_cli_uninstall_apply_requires_scope(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            code = main([
                "--json",
                "--root",
                str(root),
                "uninstall",
                "--apply",
            ])
            self.assertEqual(code, 1)

    def test_cli_precheck_reports_selected_dependencies(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "precheck",
                    "--skill",
                    "graph-verifier",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["active_skills"], ["graph-verifier"])
            self.assertTrue(payload["dependencies"])
            dependency = next(item for item in payload["dependencies"] if item["dependency"] == "python-runtime")
            self.assertIn("graph-verifier", dependency["required_by"])

    def test_cli_audit_system_reports_unmanaged_and_no_state(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            existing = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            existing.parent.mkdir(parents=True)
            existing.write_text("user-owned zotero\n", encoding="utf-8")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "audit-system",
                    "--skill",
                    "zotero",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["status"], "not-managed")
            self.assertEqual(payload["managed_state"]["artifact_count"], 0)
            coverage = payload["skill_coverage"][0]
            self.assertEqual(coverage["unmanaged_canonical"], ["zotero"])

    def test_cli_with_deps_installs_entrypoint_backing_skill(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            code = main([
                "--json",
                "--root",
                str(root),
                "install",
                "--no-skills",
                "--artifact",
                "entrypoint-alias:deep-research",
                "--with-deps",
                "--apply",
            ])
            self.assertEqual(code, 0)
            self.assertTrue((root / ".claude" / "skills" / "deep-research-workflow" / "SKILL.md").exists())
            self.assertTrue((root / ".claude" / "commands" / "deep-research.md").exists())

    def test_precheck_ignores_dependencies_for_absent_agent_filter(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "--agents",
                    "deepseek",
                    "precheck",
                    "--skill",
                    "graph-verifier",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["detected_agents"], [])
            self.assertEqual(payload["active_skills"], [])
            self.assertEqual(payload["dependencies"], [])

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
