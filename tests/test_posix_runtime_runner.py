from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


@unittest.skipIf(os.name == "nt", "POSIX runtime runner is not a native Windows target")
class PosixRuntimeRunnerTests(unittest.TestCase):
    def _runtime(
        self,
        root: Path,
        script_body: str,
        *,
        command_rel: str = "skills/demo/run.sh",
    ) -> tuple[Path, Path]:
        runtime = root / "runtime"
        command = runtime / "workspace" / command_rel
        command.parent.mkdir(parents=True)
        command.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + script_body, encoding="utf-8")
        command.chmod(0o755)
        runner = runtime / "run_skill.sh"
        source = Path(__file__).resolve().parents[1] / "canonical" / "runtime" / "runners" / "run_skill.sh"
        shutil.copy2(source, runner)
        runner.write_text(
            runner.read_text(encoding="utf-8").replace(
                "credential_runtime_enforcement=1",
                "credential_runtime_enforcement=0",
                1,
            ),
            encoding="utf-8",
        )
        runner.chmod(0o755)
        loader_source = (
            Path(__file__).resolve().parents[1]
            / "canonical"
            / "runtime"
            / "runners"
            / "load_secret_env.py"
        )
        loader = runtime / "load_secret_env.py"
        shutil.copy2(loader_source, loader)
        loader.chmod(0o644)
        runtime.chmod(0o755)
        current = command.parent
        while current.is_relative_to(runtime / "workspace"):
            current.chmod(0o755)
            if current == runtime / "workspace":
                break
            current = current.parent
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

    def test_prelude_never_runs_hostile_dirname_before_selector_and_secret_scrub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner, _ = self._runtime(
                root,
                "printf '%s|%s|%s\\n' \"$AXLE_API_KEY\" "
                "\"${OPENAI_API_KEY:-missing}\" \"${AAS_SKILL_SECRETS_FILE:-missing}\"\n",
                command_rel="skills/axiom-axle-mcp/run_axiom_axle_mcp.sh",
            )
            authority = root / "skill.env"
            authority.write_text("AXLE_API_KEY=approved-value\n", encoding="utf-8")
            authority.chmod(0o600)
            hostile = root / "hostile-bin"
            hostile.mkdir()
            marker = root / "hostile-dirname-env"
            fake_dirname = hostile / "dirname"
            fake_dirname.write_text(
                "#!/bin/sh\n"
                f"printf '%s|%s\\n' \"${{AAS_SKILL_SECRETS_FILE:-}}\" "
                f"\"${{OPENAI_API_KEY:-}}\" > {marker}\n"
                "exit 97\n",
                encoding="utf-8",
            )
            fake_dirname.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{hostile}:/usr/bin:/bin",
                    "AAS_SKILL_SECRETS_FILE": str(authority),
                    "OPENAI_API_KEY": "ambient-must-not-reach-tools",
                }
            )

            completed = subprocess.run(
                ["/bin/bash", str(runner), "skills/axiom-axle-mcp/run_axiom_axle_mcp.sh"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "approved-value|missing|missing")
            self.assertFalse(marker.exists())

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

    def test_runner_disables_python_bytecode_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module_dir = root / "probe"
            module_dir.mkdir()
            (module_dir / "imported.py").write_text("VALUE = 'loaded'\n", encoding="utf-8")
            runner, _ = self._runtime(
                root,
                "printf '%s\\n' \"$PYTHONDONTWRITEBYTECODE\"\n"
                f"PYTHONPATH={str(module_dir)!r} python3 -c 'import imported; print(imported.VALUE)'\n",
            )
            env = os.environ.copy()
            env["PYTHONDONTWRITEBYTECODE"] = "0"

            completed = subprocess.run(
                ["bash", str(runner), "skills/demo/run.sh"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.splitlines(), ["1", "loaded"])
            self.assertFalse((module_dir / "__pycache__").exists())

    def test_skill_secret_file_projects_into_a_minimal_child_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner, _ = self._runtime(
                root,
                "printf '%s|%s|%s|%s|%s|%s|%s\\n' \"$AXLE_API_KEY\" "
                "\"${ZENODO_TOKEN:-missing}\" \"${AAS_SKILL_SECRETS_FILE:-missing}\" "
                "\"${OUTSIDE_VALUE:-missing}\" \"${AWS_SECRET_ACCESS_KEY:-missing}\" "
                "\"${DATABASE_URL:-missing}\" \"${CLOUDFLARE_API_TOKEN:-missing}\"\n",
                command_rel="skills/axiom-axle-mcp/run_axiom_axle_mcp.sh",
            )
            secrets = root / "skill-secrets.env"
            secrets.write_text(
                "# restored skill credentials\n"
                "AXLE_API_KEY=restored-axle-value\n",
                encoding="utf-8",
            )
            secrets.chmod(0o600)
            env = os.environ.copy()
            env.update(
                {
                    "AAS_SKILL_SECRETS_FILE": str(secrets),
                    "AXLE_API_KEY": "stale-inherited-value",
                    "ZENODO_TOKEN": "stale-ambient-zenodo",
                    "OUTSIDE_VALUE": "preserved",
                    "AWS_SECRET_ACCESS_KEY": "ambient-aws-secret",
                    "DATABASE_URL": "ambient-database-secret",
                    "CLOUDFLARE_API_TOKEN": "ambient-cloudflare-secret",
                }
            )

            completed = subprocess.run(
                ["bash", str(runner), "skills/axiom-axle-mcp/run_axiom_axle_mcp.sh"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                completed.stdout.strip(),
                "restored-axle-value|missing|missing|missing|missing|missing|missing",
            )
            self.assertEqual(env["AXLE_API_KEY"], "stale-inherited-value")

    def test_credential_command_replacement_after_binding_executes_original_inode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner, command = self._runtime(
                root,
                "printf 'original:%s\\n' \"$AXLE_API_KEY\"\n",
                command_rel="skills/axiom-axle-mcp/run_axiom_axle_mcp.sh",
            )
            secret = root / "skill-secrets.env"
            secret.write_text("AXLE_API_KEY=descriptor-bound-value\n", encoding="utf-8")
            secret.chmod(0o600)
            ready = root / "loader-ready"
            release = root / "loader-release"
            loader = root / "runtime" / "load_secret_env.py"
            loader_text = loader.read_text(encoding="utf-8")
            needle = "    try:\n        os.execvpe(command[0], command, child_env)\n"
            synchronization = (
                f"    Path({str(ready)!r}).write_text('ready', encoding='utf-8')\n"
                "    import time\n"
                f"    while not Path({str(release)!r}).exists():\n"
                "        time.sleep(0.01)\n"
                "    try:\n"
                "        os.execvpe(command[0], command, child_env)\n"
            )
            self.assertIn(needle, loader_text)
            loader.write_text(loader_text.replace(needle, synchronization), encoding="utf-8")
            loader.chmod(0o644)
            replacement = root / "replacement.sh"
            replacement.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "printf 'replacement:%s\\n' \"$AXLE_API_KEY\"\n",
                encoding="utf-8",
            )
            replacement.chmod(0o755)
            env = os.environ.copy()
            env["AAS_SKILL_SECRETS_FILE"] = str(secret)

            process = subprocess.Popen(
                ["/bin/bash", str(runner), "skills/axiom-axle-mcp/run_axiom_axle_mcp.sh"],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "credential loader did not reach the bind barrier")
            os.replace(replacement, command)
            release.write_text("go", encoding="utf-8")
            stdout, stderr = process.communicate(timeout=15)

            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(stdout.strip(), "original:descriptor-bound-value")
            self.assertNotIn("replacement:", stdout + stderr)

    def test_direct_wrapper_ignores_unauthenticated_command_origin_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = (
                Path(__file__).resolve().parents[1]
                / "canonical"
                / "runtime"
                / "skills"
                / "research-digest-wrapper"
            )
            legitimate = root / "legitimate"
            legitimate.mkdir()
            wrapper = legitimate / "run_research_digest.sh"
            shutil.copy2(source_dir / wrapper.name, wrapper)
            wrapper.chmod(0o755)
            (legitimate / "research_digest.py").write_text(
                "print('legitimate-helper')\n", encoding="utf-8"
            )
            hostile = root / "hostile"
            hostile.mkdir()
            hostile_wrapper = hostile / wrapper.name
            hostile_wrapper.write_text("not executed\n", encoding="utf-8")
            (hostile / "research_digest.py").write_text(
                "print('hostile-helper')\n", encoding="utf-8"
            )
            env = os.environ.copy()
            env.update(
                {
                    "AAS_RUNTIME_COMMAND_PATH": str(hostile_wrapper),
                    "AAS_RUNTIME_COMMAND_FD": "999999",
                    "PATH": "/usr/bin:/bin",
                }
            )

            completed = subprocess.run(
                ["/bin/bash", str(wrapper)],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "legitimate-helper")

    def test_resolved_python_precedes_hostile_path_during_secret_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner, _ = self._runtime(
                root,
                "printf '%s\\n' \"$AXLE_API_KEY\"\n",
                command_rel="skills/axiom-axle-mcp/run_axiom_axle_mcp.sh",
            )
            secrets = root / "skill-secrets.env"
            secrets.write_text("AXLE_API_KEY=restored-safe-value\n", encoding="utf-8")
            secrets.chmod(0o600)
            hostile_bin = root / "hostile-bin"
            hostile_bin.mkdir()
            marker = root / "hostile-python-ran"
            hostile_python = hostile_bin / "python3"
            hostile_python.write_text(
                f"#!/bin/sh\ntouch {marker}\nexit 99\n",
                encoding="utf-8",
            )
            hostile_python.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "AAS_RUNTIME_PYTHON": str(Path(sys.executable).resolve()),
                    "AAS_SKILL_SECRETS_FILE": str(secrets),
                    "PATH": f"{hostile_bin}:/usr/bin:/bin",
                }
            )

            completed = subprocess.run(
                ["bash", str(runner), "skills/axiom-axle-mcp/run_axiom_axle_mcp.sh"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "restored-safe-value")
            self.assertFalse(marker.exists())

    def test_skill_secret_file_rejects_invalid_records_without_child_or_value_leak(self) -> None:
        cases = {
            "unknown": "NOT_ALLOWED=must-not-leak\n",
            "duplicate": "AXLE_API_KEY=must-not-leak\nAXLE_API_KEY=second-value\n",
            "empty": "AXLE_API_KEY=\n",
        }
        for label, body in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                marker = root / "child-ran"
                runner, _ = self._runtime(
                    root,
                    f"touch {marker}\n",
                    command_rel="skills/axiom-axle-mcp/run_axiom_axle_mcp.sh",
                )
                secrets = root / "skill-secrets.env"
                secrets.write_text(body, encoding="utf-8")
                secrets.chmod(0o600)
                env = os.environ.copy()
                env["AAS_SKILL_SECRETS_FILE"] = str(secrets)

                completed = subprocess.run(
                    ["bash", str(runner), "skills/axiom-axle-mcp/run_axiom_axle_mcp.sh"],
                    check=False,
                    text=True,
                    capture_output=True,
                    env=env,
                    timeout=30,
                )

                self.assertEqual(completed.returncode, 2)
                self.assertFalse(marker.exists())
                self.assertNotIn("must-not-leak", completed.stdout + completed.stderr)

    def test_skill_secret_file_rejects_relative_public_and_linked_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "child-ran"
            runner, _ = self._runtime(
                root,
                f"touch {marker}\n",
                command_rel="skills/axiom-axle-mcp/run_axiom_axle_mcp.sh",
            )
            secrets = root / "skill-secrets.env"
            secrets.write_text("AXLE_API_KEY=must-not-leak\n", encoding="utf-8")
            secrets.chmod(0o640)
            linked = root / "linked.env"
            linked.symlink_to(secrets)
            candidates = ("relative.env", str(secrets), str(linked))

            for candidate in candidates:
                with self.subTest(candidate=candidate):
                    env = os.environ.copy()
                    env["AAS_SKILL_SECRETS_FILE"] = candidate
                    completed = subprocess.run(
                        ["bash", str(runner), "skills/axiom-axle-mcp/run_axiom_axle_mcp.sh"],
                        check=False,
                        text=True,
                        capture_output=True,
                        env=env,
                        timeout=30,
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertFalse(marker.exists())
                    self.assertNotIn(
                        "must-not-leak", completed.stdout + completed.stderr
                    )

    @unittest.skipUnless(os.name == "posix", "POSIX single-link and size checks")
    def test_skill_secret_file_rejects_hardlinks_and_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "child-ran"
            runner, _ = self._runtime(
                root,
                f"touch {marker}\n",
                command_rel="skills/axiom-axle-mcp/run_axiom_axle_mcp.sh",
            )
            secrets = root / "skill-secrets.env"
            secrets.write_text("AXLE_API_KEY=must-not-leak\n", encoding="utf-8")
            secrets.chmod(0o600)
            hardlink = root / "hardlinked.env"
            os.link(secrets, hardlink)

            oversized = root / "oversized.env"
            oversized.write_text(
                "AXLE_API_KEY=" + ("x" * 65_536) + "\n",
                encoding="utf-8",
            )
            oversized.chmod(0o600)
            fifo = root / "blocking.env"
            os.mkfifo(fifo, 0o600)
            for candidate in (secrets, hardlink, oversized, fifo):
                with self.subTest(candidate=candidate):
                    env = os.environ.copy()
                    env["AAS_SKILL_SECRETS_FILE"] = str(candidate)
                    completed = subprocess.run(
                        ["bash", str(runner), "skills/axiom-axle-mcp/run_axiom_axle_mcp.sh"],
                        check=False,
                        text=True,
                        capture_output=True,
                        env=env,
                        timeout=30,
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertFalse(marker.exists())
                    self.assertNotIn(
                        "must-not-leak", completed.stdout + completed.stderr
                    )

    def test_unmapped_skill_pointer_has_empty_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "child-ran"
            runner, _ = self._runtime(root, f"touch {marker}\n")
            secrets = root / "skill-secrets.env"
            secrets.write_text("AXLE_API_KEY=must-not-leak\n", encoding="utf-8")
            secrets.chmod(0o600)
            env = os.environ.copy()
            env["AAS_SKILL_SECRETS_FILE"] = str(secrets)

            completed = subprocess.run(
                [str(runner), "skills/demo/run.sh"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertFalse(marker.exists())
            self.assertNotIn("must-not-leak", completed.stdout + completed.stderr)

    def test_exact_schema_scrubs_sibling_and_startup_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner, _ = self._runtime(
                root,
                "printf '%s|%s|%s|%s|%s\\n' "
                '"$SEMANTIC_SCHOLAR_API_KEY" "$UNPAYWALL_EMAIL" '
                '"${ZENODO_TOKEN:-missing}" "${BASH_ENV:-missing}" "$PATH"\n',
                command_rel="skills/submission-venue-selector/run_submission_venue_selector.sh",
            )
            secrets = root / "skill-secrets.env"
            secrets.write_text(
                "SEMANTIC_SCHOLAR_API_KEY=semantic-value\n"
                "UNPAYWALL_EMAIL=operator-at-example.invalid\n",
                encoding="utf-8",
            )
            secrets.chmod(0o600)
            startup = root / "hostile-startup.sh"
            startup_marker = root / "hostile-startup-ran"
            startup.write_text(f"touch {startup_marker}\n", encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "AAS_SKILL_SECRETS_FILE": str(secrets),
                    "ZENODO_TOKEN": "ambient-sibling-secret",
                    "BASH_ENV": str(startup),
                }
            )

            completed = subprocess.run(
                [str(runner), "skills/submission-venue-selector/run_submission_venue_selector.sh"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                completed.stdout.strip(),
                "semantic-value|operator-at-example.invalid|missing|missing|/usr/bin:/bin",
            )
            self.assertFalse(startup_marker.exists())

    def test_research_digest_rejects_unconsumed_s2_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "child-ran"
            runner, _ = self._runtime(
                root,
                f"touch {marker}\n",
                command_rel="skills/research-digest-wrapper/run_research_digest.sh",
            )
            secrets = root / "skill-secrets.env"
            secrets.write_text("S2_API_KEY=must-not-load\n", encoding="utf-8")
            secrets.chmod(0o600)
            env = os.environ.copy()
            env.update(
                {
                    "AAS_SKILL_SECRETS_FILE": str(secrets),
                    "S2_API_KEY": "ambient-alias",
                }
            )
            completed = subprocess.run(
                [str(runner), "skills/research-digest-wrapper/run_research_digest.sh"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertFalse(marker.exists())
            self.assertNotIn("must-not-load", completed.stdout + completed.stderr)

    def test_credential_launch_does_not_use_workspace_venv_or_local_bin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner, _ = self._runtime(
                root,
                "printf '%s|%s\\n' \"$(readlink -f -- \"$AAS_RUNTIME_PYTHON\")\" \"${PYTHONPATH:-missing}\"\n",
                command_rel="skills/axiom-axle-mcp/run_axiom_axle_mcp.sh",
            )
            hostile = root / "runtime" / ".venv" / "bin" / "python3"
            hostile.parent.mkdir(parents=True)
            marker = root / "hostile-python-ran"
            hostile.write_text(f"#!/bin/sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
            hostile.chmod(0o755)
            secrets = root / "skill-secrets.env"
            secrets.write_text("AXLE_API_KEY=safe-value\n", encoding="utf-8")
            secrets.chmod(0o600)
            env = os.environ.copy()
            env["AAS_SKILL_SECRETS_FILE"] = str(secrets)

            completed = subprocess.run(
                [str(runner), "skills/axiom-axle-mcp/run_axiom_axle_mcp.sh"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            python_path, projected_path = completed.stdout.strip().split("|", 1)
            self.assertTrue(Path(python_path).samefile("/usr/bin/python3"))
            self.assertEqual(projected_path, "missing")
            self.assertFalse(marker.exists())
            self.assertFalse((root / "runtime" / "workspace" / ".local").exists())

    def test_calibre_projection_is_exact_flat_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner, _ = self._runtime(
                root,
                "printf '%s|%s|%s|%s\\n' \"$GDRIVE_CREDENTIALS\" "
                '"$CALIBRE_GDRIVE_FOLDER_ID" "${ZOTERO_API_KEY:-missing}" '
                '"${AAS_SECRETS_FILE:-missing}"\n',
                command_rel="skills/calibre/run_cal.sh",
            )
            secrets = root / "calibre-secrets.json"
            secrets.write_text(
                '{"GDRIVE_CREDENTIALS":"gdrive-value",'
                '"CALIBRE_GDRIVE_FOLDER_ID":"folder-value"}\n',
                encoding="utf-8",
            )
            secrets.chmod(0o600)
            env = os.environ.copy()
            env.update(
                {
                    "AAS_CALIBRE_SECRETS_FILE": str(secrets),
                    "ZOTERO_API_KEY": "ambient-zotero-secret",
                    "AAS_SECRETS_FILE": "/must/not/reach/child",
                }
            )

            completed = subprocess.run(
                [str(runner), "skills/calibre/run_cal.sh"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                completed.stdout.strip(),
                "gdrive-value|folder-value|missing|missing",
            )


if __name__ == "__main__":
    unittest.main()
