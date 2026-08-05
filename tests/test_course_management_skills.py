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
OPENCLAW_COURSE_SELECTOR = """\
if [ "${HOME:-}" = /workspace ] && [ "${OPENCLAW_WORKSPACE:-}" = /workspace ]; then
  course_python=/opt/coding-system/python-closure/course-management/bin/python
else
  course_python="$HOME/.course_venv/bin/python"
fi
"""
CANVAS_COURSE_SELECTOR = """\
if [ "${HOME:-}" = /workspace ] && [ "${OPENCLAW_WORKSPACE:-}" = /workspace ]; then
  course_python=/opt/coding-system/python-closure/course-management/bin/python
  export CANVAS_CONFIG_PATH=/workspace/.config/course/canvas/config.json
else
  course_python="$HOME/.course_venv/bin/python"
  export CANVAS_CONFIG_PATH="${CANVAS_CONFIG_PATH:-$HOME/.config/course/canvas/config.json}"
fi
"""


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
        self.assertEqual(pkg["candidate_set"], "course")

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
            self.assertNotIn('"python3"', body.split("---", 2)[1])
            for line in body.splitlines():
                match = re.match(r"^\s*gh teacher\b", line)
                if match:
                    self.assertIn(
                        line.strip(),
                        {
                            "gh teacher --help >/dev/null || {",
                            "gh teacher --help *> $null",
                        },
                        msg=f"{sk}: forbidden line {line!r}",
                    )

    def test_skill_bodies_prefer_dedicated_windows_venv(self):
        for sk in COURSE_SKILLS:
            body = (ROOT / "canonical" / "skills" / sk / "SKILL.md").read_text(
                encoding="utf-8"
            )
            self.assertIn(r'$env:USERPROFILE\.course_venv\Scripts\python.exe', body)

    def test_skill_bodies_use_image_local_openclaw_course_environment(self):
        for sk in COURSE_SKILLS:
            body = (ROOT / "canonical" / "skills" / sk / "SKILL.md").read_text(
                encoding="utf-8"
            )
            expected = CANVAS_COURSE_SELECTOR if sk == "course-canvas" else OPENCLAW_COURSE_SELECTOR
            self.assertIn(expected, body, sk)

    def test_course_dependency_uses_only_the_documented_dedicated_venv(self):
        self.assertEqual(
            self.deps["python_candidate_sets"]["course"],
            {
                "linux": ["~/.course_venv/bin/python"],
                "windows": [r".course_venv\Scripts\python.exe"],
            },
        )
        self.assertEqual(
            self.deps["python_site_candidate_sets"]["course"],
            {
                "linux": ["~/.course_venv/lib/python*/site-packages"],
                "windows": [r".course_venv\Lib\site-packages"],
            },
        )

    def test_google_classroom_uses_restored_target_specific_credentials(self):
        body = (
            ROOT / "canonical" / "skills" / "course-google-classroom" / "SKILL.md"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'classroom_credentials="$HOME/.config/course/google-classroom/credentials.json"',
            body,
        )
        self.assertIn(
            'classroom_token="$HOME/.config/course/google-classroom/token.pickle"',
            body,
        )
        self.assertIn(
            "classroom_credentials=/workspace/.config/course/google-classroom/credentials.json",
            body,
        )
        self.assertIn(
            "classroom_token=/workspace/.config/course/google-classroom/token.pickle",
            body,
        )
        self.assertIn("GOOGLE_CLASSROOM_CREDENTIALS", body)
        self.assertIn("GOOGLE_CLASSROOM_TOKEN", body)
        self.assertNotIn("mat1204", body.lower())

    def test_canvas_uses_target_specific_portable_config_defaults(self):
        body = (ROOT / "canonical" / "skills" / "course-canvas" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'export CANVAS_CONFIG_PATH="${CANVAS_CONFIG_PATH:-$HOME/.config/course/canvas/config.json}"',
            body,
        )
        self.assertIn(
            "export CANVAS_CONFIG_PATH=/workspace/.config/course/canvas/config.json",
            body,
        )

    def test_required_deps_include_package(self):
        for sk in COURSE_SKILLS:
            deps = self.skills[sk]["required_dependencies"]
            self.assertIn("python-runtime", deps)
            self.assertIn("course-hoanganhduc-python-package", deps)
        self.assertIn("github-cli", self.skills["classroom50"]["required_dependencies"])
        self.assertIn(
            "classroom50-teacher-extension",
            self.skills["classroom50"]["required_dependencies"],
        )


if __name__ == "__main__":
    unittest.main()
