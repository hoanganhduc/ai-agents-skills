from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import unittest

from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.openclaw_target_paths import path_leak_scan
from installer.ai_agents_skills.render import (
    add_managed_header,
    load_canonical_skill,
    render_openclaw_runtime_neutral,
    render_skill_md,
)

_M = load_manifests()


def _spec(skill: str) -> dict:
    return _M["skills"]["skills"][skill]


class OpenClawRenderNeutralTest(unittest.TestCase):
    def test_content_only_skill_renders_byte_identical(self) -> None:
        # A content-only skill (no runtime refs) must not drift: same as the old
        # fall-through, no broker note, no substitution.
        skill = "prose"
        out = render_skill_md(skill, _spec(skill), "openclaw")
        self.assertEqual(out, add_managed_header(load_canonical_skill(skill), "openclaw"))
        self.assertNotIn("$AAS_RUNTIME_ROOT", out)
        self.assertNotIn("$AAS_BROKER_ENDPOINT", out)

    def test_runtime_skill_is_neutralized_and_passes_scan(self) -> None:
        skill = "graph-verifier"  # a runtime skill that neutralizes cleanly
        out = render_skill_md(skill, _spec(skill), "openclaw")
        self.assertNotIn(".codex/runtime", out)
        self.assertIn("$AAS_RUNTIME_ROOT", out)
        self.assertIn("$AAS_BROKER_ENDPOINT", out)  # the OpenClaw host-broker note
        self.assertEqual(path_leak_scan(out), [])

    def test_neutral_helper_returns_unchanged_when_no_runtime_refs(self) -> None:
        clean = "# Skill\n\nUses ~/data and $AAS_RUNTIME_ROOT only.\n"
        self.assertEqual(render_openclaw_runtime_neutral(clean), clean)

    def test_neutral_helper_fails_closed_on_residual_machine_path(self) -> None:
        leaky = "Run /home/ubuntu/.local/share/x/tool\n"
        with self.assertRaisesRegex(ValueError, "leaks machine paths"):
            render_openclaw_runtime_neutral(leaky)

    def test_substitution_then_note_then_clean(self) -> None:
        content = "Run `bash ~/.codex/runtime/run_skill.sh skills/x/run_x.sh`\n"
        out = render_openclaw_runtime_neutral(content)
        self.assertNotIn(".codex/runtime", out)
        self.assertIn("$AAS_RUNTIME_ROOT/run_skill.sh", out)
        self.assertIn("$AAS_BROKER_ENDPOINT", out)
        self.assertEqual(path_leak_scan(out), [])


if __name__ == "__main__":
    unittest.main()
