from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VnuEofficeSkillTests(unittest.TestCase):
    def test_windows_commands_use_quoted_powershell_paths(self) -> None:
        body = (ROOT / "canonical" / "skills" / "vnu-eoffice" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn(r'& "$env:USERPROFILE\.vnu-eoffice_venv\Scripts\vnu-eoffice.exe"', body)
        self.assertIn(r'& "$env:USERPROFILE\.vnu-eoffice_venv\Scripts\python.exe" -m vnu_eoffice', body)
        self.assertNotIn(r"%USERPROFILE%\.vnu-eoffice_venv", body)


if __name__ == "__main__":
    unittest.main()
