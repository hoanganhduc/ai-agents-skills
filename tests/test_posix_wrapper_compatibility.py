"""Real /bin/bash wrappers, synthetic credentials, fake Python payloads; no services.

Bash 3.2 on macOS must execute these tests, not trigger a version-wide skip.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

RUNTIME = Path(__file__).resolve().parents[1] / "canonical" / "runtime"
CASES = (
    ("calibre", "run_cal.sh", "cal.py"),
    ("hetzner-research-compute", "run_hetzner_research_compute.sh", "hetzner_research_compute.py"),
    ("hetzner-research-compute", "run_hetzner_reaper.sh", "hetzner_reaper.py"),
    ("kaggle-research-compute", "run_kaggle_research_compute.sh", "kaggle_research_compute.py"),
    ("modal-research-compute", "run_modal_research_compute.sh", "modal_research_compute.py"),
    ("lean-explore-mcp", "run_lean_explore_mcp.sh", "lean_explore_mcp.py"),
)
VALUES = {
    "HCLOUD_TOKEN": "compat-hetzner-canary",
    "HCLOUD_SSH_KEYS": "compat-ssh-canary",
    "KAGGLE_API_TOKEN": "compat-kaggle-canary",
    "KAGGLE_CONFIG_DIR": "/synthetic/kaggle-config",
    "CALIBRE_GDRIVE_FOLDER_ID": "compat-calibre-canary",
    "LEANEXPLORE_API_KEY": "compat-lean-canary",
}
OBSERVED = tuple(VALUES) + ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "OPENAI_API_KEY")


@unittest.skipIf(os.name == "nt", "POSIX wrappers are not native Windows targets")
class PosixWrapperCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not Path("/bin/bash").is_file() or not Path("/usr/bin/python3").is_file():
            raise AssertionError("native /bin/bash and attested /usr/bin/python3 are required")

    def _stage(self, root: Path, case: tuple[str, str, str], *, managed: bool = False,
               credentials: bool = True, reexec: bool = False,
               inherited: dict[int, Path] | None = None) -> tuple[list[str], dict[str, str], Path]:
        skill, wrapper_name, program_name = case
        runtime = root / "runtime"
        skill_dir = runtime / "workspace" / "skills" / skill
        skill_dir.mkdir(parents=True)
        for directory in (root, runtime, runtime / "workspace", runtime / "workspace" / "skills", skill_dir):
            directory.chmod(0o700)
        runner = runtime / "run_skill.sh"
        shutil.copy2(RUNTIME / "runners" / "run_skill.sh", runner)
        runner.chmod(0o700)
        shutil.copy2(RUNTIME / "runners" / "load_secret_env.py", runtime / "load_secret_env.py")
        (runtime / "load_secret_env.py").chmod(0o600)
        wrapper = skill_dir / wrapper_name
        shutil.copy2(RUNTIME / "skills" / skill / wrapper_name, wrapper)
        wrapper.chmod(0o700)
        env = {"HOME": str(root), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
               "AAS_RUNTIME_PYTHON": "/usr/bin/python3", "OPENAI_API_KEY": "cross-lane-canary"}
        expected: dict[str, str] = {}
        if credentials:
            if skill == "calibre":
                expected = {"CALIBRE_GDRIVE_FOLDER_ID": VALUES["CALIBRE_GDRIVE_FOLDER_ID"]}
                pointer = root / "calibre.json"
                pointer.write_text(json.dumps(expected), encoding="utf-8")
                env["AAS_CALIBRE_SECRETS_FILE"] = str(pointer)
                if not managed:
                    env.update(expected)
            elif skill == "lean-explore-mcp":
                expected = {"LEANEXPLORE_API_KEY": VALUES["LEANEXPLORE_API_KEY"]}
                pointer = root / "lean.env"
                pointer.write_text("LEANEXPLORE_API_KEY=" + VALUES["LEANEXPLORE_API_KEY"] + "\n", encoding="utf-8")
                if managed:
                    env["AAS_SKILL_SECRETS_FILE"] = str(pointer)
                else:
                    env.update(expected)
            else:
                pointer = root / "compute.env"
                keys = ("HCLOUD_TOKEN", "HCLOUD_SSH_KEYS", "KAGGLE_API_TOKEN", "KAGGLE_CONFIG_DIR")
                pointer.write_text("".join(k + "=" + VALUES[k] + "\n" for k in keys), encoding="utf-8")
                env["AAS_COMPUTE_SECRETS_FILE"] = str(pointer)
                keys = ("HCLOUD_TOKEN", "HCLOUD_SSH_KEYS") if skill.startswith("hetzner") else (
                    ("KAGGLE_API_TOKEN", "KAGGLE_CONFIG_DIR") if skill.startswith("kaggle") else ())
                expected = {k: VALUES[k] for k in keys}
            pointer.chmod(0o600)
        probe = (
            "import json, os, subprocess\n"
            f"expected = {expected!r}\n"
            f"observed = {{k: os.environ.get(k) for k in {OBSERVED!r}}}\n"
            "key_fd = os.environ.pop('AAS_LEANEXPLORE_KEY_FD', '')\n"
            "if key_fd:\n"
            "    fd = int(key_fd)\n"
            "    observed['LEANEXPLORE_API_KEY'] = os.read(fd, 4098).rstrip(b'\\n').decode('utf-8')\n"
            "    os.close(fd)\n"
            "result = {'ran': True, 'projection_ok': all(v == expected.get(k) for k, v in observed.items())}\n"
        )
        if reexec:
            probe += (
                "try:\n"
                "    child = subprocess.run([os.environ.get('AAS_RUNTIME_PYTHON', '/usr/bin/python3'), '-I', '-c', "
                "'print(42)'], close_fds=True, capture_output=True, text=True, timeout=10)\n"
                "    result['reexec_ok'] = child.returncode == 0 and child.stdout.strip() == '42'\n"
                "except OSError:\n"
                "    result['reexec_ok'] = False\n"
            )
        if inherited:
            probe += f"inherited = { {fd: str(p) for fd, p in inherited.items()}!r}\n"
            probe += (
                "result['inherited_ok'] = all((os.fstat(fd).st_dev, os.fstat(fd).st_ino) == "
                "(os.stat(path).st_dev, os.stat(path).st_ino) for fd, path in inherited.items())\n"
            )
        probe += "print(json.dumps(result, sort_keys=True))\n"
        (skill_dir / program_name).write_text(probe, encoding="utf-8")
        (skill_dir / program_name).chmod(0o600)
        command = ["/bin/bash", "-p", str(wrapper), "doctor"]
        if managed:
            command = ["/bin/bash", "-p", str(runner), f"skills/{skill}/{wrapper_name}", "doctor"]
        return command, env, wrapper

    def _run(self, command: list[str], env: dict[str, str], root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, cwd=root, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30, check=False)

    def _assert_ok(self, result: subprocess.CompletedProcess[str], *fields: str) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        for value in (*VALUES.values(), "cross-lane-canary"):
            self.assertNotIn(value, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        for field in ("ran", "projection_ok", *fields):
            self.assertIs(payload[field], True, field)

    def test_direct_and_managed_wrappers_project_only_their_credentials(self) -> None:
        for case in CASES:
            for managed in (False, True):
                with self.subTest(wrapper=case[1], managed=managed), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp).resolve()
                    command, env, _ = self._stage(root, case, managed=managed)
                    self._assert_ok(self._run(command, env, root))

    def test_every_wrapper_also_runs_without_credentials(self) -> None:
        for case in CASES:
            with self.subTest(wrapper=case[1]), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                command, env, _ = self._stage(root, case, credentials=False)
                self._assert_ok(self._run(command, env, root))

    def test_exported_python_remains_usable_after_child_closes_inherited_fds(self) -> None:
        for case in CASES:
            for managed in (False, True):
                with self.subTest(wrapper=case[1], managed=managed), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp).resolve()
                    command, env, _ = self._stage(root, case, managed=managed, reexec=True)
                    self._assert_ok(self._run(command, env, root), "reexec_ok")

    def test_occupied_read_and_write_descriptors_are_not_clobbered(self) -> None:
        for case in CASES:
            with self.subTest(wrapper=case[1]), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                read_file, write_file = root / "read-sentinel", root / "write-sentinel"
                read_file.write_text("sentinel", encoding="utf-8")
                command, env, _ = self._stage(root, case, inherited={10: read_file, 11: write_file,
                                                                   12: read_file, 13: write_file})
                prelude = 'exec 10<"$1" 11>"$2" 12<"$1" 13>"$2"; shift 2; exec "$@"'
                command = ["/bin/bash", "-p", "-c", prelude, "fd-test", str(read_file), str(write_file), *command]
                self._assert_ok(self._run(command, env, root), "inherited_ok")

    def test_paths_are_data_not_shell_source(self) -> None:
        for case in CASES:
            with self.subTest(wrapper=case[1]), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve() / "space ' quote $(false) unicode-\u00e9"
                command, env, _ = self._stage(root, case)
                self._assert_ok(self._run(command, env, root))

    def test_each_credential_wrapper_rejects_an_untrusted_interpreter(self) -> None:
        for case in CASES:
            for managed in (False, True):
                with self.subTest(wrapper=case[1], managed=managed), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp).resolve()
                    command, env, _ = self._stage(root, case, managed=managed)
                    marker = root / "untrusted-executed"
                    fake = root / "untrusted-python"
                    fake.write_text(f'#!/bin/sh\n: > "{marker}"\nexit 97\n', encoding="utf-8")
                    fake.chmod(0o700)
                    env["AAS_RUNTIME_PYTHON"] = str(fake)
                    result = self._run(command, env, root)
                    self.assertEqual(result.returncode, 127, result.stderr)
                    self.assertFalse(marker.exists())
                    self.assertEqual(result.stdout, "")

    def test_descriptor_exhaustion_fails_closed(self) -> None:
        for case in CASES:
            with self.subTest(wrapper=case[1]), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                command, env, _ = self._stage(root, case)
                prelude = 'fd=10; while [ "$fd" -lt 200 ]; do eval "exec ${fd}</dev/null"; fd=$((fd+1)); done; exec "$@"'
                command = ["/bin/bash", "-p", "-c", prelude, "fd-exhaustion", *command]
                result = self._run(command, env, root)
                self.assertEqual(result.returncode, 127, result.stderr)
                self.assertIn("no unused runtime descriptor", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_lean_key_redirection_does_not_evaluate_credential_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            command, env, wrapper = self._stage(root, CASES[-1])
            value = "literal$(printf injected);'double\"backtick`text`"
            env["LEANEXPLORE_API_KEY"] = value
            program = wrapper.with_name("lean_explore_mcp.py")
            program.write_text(program.read_text().replace(repr(VALUES["LEANEXPLORE_API_KEY"]), repr(value)), encoding="utf-8")
            result = self._run(command, env, root)
            self._assert_ok(result)
            self.assertNotIn(value, result.stdout + result.stderr)

    def test_lean_invalid_key_is_refused_without_disclosure(self) -> None:
        for value in ("first\nsecond", "first\rsecond", "x" * 4097):
            with self.subTest(length=len(value)), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                command, env, _ = self._stage(root, CASES[-1])
                env["LEANEXPLORE_API_KEY"] = value
                result = self._run(command, env, root)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertNotIn(value, result.stderr)

    def test_each_managed_wrapper_rejects_an_unsafe_launcher(self) -> None:
        for case in CASES:
            with self.subTest(wrapper=case[1]), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                command, env, _ = self._stage(root, case, managed=True)
                (root / "runtime" / "run_skill.sh").chmod(0o720)
                result = self._run(command, env, root)
                self.assertEqual(result.returncode, 127, result.stderr)
                self.assertIn("owner-controlled launcher", result.stderr)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
