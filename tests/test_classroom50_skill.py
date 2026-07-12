"""Registration checks for the classroom50 skill (ADR v2.1 A1–A6 static)."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Classroom50SkillTests(unittest.TestCase):
    def test_skill_registered(self):
        skills = json.loads((ROOT / "manifest/skills.yaml").read_text(encoding="utf-8"))
        self.assertIn("classroom50", skills["skills"])
        entry = skills["skills"]["classroom50"]
        self.assertIn("course-management", entry.get("profiles") or [])
        for dep in (
            "python-runtime",
            "github-cli",
            "course-hoanganhduc-python-package",
        ):
            self.assertIn(dep, entry["required_dependencies"])

        for v in ("file-exists", "metadata-valid", "agent-visible"):
            self.assertIn(v, entry["verification"])

    def test_package_dependency(self):
        deps = json.loads((ROOT / "manifest/dependencies.yaml").read_text(encoding="utf-8"))
        pkg = deps["packages"]["course-hoanganhduc-python-package"]
        self.assertEqual(pkg["type"], "python")
        self.assertEqual(pkg["module"], "course_hoanganhduc")
        self.assertEqual(pkg["candidate_set"], "agent")

    def test_skill_body_agent_entrypoint(self):
        body = (ROOT / "canonical/skills/classroom50/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("course_hoanganhduc.c50_agent", body)
        self.assertIn("name: classroom50", body)
        for line in body.splitlines():
            self.assertIsNone(
                re.match(r"^\s*`?gh teacher\b", line),
                msg=f"execution-shaped gh teacher line: {line!r}",
            )


if __name__ == "__main__":
    unittest.main()
