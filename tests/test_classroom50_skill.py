"""Registration checks for the classroom50 skill (ADR v2.1 A1–A6 static)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.ai_agents_skills.agents import detect_agents, target_for
from installer.ai_agents_skills.discovery import discover_tool, split_command
from installer.ai_agents_skills.manifest import load_manifests
from installer.ai_agents_skills.planner import build_plan

ROOT = Path(__file__).resolve().parents[1]
ALL_AGENT_TARGETS = (
    "codex",
    "claude",
    "deepseek",
    "copilot",
    "opencode",
    "antigravity",
    "grok",
    "kimi",
    "openclaw",
)
RESTORE_TARGET_MODES = {
    "codex": "reference",
    "claude": "symlink",
    "deepseek": "reference",
    "copilot": "reference",
    "opencode": "copy",
    "antigravity": "copy",
    "grok": "copy",
    "kimi": "copy",
    "openclaw": "copy",
}


class Classroom50SkillTests(unittest.TestCase):
    def test_skill_registered(self):
        skills = json.loads((ROOT / "manifest/skills.yaml").read_text(encoding="utf-8"))
        self.assertIn("classroom50", skills["skills"])
        entry = skills["skills"]["classroom50"]
        self.assertIn("course-management", entry.get("profiles") or [])
        for dep in (
            "python-runtime",
            "github-cli",
            "classroom50-teacher-extension",
            "course-hoanganhduc-python-package",
        ):
            self.assertIn(dep, entry["required_dependencies"])

        self.assertEqual(set(entry["supported_agents"]), set(ALL_AGENT_TARGETS))

        for v in ("file-exists", "metadata-valid", "agent-visible"):
            self.assertIn(v, entry["verification"])

    def test_package_dependency(self):
        deps = json.loads((ROOT / "manifest/dependencies.yaml").read_text(encoding="utf-8"))
        pkg = deps["packages"]["course-hoanganhduc-python-package"]
        self.assertEqual(pkg["type"], "python")
        self.assertEqual(pkg["module"], "course_hoanganhduc")
        self.assertEqual(pkg["candidate_set"], "course")

    def test_teacher_extension_dependency_is_distinct_from_generic_gh(self):
        manifests = load_manifests()
        tools = manifests["dependencies"]["tools"]
        self.assertIn("classroom50-teacher-extension", tools)
        spec = tools["classroom50-teacher-extension"]
        self.assertNotEqual(spec, tools["github-cli"])
        self.assertEqual(spec["version_constraint"], "any")
        self.assertIn(
            "~/.local/share/gh/extensions/gh-teacher/gh-teacher",
            spec["candidates"]["linux"],
        )
        self.assertNotIn("${GH_TEACHER_BIN}", spec["candidates"]["linux"])
        self.assertNotIn("%GH_TEACHER_BIN%", spec["candidates"]["windows"])
        self.assertNotRegex(spec["description"], r"\bv?\d+\.\d+\.\d+\b")

    def test_generic_gh_alone_does_not_satisfy_teacher_extension(self):
        manifests = load_manifests()
        spec = manifests["dependencies"]["tools"]["classroom50-teacher-extension"]
        with tempfile.TemporaryDirectory() as tmp:
            # Exercise the command renderer's quoting on every host, not only
            # when a native Windows temporary-directory path happens to need it.
            root = Path(tmp) / "agent root with spaces"
            bin_dir = root / "bin"
            bin_dir.mkdir(parents=True)
            gh = bin_dir / "gh"
            gh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            gh.chmod(0o755)

            with patch.dict(
                os.environ,
                {
                    "PATH": str(bin_dir),
                    "GH_TEACHER_BIN": "",
                    "XDG_DATA_HOME": "",
                },
                clear=False,
            ):
                missing = discover_tool(
                    "classroom50-teacher-extension",
                    spec,
                    "linux",
                    root,
                )
                self.assertEqual(missing["status"], "missing")

                extension = (
                    root
                    / ".local"
                    / "share"
                    / "gh"
                    / "extensions"
                    / "gh-teacher"
                    / "gh-teacher"
                )
                extension.parent.mkdir(parents=True)
                extension.write_text(
                    "#!/bin/sh\nprintf '%s\\n' 'gh-teacher fixture'\n",
                    encoding="utf-8",
                )
                extension.chmod(0o755)
                with patch(
                    "installer.ai_agents_skills.discovery.subprocess.run"
                ) as run:
                    run.return_value.returncode = 0
                    present = discover_tool(
                        "classroom50-teacher-extension",
                        spec,
                        "linux",
                        root,
                    )
                    self.assertIn(
                        ["gh", "teacher", "--help"],
                        [call.args[0] for call in run.call_args_list],
                    )

        self.assertEqual(present["status"], "ok")
        selected = split_command(present["command"])
        self.assertEqual(len(selected), 1)
        self.assertEqual(Path(selected[0]), extension)

    def test_nonfunctional_teacher_extension_is_degraded(self):
        manifests = load_manifests()
        spec = manifests["dependencies"]["tools"]["classroom50-teacher-extension"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extension = (
                root
                / ".local"
                / "share"
                / "gh"
                / "extensions"
                / "gh-teacher"
                / "gh-teacher"
            )
            extension.parent.mkdir(parents=True)
            extension.write_text("#!/bin/sh\nexit 17\n", encoding="utf-8")
            extension.chmod(0o755)
            with (
                patch.dict(
                    os.environ,
                    {"GH_TEACHER_BIN": "", "XDG_DATA_HOME": ""},
                    clear=False,
                ),
                patch("installer.ai_agents_skills.discovery.subprocess.run") as run,
            ):
                run.return_value.returncode = 17
                result = discover_tool(
                    "classroom50-teacher-extension",
                    spec,
                    "linux",
                    root,
                )

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["capabilities"], {"teacher-help": False})
        self.assertIn(
            ["gh", "teacher", "--help"],
            [call.args[0] for call in run.call_args_list],
        )

    def test_skill_body_agent_entrypoint(self):
        body = (ROOT / "canonical/skills/classroom50/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("course_hoanganhduc.c50_agent", body)
        self.assertIn("name: classroom50", body)
        raw_teacher_lines = [
            line.strip()
            for line in body.splitlines()
            if re.match(r"^\s*gh teacher\b", line)
        ]
        self.assertEqual(
            raw_teacher_lines,
            ["gh teacher --help >/dev/null || {", "gh teacher --help *> $null"],
        )
        frontmatter = body.split("---", 2)[1]
        self.assertNotIn('"python3"', frontmatter)
        self.assertIn('"gh"', frontmatter)

    def test_skill_uses_dedicated_posix_course_interpreter(self):
        body = (ROOT / "canonical/skills/classroom50/SKILL.md").read_text(encoding="utf-8")
        self.assertIn('course_python="$HOME/.course_venv/bin/python"', body)
        self.assertIn(
            "course_python=/opt/coding-system/python-closure/course-management/bin/python",
            body,
        )
        self.assertIn('[ "${OPENCLAW_WORKSPACE:-}" = /workspace ]', body)
        self.assertIn('"$course_python" -m course_hoanganhduc.c50_agent', body)
        self.assertNotIn("python3 -m course_hoanganhduc.c50_agent", body)
        self.assertIn("TECHNICAL_FAIL: dedicated course interpreter is missing", body)

    def test_skill_has_native_windows_doctor_and_commands(self):
        body = (ROOT / "canonical/skills/classroom50/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("$doctorFailed = $false", body)
        self.assertIn("if ($LASTEXITCODE -ne 0)", body)
        self.assertIn('if ($doctorFailed) { throw "Classroom50 doctor failed" }', body)
        self.assertIn(
            "& $coursePython -m course_hoanganhduc.c50_agent list-classrooms --org ORG",
            body,
        )

    @unittest.skipUnless(os.name == "posix", "executes the Bash doctor contract")
    def test_bash_doctor_fails_when_an_earlier_check_fails(self):
        body = (ROOT / "canonical/skills/classroom50/SKILL.md").read_text(encoding="utf-8")
        section = body.split("## Safe doctor and readiness", 1)[1]
        doctor = section.split("```bash", 1)[1].split("```", 1)[0].strip()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = teacher ]; then exit 17; fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            course_python = root / "course-python"
            course_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            course_python.chmod(0o755)
            result = subprocess.run(
                ["bash", "-c", doctor],
                env={
                    **os.environ,
                    "PATH": f"{bin_dir}:/usr/bin:/bin",
                    "course_python": str(course_python),
                    "CLASSROOM50_ORG_ALLOWLIST": "configured-without-printing-value",
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TECHNICAL_FAIL: gh teacher is unavailable", result.stderr)
        self.assertNotIn("configured-without-printing-value", result.stdout + result.stderr)

    def test_readiness_section_is_non_mutating_and_secret_safe(self):
        body = (ROOT / "canonical/skills/classroom50/SKILL.md").read_text(encoding="utf-8")
        readiness = body.split("## Safe doctor and readiness", 1)[1].split(
            "## Common agent commands",
            1,
        )[0]
        for forbidden in (
            "list-classrooms",
            "list-roster",
            "list-assignments",
            " sync ",
            " export ",
            "download",
            "invite",
            "unenroll",
            "teardown",
        ):
            self.assertNotIn(forbidden, readiness)
        self.assertIn("gh auth status", readiness)
        self.assertIn("Never add `--show-token`", readiness)
        self.assertIn("CLASSROOM50_ORG_ALLOWLIST=CONFIGURED", readiness)
        self.assertNotIn('printf \'%s\\n\' "$CLASSROOM50_ORG_ALLOWLIST"', readiness)

    def test_classroom50_plans_every_agent_target(self):
        manifests = load_manifests()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(
                os.environ,
                {"XDG_CONFIG_HOME": "", "KIMI_CODE_HOME": ""},
                clear=False,
            ):
                for name in ALL_AGENT_TARGETS:
                    target_for(root, name).home.mkdir(parents=True, exist_ok=True)
                agents = detect_agents(root, ALL_AGENT_TARGETS)
                plan = build_plan(
                    root,
                    manifests,
                    ["classroom50"],
                    agents,
                    platform="linux",
                    requested_agents=list(ALL_AGENT_TARGETS),
                )

        skill_actions = {
            action["agent"]: action
            for action in plan["actions"]
            if action["kind"] == "file"
            and action["artifact_type"] == "skill-file"
            and action["skill"] == "classroom50"
        }
        self.assertEqual(set(skill_actions), set(ALL_AGENT_TARGETS))
        self.assertEqual(
            {
                name: skill_actions[name]["install_mode"]
                for name in RESTORE_TARGET_MODES
            },
            RESTORE_TARGET_MODES,
        )
        self.assertEqual(
            Path(skill_actions["antigravity"]["path"]).name,
            "classroom50.md",
        )


if __name__ == "__main__":
    unittest.main()
