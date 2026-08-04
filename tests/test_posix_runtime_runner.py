from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipIf(os.name == "nt", "POSIX runtime runner is not a native Windows target")
class PosixRuntimeRunnerTests(unittest.TestCase):
    def _runtime(self, root: Path, script_body: str) -> tuple[Path, Path]:
        runtime = root / "runtime"
        command = runtime / "workspace" / "skills" / "demo" / "run.sh"
        command.parent.mkdir(parents=True)
        command.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + script_body, encoding="utf-8")
        command.chmod(0o755)
        runner = runtime / "run_skill.sh"
        source = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_skill.sh"
        shutil.copy2(source, runner)
        runner.chmod(0o755)
        return runner, command

    def test_explicit_python_is_exported_and_precedes_workspace_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner, _ = self._runtime(
                root,
                "printf '%s\\n' \"$AAS_RUNTIME_PYTHON\"\n"
                "command -v python3\n"
                "python3 -c 'import sys; print(sys.executable)'\n",
            )
            python_bin = root / "python-bin"
            python_bin.mkdir()
            explicit_python = python_bin / "python3"
            explicit_python.symlink_to(Path(sys.executable).resolve())
            env = os.environ.copy()
            env["AAS_RUNTIME_PYTHON"] = str(explicit_python)

            completed = subprocess.run(
                ["bash", str(runner), "skills/demo/run.sh"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            lines = completed.stdout.splitlines()
            self.assertEqual(lines[0], str(explicit_python))
            self.assertEqual(lines[1], str(explicit_python))
            self.assertEqual(Path(lines[2]).resolve(), Path(sys.executable).resolve())

    def test_unsafe_explicit_python_fails_before_child_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "child-ran"
            runner, _ = self._runtime(root, f"touch {marker}\n")
            env = os.environ.copy()
            env["AAS_RUNTIME_PYTHON"] = "relative/python3"

            completed = subprocess.run(
                ["bash", str(runner), "skills/demo/run.sh"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 127)
            self.assertIn("absolute path or command name", completed.stderr)
            self.assertFalse(marker.exists())

    def test_non_python_executable_fails_without_creating_version_paths(self) -> None:
        echo = Path("/bin/echo")
        if not echo.is_file():
            self.skipTest("/bin/echo is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "child-ran"
            runner, _ = self._runtime(root, f"touch {marker}\n")
            env = os.environ.copy()
            env["AAS_RUNTIME_PYTHON"] = str(echo)

            completed = subprocess.run(
                ["bash", str(runner), "skills/demo/run.sh"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 127)
            self.assertIn("version 3.10 or newer", completed.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse((root / "runtime" / "workspace" / ".local").exists())


if __name__ == "__main__":
    unittest.main()
