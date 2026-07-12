"""Registration and reciprocity checks for course-management skills/profile."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COURSE_SKILLS = (
    "classroom50",
    "course-canvas",
    "course-google-classroom",
    "course-db",
)


class CourseManagementSkillsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skills = json.loads((ROOT / "manifest/skills.yaml").read_text(encoding="utf-8"))[
            "skills"
        ]
        self.profiles = json.loads(
            (ROOT / "manifest/profiles.yaml").read_text(encoding="utf-8")
        )["profiles"]
        self.deps = json.loads(
            (ROOT / "manifest/dependencies.yaml").read_text(encoding="utf-8")
        )

    def test_profile_lists_all_course_skills(self):
        self.assertIn("course-management", self.profiles)
        listed = self.profiles["course-management"]["skills"]
        self.assertEqual(set(listed), set(COURSE_SKILLS))

    def test_reciprocal_membership(self):
        for sk in COURSE_SKILLS:
            self.assertIn(sk, self.skills)
            self.assertIn("course-management", self.skills[sk]["profiles"])

    def test_package_dep(self):
        pkg = self.deps["packages"]["course-hoanganhduc-python-package"]
        self.assertEqual(pkg["module"], "course_hoanganhduc")

    def test_skill_bodies_and_entrypoints(self):
        expected = {
            "classroom50": "course_hoanganhduc.c50_agent",
            "course-canvas": "course_hoanganhduc.canvas_agent",
            "course-google-classroom": "course_hoanganhduc.gclass_agent",
            "course-db": "course_hoanganhduc.db_agent",
        }
        for sk, entry in expected.items():
            path = ROOT / "canonical/skills" / sk / "SKILL.md"
            self.assertTrue(path.is_file(), sk)
            body = path.read_text(encoding="utf-8")
            self.assertIn(f"name: {sk}", body)
            self.assertIn(entry, body)
            for line in body.splitlines():
                self.assertIsNone(
                    re.match(r"^\s*`?gh teacher\b", line),
                    msg=f"{sk}: forbidden line {line!r}",
                )

    def test_required_deps_include_package(self):
        for sk in COURSE_SKILLS:
            deps = self.skills[sk]["required_dependencies"]
            self.assertIn("python-runtime", deps)
            self.assertIn("course-hoanganhduc-python-package", deps)
        self.assertIn("github-cli", self.skills["classroom50"]["required_dependencies"])


if __name__ == "__main__":
    unittest.main()
