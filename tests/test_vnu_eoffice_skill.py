from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VnuEofficeSkillTests(unittest.TestCase):
    def test_windows_commands_use_quoted_powershell_paths(self) -> None:
        body = (ROOT / "canonical" / "skills" / "vnu-eoffice" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn(r'& "$env:USERPROFILE\.vnu-eoffice_venv\Scripts\vnu-eoffice.exe"', body)
        self.assertIn(r'& "$env:USERPROFILE\.vnu-eoffice_venv\Scripts\python.exe" -m vnu_eoffice', body)
        self.assertNotIn(r"%USERPROFILE%\.vnu-eoffice_venv", body)

    def test_frontmatter_does_not_require_python3_alias(self) -> None:
        body = (ROOT / "canonical" / "skills" / "vnu-eoffice" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn('"python3"', body.split("---", 2)[1])

    def test_posix_commands_prefer_the_managed_dedicated_venv(self) -> None:
        body = (ROOT / "canonical" / "skills" / "vnu-eoffice" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn('vnu_python="$HOME/.vnu-eoffice_venv/bin/python"', body)
        self.assertIn('"$vnu_python" -m vnu_eoffice <command> [options]', body)
        self.assertIn("do not silently\n  install or switch environments", body)

    def test_required_python_packages_share_vnu_candidate_set(self) -> None:
        skills = json.loads((ROOT / "manifest" / "skills.yaml").read_text(encoding="utf-8"))["skills"]
        dependencies = json.loads(
            (ROOT / "manifest" / "dependencies.yaml").read_text(encoding="utf-8")
        )["packages"]
        name = "vnu-eoffice-python-package"
        self.assertIn(name, skills["vnu-eoffice"]["required_dependencies"])
        self.assertEqual(dependencies[name]["module"], "vnu_eoffice")
        self.assertEqual(dependencies[name]["modules"], ["vnu_eoffice", "requests", "bs4"])
        self.assertEqual(dependencies[name]["candidate_set"], "vnu-eoffice")
        self.assertTrue(dependencies[name]["authoritative_first_existing"])

    def test_delivery_is_queue_only_and_no_bot_authority_reaches_package(self) -> None:
        body = (ROOT / "canonical" / "skills" / "vnu-eoffice" / "SKILL.md").read_text(encoding="utf-8")
        skills = json.loads((ROOT / "manifest" / "skills.yaml").read_text(encoding="utf-8"))["skills"]

        self.assertIn("authenticated host queue", body)
        self.assertIn("never invoke its `send` command", body)
        self.assertIn("AAS_FILE_DELIVERY_SECRETS_FILE", body)
        self.assertNotIn("python3 -m vnu_eoffice send", body)
        self.assertNotIn("telegram-bot-config", skills["vnu-eoffice"]["optional_dependencies"])
        self.assertIn(
            "authenticated-host-file-delivery",
            skills["vnu-eoffice"]["optional_capabilities"],
        )


if __name__ == "__main__":
    unittest.main()
