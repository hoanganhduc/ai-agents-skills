from __future__ import annotations

import unittest

from installer.ai_agents_skills.sanitize import has_sensitive_material, sanitize_text


class SanitizationTests(unittest.TestCase):
    def test_sanitize_replaces_personal_paths_and_tokens(self) -> None:
        fake_token = "gho_" + "abcdefghijklmnopqrstuvwxyz123456"
        fake_aws_key = "AKIA" + "A" * 16
        fake_google_key = "AIza" + "A" * 35
        fake_slack_token = "xoxb-" + "1" * 24
        text = (
            "path=/home/exampleuser/project\n"
            "win=/windows/Users/exampleuser/.codex\n"
            "email=person@example.com\n"
            f"token={fake_token}\n"
            f"aws={fake_aws_key}\n"
            f"google={fake_google_key}\n"
            f"slack={fake_slack_token}\n"
        )
        result = sanitize_text(text, canonical_name="sample-skill")
        self.assertNotIn("/home/exampleuser", result)
        self.assertNotIn("/windows/Users/exampleuser", result)
        self.assertNotIn("person@example.com", result)
        self.assertNotIn(fake_token, result)
        self.assertNotIn(fake_aws_key, result)
        self.assertNotIn(fake_google_key, result)
        self.assertNotIn(fake_slack_token, result)
        self.assertIn("<LINUX_HOME>", result)
        self.assertIn("<WINDOWS_HOME>", result)
        self.assertIn("<EMAIL>", result)
        self.assertIn("<REDACTED_SECRET>", result)

    def test_sanitize_normalizes_frontmatter_name(self) -> None:
        text = "---\nname: legacy_name\ndescription: test\n---\n\n# Test\n"
        self.assertIn("name: canonical-name", sanitize_text(text, "canonical-name"))

    def test_sensitive_material_detector_ignores_placeholders(self) -> None:
        self.assertFalse(has_sensitive_material("<LINUX_HOME> <WINDOWS_HOME> <EMAIL>"))
        self.assertTrue(has_sensitive_material("/home/exampleuser/file"))


if __name__ == "__main__":
    unittest.main()
