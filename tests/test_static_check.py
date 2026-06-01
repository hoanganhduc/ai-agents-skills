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


if __name__ == "__main__":
    unittest.main()
