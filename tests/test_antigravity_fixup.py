from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.antigravity_fixup import antigravity_fixup, merged_settings
from installer.ai_agents_skills.cli import main
from installer.ai_agents_skills.render import render_management_notice


class AntigravityFixupTests(unittest.TestCase):
    def test_merged_settings_adds_workspace_and_repairs_known_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            merged, changes = merged_settings(
                {
                    "gcp": {"project": "demo-project\n"},
                    "model": "Gemini 3.1 Pro (High)",
                    "statusLine": {"type": "", "command": "", "enabled": True},
                    "trustedWorkspaces": [],
                },
                [workspace.resolve()],
            )
            self.assertEqual(merged["gcp"]["project"], "demo-project")
            self.assertEqual(merged["statusLine"], {"enabled": False})
            self.assertEqual(merged["trustedWorkspaces"], [str(workspace.resolve())])
            self.assertEqual(
                [change["action"] for change in changes],
                ["trim-whitespace", "add-workspaces", "disable-empty-status-line"],
            )

    def test_fixup_apply_preserves_existing_settings_and_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "repo"
            workspace.mkdir()
            settings_path = root / ".gemini" / "antigravity-cli" / "settings.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text(
                json.dumps({
                    "enableTelemetry": False,
                    "trustedWorkspaces": ["/tmp/existing"],
                }, indent=2) + "\n",
                encoding="utf-8",
            )
            result = antigravity_fixup(root, workspace=str(workspace), apply=True)
            self.assertEqual(result["operation"], "update")
            written = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(written["enableTelemetry"], False)
            self.assertEqual(
                written["trustedWorkspaces"],
                ["/tmp/existing", str(workspace.resolve())],
            )

    def test_cli_fixup_defaults_to_current_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "home"
            workspace = Path(tmp) / "repo"
            workspace.mkdir()
            previous = Path.cwd()
            try:
                os.chdir(workspace)
                exit_code = main(["--root", str(root), "antigravity-fixup", "--apply"])
            finally:
                os.chdir(previous)
            self.assertEqual(exit_code, 0)
            settings_path = root / ".gemini" / "antigravity-cli" / "settings.json"
            written = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(written["trustedWorkspaces"], [str(workspace.resolve())])

    def test_antigravity_management_notice_includes_workspace_guardrails(self) -> None:
        notice = render_management_notice("antigravity")
        self.assertIn("Resolve the intended workspace", notice)
        self.assertIn("explicit confirmation before execution", notice)


if __name__ == "__main__":
    unittest.main()
