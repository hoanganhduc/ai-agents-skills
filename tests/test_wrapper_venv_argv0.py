from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
RUNTIME_SOURCE = REPO / "canonical" / "runtime"
ADOPTING_COMMANDS = (
    ("skills/zotero/run_zot.sh", ("--help",), "zot.py"),
    ("skills/calibre/run_cal.sh", ("--help",), "cal.py"),
    ("skills/docling/run_docling.sh", ("doctor",), "doctor.py"),
    (
        "skills/research-digest-wrapper/run_research_digest.sh",
        ("--help",),
        "research_digest.py",
    ),
)
NON_ADOPTING_COMMANDS = (
    ("skills/axiom-axle-mcp/run_axiom_axle_mcp.sh", ("smoke",), "smoke"),
    (
        "skills/submission-venue-selector/run_submission_venue_selector.sh",
        ("smoke",),
        "smoke",
    ),
    (
        "skills/lean-research-library/run_lean_research_library.sh",
        ("doctor",),
        "doctor",
    ),
    ("skills/send-email/run_send_email.sh", ("--help",), "help"),
    ("skills/send-email/send_email.py", ("--help",), "help"),
    ("skills/remote-bridge/run_remote_bridge.sh", ("--help",), "help"),
    ("skills/remote-bridge/remote_bridge.py", ("--help",), "help"),
    ("skills/remote-bridge/dispatch_aas.py", (), "retired"),
    ("skills/vnthuquan/run_vnthuquan.sh", ("--help",), "help"),
    ("skills/vnthuquan/vnthuquan_wrapper.py", ("--help",), "help"),
    ("skills/zotero/send_file.sh", (), "delivery"),
    ("skills/zotero/send_telegram.sh", (), "delivery"),
    ("skills/zotero/send_queue_worker.sh", ("--help",), "help"),
    ("skills/zotero/send_queue.py", ("--help",), "help"),
    (
        "skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh",
        ("selftest",),
        "selftest",
    ),
    (
        "skills/autonomous-research-loop-runtime/force-loop/run_force_loop.sh",
        ("--help",),
        "help",
    ),
)
DOCLING_USAGE = (
    "usage: run_docling.sh <doctor|convert|extract|chunk|quality|ocrspace-smoke> [args...]\n"
)


def _bash_supports_descriptor_binding() -> bool:
    try:
        probe = subprocess.run(
            ["/bin/bash", "-c", 'printf %s "${BASH_VERSINFO[0]}.${BASH_VERSINFO[1]}"'],
            check=False,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=10,
        )
    except OSError:
        return False
    parts = probe.stdout.strip().split(".")
    return len(parts) == 2 and all(part.isdigit() for part in parts) and (
        int(parts[0]), int(parts[1])
    ) >= (4, 4)


@unittest.skipUnless(sys.platform == "linux", "venv argv0 checks require Linux")
@unittest.skipUnless(Path("/usr/bin/python3").is_file(), "attested OS python3 is absent")
@unittest.skipUnless(_bash_supports_descriptor_binding(), "bash >= 4.4 is required")
class WrapperVenvArgv0Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.root.chmod(0o700)
        previous_umask = os.umask(0o077)
        try:
            cls.home = cls.root / "home"
            cls.home.mkdir(mode=0o700)
            cls.venv = cls.root / "venv"
            subprocess.run(
                ["/usr/bin/python3", "-I", "-m", "venv", "--without-pip", str(cls.venv)],
                check=True,
                text=True,
                encoding="utf-8",
                capture_output=True,
                env={"HOME": str(cls.home), "PATH": "/usr/bin:/bin"},
                timeout=30,
            )
            cls.markers = cls.venv / "markers"
            cls.markers.mkdir(mode=0o700)
            site_packages = next((cls.venv / "lib").glob("python*/site-packages"))
            (site_packages / "sitecustomize.py").write_text(
                "import json, os, sys\n"
                f"with open({str(cls.markers)!r} + '/' + str(os.getpid()) + '.json', "
                "'a', encoding='utf-8') as stream:\n"
                "    stream.write(json.dumps({'argv': sys.argv, 'prefix': sys.prefix, "
                "'executable': sys.executable, 'path': os.environ.get('PATH')}) + '\\n')\n",
                encoding="utf-8",
            )
            # Debian's stdlib sitecustomize precedes site-packages. Put the
            # controlled fixture first so the normal startup import sees it.
            (site_packages / "marker_sitecustomize.pth").write_text(
                f"import sys; sys.path.insert(0, {str(site_packages)!r})\n",
                encoding="utf-8",
            )
            cls.runtime = cls.root / "runtime"
            shutil.copytree(RUNTIME_SOURCE / "workspace", cls.runtime / "workspace")
            shutil.copytree(RUNTIME_SOURCE / "skills", cls.runtime / "workspace" / "skills")
            for source in (RUNTIME_SOURCE / "runners").iterdir():
                if source.is_file():
                    shutil.copy2(source, cls.runtime / source.name)
            for path in cls.runtime.rglob("*"):
                if not path.is_symlink():
                    path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
            for command, _, _ in (*ADOPTING_COMMANDS, *NON_ADOPTING_COMMANDS):
                (cls.runtime / "workspace" / command).chmod(0o755)
            cls.runtime.chmod(0o700)
            cls.runner = cls.runtime / "run_skill.sh"
            cls.group_writable_venv = cls.root / "group-writable-venv"
            shutil.copytree(cls.venv, cls.group_writable_venv, symlinks=True)
            cls.group_writable_venv.chmod(0o770)
            cls.hostile_venv = cls.root / "hostile-venv"
            shutil.copytree(cls.venv, cls.hostile_venv, symlinks=True)
            cls.hostile_venv.chmod(0o700)
            # Keep bin/python independently valid so admission reaches the
            # separate bin/python3 symlink check rather than its target check.
            hostile_argv0 = cls.hostile_venv / "bin" / "python"
            hostile_argv0.unlink()
            hostile_argv0.symlink_to(Path("/usr/bin/python3").resolve())
            hostile_python = cls.hostile_venv / "bin" / "python3"
            hostile_python.unlink()
            cls.hostile_marker = cls.root / "hostile-python-ran"
            hostile_python.write_text(
                f"#!/bin/sh\nprintf ran > '{cls.hostile_marker}'\n",
                encoding="utf-8",
            )
            hostile_python.chmod(0o700)
        finally:
            os.umask(previous_umask)

    def setUp(self) -> None:
        self._clear_markers()

    def _clear_markers(self) -> None:
        for path in self.markers.glob("*.json"):
            path.unlink()

    def _launch(
        self,
        command: str,
        arguments: tuple[str, ...],
        *,
        venv: Path | None,
        direct: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if venv is not None:
            env["AAS_SKILL_VENV"] = str(venv)
        argv = (
            [str(self.runtime / "workspace" / command), *arguments]
            if direct
            else ["/bin/bash", str(self.runner), command, *arguments]
        )
        return subprocess.run(
            argv,
            input="not json" if command.endswith(("/send_file.sh", "/send_telegram.sh")) else "",
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            env=env,
            cwd=self.runtime / "workspace",
            timeout=60,
        )

    def _assert_adoption(self, row: tuple[str, tuple[str, ...], str]) -> None:
        command, arguments, script_name = row
        completed = self._launch(command, arguments, venv=self.venv)
        records = [
            json.loads(line)
            for path in self.markers.glob("*.json")
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        expected_script = str(self.runtime / "workspace" / Path(command).parent / script_name)
        self.assertTrue(
            any(
                record["prefix"] == str(self.venv)
                and record["executable"] == str(self.venv / "bin" / "python")
                and record["path"].startswith(f"{self.venv}/bin:")
                and expected_script in record["argv"]
                for record in records
            ),
            f"no venv marker for {command}: {records}; rc={completed.returncode}; "
            f"stderr={completed.stderr}",
        )

    def test_adopting_commands_use_venv(self) -> None:
        for row in ADOPTING_COMMANDS:
            with self.subTest(command=row[0]):
                self._clear_markers()
                self._assert_adoption(row)

    def test_non_adopting_commands_reach_the_program_without_venv(self) -> None:
        for command, arguments, expectation in NON_ADOPTING_COMMANDS:
            with self.subTest(command=command):
                self._clear_markers()
                completed = self._launch(command, arguments, venv=self.venv)
                self.assertEqual(list(self.markers.glob("*.json")), [])
                expected_rc = 2 if expectation in {"delivery", "retired"} else 0
                self.assertEqual(completed.returncode, expected_rc, completed.stderr)
                if expectation == "help":
                    self.assertRegex(completed.stdout, r"(?i)usage")
                elif expectation == "retired":
                    self.assertEqual(
                        json.loads(completed.stdout)["error_code"],
                        "openclaw_control_adapter_retired",
                    )
                elif expectation == "delivery":
                    payload = json.loads(completed.stdout)
                    self.assertEqual(payload["status"], "error")
                    self.assertEqual(
                        payload["message"],
                        "file-delivery stdin is not valid bounded UTF-8 JSON",
                    )
                elif expectation == "selftest":
                    self.assertIn('"smoke_mode": "offline"', completed.stdout)

    def test_group_writable_venv_is_refused_before_either_kind_of_wrapper(self) -> None:
        for command, arguments, _ in (ADOPTING_COMMANDS[0], NON_ADOPTING_COMMANDS[0]):
            with self.subTest(command=command):
                completed = self._launch(command, arguments, venv=self.group_writable_venv)
                self.assertEqual(completed.returncode, 127, completed.stderr)
                self.assertIn("inadmissible skill Python venv", completed.stderr)
                self.assertEqual(list(self.markers.glob("*.json")), [])

    def test_regular_venv_python_is_refused_without_execution(self) -> None:
        for command, arguments, _ in (ADOPTING_COMMANDS[0], NON_ADOPTING_COMMANDS[0]):
            with self.subTest(command=command):
                completed = self._launch(command, arguments, venv=self.hostile_venv)
                self.assertEqual(completed.returncode, 127, completed.stderr)
                self.assertIn("bin/python3 is not a symlink", completed.stderr)
                self.assertFalse(self.hostile_marker.exists())
                self.assertEqual(list(self.markers.glob("*.json")), [])

    def test_no_venv_preserves_the_wrappers_own_exit_codes(self) -> None:
        for command, arguments, _ in (*ADOPTING_COMMANDS, *NON_ADOPTING_COMMANDS):
            with self.subTest(command=command):
                baseline = self._launch(command, arguments, venv=None, direct=True)
                completed = self._launch(command, arguments, venv=None)
                self.assertEqual(completed.returncode, baseline.returncode, completed.stderr)
                self.assertEqual(list(self.markers.glob("*.json")), [])

    def test_docling_help_aliases_print_usage_to_stdout(self) -> None:
        for argument in ("--help", "-h", "help"):
            with self.subTest(argument=argument):
                completed = self._launch("skills/docling/run_docling.sh", (argument,), venv=None)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, DOCLING_USAGE)
                self.assertEqual(completed.stderr, "")

    def test_docling_without_arguments_keeps_usage_on_stderr(self) -> None:
        completed = self._launch("skills/docling/run_docling.sh", (), venv=None)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, DOCLING_USAGE)


if __name__ == "__main__":
    unittest.main()
