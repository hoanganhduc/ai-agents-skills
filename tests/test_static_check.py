from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.static_check import bash_syntax_path, powershell_parse_script, powershell_single_quoted


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

    def test_windows_python_runner_selects_one_command_when_path_has_duplicates(self) -> None:
        text = Path("canonical/runtime/runners/run_python.ps1").read_text(encoding="utf-8")

        self.assertIn("$commands = @(Get-Command", text)
        self.assertIn("return [string]($commands[0].Source)", text)
        self.assertIn("$python = [string]($commands[0].Source)", text)
        self.assertNotIn("$python = $command.Source", text)

    def test_installed_runtime_copy_uses_portable_open_flags(self) -> None:
        text = Path("installer/ai_agents_skills/runtime_smoke.py").read_text(encoding="utf-8")

        self.assertIn('getattr(os, "O_CLOEXEC", 0)', text)
        self.assertIn('getattr(os, "O_BINARY", 0)', text)
        self.assertNotIn("os.O_RDONLY | os.O_CLOEXEC", text)

    def test_wsl_bash_syntax_path_uses_wslpath_with_forward_slashes(self) -> None:
        converted = Mock(returncode=0, stdout="/mnt/c/repo/script.sh\n")
        script_path = Path("C:/repo/script.sh")
        with (
            patch("tools.static_check.os.name", "nt"),
            patch("tools.static_check.shutil.which", return_value="C:\\Windows\\system32\\wsl.exe"),
            patch("tools.static_check.subprocess.run", return_value=converted) as run,
        ):
            result = bash_syntax_path(
                script_path,
                "C:\\Windows\\system32\\bash.EXE",
            )

        self.assertEqual(result, "/mnt/c/repo/script.sh")
        self.assertNotIn("\\", run.call_args.args[0][-1])

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
