from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.agents import antigravity_plugin_root, detect_agents, target_for
from installer.ai_agents_skills.apply import apply_plan
from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.openclaw_target_paths import path_leak_scan
from installer.ai_agents_skills.planner import build_plan
from installer.ai_agents_skills.render import (
    MANAGED_MARKER,
    add_managed_support_header,
    render_skill_md,
)


NATIVE_WINDOWS_MUTATION_SKIP = unittest.skipIf(
    os.name == "nt",
    "native Windows apply is dry-run-only until handle-bound mutation lands",
)


CANONICAL_SKILLS = Path("canonical/skills")


def _install(root: Path, skills: list[str], agent: str) -> None:
    target_for(root, agent).home.mkdir(parents=True, exist_ok=True)
    plan = build_plan(
        root, load_manifests(), skills, detect_agents(root, [agent]),
        platform="linux", requested_agents=[agent],
    )
    apply_plan(root, plan, dry_run=False)


class ManagedHeaderTargetTests(unittest.TestCase):
    def test_no_canonical_source_carries_a_generated_header(self) -> None:
        # A rendered copy committed back over its source makes that source dictate
        # every other agent's provenance marker.
        offenders = [
            str(path)
            for path in sorted(CANONICAL_SKILLS.rglob("*"))
            if path.is_file()
            and path.suffix in {".md", ".py", ".sh", ".ps1", ".yaml", ".yml", ".toml"}
            and MANAGED_MARKER in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual(offenders, [])

    def test_a_stale_header_is_retargeted_rather_than_kept(self) -> None:
        stale = "<!-- Managed by ai-agents-skills. Generated target: grok. -->\n\n# Body\n"
        self.assertIn(
            "Generated target: claude.",
            add_managed_support_header(stale, "claude", "references/x.md"),
        )


class SupportFileRenderTests(unittest.TestCase):
    def test_support_files_do_not_name_the_codex_runtime(self) -> None:
        offenders = [
            str(path)
            for path in sorted(CANONICAL_SKILLS.rglob("*"))
            if path.is_file()
            and path.name != "SKILL.md"
            and path.suffix in {".md", ".sh", ".py", ".ps1"}
            and ".codex/runtime" in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual(offenders, [])

    @NATIVE_WINDOWS_MUTATION_SKIP
    def test_an_opencode_install_ships_no_codex_runtime_path(self) -> None:
        # SKILL.md is neutralized for opencode; a support file in the same installed
        # directory that still says ~/.codex/runtime documents a path that home lacks.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "home"
            root.mkdir()
            _install(root, ["docling", "vnthuquan"], "opencode")
            offenders = [
                str(path.relative_to(root))
                for path in sorted(root.rglob("*"))
                if path.is_file() and ".codex/runtime" in path.read_text(encoding="utf-8", errors="replace")
            ]
            self.assertEqual(offenders, [])


class OpenClawRenderTests(unittest.TestCase):
    def test_the_windows_runtime_fallback_survives_neutralization(self) -> None:
        # Rewriting the else-branch token by token substitutes the very variable the
        # if-branch just tested as unset, leaving a fallback that always yields "".
        specs = load_manifests()["skills"]["skills"]
        degenerate = []
        for skill, spec in sorted(specs.items()):
            for line in render_skill_md(skill, spec, "openclaw").splitlines():
                if 'else { "$env:AAS_RUNTIME_ROOT" }' in line:
                    degenerate.append(skill)
        self.assertEqual(degenerate, [])

    def test_every_skill_renders_for_openclaw(self) -> None:
        specs = load_manifests()["skills"]["skills"]
        failures = {}
        for skill, spec in sorted(specs.items()):
            try:
                render_skill_md(skill, spec, "openclaw")
            except ValueError as exc:
                failures[skill] = str(exc)
        self.assertEqual(failures, {})

    def test_a_placeholder_segment_is_not_a_home_path(self) -> None:
        self.assertEqual(path_leak_scan("inspection of `/windows/Users/...`"), [])
        self.assertEqual(path_leak_scan("/home/..."), [])

    def test_a_named_home_is_still_a_leak(self) -> None:
        # Two of the samples are assembled from fragments so that this file stays
        # inside the repository's own secret scan.  Spelling them out would cost a
        # whole-file allowlist entry, which switches the scan off here for every
        # future edit as well -- more than these two literals are worth.
        windows_home = "/windows/Users/" + "alice"
        posix_home = "cd /home/" + "agent/work"
        self.assertEqual(path_leak_scan("/Users/alice/Library"), ["macos-home-path"])
        self.assertEqual(path_leak_scan(windows_home), ["macos-home-path"])
        self.assertEqual(path_leak_scan(posix_home), ["posix-home-path"])


class AntigravityNoteTests(unittest.TestCase):
    def _antigravity_home(self, root: Path, migrated: bool) -> None:
        (root / ".gemini" / "antigravity-cli" / "skills").mkdir(parents=True)
        (root / ".gemini" / "antigravity-cli" / "plugins" / "ai-agents-skills").mkdir(parents=True)
        if not migrated:
            return
        config = root / ".gemini" / "config"
        (config / "skills").mkdir(parents=True)
        (config / "plugins").mkdir(parents=True)
        (config / ".migrated").write_text("1", encoding="utf-8")
        legacy = root / ".gemini" / "antigravity-cli" / "skills"
        shutil.rmtree(legacy)
        legacy.symlink_to(config / "skills")

    def _note(self, root: Path) -> str:
        skill_file = next(root.rglob("graph-verifier.md"))
        return skill_file.read_text(encoding="utf-8")

    @NATIVE_WINDOWS_MUTATION_SKIP
    def test_the_note_names_the_migrated_plugin_tree(self) -> None:
        # install --apply empties the pre-migration plugin tree, so a note naming it
        # sends the reading agent to a directory this same run cleared.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "home"
            root.mkdir()
            self._antigravity_home(root, migrated=True)
            _install(root, ["graph-verifier"], "antigravity")

            self.assertEqual(antigravity_plugin_root(root), root / ".gemini" / "config" / "plugins")
            note = self._note(root)
            self.assertIn("`~/.gemini/config/plugins/ai-agents-skills/`", note)
            self.assertIn("`~/.gemini/config/skills/`", note)
            self.assertNotIn("~/.gemini/antigravity-cli/plugins", note)

    @NATIVE_WINDOWS_MUTATION_SKIP
    def test_the_note_still_names_the_legacy_tree_on_an_unmigrated_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "home"
            root.mkdir()
            self._antigravity_home(root, migrated=False)
            _install(root, ["graph-verifier"], "antigravity")

            note = self._note(root)
            self.assertIn("`~/.gemini/antigravity-cli/plugins/ai-agents-skills/`", note)
            self.assertIn("`~/.gemini/antigravity-cli/skills/`", note)


if __name__ == "__main__":
    unittest.main()
