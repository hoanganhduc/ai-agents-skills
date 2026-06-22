from __future__ import annotations

import unittest
from pathlib import Path

from tools.static_check import powershell_parse_script, powershell_single_quoted


class StaticCheckTests(unittest.TestCase):
    def test_powershell_single_quoted_escapes_embedded_quotes(self) -> None:
        self.assertEqual(powershell_single_quoted("a'b"), "'a''b'")

    def test_powershell_parse_script_embeds_absolute_path_without_args(self) -> None:
        path = Path("canonical/runtime/runners/run_skill.ps1")
        script = powershell_parse_script(path)

        self.assertNotIn("$args[0]", script)
        self.assertIn("$path=", script)
        self.assertIn("[System.Management.Automation.Language.Parser]::ParseFile($path", script)
        self.assertIn(str(path.resolve()).replace("'", "''"), script)

    def test_runtime_skill_sources_do_not_document_codex_runtime_runner(self) -> None:
        forbidden = (
            "bash ~/.codex/runtime/run_skill.sh",
            "~/.codex/runtime/workspace",
            "$HOME/.codex/runtime/workspace",
            "%USERPROFILE%\\.codex\\runtime",
        )
        root = Path("canonical/runtime/skills")
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".md", ".py", ".sh", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                with self.subTest(path=str(path), token=token):
                    self.assertNotIn(token, text)

    def test_canonical_skill_runtime_guidance_avoids_codex_runtime_paths(self) -> None:
        forbidden = (
            "bash ~/.codex/runtime/run_skill.sh",
            "~/.codex/runtime/workspace",
            "$HOME/.codex/runtime",
            "$env:USERPROFILE\\.codex\\runtime",
            "%USERPROFILE%\\.codex\\runtime",
            "Codex-only installs the runtime",
            "Codex runtime runner",
            "vendored Codex runtime",
        )
        for path in Path("canonical/skills").glob("*/SKILL.md"):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                with self.subTest(path=str(path), token=token):
                    self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
