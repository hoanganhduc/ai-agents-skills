from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from installer.ai_agents_skills.apply import apply_plan, replace_with_text
from installer.ai_agents_skills.cli import INSTALL_CONFIRMATION_PHRASE, main
from installer.ai_agents_skills.delegation import PROVIDER_CLI_SPECS
from installer.ai_agents_skills.delegation_dispatch import split_dispatch_command
from installer.ai_agents_skills.discovery import current_platform
from installer.ai_agents_skills.docs import check_docs_current, generate_docs, render_docs
from installer.ai_agents_skills.lifecycle import rollback, uninstall
from installer.ai_agents_skills.manifest import REPO_ROOT, load_manifests
from installer.ai_agents_skills.planner import build_plan
from installer.ai_agents_skills.render import render_artifact_content, render_reference_skill_md
from installer.ai_agents_skills.selectors import artifact_dependency_skills, resolve_artifacts, resolve_skills
from installer.ai_agents_skills.state import artifact_signature, load_state, save_state, write_run_record
from installer.ai_agents_skills.target_surfaces import target_surface_for, target_surface_rows
from installer.ai_agents_skills.target_prechecks import path_style_for_platform
from installer.ai_agents_skills.verify import verify
from tools.import_canonical_skills import assert_admitted_source_file


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


def root_snapshot(root: Path) -> dict[str, tuple[str, str | bytes | None]]:
    snapshot: dict[str, tuple[str, str | bytes | None]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        if rel == ".ai-agents-skills" or rel.startswith(".ai-agents-skills/"):
            continue
        if path.is_symlink():
            snapshot[rel] = ("symlink", os.readlink(path))
        elif path.is_dir():
            snapshot[rel] = ("dir", None)
        elif path.is_file():
            snapshot[rel] = ("file", path.read_bytes())
    return snapshot


class ManifestTests(unittest.TestCase):
    def test_skill_and_profile_membership_is_reciprocal(self) -> None:
        manifests = load_manifests()
        skills = manifests["skills"]["skills"]
        profiles = manifests["profiles"]["profiles"]

        for profile_name, spec in profiles.items():
            profile_skills = spec.get("skills", [])
            if profile_skills == ["*"]:
                continue
            for skill in profile_skills:
                self.assertIn(profile_name, skills[skill]["profiles"])

        explicit_profiles = {
            profile_name: set(spec.get("skills", []))
            for profile_name, spec in profiles.items()
            if spec.get("skills") != ["*"]
        }
        for skill, spec in skills.items():
            for profile_name in spec["profiles"]:
                if profile_name in explicit_profiles:
                    self.assertIn(skill, explicit_profiles[profile_name])

    def test_default_profile_resolves_research_core(self) -> None:
        manifests = load_manifests()
        args = Args()
        selected = resolve_skills(args, manifests)
        self.assertIn("deep-research-workflow", selected)
        self.assertIn("research-briefing", selected)
        self.assertNotIn("tikz-draw", selected)
        self.assertNotIn("axiom-axle-mcp", selected)

    def test_axiom_axle_is_explicit_remote_formal_skill(self) -> None:
        manifests = load_manifests()
        skill = manifests["skills"]["skills"]["axiom-axle-mcp"]

        self.assertEqual(set(skill["profiles"]), {"formal-research-remote", "full-research"})
        self.assertIn("uvx-cli", skill["optional_dependencies"])
        self.assertIn("axle-auth", skill["optional_dependencies"])

        args = Args()
        args.profile = "formal-research"
        self.assertNotIn("axiom-axle-mcp", resolve_skills(args, manifests))
        args.profile = "formal-research-remote"
        self.assertIn("axiom-axle-mcp", resolve_skills(args, manifests))
        args.profile = "full-research"
        self.assertIn("axiom-axle-mcp", resolve_skills(args, manifests))
        args.profile = "research-core"
        self.assertNotIn("axiom-axle-mcp", resolve_skills(args, manifests))

    def test_delegation_manifest_requires_true_cross_provider_policy(self) -> None:
        delegation = load_manifests()["delegation"]
        self.assertEqual(delegation["policy"]["mode"], "prefer")
        self.assertEqual(delegation["policy"]["research_model_policy"], "latest_model_highest_reasoning_required")
        self.assertEqual(delegation["policy"]["active_providers"], ["codex", "claude", "deepseek", "copilot"])
        self.assertEqual(delegation["policy"]["reference_only_providers"], ["openclaw"])
        self.assertEqual(delegation["providers"]["codex"]["recipient_profile"], "codex-like-coding-reviewer")
        self.assertTrue(delegation["nested_delegation"]["enabled"])
        self.assertTrue(delegation["nested_delegation"]["require_same_model_as_manager"])

    def test_deepseek_cli_candidates_prefer_codewhale_rename(self) -> None:
        candidates = PROVIDER_CLI_SPECS["deepseek"]["candidates"]

        for platform in ("linux", "macos", "wsl"):
            self.assertLess(candidates[platform].index("codewhale"), candidates[platform].index("deepseek"))
            self.assertIn("codewhale-tui", candidates[platform])
            self.assertIn("deepseek-cli", candidates[platform])

        windows = candidates["windows"]
        self.assertLess(windows.index("codewhale.cmd"), windows.index("deepseek.cmd"))
        self.assertIn("%APPDATA%\\npm\\codewhale.cmd", windows)
        self.assertIn("%APPDATA%\\npm\\codewhale-tui.cmd", windows)
        self.assertIn("deepseek", windows)

    def test_legacy_alias_resolves_to_canonical(self) -> None:
        manifests = load_manifests()
        args = Args()
        args.skills = "deep-research,research_digest_wrapper,openclaw-research"
        selected = resolve_skills(args, manifests)
        self.assertEqual(selected, ["deep-research-workflow", "research-digest-wrapper", "source-research"])

    def test_writing_workflow_profile_resolves_draft_writing(self) -> None:
        manifests = load_manifests()
        args = Args()
        args.profile = "writing-workflow"
        selected = resolve_skills(args, manifests)
        self.assertEqual(selected, ["draft-writing"])

    def test_writing_workflow_artifacts_resolve_with_backing_dependency(self) -> None:
        manifests = load_manifests()
        args = Args()
        args.no_skills = True
        args.artifact_profile = "writing-workflow"
        artifacts = resolve_artifacts(args, manifests)

        self.assertEqual(
            artifacts,
            [
                ("instruction-doc", "claim-preserving-writing"),
                ("template", "draft-claim-ledger"),
                ("template", "draft-revision-map"),
            ],
        )
        self.assertEqual(artifact_dependency_skills(artifacts, manifests), {"draft-writing"})

    def test_draft_writing_artifact_sources_render_from_expected_directories(self) -> None:
        manifests = load_manifests()
        artifact_specs = manifests["artifacts"]["artifacts"]

        ledger = render_artifact_content(
            "template",
            "draft-claim-ledger",
            artifact_specs["template"]["draft-claim-ledger"],
            "codex",
        )
        instruction = render_artifact_content(
            "instruction-doc",
            "claim-preserving-writing",
            artifact_specs["instruction-doc"]["claim-preserving-writing"],
            "codex",
        )

        self.assertIn("# Draft Claim Ledger", ledger)
        self.assertIn("# Claim-Preserving Writing", instruction)
        self.assertIn("Managed by ai-agents-skills", ledger)
        self.assertIn("Managed by ai-agents-skills", instruction)
        self.assertIn("Generated target: codex", ledger)

    def test_target_surface_support_claims_are_explicit(self) -> None:
        rows = target_surface_rows()

        self.assertGreaterEqual(len(rows), 15)
        self.assertEqual(target_surface_for("codex", "skill-file").support, "supported")
        self.assertEqual(target_surface_for("claude", "entrypoint-alias").mechanism, "native-command")
        self.assertEqual(target_surface_for("deepseek", "runtime-file").claim_basis, "runtime-manifest")
        self.assertEqual(target_surface_for("copilot", "entrypoint-alias").support, "unsupported")
        self.assertEqual(target_surface_for("openclaw", "runtime-file").support, "blocked")

    def test_canonical_import_admission_blocks_runtime_and_secret_files(self) -> None:
        accepted = [
            Path("SKILL.md"),
            Path("references/guide.md"),
            Path("scripts/helper.py"),
        ]
        rejected = [
            Path(".codex/config.toml"),
            Path("hooks/pre-run.sh"),
            Path("scripts/provider_tokens.md"),
            Path("references/local.sqlite"),
            Path("AGENTS.md"),
        ]

        for rel in accepted:
            with self.subTest(rel=rel.as_posix()):
                assert_admitted_source_file(rel)
        for rel in rejected:
            with self.subTest(rel=rel.as_posix()):
                with self.assertRaises(ValueError):
                    assert_admitted_source_file(rel)


class PlanInstallVerifyTests(unittest.TestCase):
    def test_reference_adapter_quotes_yaml_scalars_with_colons(self) -> None:
        content = render_reference_skill_md(
            "deep-research-workflow",
            {"description": "Phased workflow: search, analyze, write."},
            "codex",
            REPO_ROOT / "canonical" / "skills" / "deep-research-workflow" / "SKILL.md",
        )

        self.assertIn('name: "deep-research-workflow"', content)
        self.assertIn('description: "Phased workflow: search, analyze, write."', content)
        self.assertIn('short-description: "Phased workflow: search, analyze, write."', content)

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
            self.assertEqual(checked_skills, {"runtime-runner", "zotero"})

    def test_no_detected_agent_means_no_actions(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, [])
            self.assertEqual(plan["actions"], [])
            self.assertEqual(len(plan["skipped_agents"]), 4)

    def test_symlinked_agent_home_is_skipped_without_writing_target(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp, fake_root() as outside_tmp:
            root = Path(tmp)
            outside = Path(outside_tmp)
            (root / ".claude").symlink_to(outside, target_is_directory=True)
            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)

            from installer.ai_agents_skills.agents import detect_agents

            plan = build_plan(root, manifests, selected, detect_agents(root, ["claude"]))
            result = apply_plan(root, plan, dry_run=False)

            self.assertEqual(result["actions"], [])
            self.assertFalse((outside / "skills" / "zotero" / "SKILL.md").exists())
            skipped = {item["agent"]: item["reason"] for item in plan["skipped_agents"]}
            self.assertEqual(skipped["claude"], "agent home is a symlink")

    def test_apply_refuses_symlinked_state_dir_before_writes(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp, fake_root() as outside_tmp:
            root = Path(tmp)
            outside = Path(outside_tmp)
            create_agent_homes(root, "codex")
            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)

            from installer.ai_agents_skills.agents import detect_agents

            plan = build_plan(root, manifests, selected, detect_agents(root, ["codex"]))
            (root / ".ai-agents-skills").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "installer state"):
                apply_plan(root, plan, dry_run=False)

            self.assertFalse((root / ".codex" / "skills" / "zotero" / "SKILL.md").exists())
            self.assertFalse((outside / "state.json").exists())

    def test_atomic_text_replace_does_not_follow_existing_target_symlink(self) -> None:
        with fake_root() as tmp, fake_root() as outside_tmp:
            root = Path(tmp)
            outside = Path(outside_tmp)
            target = root / "managed.txt"
            victim = outside / "victim.txt"
            victim.write_text("outside\n", encoding="utf-8")
            target.symlink_to(victim)

            replace_with_text(target, "managed\n")

            self.assertEqual(victim.read_text(encoding="utf-8"), "outside\n")
            self.assertFalse(target.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "managed\n")

    def test_apply_preflight_refuses_blocked_parent_before_partial_writes(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex", "claude")
            blocked_parent = root / ".claude" / "skills" / "zotero"
            blocked_parent.parent.mkdir(parents=True)
            blocked_parent.write_text("blocking file\n", encoding="utf-8")
            args = Args()
            args.skills = "zotero"
            args.install_mode = "copy"
            selected = resolve_skills(args, manifests)

            from installer.ai_agents_skills.agents import detect_agents

            plan = build_plan(root, manifests, selected, detect_agents(root, ["codex", "claude"]), install_mode="copy")

            with self.assertRaisesRegex(ValueError, "non-directory parent"):
                apply_plan(root, plan, dry_run=False)

            self.assertFalse((root / ".codex" / "skills" / "zotero" / "SKILL.md").exists())
            self.assertFalse((root / ".ai-agents-skills" / "state.json").exists())

    def test_apply_allows_symlink_prefix_above_selected_root(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            base = Path(tmp)
            real_parent = base / "real"
            linked_parent = base / "linked"
            real_parent.mkdir()
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            root = linked_parent / "home" / "agent"
            create_agent_homes(root, "codex")
            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)

            from installer.ai_agents_skills.agents import detect_agents

            plan = build_plan(root, manifests, selected, detect_agents(root, ["codex"]))
            apply_plan(root, plan, dry_run=False)

            target = root / ".codex" / "skills" / "zotero" / "SKILL.md"
            self.assertTrue(target.exists())
            self.assertFalse(target.is_symlink())
            self.assertEqual(verify(root)["status"], "ok")

    def test_directory_at_skill_file_is_reported_as_conflict(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            target = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            target.mkdir(parents=True)
            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)

            from installer.ai_agents_skills.agents import detect_agents

            plan = build_plan(root, manifests, selected, detect_agents(root, ["claude"]), install_mode="copy")
            file_action = next(action for action in plan["actions"] if action["kind"] == "file")

            self.assertEqual(file_action["classification"], "conflict")
            self.assertEqual(file_action["operation"], "skip")
            self.assertEqual(file_action["reason"], "target path is a directory")

    def test_apply_with_no_detected_agent_writes_no_state_run(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, [])

            result = apply_plan(root, plan, dry_run=False)

            self.assertFalse(result["dry_run"])
            self.assertEqual(result["actions"], [])
            self.assertFalse((root / ".ai-agents-skills" / "state.json").exists())
            self.assertEqual(verify(root)["status"], "no-managed-artifacts")

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

    def test_matching_instruction_block_replans_as_noop(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")

            from installer.ai_agents_skills.agents import detect_agents

            selected = ["zotero"]
            plan = build_plan(root, manifests, selected, detect_agents(root))
            apply_plan(root, plan, dry_run=False)
            next_plan = build_plan(root, manifests, selected, detect_agents(root))
            block_actions = [a for a in next_plan["actions"] if a["kind"] == "managed-block"]

            self.assertEqual(block_actions[0]["classification"], "managed")
            self.assertEqual(block_actions[0]["operation"], "noop")

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

    def test_windows_unmanaged_and_legacy_fixture_requires_adopt_migrate(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex", "claude", "deepseek")
            (root / ".codex" / "skills" / "zotero").mkdir(parents=True)
            (root / ".codex" / "skills" / "zotero" / "SKILL.md").write_text("codex zotero\n", encoding="utf-8")
            (root / ".claude" / "skills" / "zotero").mkdir(parents=True)
            (root / ".claude" / "skills" / "zotero" / "SKILL.md").write_text("claude zotero\n", encoding="utf-8")
            (root / ".deepseek" / "skills" / "zotero").mkdir(parents=True)
            (root / ".deepseek" / "skills" / "zotero" / "SKILL.md").write_text("deepseek zotero\n", encoding="utf-8")
            (root / ".agents" / "skills" / "openclaw-research").mkdir(parents=True)
            (root / ".agents" / "skills" / "openclaw-research" / "SKILL.md").write_text(
                "legacy source research\n",
                encoding="utf-8",
            )
            (root / ".claude" / "skills" / "deep-research").mkdir(parents=True)
            (root / ".claude" / "skills" / "deep-research" / "SKILL.md").write_text(
                "legacy deep research\n",
                encoding="utf-8",
            )

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "source-research,zotero,deep-research-workflow"
            selected = resolve_skills(args, manifests)
            default_plan = build_plan(root, manifests, selected, detect_agents(root))
            default_file_actions = [
                action for action in default_plan["actions"]
                if action["kind"] == "file" and action["artifact_type"] == "skill-file"
            ]
            self.assertIn("skip", {action["operation"] for action in default_file_actions})
            self.assertIn("unmanaged", {action["classification"] for action in default_file_actions})
            self.assertIn("legacy", {action["classification"] for action in default_file_actions})
            self.assertEqual(verify(root)["status"], "no-managed-artifacts")

            reviewed_plan = build_plan(root, manifests, selected, detect_agents(root), adopt=True, migrate=True)
            operations = {action["operation"] for action in reviewed_plan["actions"]}
            self.assertIn("adopt", operations)
            self.assertIn("migrate-install", operations)
            self.assertIn("remove-legacy", operations)
            self.assertFalse((root / ".ai-agents-skills" / "state.json").exists())

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
            self.assertTrue(remove_results[0]["managed"])
            self.assertIsNotNone(remove_results[0]["backup"])
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
            self.assertIn("canonical/skills/zotero/SKILL.md", target.resolve().as_posix())
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
            self.assertIn("canonical/skills/deep-research-workflow/SKILL.md", text)
            self.assertNotIn("\\canonical\\skills\\deep-research-workflow\\SKILL.md", text)
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

    def test_switching_to_reference_preserves_changed_managed_support_file(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "deep-research-workflow"
            selected = resolve_skills(args, manifests)
            initial_plan = build_plan(root, manifests, selected, detect_agents(root), install_mode="copy")
            apply_plan(root, initial_plan, dry_run=False)
            support = root / ".claude" / "skills" / "deep-research-workflow" / "references" / "output-structure.md"
            self.assertTrue(support.exists())
            self.assertFalse(support.is_symlink())
            support.write_text(support.read_text(encoding="utf-8") + "\nuser edit\n", encoding="utf-8")

            reference_plan = build_plan(root, manifests, selected, detect_agents(root), install_mode="reference")
            remove_actions = [a for a in reference_plan["actions"] if a["operation"] == "remove-obsolete"]
            self.assertTrue(remove_actions)
            result = apply_plan(root, reference_plan, dry_run=False)

            removed = [a for a in result["actions"] if a.get("artifact_type") == "skill-support-file"]
            self.assertTrue(removed)
            self.assertEqual(removed[0]["operation"], "skip-conflict")
            self.assertTrue(support.exists())
            state = (root / ".ai-agents-skills" / "state.json").read_text(encoding="utf-8")
            self.assertIn("output-structure.md", state)

    def test_deepseek_default_reference_mode_writes_adapter(self) -> None:
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
            self.assertEqual(file_actions[0]["install_mode"], "reference")
            apply_plan(root, plan, dry_run=False)

            target = root / ".deepseek" / "skills" / "zotero" / "SKILL.md"
            self.assertTrue(target.exists())
            self.assertFalse(target.is_symlink())
            text = target.read_text(encoding="utf-8")
            self.assertIn("Install mode: reference", text)
            self.assertIn("canonical/skills/zotero/SKILL.md", text)
            self.assertNotIn("\\canonical\\skills\\zotero\\SKILL.md", text)
            self.assertEqual(verify(root)["status"], "ok")

    def test_openclaw_is_explicit_only_and_fake_root_eligible(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "openclaw")

            from installer.ai_agents_skills.agents import detect_agents

            self.assertEqual(detect_agents(root), [])
            self.assertEqual([agent.name for agent in detect_agents(root, ["openclaw"])], ["openclaw"])

    def test_openclaw_fake_root_installs_skill_file_without_instruction_block(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "openclaw")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "model-router"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root, ["openclaw"]))
            file_actions = [a for a in plan["actions"] if a["kind"] == "file" and a["artifact_type"] == "skill-file"]
            block_actions = [a for a in plan["actions"] if a["kind"] == "managed-block"]

            self.assertEqual(len(file_actions), 1)
            self.assertEqual(file_actions[0]["agent"], "openclaw")
            self.assertEqual(file_actions[0]["install_mode"], "copy")
            self.assertEqual(file_actions[0]["operation"], "create")
            self.assertFalse(block_actions)

            apply_plan(root, plan, dry_run=False)

            target = root / ".openclaw" / "skills" / "model-router" / "SKILL.md"
            self.assertTrue(target.exists())
            self.assertFalse(target.is_symlink())
            self.assertIn("Generated target: openclaw", target.read_text(encoding="utf-8"))
            self.assertFalse((root / ".openclaw" / "AGENTS.md").exists())
            self.assertEqual(verify(root, agent_filter={"openclaw"})["status"], "ok")

            uninstall(root, agents={"openclaw"}, dry_run=False)
            self.assertFalse(target.exists())

    def test_openclaw_fake_root_can_plan_draft_writing_skill_only(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "openclaw")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.profile = "writing-workflow"
            selected = resolve_skills(args, manifests)
            plan = build_plan(
                root,
                manifests,
                selected,
                detect_agents(root, ["openclaw"]),
                requested_agents=["openclaw"],
            )
            file_actions = [
                action for action in plan["actions"]
                if action["kind"] == "file" and action["artifact_type"] == "skill-file"
            ]
            block_actions = [action for action in plan["actions"] if action["kind"] == "managed-block"]

            self.assertEqual(len(file_actions), 1)
            self.assertEqual(file_actions[0]["agent"], "openclaw")
            self.assertEqual(file_actions[0]["operation"], "create")
            self.assertEqual(file_actions[0]["install_mode"], "copy")
            self.assertFalse(block_actions)

            apply_plan(root, plan, dry_run=False)
            target = root / ".openclaw" / "skills" / "draft-writing" / "SKILL.md"
            self.assertTrue(target.exists())
            self.assertFalse(target.is_symlink())
            self.assertIn("Generated target: openclaw", target.read_text(encoding="utf-8"))
            self.assertEqual(verify(root, agent_filter={"openclaw"})["status"], "ok")

    def test_openclaw_existing_unmanaged_skill_is_skipped_by_default(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "openclaw")
            existing = root / ".openclaw" / "skills" / "model-router" / "SKILL.md"
            existing.parent.mkdir(parents=True)
            existing.write_text("user-owned OpenClaw skill\n", encoding="utf-8")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "model-router"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root, ["openclaw"]))
            file_action = next(action for action in plan["actions"] if action["artifact_type"] == "skill-file")

            self.assertEqual(file_action["classification"], "unmanaged")
            self.assertEqual(file_action["operation"], "skip")
            self.assertFalse([a for a in plan["actions"] if a["kind"] == "managed-block"])

    def test_openclaw_runtime_backed_skill_is_blocked_without_runtime_actions(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "openclaw")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "graph-verifier"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root, ["openclaw"]))
            file_action = next(action for action in plan["actions"] if action["artifact_type"] == "skill-file")

            self.assertEqual(file_action["classification"], "blocked")
            self.assertEqual(file_action["operation"], "skip")
            self.assertEqual(file_action["reason"], "OpenClaw runtime-backed skills require neutral runtime evidence")
            self.assertFalse([action for action in plan["actions"] if action["artifact_type"] == "runtime-file"])
            self.assertFalse([action for action in plan["actions"] if action["kind"] == "managed-block"])

    def test_openclaw_symlink_and_reference_modes_are_blocked(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "openclaw")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "model-router"
            selected = resolve_skills(args, manifests)
            for mode in ("symlink", "reference"):
                with self.subTest(mode=mode):
                    plan = build_plan(root, manifests, selected, detect_agents(root, ["openclaw"]), install_mode=mode)
                    file_action = next(action for action in plan["actions"] if action["artifact_type"] == "skill-file")
                    self.assertEqual(file_action["classification"], "blocked")
                    self.assertEqual(file_action["operation"], "skip")
                    self.assertEqual(file_action["reason"], f"OpenClaw {mode} install mode requires native target evidence")

    def test_openclaw_adopt_backup_replace_and_migrate_are_blocked(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "openclaw")
            existing = root / ".openclaw" / "skills" / "model-router" / "SKILL.md"
            existing.parent.mkdir(parents=True)
            existing.write_text("user-owned OpenClaw skill\n", encoding="utf-8")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "model-router"
            selected = resolve_skills(args, manifests)
            for kwargs in ({"adopt": True}, {"backup_replace": True}):
                with self.subTest(kwargs=kwargs):
                    plan = build_plan(root, manifests, selected, detect_agents(root, ["openclaw"]), **kwargs)
                    file_action = next(action for action in plan["actions"] if action["artifact_type"] == "skill-file")
                    self.assertEqual(file_action["classification"], "blocked")
                    self.assertEqual(file_action["operation"], "skip")
                    self.assertEqual(
                        file_action["reason"],
                        "OpenClaw adopt, backup-replace, and migrate require native target evidence",
                    )
            existing.unlink()
            legacy = root / ".openclaw" / "skills" / "smart_model_router" / "SKILL.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("legacy OpenClaw skill\n", encoding="utf-8")

            plan = build_plan(root, manifests, selected, detect_agents(root, ["openclaw"]), migrate=True)
            file_action = next(action for action in plan["actions"] if action["artifact_type"] == "skill-file")
            self.assertEqual(file_action["classification"], "blocked")
            self.assertEqual(file_action["operation"], "skip")
            self.assertEqual(
                file_action["reason"],
                "OpenClaw adopt, backup-replace, and migrate require native target evidence",
            )

    def test_openclaw_support_files_require_manifest_metadata(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "openclaw")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "source-research"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root, ["openclaw"]))
            file_action = next(action for action in plan["actions"] if action["artifact_type"] == "skill-file")
            support_actions = [a for a in plan["actions"] if a["artifact_type"] == "skill-support-file"]

            self.assertEqual(file_action["classification"], "blocked")
            self.assertEqual(file_action["operation"], "skip")
            self.assertEqual(file_action["reason"], "OpenClaw support files require target-support-file manifest metadata")
            self.assertFalse(support_actions)

            apply_plan(root, plan, dry_run=False)
            target = root / ".openclaw" / "skills" / "source-research" / "SKILL.md"
            support = root / ".openclaw" / "skills" / "source-research" / "references" / "specialist-subagents.md"
            self.assertFalse(target.exists())
            self.assertFalse(support.exists())
            self.assertEqual(verify(root, agent_filter={"openclaw"})["status"], "no-managed-artifacts")

    def test_openclaw_real_system_write_guards_fail_closed(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "openclaw")

            from installer.ai_agents_skills.agents import target_for

            args = Args()
            args.skills = "source-research"
            selected = resolve_skills(args, manifests)
            with patch("installer.ai_agents_skills.planner.looks_like_real_system_root", return_value=True):
                plan = build_plan(root, manifests, selected, [target_for(root, "openclaw")])
            self.assertEqual(plan["actions"], [])
            self.assertIn(
                {
                    "agent": "openclaw",
                    "reason": "OpenClaw target writes are fake-root only before native target evidence",
                },
                plan["skipped_agents"],
            )

            action = {
                "kind": "file",
                "agent": "openclaw",
                "skill": "source-research",
                "path": str(root / ".openclaw" / "skills" / "source-research" / "SKILL.md"),
                "content": "blocked\n",
                "classification": "missing",
                "operation": "create",
                "artifact_type": "skill-file",
                "install_mode": "copy",
            }
            with patch("installer.ai_agents_skills.apply.looks_like_real_system_root", return_value=True):
                with self.assertRaisesRegex(ValueError, "real-system OpenClaw"):
                    apply_plan(root, {"actions": [action], "skipped_agents": [], "root": str(root)}, dry_run=True)
            self.assertFalse((root / ".openclaw" / "skills" / "source-research" / "SKILL.md").exists())

    def test_all_default_agent_fake_root_detects_available_homes(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex", "claude", "deepseek", "copilot")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            self.assertEqual(plan["skipped_agents"], [])
            modes = {
                action["agent"]: action["install_mode"]
                for action in plan["actions"]
                if action["kind"] == "file" and action["artifact_type"] == "skill-file"
            }
            self.assertEqual(
                modes,
                {
                    "codex": "reference",
                    "claude": "symlink",
                    "deepseek": "reference",
                    "copilot": "reference",
                },
            )
            skill_actions = [
                action for action in plan["actions"]
                if action["kind"] == "file" and action["artifact_type"] == "skill-file"
            ]
            self.assertTrue(all(action.get("mode_reason") for action in skill_actions))
            self.assertTrue(all(action.get("capability_evidence") for action in skill_actions))

    def test_all_agent_fake_root_install_verify_uninstall_lifecycle(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex", "claude", "deepseek")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "source-research,zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            apply_plan(root, plan, dry_run=False)

            self.assertEqual(verify(root)["status"], "ok")
            codex_skill = root / ".codex" / "skills" / "zotero" / "SKILL.md"
            claude_skill = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            deepseek_skill = root / ".deepseek" / "skills" / "zotero" / "SKILL.md"
            self.assertFalse(codex_skill.is_symlink())
            self.assertTrue(claude_skill.is_symlink())
            self.assertFalse(deepseek_skill.is_symlink())

            uninstall(root, dry_run=False)

            self.assertEqual(verify(root)["status"], "no-managed-artifacts")
            self.assertFalse((root / ".codex" / "AGENTS.md").exists())
            self.assertFalse((root / ".claude" / "CLAUDE.md").exists())
            self.assertFalse((root / ".deepseek" / "AGENTS.md").exists())

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
            self.assertIn("canonical/skills/zotero/SKILL.md", target.resolve().as_posix())
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

    def test_multi_skill_rollback_removes_shared_instruction_blocks_atomically(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "source-research,zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            result = apply_plan(root, plan, dry_run=False)
            self.assertEqual(verify(root)["status"], "ok")

            rollback(root, run_id=result["run_id"], dry_run=False)

            self.assertFalse((root / ".claude" / "skills" / "source-research" / "SKILL.md").exists())
            self.assertFalse((root / ".claude" / "skills" / "zotero" / "SKILL.md").exists())
            self.assertFalse((root / ".claude" / "CLAUDE.md").exists())
            self.assertEqual(verify(root)["status"], "no-managed-artifacts")

    def test_rollback_conflict_preflight_prevents_partial_mutation(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "source-research,zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            result = apply_plan(root, plan, dry_run=False)
            before = root_snapshot(root)
            instructions = root / ".claude" / "CLAUDE.md"
            instructions.write_text(instructions.read_text(encoding="utf-8") + "\nuser edit\n", encoding="utf-8")
            after_user_edit = root_snapshot(root)

            with self.assertRaises(ValueError):
                rollback(root, run_id=result["run_id"], dry_run=False)

            self.assertEqual(root_snapshot(root), after_user_edit)
            self.assertNotEqual(before, after_user_edit)
            self.assertEqual(verify(root)["status"], "ok")

    def test_rollback_rejects_invalid_or_unlisted_run_ids(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            save_state(root, {"schema_version": 1, "artifacts": [], "runs": []})

            with self.assertRaisesRegex(ValueError, "invalid run id"):
                rollback(root, run_id="../outside", dry_run=True)

            write_run_record(root, "unlisted-run", [])
            with self.assertRaisesRegex(ValueError, "unknown run id"):
                rollback(root, run_id="unlisted-run", dry_run=True)

    def test_rollback_refuses_run_artifact_outside_root_before_mutation(self) -> None:
        with fake_root() as tmp, tempfile.TemporaryDirectory() as outside_tmp:
            root = Path(tmp)
            outside = Path(outside_tmp) / "outside.txt"
            outside.write_text("do not remove\n", encoding="utf-8")
            run_id = "rollback-boundary"
            save_state(root, {"schema_version": 1, "artifacts": [], "runs": [{"run_id": run_id, "action_count": 1}]})
            write_run_record(
                root,
                run_id,
                [
                    {
                        "key": "tampered",
                        "run_id": run_id,
                        "agent": "claude",
                        "skill": "zotero",
                        "artifact": str(outside),
                        "artifact_type": "skill-file",
                        "managed": True,
                        "applied": True,
                        "installed_signature": artifact_signature(outside),
                    }
                ],
            )

            with self.assertRaises(ValueError):
                rollback(root, run_id=run_id, dry_run=False)

            self.assertEqual(outside.read_text(encoding="utf-8"), "do not remove\n")

    def test_rollback_refuses_backup_outside_installer_state_before_mutation(self) -> None:
        with fake_root() as tmp, tempfile.TemporaryDirectory() as outside_tmp:
            root = Path(tmp)
            target = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text("managed\n", encoding="utf-8")
            outside_backup = Path(outside_tmp) / "backup.txt"
            outside_backup.write_text("outside backup\n", encoding="utf-8")
            run_id = "rollback-backup-boundary"
            save_state(root, {"schema_version": 1, "artifacts": [], "runs": [{"run_id": run_id, "action_count": 1}]})
            write_run_record(
                root,
                run_id,
                [
                    {
                        "key": "tampered",
                        "run_id": run_id,
                        "agent": "claude",
                        "skill": "zotero",
                        "artifact": str(target),
                        "artifact_type": "skill-file",
                        "managed": True,
                        "applied": True,
                        "backup": str(outside_backup),
                        "installed_signature": artifact_signature(target),
                    }
                ],
            )

            with self.assertRaises(ValueError):
                rollback(root, run_id=run_id, dry_run=False)

            self.assertEqual(target.read_text(encoding="utf-8"), "managed\n")

    def test_instruction_block_lifecycle_refuses_symlinked_instruction_file(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp, tempfile.TemporaryDirectory() as outside_tmp:
            root = Path(tmp)
            outside = Path(outside_tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            result = apply_plan(root, plan, dry_run=False)
            instructions = root / ".claude" / "CLAUDE.md"
            outside_instructions = outside / "CLAUDE.md"
            original_text = instructions.read_text(encoding="utf-8")
            outside_instructions.write_text(original_text, encoding="utf-8")
            instructions.unlink()
            try:
                instructions.symlink_to(outside_instructions)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"file symlink unavailable: {exc}")

            verification = verify(root)
            self.assertEqual(verification["status"], "failed")
            block_result = next(
                item for item in verification["results"]
                if item["artifact_type"] == "instruction-block"
            )
            checks = {item["name"]: item["ok"] for item in block_result["checks"]}
            self.assertFalse(checks["instruction-regular-file"])

            dry_uninstall = uninstall(root, skills={"zotero"}, dry_run=True)
            block_action = next(
                item for item in dry_uninstall["actions"]
                if item["artifact_type"] == "instruction-block"
            )
            self.assertEqual(block_action["operation"], "skip-conflict")
            self.assertEqual(block_action["reason"], "instruction file is symlinked")

            with self.assertRaisesRegex(ValueError, "instruction file is symlinked"):
                rollback(root, run_id=result["run_id"], dry_run=False)
            self.assertEqual(outside_instructions.read_text(encoding="utf-8"), original_text)

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
            self.assertIn("canonical/skills/deep-research-workflow/references/output-structure.md", support.resolve().as_posix())
            result = verify(root)
            self.assertEqual(result["status"], "ok")

    def test_windows_plan_blocks_posix_support_helpers(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "self-improving-agent"
            selected = resolve_skills(args, manifests)
            plan = build_plan(
                root,
                manifests,
                selected,
                detect_agents(root, ["claude"]),
                install_mode="copy",
                runtime_profile="none",
                platform="windows",
            )
            blocked_shell = [
                action for action in plan["actions"]
                if action.get("artifact_type") == "skill-support-file"
                and str(action.get("source_path", "")).endswith(".sh")
            ]

            self.assertTrue(blocked_shell)
            self.assertEqual({action["classification"] for action in blocked_shell}, {"blocked"})
            self.assertEqual({action["operation"] for action in blocked_shell}, {"skip"})
            self.assertEqual(
                {action["reason"] for action in blocked_shell},
                {"POSIX shell support file is not installed for Windows targets"},
            )

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

    def test_malformed_instruction_block_is_skipped_as_conflict(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            instructions = root / ".claude" / "CLAUDE.md"
            instructions.write_text("<!-- ai-agents-skills:zotero:start -->\ntruncated\n", encoding="utf-8")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root))
            block_action = next(action for action in plan["actions"] if action["kind"] == "managed-block")

            self.assertEqual(block_action["classification"], "conflict")
            self.assertEqual(block_action["operation"], "skip")
            self.assertEqual(block_action["reason"], "managed instruction block is malformed or duplicated")

    def test_verify_detects_changed_managed_instruction_block(self) -> None:
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
            instructions = root / ".claude" / "CLAUDE.md"
            instructions.write_text(
                instructions.read_text(encoding="utf-8").replace(
                    "<!-- ai-agents-skills:zotero:end -->",
                    "tampered managed content\n<!-- ai-agents-skills:zotero:end -->",
                ),
                encoding="utf-8",
            )

            result = verify(root)

            self.assertEqual(result["status"], "failed")
            block_result = next(item for item in result["results"] if item["artifact_type"] == "instruction-block")
            checks = {check["name"]: check["ok"] for check in block_result["checks"]}
            self.assertFalse(checks["managed-block-match"])

    def test_verify_detects_changed_managed_file_signature(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root), install_mode="copy")
            apply_plan(root, plan, dry_run=False)
            target = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            target.write_text(target.read_text(encoding="utf-8") + "\nuser edit\n", encoding="utf-8")

            result = verify(root)

            self.assertEqual(result["status"], "failed")
            changed = next(item for item in result["results"] if item["artifact"] == str(target))
            checks = {check["name"]: check["ok"] for check in changed["checks"]}
            self.assertFalse(checks["installed-signature-match"])

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

    def test_uninstall_preserves_unmanaged_extra_files_inside_skill_dir(self) -> None:
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

            extra = root / ".claude" / "skills" / "zotero" / "user-note.txt"
            extra.write_text("user-owned note\n", encoding="utf-8")
            uninstall(root, skills={"zotero"}, dry_run=False)

            self.assertFalse((root / ".claude" / "skills" / "zotero" / "SKILL.md").exists())
            self.assertTrue(extra.exists())

    def test_uninstall_restores_backup_replaced_preinstall_snapshot(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            existing = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            existing.parent.mkdir(parents=True)
            existing.write_text("user-owned zotero\n", encoding="utf-8")
            instructions = root / ".claude" / "CLAUDE.md"
            instructions.write_text("user instructions\n", encoding="utf-8")
            before = root_snapshot(root)

            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(
                root,
                manifests,
                selected,
                detect_agents(root),
                backup_replace=True,
                runtime_profile="none",
            )
            apply_plan(root, plan, dry_run=False)
            uninstall(root, skills={"zotero"}, dry_run=False)

            self.assertEqual(root_snapshot(root), before)

    def test_uninstall_preserves_changed_managed_skill_file(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root), install_mode="copy")
            apply_plan(root, plan, dry_run=False)

            target = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            target.write_text("user edit after install\n", encoding="utf-8")
            result = uninstall(root, skills={"zotero"}, dry_run=False)

            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "user edit after install\n")
            self.assertIn("skip-conflict", {action["operation"] for action in result["actions"]})
            self.assertNotEqual(verify(root)["status"], "no-managed-artifacts")

    def test_uninstall_unmanages_adopted_file_without_deleting_it(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            existing = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            existing.parent.mkdir(parents=True)
            existing.write_text("user-owned zotero\n", encoding="utf-8")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(
                root,
                manifests,
                selected,
                detect_agents(root),
                adopt=True,
                runtime_profile="none",
            )
            apply_plan(root, plan, dry_run=False)
            uninstall(root, skills={"zotero"}, dry_run=False)

            self.assertTrue(existing.exists())
            self.assertEqual(existing.read_text(encoding="utf-8"), "user-owned zotero\n")
            self.assertEqual(verify(root)["status"], "no-managed-artifacts")

    def test_uninstall_removes_managed_block_and_preserves_user_text(self) -> None:
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

            instructions = root / ".claude" / "CLAUDE.md"
            instructions.write_text(
                "prefix user text\n" + instructions.read_text(encoding="utf-8") + "suffix user text\n",
                encoding="utf-8",
            )
            uninstall(root, skills={"zotero"}, dry_run=False)

            text = instructions.read_text(encoding="utf-8")
            self.assertIn("prefix user text", text)
            self.assertIn("suffix user text", text)
            self.assertNotIn("ai-agents-skills:zotero", text)

    def test_uninstall_restores_tombstoned_backup_with_missing_parent(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            support = root / ".claude" / "skills" / "deep-research-workflow" / "references" / "output-structure.md"
            support.parent.mkdir(parents=True)
            support.write_text("user-owned support\n", encoding="utf-8")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "deep-research-workflow"
            selected = resolve_skills(args, manifests)
            plan = build_plan(
                root,
                manifests,
                selected,
                detect_agents(root),
                backup_replace=True,
                install_mode="copy",
            )
            apply_plan(root, plan, dry_run=False)

            plan = build_plan(root, manifests, selected, detect_agents(root), install_mode="reference")
            apply_plan(root, plan, dry_run=False)
            self.assertFalse(support.exists())
            support.parent.rmdir()
            self.assertFalse(support.parent.exists())

            uninstall(root, skills={"deep-research-workflow"}, dry_run=False)

            self.assertTrue(support.exists())
            self.assertEqual(support.read_text(encoding="utf-8"), "user-owned support\n")

    def test_uninstall_refuses_state_artifact_outside_root(self) -> None:
        with fake_root() as tmp, tempfile.TemporaryDirectory() as outside_tmp:
            root = Path(tmp)
            outside = Path(outside_tmp) / "outside.txt"
            outside.write_text("do not remove\n", encoding="utf-8")
            save_state(
                root,
                {
                    "schema_version": 1,
                    "runs": [],
                    "artifacts": [
                        {
                            "key": "tampered",
                            "agent": "claude",
                            "skill": "zotero",
                            "artifact": str(outside),
                            "artifact_type": "skill-file",
                            "managed": True,
                            "uninstall": {"action": "delete-created"},
                            "installed_signature": {
                                "exists": True,
                                "kind": "file",
                                "hash": "sha256:tampered",
                            },
                        }
                    ],
                },
            )

            result = uninstall(root, skills={"zotero"}, dry_run=False)

            self.assertEqual(outside.read_text(encoding="utf-8"), "do not remove\n")
            self.assertEqual(result["actions"][0]["operation"], "skip-conflict")
            state = json.loads((root / ".ai-agents-skills" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["artifacts"][0]["key"], "tampered")

    def test_uninstall_refuses_unmanaged_state_record(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            target = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text("user-owned file\n", encoding="utf-8")
            save_state(
                root,
                {
                    "schema_version": 1,
                    "runs": [],
                    "artifacts": [
                        {
                            "key": "tampered",
                            "agent": "claude",
                            "skill": "zotero",
                            "artifact": str(target),
                            "artifact_type": "skill-file",
                            "uninstall": {"action": "delete-created"},
                            "installed_signature": artifact_signature(target),
                        }
                    ],
                },
            )

            result = uninstall(root, skills={"zotero"}, dry_run=False)

            self.assertEqual(target.read_text(encoding="utf-8"), "user-owned file\n")
            self.assertEqual(result["actions"][0]["operation"], "skip-conflict")
            self.assertEqual(result["actions"][0]["reason"], "state record is not marked managed")

    def test_adopted_existing_file_verifies_by_recorded_hash(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            existing = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            existing.parent.mkdir(parents=True)
            existing.write_text("user-owned zotero\n", encoding="utf-8")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root), adopt=True)
            result = apply_plan(root, plan, dry_run=False)
            adopted = [
                action for action in result["actions"]
                if action["operation"] == "adopt" and action["artifact_type"] == "skill-file"
            ]
            self.assertTrue(adopted)
            self.assertTrue(adopted[0]["adopted"])
            self.assertEqual(verify(root)["status"], "ok")

            existing.write_text("changed user-owned zotero\n", encoding="utf-8")
            self.assertEqual(verify(root)["status"], "failed")

    def test_migration_rollback_restores_legacy_alias_directory(self) -> None:
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
            result = apply_plan(root, plan, dry_run=False)
            self.assertFalse(legacy.parent.exists())

            rollback(root, run_id=result["run_id"], dry_run=False)
            self.assertTrue(legacy.exists())
            self.assertEqual(legacy.read_text(encoding="utf-8"), "legacy alias skill\n")
            self.assertTrue(legacy_extra.exists())

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

    def test_writing_workflow_profile_installs_skill_templates_and_instruction_docs(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex", "claude", "deepseek")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.profile = "writing-workflow"
            args.artifact_profile = "writing-workflow"
            selected = resolve_skills(args, manifests)
            artifacts = resolve_artifacts(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root), artifacts=artifacts)
            apply_plan(root, plan, dry_run=False)

            self.assertTrue((root / ".codex" / "skills" / "draft-writing" / "SKILL.md").exists())
            self.assertTrue((root / ".codex" / "templates" / "draft-claim-ledger.md").exists())
            self.assertTrue((root / ".claude" / "templates" / "draft-revision-map.md").exists())
            self.assertTrue((root / ".deepseek" / "instructions" / "claim-preserving-writing.md").exists())
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

    def test_management_notice_conflict_is_skipped(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex")
            instructions = root / ".codex" / "AGENTS.md"
            instructions.write_text(
                "<!-- ai-agents-skills:repo-management:start -->\n"
                "first\n"
                "<!-- ai-agents-skills:repo-management:end -->\n"
                "<!-- ai-agents-skills:repo-management:start -->\n"
                "second\n"
                "<!-- ai-agents-skills:repo-management:end -->\n",
                encoding="utf-8",
            )
            from installer.ai_agents_skills.agents import detect_agents

            action = build_plan(
                root,
                manifests,
                [],
                detect_agents(root),
                artifacts=[("management-notice", "repo-management")],
            )["actions"][0]

            self.assertEqual(action["classification"], "conflict")
            self.assertEqual(action["operation"], "skip")
            self.assertIn("malformed or duplicated", action["reason"])

    def test_uninstall_refuses_tampered_backup_restore(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex")
            target = root / ".codex" / "skills" / "graph-verifier" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text("original user content\n", encoding="utf-8")
            from installer.ai_agents_skills.agents import detect_agents

            plan = build_plan(
                root,
                manifests,
                ["graph-verifier"],
                detect_agents(root, ["codex"]),
                backup_replace=True,
            )
            apply_result = apply_plan(root, plan, dry_run=False)
            skill_record = next(
                item for item in apply_result["actions"]
                if item.get("artifact_type") == "skill-file"
            )
            Path(skill_record["backup"]).write_text("tampered backup\n", encoding="utf-8")

            result = uninstall(root, skills={"graph-verifier"}, dry_run=False)
            action = next(
                item for item in result["actions"]
                if item.get("artifact_type") == "skill-file"
            )

            self.assertEqual(action["operation"], "skip-conflict")
            self.assertIn("backup signature changed", action["reason"])
            self.assertNotEqual(target.read_text(encoding="utf-8"), "tampered backup\n")


class DocsAndLauncherTests(unittest.TestCase):
    def assert_target_precheck_schema(self, target: dict[str, object]) -> None:
        expected = {
            "target",
            "status",
            "platform",
            "path_style",
            "target_home",
            "skills_dir",
            "instructions_file",
            "artifact_dirs",
            "optional_skills_dirs",
            "legacy_skills_dirs",
            "capabilities",
            "read_policy",
            "home_status",
            "notes",
        }
        self.assertFalse(expected - set(target), expected - set(target))

    def test_generated_docs_include_manifest_skills(self) -> None:
        manifests = load_manifests()
        rendered = render_docs(manifests)
        self.assertIn(REPO_ROOT / "README.md", rendered)
        readme = rendered[REPO_ROOT / "README.md"]
        for skill in ("deep-research-workflow", "draft-writing", "source-research", "zotero", "vnthuquan"):
            self.assertIn(f"`{skill}`", readme)
        self.assertIn("`cross-provider-delegation`", readme)
        self.assertIn("`template:cross-provider-research-panel`", readme)
        self.assertNotIn("`openclaw-research`", readme)
        self.assertIn("docs/workflow-overview.md", readme)
        self.assertIn("docs/multi-agent-examples.md", readme)
        self.assertIn("Graph Reconfiguration Specialist", readme)
        self.assertIn("docs/system-profile.md", readme)
        self.assertIn("docs/agent-locations.md", readme)
        self.assertIn("docs/surfaces.md", readme)
        self.assertIn("docs/audit-and-migration.md", readme)
        self.assertIn(REPO_ROOT / "docs" / "agent-locations.md", rendered)
        self.assertIn(REPO_ROOT / "docs" / "surfaces.md", rendered)
        self.assertIn(REPO_ROOT / "docs" / "audit-and-migration.md", rendered)

    def test_generated_surfaces_docs_define_support_claim_basis(self) -> None:
        rendered = render_docs(load_manifests())
        text = rendered[REPO_ROOT / "docs" / "surfaces.md"]

        self.assertIn("# Target Surface Support Matrix", text)
        self.assertIn("Support claims are intentionally separate from skill selection", text)
        self.assertIn("| `openclaw` | `runtime-file` | `blocked` | `unsupported` |", text)
        self.assertIn("Do not infer runtime support from `supported_agents` alone", text)

    def test_generated_root_and_sphinx_docs_do_not_drift(self) -> None:
        tracked = [
            REPO_ROOT / "README.md",
            *((REPO_ROOT / "docs").glob("*.md")),
            *((REPO_ROOT / "docs" / "source").glob("*.md")),
        ]
        before = {path: path.read_text(encoding="utf-8") for path in tracked}
        result = check_docs_current(load_manifests())
        after = {path: path.read_text(encoding="utf-8") for path in tracked}
        self.assertEqual(result["status"], "ok")
        self.assertEqual(after, before)
        shared_docs = sorted(
            path.name
            for path in (REPO_ROOT / "docs").glob("*.md")
            if (REPO_ROOT / "docs" / "source" / path.name).exists()
        )
        self.assertTrue(shared_docs)
        for name in shared_docs:
            with self.subTest(name=name):
                root_text = (REPO_ROOT / "docs" / name).read_text(encoding="utf-8")
                source_text = (REPO_ROOT / "docs" / "source" / name).read_text(encoding="utf-8")
                self.assertEqual(root_text, source_text)

    def test_cli_docs_check_is_non_mutating(self) -> None:
        tracked = [REPO_ROOT / "README.md", *((REPO_ROOT / "docs").glob("*.md"))]
        before = {path: path.read_bytes() for path in tracked}
        stream = io.StringIO()

        with contextlib.redirect_stdout(stream):
            code = main(["--json", "docs-check"])

        payload = json.loads(stream.getvalue())
        after = {path: path.read_bytes() for path in tracked}
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(after, before)

    def test_source_only_docs_are_intentional(self) -> None:
        root_docs = {path.name for path in (REPO_ROOT / "docs").glob("*.md")}
        source_docs = {path.name for path in (REPO_ROOT / "docs" / "source").glob("*.md")}

        source_only_docs = source_docs - root_docs
        self.assertEqual(source_only_docs, {"index.md", "overview.md"})

        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/source/index.md", readme)
        self.assertIn("docs/source/overview.md", readme)

    def test_verification_docs_describe_post_install_smoke(self) -> None:
        text = (REPO_ROOT / "docs" / "source" / "verification.md").read_text(encoding="utf-8")
        self.assertIn("Post-install smoke", text)
        self.assertIn("--post-install-smoke strict", text)
        self.assertIn("runtime smoke for installed runtime-backed skills", text.replace("\n  ", " "))
        self.assertIn("Runtime smoke coverage", text)
        self.assertIn("manual-native", text)

    def test_make_bat_prefers_pwsh_and_forwards_all_args(self) -> None:
        text = (REPO_ROOT / "make.bat").read_text(encoding="utf-8")
        self.assertIn("where pwsh", text)
        self.assertIn("where powershell.exe", text)
        self.assertIn("%*", text)
        self.assertIn('if "%~1"=="help"', text)
        self.assertIn("list-artifacts", text)
        self.assertIn("runtime-smoke", text)
        self.assertIn("docs-check", text)
        self.assertIn("static-check", text)
        self.assertIn('if /I "%~1"=="sanitize-check"', text)
        self.assertIn('if /I "%~1"=="docs-check"', text)
        self.assertIn('if /I "%~1"=="static-check"', text)
        self.assertIn('if /I "%~1"=="test"', text)
        self.assertIn("--run-python tools/sanitization_check.py", text)
        self.assertIn("--run-python tools/static_check.py", text)
        self.assertIn("--run-python -m unittest discover -s tests -v", text)
        self.assertIn("no PowerShell runtime found", text)
        self.assertLess(text.index("where pwsh"), text.index("where powershell.exe"))
        self.assertIn("set \"AAS_PS=pwsh\"", text)
        self.assertIn("set \"AAS_PS=powershell.exe\"", text)
        self.assertNotIn("if %ERRORLEVEL% EQU 0 (\n", text)

    def test_makefile_uses_bootstrap_python_contract_for_tests(self) -> None:
        text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("./installer/bootstrap.sh --run-python -m unittest discover -s tests -v", text)
        self.assertNotIn("PYTHONPATH=. python -m unittest", text)
        self.assertIn("lifecycle-test:", text)
        self.assertIn("runtime-smoke:", text)
        self.assertIn("delegate-agent:", text)
        self.assertIn("help:", text)
        self.assertIn("list-artifacts:", text)
        self.assertIn("./installer/bootstrap.sh list-artifacts $(ARGS)", text)
        self.assertIn("./installer/bootstrap.sh runtime-smoke $(ARGS)", text)
        self.assertIn("./installer/bootstrap.sh delegate-agent $(ARGS)", text)
        self.assertIn("generate-docs: docs", text)
        self.assertIn("docs-check:", text)
        self.assertIn("./installer/bootstrap.sh docs-check $(ARGS)", text)
        self.assertIn("static-check:", text)
        self.assertIn("./installer/bootstrap.sh --run-python tools/static_check.py", text)
        self.assertIn("release-check: docs-check static-check sanitize-check test runtime-smoke", text)
        self.assertIn("./installer/bootstrap.sh --run-python -m sphinx", text)

    def test_cli_delegate_agent_research_blocks_without_resolved_model(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            fake_cli = root / "bin" / "fake-claude"
            fake_cli.parent.mkdir()
            fake_cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_cli.chmod(0o755)
            stream = io.StringIO()
            with (
                contextlib.redirect_stdout(stream),
                patch.dict(
                    os.environ,
                    {
                        "AAS_CLAUDE": str(fake_cli),
                        "AAS_CLAUDE_DISPATCH_COMMAND": str(fake_cli),
                    },
                    clear=False,
                ),
            ):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "delegate-agent",
                    "--provider",
                    "claude",
                    "--task",
                    "Review the claim.",
                    "--research",
                    "--dry-run",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["status"], "blocked")
            self.assertIn("research dispatch requires", payload["dispatch_plan"][0]["reason"])
            self.assertNotIn(str(root), json.dumps(payload["dispatch_plan"]))

    def test_cli_delegate_agent_live_requires_external_cli_opt_in(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            fake_cli = root / "bin" / "fake-claude"
            fake_cli.parent.mkdir()
            fake_cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_cli.chmod(0o755)
            stream = io.StringIO()
            with (
                contextlib.redirect_stdout(stream),
                patch.dict(
                    os.environ,
                    {
                        "AAS_CLAUDE": str(fake_cli),
                        "AAS_CLAUDE_DISPATCH_COMMAND": str(fake_cli),
                        "AAS_CLAUDE_LATEST_MODEL": "claude-fake-latest",
                        "AAS_CLAUDE_HIGHEST_THINKING": "xhigh",
                    },
                    clear=False,
                ),
            ):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "delegate-agent",
                    "--provider",
                    "claude",
                    "--task",
                    "Review the claim.",
                    "--research",
                ])
            self.assertEqual(code, 1)
            payload = json.loads(stream.getvalue())
            self.assertIn("live external CLI dispatch requires --allow-external-cli", payload["error"])

    def test_dispatch_command_split_strips_windows_wrapper_quotes(self) -> None:
        command = '"C:\\Path With Spaces\\python.exe" "D:\\tmp\\fake.py" --model x'
        with patch("installer.ai_agents_skills.delegation_dispatch.os.name", "nt"):
            self.assertEqual(
                split_dispatch_command(command),
                ["C:\\Path With Spaces\\python.exe", "D:\\tmp\\fake.py", "--model", "x"],
            )

    def test_cli_delegate_agent_dispatches_fake_external_cli(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            fake_cli = root / "bin" / "fake_claude.py"
            fake_cli.parent.mkdir()
            fake_cli.write_text(
                "from __future__ import annotations\n"
                "import os\n"
                "import sys\n"
                "sys.stdin.read()\n"
                "print('AAS_RESULT_JSON_START')\n"
                "print('{\"status\":\"ok\",\"findings\":[{\"id\":\"F1\",\"summary\":\"done\",\"evidence_refs\":[\"task\"]}],\"limitations\":[],\"warnings\":[]}')\n"
                "print('AAS_RESULT_JSON_END')\n"
                "print(os.environ['AAS_DELEGATION_FINAL_MARKER'])\n",
                encoding="utf-8",
            )
            stream = io.StringIO()
            with (
                contextlib.redirect_stdout(stream),
                patch.dict(
                    os.environ,
                    {
                        "AAS_CLAUDE": str(fake_cli),
                        "AAS_CLAUDE_DISPATCH_COMMAND": (
                            f"\"{sys.executable}\" \"{fake_cli}\" --model {{model}} --thinking {{thinking}}"
                        ),
                        "AAS_CLAUDE_LATEST_MODEL": "claude-fake-latest",
                        "AAS_CLAUDE_HIGHEST_THINKING": "xhigh",
                    },
                    clear=False,
                ),
            ):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "delegate-agent",
                    "--provider",
                    "claude",
                    "--task",
                    "Review the claim.",
                    "--research",
                    "--allow-external-cli",
                    "--timeout",
                    "5",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["participants"][0]["status"], "ok")
            self.assertEqual(payload["participants"][0]["result"]["findings"][0]["summary"], "done")
            self.assertEqual(payload["dispatch_plan"][0]["command_shape"], f"{Path(sys.executable).name} <args-redacted>")
            self.assertNotIn("command", payload["dispatch_plan"][0])
            run_dir = Path(payload["run_dir"])
            self.assertTrue((run_dir / "manifest.json").is_file())
            self.assertTrue((run_dir / "transport_manifest.json").is_file())
            self.assertTrue((run_dir / "timeout_events.jsonl").is_file())
            self.assertTrue((run_dir / "truncation_events.jsonl").is_file())
            self.assertTrue((run_dir / "evidence-map.jsonl").is_file())
            self.assertTrue((run_dir / "profiles" / "claude-external-1.json").is_file())
            self.assertTrue((run_dir / "raw" / "claude-external-1" / "command-shape.txt").is_file())
            self.assertTrue((run_dir / "parsed" / "claude-external-1.result.json").is_file())
            result_packet = json.loads((run_dir / "parsed" / "claude-external-1.result.json").read_text(encoding="utf-8"))
            self.assertEqual(result_packet["schema_version"], "cross-agent-delegation.result.v1")
            self.assertEqual(result_packet["next_step"], "parent_decides")
            validation = json.loads((run_dir / "validation" / "claude-external-1.json").read_text(encoding="utf-8"))
            self.assertEqual(validation["result_packet_validation"], [])
            profile = json.loads((run_dir / "profiles" / "claude-external-1.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["research_model_policy"]["resolved_model"], "claude-fake-latest")
            self.assertEqual(profile["research_model_policy"]["resolved_thinking"], "xhigh")
            self.assertNotIn(str(root), json.dumps(payload["dispatch_plan"]))

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

    def test_cli_plan_reports_missing_explicit_openclaw_target(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "--agent",
                    "openclaw",
                    "plan",
                    "--skill",
                    "source-research",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["action_count"], 0)
            self.assertEqual(
                payload["skipped_agents"],
                [{"agent": "openclaw", "reason": "agent home not detected"}],
            )
            self.assertFalse((root / ".openclaw").exists())

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

    def test_cli_rollback_rejects_dry_run_with_apply(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            code = main([
                "--json",
                "--root",
                str(root),
                "rollback",
                "--dry-run",
                "--apply",
            ])
            self.assertEqual(code, 1)

    def test_cli_install_apply_requires_process_confirmation(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            stream = io.StringIO()
            with (
                contextlib.redirect_stdout(stream),
                contextlib.redirect_stderr(io.StringIO()),
                patch("sys.stdin", io.StringIO("")),
            ):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "install",
                    "--skill",
                    "formal-skeleton-helper",
                    "--apply",
                ])
            self.assertEqual(code, 1)
            payload = json.loads(stream.getvalue())
            self.assertIn("confirmation required", payload["error"])
            self.assertFalse((root / ".claude" / "skills" / "zotero" / "SKILL.md").exists())

    def test_cli_install_apply_rejects_wrong_process_confirmation(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            stream = io.StringIO()
            with (
                contextlib.redirect_stdout(stream),
                contextlib.redirect_stderr(io.StringIO()),
                patch("sys.stdin", io.StringIO("yes\n")),
            ):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "install",
                    "--skill",
                    "formal-skeleton-helper",
                    "--apply",
                ])
            self.assertEqual(code, 1)
            payload = json.loads(stream.getvalue())
            self.assertIn("confirmation phrase did not match", payload["error"])
            self.assertFalse((root / ".claude" / "skills" / "zotero" / "SKILL.md").exists())

    def test_cli_install_fake_root_succeeds_with_process_confirmation(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            with (
                contextlib.redirect_stderr(io.StringIO()),
                patch("sys.stdin", io.StringIO(f"{INSTALL_CONFIRMATION_PHRASE}\n")),
            ):
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

    def test_cli_rollback_apply_requires_process_confirmation(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            with (
                contextlib.redirect_stderr(io.StringIO()),
                patch("sys.stdin", io.StringIO(f"{INSTALL_CONFIRMATION_PHRASE}\n")),
            ):
                install_code = main([
                    "--json",
                    "--root",
                    str(root),
                    "install",
                    "--skill",
                    "zotero",
                    "--apply",
                ])
            self.assertEqual(install_code, 0)
            state = json.loads((root / ".ai-agents-skills" / "state.json").read_text(encoding="utf-8"))
            run_id = state["runs"][0]["run_id"]

            stream = io.StringIO()
            with (
                contextlib.redirect_stdout(stream),
                contextlib.redirect_stderr(io.StringIO()),
                patch("sys.stdin", io.StringIO("")),
            ):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "rollback",
                    "--run",
                    run_id,
                    "--apply",
                ])
            self.assertEqual(code, 1)
            payload = json.loads(stream.getvalue())
            self.assertIn("rollback confirmation required", payload["error"])
            self.assertTrue((root / ".claude" / "skills" / "zotero" / "SKILL.md").exists())

    def test_cli_uninstall_apply_requires_process_confirmation(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            with (
                contextlib.redirect_stderr(io.StringIO()),
                patch("sys.stdin", io.StringIO(f"{INSTALL_CONFIRMATION_PHRASE}\n")),
            ):
                install_code = main([
                    "--json",
                    "--root",
                    str(root),
                    "install",
                    "--skill",
                    "zotero",
                    "--apply",
                ])
            self.assertEqual(install_code, 0)

            stream = io.StringIO()
            err = io.StringIO()
            with (
                contextlib.redirect_stdout(stream),
                contextlib.redirect_stderr(err),
                patch("sys.stdin", io.StringIO("")),
            ):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "uninstall",
                    "--skill",
                    "zotero",
                    "--apply",
                ])
            self.assertEqual(code, 1)
            payload = json.loads(stream.getvalue())
            self.assertIn("uninstall confirmation required", payload["error"])
            prompt = err.getvalue()
            self.assertIn("Uninstall confirmation required", prompt)
            self.assertIn(f"root: {root}", prompt)
            self.assertIn("scope: skill=zotero", prompt)
            self.assertIn("planned uninstall actions:", prompt)
            self.assertIn("planned operations:", prompt)
            self.assertIn("What uninstall does:", prompt)
            self.assertIn("Safety boundary:", prompt)
            self.assertIn("skips changed or suspicious artifacts", prompt)
            self.assertTrue((root / ".claude" / "skills" / "zotero" / "SKILL.md").exists())

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

    def test_cli_rollback_apply_requires_scope(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            code = main([
                "--json",
                "--root",
                str(root),
                "rollback",
                "--apply",
            ])
            self.assertEqual(code, 1)

    def test_cli_uninstall_refuses_real_system_without_flag(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main([
                "--json",
                "uninstall",
                "--skill",
                "zotero",
                "--apply",
            ])
        self.assertEqual(code, 1)
        payload = json.loads(stream.getvalue())
        self.assertIn("real-system writes require --real-system", payload["error"])

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
            self.assertEqual([item["target"] for item in payload["target_prechecks"]], ["claude"])
            target = payload["target_prechecks"][0]
            self.assertEqual(target["status"], "ready")
            self.assertEqual(target["target_home"]["status"], "directory")
            self.assertEqual(target["capabilities"]["default_install_mode"], "symlink")
            self.assertFalse(target["read_policy"]["file_contents_read"])
            self.assertFalse(target["read_policy"]["secret_values_read"])
            self.assert_target_precheck_schema(target)

    def test_cli_precheck_reports_detected_target_prechecks_without_agent_filter(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex", "claude", "deepseek")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "precheck",
                    "--no-skills",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["detected_agents"], ["codex", "claude", "deepseek"])
            self.assertEqual(
                [item["target"] for item in payload["target_prechecks"]],
                payload["detected_agents"],
            )
            for target in payload["target_prechecks"]:
                self.assertEqual(target["status"], "ready")
                self.assert_target_precheck_schema(target)

    def test_cli_precheck_reports_no_agent_homes(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "precheck",
                    "--profile",
                    "research-core",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["status"], "no-agent-homes")
            self.assertEqual(payload["detected_agents"], [])
            self.assertEqual(payload["dependencies"], [])
            self.assertEqual(payload["target_prechecks"], [])

    def test_cli_precheck_reports_requested_missing_target_precheck(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "--agents",
                    "openclaw",
                    "precheck",
                    "--no-skills",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["detected_agents"], [])
            self.assertEqual(len(payload["target_prechecks"]), 1)
            target = payload["target_prechecks"][0]
            self.assertEqual(target["target"], "openclaw")
            self.assertEqual(target["status"], "home-missing")
            self.assertEqual(target["target_home"]["status"], "missing")
            self.assertTrue(target["capabilities"]["fake_root_only"])
            self.assertFalse((root / ".openclaw").exists())
            self.assert_target_precheck_schema(target)

    def test_cli_precheck_reports_copilot_target_and_cli_status_separately(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "--agents",
                    "copilot",
                    "precheck",
                    "--no-skills",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            target = payload["target_prechecks"][0]
            self.assertEqual(target["target"], "copilot")
            self.assertEqual(target["status"], "home-missing")
            self.assertEqual(target["base"]["status"], "home-missing")
            self.assertIn(target["copilot_status"], {"cli-missing", "probe-disabled", "offline-unverified"})
            self.assertEqual(target["path_style"], path_style_for_platform(current_platform()))
            self.assert_target_precheck_schema(target)

    def test_cli_precheck_reports_sanitized_external_agent_delegation_policy(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            fake_cli = root / "bin" / "claude-test"
            fake_cli.parent.mkdir()
            fake_cli.write_text("#!/bin/sh\nprintf 'claude-test 1.0\\n'\n", encoding="utf-8")
            fake_cli.chmod(0o755)
            stream = io.StringIO()
            with (
                contextlib.redirect_stdout(stream),
                patch.dict(os.environ, {"AAS_CLAUDE": str(fake_cli)}, clear=False),
            ):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "precheck",
                    "--no-skills",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            external = payload["external_agent_prechecks"]
            self.assertEqual(external["schema_version"], "external-agent-precheck.v1")
            self.assertEqual(external["policy"]["mode"], "prefer")
            self.assertEqual(external["policy"]["research_model_policy"], "latest_model_highest_reasoning_required")
            self.assertTrue(external["nested_delegation"]["require_same_model_as_manager"])
            by_provider = {item["provider"]: item for item in external["providers"]}
            self.assertEqual(by_provider["codex"]["status"], "parent-runtime-probe-required")
            self.assertEqual(
                by_provider["codex"]["research_model_policy"]["status"],
                "runtime-probe-required",
            )
            self.assertEqual(by_provider["openclaw"]["status"], "reference-only")
            self.assertEqual(by_provider["claude"]["cli"]["command"], "claude-test")
            self.assertEqual(by_provider["claude"]["cli"]["version"], "output-redacted")
            self.assertNotIn(str(root), by_provider["claude"]["cli"]["command"])
            self.assertFalse(by_provider["claude"]["read_policy"]["secret_values_read"])
            self.assertEqual(
                by_provider["claude"]["research_model_policy"]["status"],
                "runtime-probe-required",
            )
            self.assertEqual(
                by_provider["claude"]["nested_delegation"]["status"],
                "runtime-probe-required",
            )

    def test_cli_audit_system_includes_external_agent_prechecks(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "audit-system",
                    "--profile",
                    "multi-agent",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            self.assertIn("external_agent_prechecks", payload)
            self.assertEqual(
                payload["external_agent_prechecks"]["policy"]["template_policy"],
                "prefer_installed_templates",
            )

    def test_cli_precheck_reports_all_targets_for_all_platform_overrides(self) -> None:
        target_names = ["codex", "claude", "deepseek", "copilot", "openclaw"]
        expected_path_styles = {
            "linux": "posix",
            "macos": "posix",
            "windows": "windows",
            "wsl": "wsl-posix",
        }
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, *target_names)
            for platform, path_style in expected_path_styles.items():
                with self.subTest(platform=platform):
                    stream = io.StringIO()
                    with contextlib.redirect_stdout(stream):
                        code = main([
                            "--json",
                            "--root",
                            str(root),
                            "--platform",
                            platform,
                            "--agents",
                            ",".join(target_names),
                            "precheck",
                            "--no-skills",
                        ])
                    self.assertEqual(code, 0)
                    payload = json.loads(stream.getvalue())
                    prechecks = payload["target_prechecks"]
                    self.assertEqual([item["target"] for item in prechecks], target_names)
                    by_target = {item["target"]: item for item in prechecks}
                    for target in prechecks:
                        self.assertEqual(target["platform"], platform)
                        self.assertEqual(target["path_style"], path_style)
                        self.assertTrue(target["target_home"]["path"].startswith(str(root)))
                        self.assertFalse(target["read_policy"]["secret_values_read"])
                        self.assert_target_precheck_schema(target)
                    self.assertEqual(by_target["codex"]["capabilities"]["default_install_mode"], "reference")
                    self.assertEqual(by_target["claude"]["capabilities"]["default_install_mode"], "symlink")
                    self.assertEqual(by_target["deepseek"]["capabilities"]["default_install_mode"], "reference")
                    self.assertEqual(by_target["openclaw"]["status"], "fake-root-only")
                    self.assertEqual(by_target["openclaw"]["capabilities"]["default_install_mode"], "copy")
                    self.assertEqual(by_target["copilot"]["status"], "ready")
                    self.assertIn(by_target["copilot"]["copilot_status"], {"cli-missing", "probe-disabled", "offline-unverified"})

    def test_cli_precheck_reports_target_home_safety_statuses(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            (root / "real-codex").mkdir()
            (root / ".codex").symlink_to(root / "real-codex")
            (root / ".claude").write_text("not a directory\n", encoding="utf-8")

            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "--agents",
                    "codex,claude",
                    "precheck",
                    "--no-skills",
                ])
            self.assertEqual(code, 0)
            by_target = {item["target"]: item for item in json.loads(stream.getvalue())["target_prechecks"]}
            self.assertEqual(by_target["codex"]["status"], "home-symlink")
            self.assertEqual(by_target["codex"]["target_home"]["status"], "blocked")
            self.assertEqual(by_target["claude"]["status"], "home-invalid")
            self.assertEqual(by_target["claude"]["target_home"]["status"], "file")

    def test_cli_precheck_reports_openclaw_real_system_block(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "openclaw")
            stream = io.StringIO()
            with patch("installer.ai_agents_skills.agents.looks_like_real_system_root", return_value=True):
                with contextlib.redirect_stdout(stream):
                    code = main([
                        "--json",
                        "--root",
                        str(root),
                        "--agents",
                        "openclaw",
                        "precheck",
                        "--no-skills",
                    ])
            self.assertEqual(code, 0)
            target = json.loads(stream.getvalue())["target_prechecks"][0]
            self.assertEqual(target["status"], "blocked-real-system")
            self.assertFalse(target["home_status"]["eligible"])

    def test_cli_precheck_reports_symlinked_parent_paths_as_blocked(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex")
            (root / "shared-agents").mkdir()
            (root / ".agents").symlink_to(root / "shared-agents")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "--agents",
                    "codex",
                    "precheck",
                    "--no-skills",
                ])
            self.assertEqual(code, 0)
            target = json.loads(stream.getvalue())["target_prechecks"][0]
            optional = target["optional_skills_dirs"][0]
            self.assertEqual(optional["status"], "blocked")
            self.assertIn("symlinked parent", optional["reason"])

    def test_target_precheck_path_status_blocks_outside_paths(self) -> None:
        from installer.ai_agents_skills.target_prechecks import path_status

        with fake_root() as tmp, fake_root() as outside_tmp:
            root = Path(tmp)
            outside = Path(outside_tmp) / "SKILL.md"
            status = path_status(root, outside)
            self.assertEqual(status["status"], "blocked")
            self.assertIn("outside selected root", status["reason"])

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

    def test_cli_audit_system_migration_report_groups_existing_files(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            existing = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            existing.parent.mkdir(parents=True)
            existing.write_text("user-owned zotero\n", encoding="utf-8")
            legacy = root / ".claude" / "skills" / "deep-research" / "SKILL.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("legacy deep research\n", encoding="utf-8")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "audit-system",
                    "--skills",
                    "zotero,deep-research-workflow",
                    "--migration-report",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            report = payload["migration_report"]["agents"][0]
            self.assertEqual(report["canonical_unmanaged"], ["zotero"])
            self.assertEqual(report["legacy_aliases"], {"deep-research-workflow": "deep-research"})
            self.assertTrue(any("--migrate" in command for command in report["recommended_dry_runs"]))
            self.assertTrue(any("--adopt" in command for command in report["recommended_dry_runs"]))

    def test_cli_audit_system_reports_symlink_install_as_managed(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            with (
                contextlib.redirect_stderr(io.StringIO()),
                patch("sys.stdin", io.StringIO(f"{INSTALL_CONFIRMATION_PHRASE}\n")),
            ):
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

            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                audit_code = main([
                    "--json",
                    "--root",
                    str(root),
                    "audit-system",
                    "--skill",
                    "zotero",
                ])
            self.assertEqual(audit_code, 0)
            payload = json.loads(stream.getvalue())
            coverage = payload["skill_coverage"][0]
            self.assertEqual(coverage["managed_canonical"], ["zotero"])
            self.assertEqual(coverage["unmanaged_canonical"], [])

    def test_cli_precheck_save_state_uses_state_path_guard(self) -> None:
        with fake_root() as tmp, tempfile.TemporaryDirectory() as outside_tmp:
            root = Path(tmp)
            outside = Path(outside_tmp)
            (root / ".ai-agents-skills").symlink_to(outside, target_is_directory=True)
            stream = io.StringIO()

            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "precheck",
                    "--save-state",
                ])

            self.assertEqual(code, 1)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["status"], "error")
            self.assertIn("installer state", payload["error"])
            self.assertFalse((outside / "precheck.json").exists())

    def test_cli_precheck_save_state_refuses_real_system_without_flag(self) -> None:
        with fake_root() as tmp, patch("installer.ai_agents_skills.cli.is_real_system_root", return_value=True):
            root = Path(tmp)
            create_agent_homes(root, "codex")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main(["--json", "--root", str(root), "precheck", "--save-state"])

        self.assertEqual(code, 1)
        payload = json.loads(stream.getvalue())
        self.assertIn("real-system precheck state writes require --real-system", payload["error"])
        self.assertFalse((root / ".ai-agents-skills" / "precheck.json").exists())

    def test_cli_verify_returns_nonzero_for_failed_status(self) -> None:
        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            from installer.ai_agents_skills.agents import detect_agents

            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)
            plan = build_plan(root, manifests, selected, detect_agents(root, ["claude"]), install_mode="copy")
            apply_plan(root, plan, dry_run=False)
            target = root / ".claude" / "skills" / "zotero" / "SKILL.md"
            target.write_text(target.read_text(encoding="utf-8") + "\nuser edit\n", encoding="utf-8")

            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "verify",
                    "--skill",
                    "zotero",
                ])

            self.assertEqual(code, 1)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["status"], "failed")

    def test_cli_smoke_returns_nonzero_without_managed_artifacts(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "smoke",
                ])

            self.assertEqual(code, 1)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["status"], "no-managed-artifacts")

    def test_cli_smoke_reports_managed_skill_visibility(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            with (
                contextlib.redirect_stderr(io.StringIO()),
                patch("sys.stdin", io.StringIO(f"{INSTALL_CONFIRMATION_PHRASE}\n")),
            ):
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
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                smoke_code = main([
                    "--json",
                    "--root",
                    str(root),
                    "smoke",
                    "--skill",
                    "zotero",
                ])
            self.assertEqual(smoke_code, 0)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["checked"], 1)

    def test_cli_install_apply_runs_post_install_smoke(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "codex")
            stream = io.StringIO()
            with (
                contextlib.redirect_stdout(stream),
                contextlib.redirect_stderr(io.StringIO()),
                patch("sys.stdin", io.StringIO(f"{INSTALL_CONFIRMATION_PHRASE}\n")),
            ):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "install",
                    "--skill",
                    "formal-skeleton-helper",
                    "--apply",
                ])

            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            post_install = payload["post_install"]
            self.assertEqual(post_install["mode"], "auto")
            self.assertEqual(post_install["status"], "ok")
            self.assertEqual(post_install["verify"]["status"], "ok")
            self.assertEqual(post_install["skill_smoke"]["status"], "ok")
            self.assertEqual(post_install["runtime_smoke"]["status"], "ok")
            self.assertTrue(Path(post_install["report_path"]).is_file())
            state = load_state(root)
            run = next(item for item in state["runs"] if item["run_id"] == payload["run_id"])
            self.assertEqual(run["post_install"]["status"], "ok")

    def test_cli_install_dry_run_skips_post_install_smoke(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "install",
                    "--skill",
                    "zotero",
                    "--dry-run",
                    "--post-install-smoke",
                    "strict",
                ])

            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            self.assertTrue(payload["dry_run"])
            self.assertNotIn("post_install", payload)
            self.assertFalse((root / ".ai-agents-skills").exists())

    def test_cli_install_strict_post_install_failure_preserves_apply_json(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            stream = io.StringIO()
            with (
                contextlib.redirect_stdout(stream),
                contextlib.redirect_stderr(io.StringIO()),
                patch("sys.stdin", io.StringIO(f"{INSTALL_CONFIRMATION_PHRASE}\n")),
                patch(
                    "installer.ai_agents_skills.cli.run_post_install_smoke",
                    return_value={"status": "failed", "mode": "strict", "verify": {"status": "failed"}},
                ),
            ):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "install",
                    "--skill",
                    "zotero",
                    "--apply",
                    "--post-install-smoke",
                    "strict",
                ])

            self.assertEqual(code, 1)
            payload = json.loads(stream.getvalue())
            self.assertFalse(payload["dry_run"])
            self.assertTrue(payload["actions"])
            self.assertEqual(payload["post_install"]["status"], "failed")
            self.assertTrue((root / ".claude" / "skills" / "zotero" / "SKILL.md").is_file())

    def test_cli_fake_root_lifecycle_runs_without_real_home(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main([
                "--json",
                "fake-root-lifecycle",
                "--skill",
                "zotero",
                "--platform-shape",
                "linux",
            ])
        self.assertEqual(code, 0)
        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["status"], "ok")
        run = payload["runs"][0]
        self.assertEqual(run["scenario"], "custom")
        self.assertTrue(run["install"]["dry_run_preserved_root"])
        self.assertTrue(run["install"]["dry_apply_actions_match"])
        self.assertEqual(run["install"]["verify_status"], "ok")
        self.assertEqual(run["install"]["smoke_status"], "ok")
        self.assertTrue(run["uninstall"]["dry_run_preserved_root"])
        self.assertTrue(run["uninstall"]["dry_apply_actions_match"])
        self.assertTrue(run["uninstall"]["final_preserved_root"])
        self.assertEqual(run["uninstall"]["verify_status"], "no-managed-artifacts")

    def test_cli_lifecycle_test_runs_named_scenarios(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main([
                "--json",
                "lifecycle-test",
                "--scenarios",
                "clean-auto,adopt-unmanaged,migrate-legacy",
                "--platform-shape",
                "linux",
            ])
        self.assertEqual(code, 0)
        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["scenario_count"], 3)
        self.assertEqual(payload["run_count"], 3)
        self.assertTrue(all(run["install"]["dry_run_preserved_root"] for run in payload["runs"]))
        self.assertTrue(all(run["install"]["dry_apply_actions_match"] for run in payload["runs"]))
        self.assertTrue(all(run["uninstall"]["final_preserved_root"] for run in payload["runs"]))

    def test_cli_lifecycle_test_stress_runs_state_checks(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main([
                "--json",
                "lifecycle-test",
                "--matrix",
                "stress",
                "--platform-shape",
                "linux",
            ])
        self.assertEqual(code, 0)
        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["status"], "ok")
        self.assertGreaterEqual(payload["scenario_count"], 18)
        self.assertEqual(
            {item["check"] for item in payload["state_checks"]},
            {
                "changed-managed-file-preserved",
                "missing-managed-file-forget",
                "outside-root-state-refused",
                "corrupt-state-reports-error",
            },
        )
        self.assertTrue(all(item["status"] == "ok" for item in payload["state_checks"]))

    def test_cli_with_deps_installs_entrypoint_backing_skill(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "claude")
            with (
                contextlib.redirect_stderr(io.StringIO()),
                patch("sys.stdin", io.StringIO(f"{INSTALL_CONFIRMATION_PHRASE}\n")),
            ):
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


class CopilotTargetTests(unittest.TestCase):
    def test_adapter_target_readmes_capture_install_boundaries(self) -> None:
        copilot = (REPO_ROOT / "targets" / "copilot" / "README.md").read_text(encoding="utf-8")
        openclaw = (REPO_ROOT / "targets" / "openclaw" / "README.md").read_text(encoding="utf-8")

        self.assertIn("adapter-only", copilot)
        self.assertIn("does not receive Codex or\nClaude instruction blocks", copilot)
        self.assertIn("fake-root-only", openclaw)
        self.assertIn("Runtime-backed skills are blocked", openclaw)

    def test_copilot_is_known_and_detected_by_default(self) -> None:
        from installer.ai_agents_skills.agents import all_agent_names, detect_agents, known_agent_names

        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "copilot")

            self.assertIn("copilot", all_agent_names())
            self.assertIn("copilot", known_agent_names())
            self.assertEqual([agent.name for agent in detect_agents(root)], ["copilot"])
            self.assertEqual([agent.name for agent in detect_agents(root, ["copilot"])], ["copilot"])

    def test_explicit_copilot_skill_install_uses_personal_skill_surface(self) -> None:
        from installer.ai_agents_skills.agents import detect_agents

        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "copilot")
            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)

            plan = build_plan(
                root,
                manifests,
                selected,
                detect_agents(root, ["copilot"]),
                requested_agents=["copilot"],
            )
            skill_actions = [
                action for action in plan["actions"]
                if action["kind"] == "file" and action["artifact_type"] == "skill-file"
            ]

            self.assertEqual(len(skill_actions), 1)
            action = skill_actions[0]
            self.assertEqual(action["agent"], "copilot")
            self.assertEqual(Path(action["path"]), root / ".copilot" / "skills" / "zotero" / "SKILL.md")
            self.assertEqual(action["install_mode"], "reference")
            self.assertIn("Copilot agent skills are regular SKILL.md files", action["mode_reason"])

            apply_plan(root, plan, dry_run=False)
            installed = root / ".copilot" / "skills" / "zotero" / "SKILL.md"
            self.assertTrue(installed.is_file())
            self.assertFalse(installed.is_symlink())
            self.assertIn("Install mode: reference", installed.read_text(encoding="utf-8"))

    def test_explicit_copilot_writing_workflow_gets_skill_adapter_not_artifacts(self) -> None:
        from installer.ai_agents_skills.agents import detect_agents

        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "copilot")
            args = Args()
            args.profile = "writing-workflow"
            args.artifact_profile = "writing-workflow"
            selected = resolve_skills(args, manifests)
            artifacts = resolve_artifacts(args, manifests)

            plan = build_plan(
                root,
                manifests,
                selected,
                detect_agents(root, ["copilot"]),
                artifacts=artifacts,
                requested_agents=["copilot"],
            )
            skill_actions = [
                action for action in plan["actions"]
                if action["kind"] == "file" and action["artifact_type"] == "skill-file"
            ]
            artifact_actions = [action for action in plan["actions"] if action.get("artifact_id")]

            self.assertEqual(len(skill_actions), 1)
            self.assertEqual(skill_actions[0]["agent"], "copilot")
            self.assertEqual(
                Path(skill_actions[0]["path"]),
                root / ".copilot" / "skills" / "draft-writing" / "SKILL.md",
            )
            self.assertEqual(artifact_actions, [])

    def test_copilot_agent_persona_uses_agent_md_surface(self) -> None:
        from installer.ai_agents_skills.agents import detect_agents

        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "copilot")

            plan = build_plan(
                root,
                manifests,
                [],
                detect_agents(root, ["copilot"]),
                artifacts=[("agent-persona", "code-reviewer")],
                runtime_profile="none",
                requested_agents=["copilot"],
            )
            action = next(action for action in plan["actions"] if action["artifact_type"] == "agent-persona")

            self.assertEqual(Path(action["path"]), root / ".copilot" / "agents" / "code-reviewer.agent.md")
            self.assertIn("target: github-copilot", action["content"])
            self.assertIn('tools: ["*"]', action["content"])

    def test_copilot_precheck_reports_cli_config_and_redacts_secret_values(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "copilot")
            settings = root / ".copilot" / "settings.json"
            settings.write_text('{"token":"sk-should-not-appear"}\n', encoding="utf-8")

            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([
                    "--json",
                    "--root",
                    str(root),
                    "--agents",
                    "copilot",
                    "precheck",
                    "--no-skills",
                ])

            self.assertEqual(code, 0)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["detected_agents"], ["copilot"])
            self.assertEqual(len(payload["target_prechecks"]), 1)
            precheck = payload["target_prechecks"][0]
            self.assertEqual(precheck["target"], "copilot")
            self.assertEqual(precheck["status"], "ready")
            self.assertIn(precheck["copilot_status"], {"cli-missing", "probe-disabled", "offline-unverified"})
            self.assertEqual(precheck["config_dir"]["children"]["settings_json"]["status"], "file")
            self.assertFalse(precheck["config_dir"]["file_contents_read"])
            self.assertFalse(precheck["auth"]["secret_values_read"])
            self.assertNotIn("sk-should-not-appear", json.dumps(payload))

    def test_copilot_precheck_redacts_env_override_command_args_and_version_output(self) -> None:
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "copilot")
            command_name = "copilot.cmd" if current_platform() == "windows" else "copilot"
            tool = root / command_name
            if current_platform() == "windows":
                tool.write_text("@echo off\r\necho GitHub Copilot sk-version-secret\r\n", encoding="utf-8")
            else:
                tool.write_text("#!/bin/sh\nprintf 'GitHub Copilot sk-version-secret\\n'\n", encoding="utf-8")
            tool.chmod(0o755)

            stream = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "AAS_COPILOT": f"{command_name} --token sk-command-secret",
                    "PATH": f"{root}{os.pathsep}{os.environ.get('PATH', '')}",
                },
            ):
                with contextlib.redirect_stdout(stream):
                    code = main([
                        "--json",
                        "--root",
                        str(root),
                        "--agents",
                        "copilot",
                        "precheck",
                        "--no-skills",
                    ])

            self.assertEqual(code, 0)
            payload_text = stream.getvalue()
            self.assertNotIn("sk-command-secret", payload_text)
            self.assertNotIn("sk-version-secret", payload_text)
            precheck = json.loads(payload_text)["target_prechecks"][0]
            self.assertIn("<args-redacted>", precheck["cli"]["command"])
            self.assertEqual(precheck["cli"]["version"], "output-redacted")

    def test_copilot_status_reduction_prioritizes_blocking_statuses(self) -> None:
        from installer.ai_agents_skills.copilot import reduce_copilot_status

        self.assertEqual(reduce_copilot_status("supported", "unsupported-model"), "unsupported-model")
        self.assertEqual(reduce_copilot_status("supported", "provider-unavailable"), "provider-unavailable")
        self.assertEqual(reduce_copilot_status("supported", "probe-timeout"), "probe-timeout")
        self.assertEqual(reduce_copilot_status("supported", "probe-disabled"), "probe-disabled")
        self.assertEqual(reduce_copilot_status("supported", "not-a-status"), "unknown-entitlement")

    def test_copilot_symlink_mode_fails_closed(self) -> None:
        from installer.ai_agents_skills.agents import detect_agents

        manifests = load_manifests()
        with fake_root() as tmp:
            root = Path(tmp)
            create_agent_homes(root, "copilot")
            args = Args()
            args.skills = "zotero"
            selected = resolve_skills(args, manifests)

            plan = build_plan(
                root,
                manifests,
                selected,
                detect_agents(root, ["copilot"]),
                install_mode="symlink",
                requested_agents=["copilot"],
            )
            action = next(
                action for action in plan["actions"]
                if action["kind"] == "file" and action["artifact_type"] == "skill-file"
            )
            self.assertEqual(action["operation"], "skip")
            self.assertEqual(action["classification"], "blocked")
            self.assertIn("symlinked skill loading has not been verified", action["reason"])


if __name__ == "__main__":
    unittest.main()
