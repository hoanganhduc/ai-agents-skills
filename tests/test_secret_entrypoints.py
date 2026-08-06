from __future__ import annotations

import json
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
RUNTIME_SOURCE = REPO / "canonical" / "runtime"
COMPUTE_KEYS = {
    "HCLOUD_TOKEN",
    "HCLOUD_SSH_KEYS",
    "KAGGLE_API_TOKEN",
    "KAGGLE_CONFIG_DIR",
}
MODAL_KEYS = {"MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"}
PROVIDER_KEYS = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "COPILOT_GITHUB_TOKEN",
    "COPILOT_PROVIDER_API_KEY",
    "COPILOT_PROVIDER_BEARER_TOKEN",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GOOGLE_API_KEY",
    "GROK_API_KEY",
    "KIMI_API_KEY",
    "MOONSHOT_API_KEY",
    "OPENAI_API_KEY",
    "OPENCODE_API_KEY",
    "XAI_API_KEY",
}
SKILL_KEYS = {
    "AXLE_API_KEY",
    "LEANEXPLORE_API_KEY",
    "OCR_SPACE_API_KEY",
    "OCR_SPACE_KEY",
    "OCRSPACE_API_KEY",
    "OCRSPACE_KEY",
    "OPENCLAW_S2_API_KEY",
    "SEMANTIC_SCHOLAR_API_KEY",
    "UNPAYWALL_EMAIL",
    "ZENODO_TOKEN",
}
OBSERVED_KEYS = sorted(COMPUTE_KEYS | PROVIDER_KEYS)
OBSERVED_ENV_KEYS = sorted(COMPUTE_KEYS | MODAL_KEYS | PROVIDER_KEYS | SKILL_KEYS) + [
    "PATENTSVIEW_API_KEY",
    "S2_API_KEY",
    "AAS_COMPUTE_SECRETS_FILE",
    "AAS_PROVIDER_SECRETS_FILE",
    "AAS_SKILL_SECRETS_FILE",
    "AAS_HETZNER_HCLOUD_BIN",
    "AAS_HETZNER_SCP_BIN",
    "AAS_HETZNER_SSH_BIN",
    "AAS_HETZNER_RSYNC_BIN",
    "PYTHONHOME",
    "PYTHONPATH",
]
HCLOUD_KEYS = {"HCLOUD_TOKEN", "HCLOUD_SSH_KEYS"}
KAGGLE_KEYS = {"KAGGLE_API_TOKEN", "KAGGLE_CONFIG_DIR"}


@unittest.skipIf(os.name == "nt", "POSIX wrappers are not native Windows targets")
class PosixSecretEntrypointTests(unittest.TestCase):
    @staticmethod
    def _copy_test_runner(destination: Path) -> None:
        shutil.copy2(RUNTIME_SOURCE / "runners" / "run_skill.sh", destination)
        destination.write_text(
            destination.read_text(encoding="utf-8").replace(
                "credential_runtime_enforcement=1",
                "credential_runtime_enforcement=0",
                1,
            ),
            encoding="utf-8",
        )

    def _stage_entrypoint(
        self,
        root: Path,
        *,
        skill: str,
        wrapper: str,
        python_entrypoint: str,
    ) -> Path:
        runtime = root / "runtime"
        skill_dir = runtime / "workspace" / "skills" / skill
        skill_dir.mkdir(parents=True)
        shutil.copy2(
            RUNTIME_SOURCE / "runners" / "load_secret_env.py",
            runtime / "load_secret_env.py",
        )
        (runtime / "load_secret_env.py").chmod(0o644)
        wrapper_path = skill_dir / wrapper
        shutil.copy2(RUNTIME_SOURCE / "skills" / skill / wrapper, wrapper_path)
        if skill == "lean-explore-mcp":
            wrapper_path.write_text(
                wrapper_path.read_text(encoding="utf-8").replace(
                    "lean_explore_exact_generation_enforcement=1",
                    "lean_explore_exact_generation_enforcement=0",
                    1,
                ),
                encoding="utf-8",
            )
        wrapper_path.chmod(0o755)
        (skill_dir / python_entrypoint).write_text(
            "from __future__ import annotations\n"
            "import json, os\n"
            "lean_fd_value = None\n"
            "lean_fd = os.environ.pop('AAS_LEANEXPLORE_KEY_FD', '')\n"
            "if lean_fd:\n"
            "    descriptor = int(lean_fd)\n"
            "    lean_fd_value = os.read(descriptor, 4098).rstrip(b'\\n').decode('utf-8')\n"
            "    os.close(descriptor)\n"
            "marker = os.environ.get('TEST_CHILD_MARKER')\n"
            "if marker:\n"
            "    open(marker, 'w', encoding='utf-8').write('ran\\n')\n"
            f"observed = {{key: os.environ.get(key) for key in {OBSERVED_ENV_KEYS!r}}}\n"
            "observed['TEST_LEANEXPLORE_FD_VALUE'] = lean_fd_value\n"
            "print(json.dumps(observed, sort_keys=True))\n",
            encoding="utf-8",
        )
        (skill_dir / python_entrypoint).chmod(0o644)
        for directory in (runtime, runtime / "workspace", runtime / "workspace" / "skills", skill_dir):
            directory.chmod(0o755)
        return wrapper_path

    @staticmethod
    def _private_file(root: Path, name: str, body: str) -> Path:
        path = root / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o600)
        return path

    @staticmethod
    def _env(root: Path) -> dict[str, str]:
        env = {
            "HOME": str(root),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "AAS_RUNTIME_PYTHON": sys.executable,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        return env

    def test_outer_runner_scrubs_every_declared_authority_and_unsupported_alias(self) -> None:
        source = (RUNTIME_SOURCE / "runners" / "run_skill.sh").read_text(
            encoding="utf-8"
        )
        block = source.split("ambient_secret_keys=(", 1)[1].split("\n)", 1)[0]
        scrubbed = set(re.findall(r"\b[A-Z][A-Z0-9_]+\b", block))
        self.assertTrue(
            (COMPUTE_KEYS | MODAL_KEYS | PROVIDER_KEYS | SKILL_KEYS).issubset(scrubbed),
            sorted((COMPUTE_KEYS | MODAL_KEYS | PROVIDER_KEYS | SKILL_KEYS) - scrubbed),
        )
        self.assertTrue({"S2_API_KEY", "PATENTSVIEW_API_KEY"}.issubset(scrubbed))

    def test_direct_structured_wrappers_bind_system_python_and_ignore_hostile_path(self) -> None:
        cases = (
            (
                "send-email",
                "run_send_email.sh",
                "send_email.py",
                "SEND_EMAIL_SECRETS_FILE",
                "SMTP_PASSWORD",
            ),
            (
                "remote-bridge",
                "run_remote_bridge.sh",
                "remote_bridge.py",
                "REMOTE_BRIDGE_SECRETS_FILE",
                "ZULIP_API_KEY",
            ),
        )
        for skill, wrapper_name, entrypoint, pointer, ambient in cases:
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                wrapper = self._stage_entrypoint(
                    root,
                    skill=skill,
                    wrapper=wrapper_name,
                    python_entrypoint=entrypoint,
                )
                program = wrapper.parent / entrypoint
                program.write_text(
                    "import json, os\n"
                    f"print(json.dumps({{'pointer': os.environ.get({pointer!r}), "
                    f"'ambient': os.environ.get({ambient!r})}}))\n",
                    encoding="utf-8",
                )
                program.chmod(0o644)
                authority = self._private_file(root, "structured.json", "{}\n")
                hostile = root / "hostile-bin"
                hostile.mkdir()
                marker = root / "hostile-interpreter-ran"
                for name in ("python3", "python", "dirname", "pwd"):
                    candidate = hostile / name
                    candidate.write_text(
                        f"#!/bin/sh\n: > {marker}\nexit 97\n",
                        encoding="utf-8",
                    )
                    candidate.chmod(0o755)
                env = self._env(root)
                env.update(
                    {
                        "PATH": str(hostile),
                        pointer: str(authority),
                        ambient: "ambient-must-be-scrubbed",
                    }
                )

                completed = subprocess.run(
                    ["/bin/bash", str(wrapper), "selftest"],
                    check=False,
                    text=True,
                    capture_output=True,
                    env=env,
                    timeout=30,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                child = json.loads(completed.stdout)
                self.assertEqual(child["pointer"], str(authority))
                self.assertIsNone(child["ambient"])
                self.assertFalse(marker.exists())
                body = wrapper.read_text(encoding="utf-8")
                self.assertIn("/usr/bin/python3", body)
                self.assertIn('exec "$PYTHON" -I -c "$secure_loader"', body)
                self.assertIn('getattr(os,"O_NOFOLLOW",0)', body)
                self.assertNotIn("command -v python", body)

    def test_direct_structured_wrapper_rejects_replaced_symlink_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wrapper = self._stage_entrypoint(
                root,
                skill="send-email",
                wrapper="run_send_email.sh",
                python_entrypoint="send_email.py",
            )
            helper = wrapper.parent / "send_email.py"
            hostile = root / "hostile.py"
            marker = root / "hostile-helper-ran"
            hostile.write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
                encoding="utf-8",
            )
            hostile.chmod(0o644)
            helper.unlink()
            helper.symlink_to(hostile)
            authority = self._private_file(
                root,
                "send-email.json",
                '{"smtp":{"password":"must-not-leak"}}\n',
            )
            env = self._env(root)
            env["SEND_EMAIL_SECRETS_FILE"] = str(authority)

            completed = subprocess.run(
                ["/bin/bash", str(wrapper), "selftest"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(marker.exists())
            self.assertNotIn("must-not-leak", completed.stdout + completed.stderr)

    def test_direct_zotero_calibre_and_vnthuquan_wrappers_scrub_prelude_and_bind_child(self) -> None:
        cases = (
            (
                "zotero", "run_zot.sh", "zot.py",
                {"AAS_ZOTERO_SECRETS_FILE": "authority.json", "ZOTERO_API_KEY": "selected"},
                {"AAS_ZOTERO_SECRETS_FILE": "authority.json", "ZOTERO_API_KEY": "selected"},
            ),
            (
                "calibre", "run_cal.sh", "cal.py",
                {"AAS_CALIBRE_SECRETS_FILE": "authority.json", "GDRIVE_CREDENTIALS": "selected"},
                {"AAS_CALIBRE_SECRETS_FILE": "authority.json", "GDRIVE_CREDENTIALS": "selected"},
            ),
            (
                "vnthuquan", "run_vnthuquan.sh", "vnthuquan_wrapper.py",
                {
                    "AAS_CALIBRE_SECRETS_FILE": "authority.json",
                    "AAS_FILE_DELIVERY_SECRETS_FILE": "delivery.json",
                    "REMOTE_BRIDGE_SECRETS_FILE": "remote.json",
                    "GDRIVE_CREDENTIALS": "ambient-must-not-cross",
                },
                {
                    "AAS_CALIBRE_SECRETS_FILE": "authority.json",
                    "AAS_FILE_DELIVERY_SECRETS_FILE": None,
                    "REMOTE_BRIDGE_SECRETS_FILE": None,
                    "GDRIVE_CREDENTIALS": None,
                },
            ),
        )
        observed = sorted({
            "AAS_ZOTERO_SECRETS_FILE", "AAS_CALIBRE_SECRETS_FILE",
            "AAS_FILE_DELIVERY_SECRETS_FILE", "REMOTE_BRIDGE_SECRETS_FILE",
            "ZOTERO_API_KEY", "GDRIVE_CREDENTIALS", "PYTHONPATH", "PYTHONHOME",
        })
        for skill, wrapper_name, entrypoint, supplied, expected in cases:
            with self.subTest(wrapper=wrapper_name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                wrapper = self._stage_entrypoint(
                    root, skill=skill, wrapper=wrapper_name, python_entrypoint=entrypoint
                )
                program = wrapper.parent / entrypoint
                program.write_text(
                    "import json, os\n"
                    f"print(json.dumps({{key: os.environ.get(key) for key in {observed!r}}}, sort_keys=True))\n",
                    encoding="utf-8",
                )
                program.chmod(0o644)
                for relative in {value for key, value in supplied.items() if key.endswith("SECRETS_FILE")}:
                    self._private_file(root, relative, "{}\n")
                hostile = root / "hostile-bin"
                hostile.mkdir()
                marker = root / "hostile-prelude-ran"
                for name in ("python3", "python", "dirname", "pwd", "readlink", "stat", "id"):
                    candidate = hostile / name
                    candidate.write_text(
                        f"#!/bin/sh\n: > {marker}\nexit 97\n", encoding="utf-8"
                    )
                    candidate.chmod(0o755)
                preload = root / "preload"
                preload.mkdir()
                (preload / "sitecustomize.py").write_text(
                    f"from pathlib import Path\nPath({str(marker)!r}).write_text('preloaded')\n",
                    encoding="utf-8",
                )
                env = self._env(root)
                env.update({
                    "PATH": str(hostile),
                    "PYTHONPATH": str(preload),
                    "PYTHONHOME": str(root / "hostile-home"),
                    "OPENAI_API_KEY": "cross-lane-must-not-cross",
                })
                for key, value in supplied.items():
                    env[key] = str(root / value) if key.endswith("SECRETS_FILE") else value

                completed = subprocess.run(
                    ["/bin/bash", str(wrapper), "doctor"],
                    check=False,
                    text=True,
                    capture_output=True,
                    env=env,
                    timeout=30,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                child = json.loads(completed.stdout)
                for key, value in expected.items():
                    expected_value = (
                        str(root / value) if value is not None and key.endswith("SECRETS_FILE") else value
                    )
                    self.assertEqual(child[key], expected_value, key)
                self.assertIsNone(child["PYTHONPATH"])
                self.assertIsNone(child["PYTHONHOME"])
                self.assertFalse(marker.exists())
                body = wrapper.read_text(encoding="utf-8")
                self.assertIn("/usr/bin/python3", body)
                self.assertIn("PYTHON_FD", body)
                self.assertIn('exec "$PYTHON" -I -c "$secure_loader"', body)
                self.assertNotIn("command -v python", body)

    def test_direct_zotero_calibre_and_vnthuquan_wrappers_reject_hostile_python_and_linked_helper(self) -> None:
        cases = (
            ("zotero", "run_zot.sh", "zot.py", "AAS_ZOTERO_SECRETS_FILE"),
            ("calibre", "run_cal.sh", "cal.py", "AAS_CALIBRE_SECRETS_FILE"),
            ("vnthuquan", "run_vnthuquan.sh", "vnthuquan_wrapper.py", "AAS_CALIBRE_SECRETS_FILE"),
        )
        for skill, wrapper_name, entrypoint, pointer in cases:
            with self.subTest(wrapper=wrapper_name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                wrapper = self._stage_entrypoint(
                    root, skill=skill, wrapper=wrapper_name, python_entrypoint=entrypoint
                )
                authority = self._private_file(root, "authority.json", "{}\n")
                hostile_python_marker = root / "hostile-python-ran"
                hostile_python = root / "hostile-python"
                hostile_python.write_text(
                    f"#!/bin/sh\n: > {hostile_python_marker}\nexit 99\n", encoding="utf-8"
                )
                hostile_python.chmod(0o755)
                env = self._env(root)
                env.update({pointer: str(authority), "AAS_RUNTIME_PYTHON": str(hostile_python)})
                rejected = subprocess.run(
                    ["/bin/bash", str(wrapper), "doctor"], check=False, text=True,
                    capture_output=True, env=env, timeout=30,
                )
                self.assertEqual(rejected.returncode, 127)
                self.assertFalse(hostile_python_marker.exists())

                helper = wrapper.parent / entrypoint
                hostile_helper_marker = root / "hostile-helper-ran"
                hostile_helper = root / "hostile-helper.py"
                hostile_helper.write_text(
                    f"from pathlib import Path\nPath({str(hostile_helper_marker)!r}).write_text('ran')\n",
                    encoding="utf-8",
                )
                hostile_helper.chmod(0o644)
                helper.unlink()
                helper.symlink_to(hostile_helper)
                env["AAS_RUNTIME_PYTHON"] = sys.executable
                linked = subprocess.run(
                    ["/bin/bash", str(wrapper), "doctor"], check=False, text=True,
                    capture_output=True, env=env, timeout=30,
                )
                self.assertNotEqual(linked.returncode, 0)
                self.assertFalse(hostile_helper_marker.exists())

    def test_compute_wrappers_project_only_their_lane_from_shared_authority(self) -> None:
        cases = (
            (
                "modal-research-compute",
                "run_modal_research_compute.sh",
                "modal_research_compute.py",
                "doctor",
                set(),
            ),
            (
                "hetzner-research-compute",
                "run_hetzner_research_compute.sh",
                "hetzner_research_compute.py",
                "doctor",
                HCLOUD_KEYS,
            ),
            (
                "hetzner-research-compute",
                "run_hetzner_reaper.sh",
                "hetzner_reaper.py",
                "reap",
                HCLOUD_KEYS,
            ),
            (
                "kaggle-research-compute",
                "run_kaggle_research_compute.sh",
                "kaggle_research_compute.py",
                "doctor",
                KAGGLE_KEYS,
            ),
        )
        restored = {
            "HCLOUD_TOKEN": "restored-hetzner",
            "HCLOUD_SSH_KEYS": "restored-ssh",
            "KAGGLE_API_TOKEN": "restored-kaggle",
            "KAGGLE_CONFIG_DIR": "/restored/kaggle",
        }
        for skill, wrapper_name, python_entrypoint, command, expected_keys in cases:
            with self.subTest(wrapper=wrapper_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                wrapper = self._stage_entrypoint(
                    root,
                    skill=skill,
                    wrapper=wrapper_name,
                    python_entrypoint=python_entrypoint,
                )
                secrets = self._private_file(
                    root,
                    "compute.env",
                    "HCLOUD_TOKEN=restored-hetzner\n"
                    "HCLOUD_SSH_KEYS=restored-ssh\n"
                    "KAGGLE_API_TOKEN=restored-kaggle\n"
                    "KAGGLE_CONFIG_DIR=/restored/kaggle\n",
                )
                env = self._env(root)
                env.update(
                    {
                        "AAS_COMPUTE_SECRETS_FILE": str(secrets),
                        "HCLOUD_TOKEN": "stale-ambient-value",
                        "HCLOUD_SSH_KEYS": "stale-ambient-ssh",
                        "KAGGLE_API_TOKEN": "stale-ambient-kaggle",
                        "KAGGLE_CONFIG_DIR": "/stale/ambient/kaggle",
                    }
                )

                completed = subprocess.run(
                    ["bash", str(wrapper), command],
                    check=False,
                    text=True,
                    capture_output=True,
                    env=env,
                    timeout=30,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                child = json.loads(completed.stdout)
                for key in COMPUTE_KEYS:
                    expected = restored[key] if key in expected_keys else None
                    self.assertEqual(child[key], expected, key)
                self.assertIsNone(child["AAS_COMPUTE_SECRETS_FILE"])
                self.assertEqual(env["HCLOUD_TOKEN"], "stale-ambient-value")

    def test_compute_wrappers_scrub_ambient_cross_lane_authority(self) -> None:
        cases = (
            (
                "modal-research-compute",
                "run_modal_research_compute.sh",
                "modal_research_compute.py",
                MODAL_KEYS,
            ),
            (
                "hetzner-research-compute",
                "run_hetzner_research_compute.sh",
                "hetzner_research_compute.py",
                HCLOUD_KEYS,
            ),
            (
                "hetzner-research-compute",
                "run_hetzner_reaper.sh",
                "hetzner_reaper.py",
                HCLOUD_KEYS,
            ),
            (
                "kaggle-research-compute",
                "run_kaggle_research_compute.sh",
                "kaggle_research_compute.py",
                set(),
            ),
        )
        for skill, wrapper_name, python_entrypoint, expected_keys in cases:
            with self.subTest(wrapper=wrapper_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                wrapper = self._stage_entrypoint(
                    root,
                    skill=skill,
                    wrapper=wrapper_name,
                    python_entrypoint=python_entrypoint,
                )
                env = self._env(root)
                env.update(
                    {
                        key: f"ambient-{key.lower()}"
                        for key in COMPUTE_KEYS | MODAL_KEYS
                    }
                )
                completed = subprocess.run(
                    ["bash", str(wrapper), "doctor"],
                    check=False,
                    text=True,
                    capture_output=True,
                    env=env,
                    timeout=30,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                child = json.loads(completed.stdout)
                for key in COMPUTE_KEYS | MODAL_KEYS:
                    expected = env[key] if key in expected_keys else None
                    self.assertEqual(child[key], expected, key)
                self.assertIsNone(child["AAS_COMPUTE_SECRETS_FILE"])

    def test_direct_compute_credentials_are_scrubbed_before_discovery(self) -> None:
        cases = (
            (
                "hetzner-research-compute/run_hetzner_research_compute.sh",
                {
                    "AAS_COMPUTE_SECRETS_FILE": "compute_pointer",
                    "HCLOUD_TOKEN": "hcloud_token",
                    "HCLOUD_SSH_KEYS": "hcloud_ssh_keys",
                },
            ),
            (
                "hetzner-research-compute/run_hetzner_reaper.sh",
                {
                    "AAS_COMPUTE_SECRETS_FILE": "compute_pointer",
                    "HCLOUD_TOKEN": "hcloud_token",
                    "HCLOUD_SSH_KEYS": "hcloud_ssh_keys",
                },
            ),
            (
                "modal-research-compute/run_modal_research_compute.sh",
                {
                    "AAS_COMPUTE_SECRETS_FILE": "compute_pointer",
                    "MODAL_TOKEN_ID": "modal_token_id",
                    "MODAL_TOKEN_SECRET": "modal_token_secret",
                },
            ),
        )
        for relative_path, captures in cases:
            with self.subTest(wrapper=relative_path):
                body = (RUNTIME_SOURCE / "skills" / relative_path).read_text(
                    encoding="utf-8"
                )
                discovery = body.index(
                    'script_path="${BASH_SOURCE[0]:-$0}"'
                )
                for key, local_name in captures.items():
                    capture = f'{local_name}="${{{key}:-}}"'
                    self.assertIn(capture, body)
                    self.assertLess(body.index(capture), discovery)
                unset = "unset " + " ".join(captures)
                self.assertIn(unset, body)
                self.assertLess(body.index(unset), discovery)

    def test_direct_ambient_compute_credentials_reject_hostile_python_before_execution(self) -> None:
        cases = (
            (
                "hetzner-research-compute",
                "run_hetzner_research_compute.sh",
                "hetzner_research_compute.py",
                ["doctor"],
                {"HCLOUD_TOKEN": "ambient-hcloud-canary"},
            ),
            (
                "hetzner-research-compute",
                "run_hetzner_reaper.sh",
                "hetzner_reaper.py",
                ["reap"],
                {"HCLOUD_SSH_KEYS": "ambient-ssh-key-canary"},
            ),
            (
                "modal-research-compute",
                "run_modal_research_compute.sh",
                "modal_research_compute.py",
                ["doctor"],
                {"MODAL_TOKEN_SECRET": "ambient-modal-canary"},
            ),
        )
        for skill, wrapper_name, entrypoint, arguments, credential in cases:
            with self.subTest(wrapper=wrapper_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                wrapper = self._stage_entrypoint(
                    root,
                    skill=skill,
                    wrapper=wrapper_name,
                    python_entrypoint=entrypoint,
                )
                marker = root / "hostile-python-ran"
                hostile_python = root / "hostile-python"
                hostile_python.write_text(
                    f"#!/bin/sh\ntouch {marker}\nexit 99\n", encoding="utf-8"
                )
                hostile_python.chmod(0o755)
                env = self._env(root)
                env.update(credential)
                env["AAS_RUNTIME_PYTHON"] = str(hostile_python)

                completed = subprocess.run(
                    ["/bin/bash", str(wrapper), *arguments],
                    check=False,
                    text=True,
                    capture_output=True,
                    env=env,
                    timeout=30,
                )

                self.assertEqual(completed.returncode, 127)
                self.assertFalse(marker.exists())
                output = completed.stdout + completed.stderr
                for value in credential.values():
                    self.assertNotIn(value, output)

    def test_managed_lean_explore_projection_reaches_only_final_helper_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = self._stage_entrypoint(
                root,
                skill="lean-explore-mcp",
                wrapper="run_lean_explore_mcp.sh",
                python_entrypoint="lean_explore_mcp.py",
            )
            runtime = wrapper.parents[3]
            runner = runtime / "run_skill.sh"
            self._copy_test_runner(runner)
            runner.chmod(0o755)
            authority = self._private_file(
                root,
                "skill.env",
                "LEANEXPLORE_API_KEY=synthetic-lean-explore-key\n",
            )
            env = self._env(root)
            env["AAS_SKILL_SECRETS_FILE"] = str(authority)

            completed = subprocess.run(
                [
                    "/bin/bash",
                    str(runner),
                    "skills/lean-explore-mcp/run_lean_explore_mcp.sh",
                    "doctor",
                ],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            child = json.loads(completed.stdout)
            self.assertIsNone(child["LEANEXPLORE_API_KEY"])
            self.assertEqual(child["TEST_LEANEXPLORE_FD_VALUE"], "synthetic-lean-explore-key")
            self.assertIsNone(child["AAS_SKILL_SECRETS_FILE"])
            for key in (COMPUTE_KEYS | MODAL_KEYS | PROVIDER_KEYS | (SKILL_KEYS - {"LEANEXPLORE_API_KEY"})):
                self.assertIsNone(child[key], key)

    def test_credential_runner_allows_its_attested_workspace_but_rejects_an_external_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = self._stage_entrypoint(
                root,
                skill="lean-explore-mcp",
                wrapper="run_lean_explore_mcp.sh",
                python_entrypoint="lean_explore_mcp.py",
            )
            runtime = wrapper.parents[3]
            runner = runtime / "run_skill.sh"
            self._copy_test_runner(runner)
            runner.chmod(0o755)
            authority = self._private_file(
                root,
                "skill.env",
                "LEANEXPLORE_API_KEY=synthetic-workspace-key\n",
            )
            env = self._env(root)
            env.update(
                {
                    "AAS_SKILL_SECRETS_FILE": str(authority),
                    "AAS_ALLOW_EXTERNAL_RUNTIME_WORKSPACE": "1",
                    "AAS_RUNTIME_WORKSPACE": str(runtime / "workspace"),
                }
            )
            argv = [
                "/bin/bash",
                str(runner),
                "skills/lean-explore-mcp/run_lean_explore_mcp.sh",
                "doctor",
            ]

            same_workspace = subprocess.run(
                argv,
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(same_workspace.returncode, 0, same_workspace.stderr)
            self.assertEqual(
                json.loads(same_workspace.stdout)["TEST_LEANEXPLORE_FD_VALUE"],
                "synthetic-workspace-key",
            )

            external_workspace = root / "external-workspace"
            shutil.copytree(runtime / "workspace", external_workspace)
            external_env = {**env, "AAS_RUNTIME_WORKSPACE": str(external_workspace)}
            external = subprocess.run(
                argv,
                check=False,
                text=True,
                capture_output=True,
                env=external_env,
                timeout=30,
            )

            self.assertEqual(external.returncode, 127)
            self.assertIn(
                "credential-bearing launch refuses an external runtime workspace",
                external.stderr,
            )
            self.assertNotIn("synthetic-workspace-key", external.stdout + external.stderr)

    def test_kaggle_direct_launch_rejects_ambient_credentials_without_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = self._stage_entrypoint(
                root,
                skill="kaggle-research-compute",
                wrapper="run_kaggle_research_compute.sh",
                python_entrypoint="kaggle_research_compute.py",
            )
            env = self._env(root)
            env.update(
                {
                    "KAGGLE_API_TOKEN": "ambient-token-must-not-cross",
                    "KAGGLE_CONFIG_DIR": "/ambient/config/must-not-cross",
                    "KAGGLE_USERNAME": "ambient-user-must-not-cross",
                    "KAGGLE_KEY": "ambient-key-must-not-cross",
                }
            )

            completed = subprocess.run(
                ["bash", str(wrapper), "doctor"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            child = json.loads(completed.stdout)
            self.assertIsNone(child["KAGGLE_API_TOKEN"])
            self.assertIsNone(child["KAGGLE_CONFIG_DIR"])
            self.assertIsNone(child["AAS_COMPUTE_SECRETS_FILE"])

    def test_stdlib_wrappers_treat_exact_managed_selector_as_advisory_only(self) -> None:
        cases = (
            ("remote-bridge", "run_remote_bridge.sh", "remote_bridge.py"),
            ("send-email", "run_send_email.sh", "send_email.py"),
        )
        for skill, wrapper_name, entrypoint in cases:
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                wrapper = self._stage_entrypoint(
                    root,
                    skill=skill,
                    wrapper=wrapper_name,
                    python_entrypoint=entrypoint,
                )
                program = wrapper.parent / entrypoint
                program.write_text(
                    "import json, os\n"
                    "print(json.dumps({'runtime': os.environ.get('AAS_RUNTIME_PYTHON')}))\n",
                    encoding="utf-8",
                )
                program.chmod(0o644)
                selected = (
                    root
                    / ".local/share/coding-system/python-closure/shared/bin/python"
                )
                selected.parent.mkdir(parents=True)
                marker = root / "caller-closure-ran"
                selected.write_text(
                    f"#!/bin/sh\ntouch {marker}\nexit 99\n",
                    encoding="utf-8",
                )
                selected.chmod(0o755)
                hostile_modules = root / "hostile-modules"
                hostile_modules.mkdir()
                startup_marker = root / "ambient-python-hook-ran"
                (hostile_modules / "sitecustomize.py").write_text(
                    f"from pathlib import Path\nPath({str(startup_marker)!r}).write_text('ran')\n",
                    encoding="utf-8",
                )
                env = self._env(root)
                env.update(
                    {
                        "AAS_RUNTIME_PYTHON": str(selected),
                        "PYTHONPATH": str(hostile_modules),
                        "PYTHONSTARTUP": str(hostile_modules / "sitecustomize.py"),
                        "PYTHONINSPECT": "1",
                    }
                )

                completed = subprocess.run(
                    ["bash", str(wrapper), "selftest"],
                    check=False,
                    text=True,
                    capture_output=True,
                    env=env,
                    timeout=30,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertFalse(marker.exists())
                self.assertFalse(startup_marker.exists())
                runtime = json.loads(completed.stdout)["runtime"]
                self.assertRegex(runtime, r"^/(?:proc/self|dev)/fd/[0-9]+$")

    def test_stdlib_wrappers_reject_arbitrary_runtime_selector_without_execution(self) -> None:
        cases = (
            ("remote-bridge", "run_remote_bridge.sh", "remote_bridge.py"),
            ("send-email", "run_send_email.sh", "send_email.py"),
        )
        for skill, wrapper_name, entrypoint in cases:
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                wrapper = self._stage_entrypoint(
                    root,
                    skill=skill,
                    wrapper=wrapper_name,
                    python_entrypoint=entrypoint,
                )
                marker = root / "arbitrary-selector-ran"
                selected = root / "arbitrary-python"
                selected.write_text(
                    f"#!/bin/sh\ntouch {marker}\nexit 99\n",
                    encoding="utf-8",
                )
                selected.chmod(0o755)
                env = self._env(root)
                env["AAS_RUNTIME_PYTHON"] = str(selected)
                completed = subprocess.run(
                    ["bash", str(wrapper), "selftest"],
                    check=False,
                    text=True,
                    capture_output=True,
                    env=env,
                    timeout=30,
                )
                self.assertEqual(completed.returncode, 127)
                self.assertFalse(marker.exists())

    def test_outer_runner_allows_advisory_selector_only_for_stdlib_credentials(self) -> None:
        cases = (
            (
                "send-email",
                "run_send_email.sh",
                "send_email.py",
                "SEND_EMAIL_SECRETS_FILE",
                "{}\n",
                True,
            ),
            (
                "remote-bridge",
                "run_remote_bridge.sh",
                "remote_bridge.py",
                "REMOTE_BRIDGE_SECRETS_FILE",
                "{}\n",
                True,
            ),
            (
                "zotero",
                "run_zot.sh",
                "zot.py",
                "AAS_ZOTERO_SECRETS_FILE",
                '{"ZOTERO_API_KEY":"selected"}\n',
                False,
            ),
        )
        for skill, wrapper_name, entrypoint, pointer, authority_body, accepted in cases:
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                wrapper = self._stage_entrypoint(
                    root,
                    skill=skill,
                    wrapper=wrapper_name,
                    python_entrypoint=entrypoint,
                )
                program = wrapper.parent / entrypoint
                program.write_text(
                    "import json, os\n"
                    "print(json.dumps({'runtime': os.environ.get('AAS_RUNTIME_PYTHON')}))\n",
                    encoding="utf-8",
                )
                program.chmod(0o644)
                runtime = wrapper.parents[3]
                runner = runtime / "run_skill.sh"
                self._copy_test_runner(runner)
                runner.chmod(0o755)
                authority = self._private_file(root, "authority.json", authority_body)
                selected = (
                    root
                    / ".local/share/coding-system/python-closure/shared/bin/python"
                )
                selected.parent.mkdir(parents=True)
                marker = root / "managed-selector-ran"
                selected.write_text(
                    f"#!/bin/sh\ntouch {marker}\nexit 99\n",
                    encoding="utf-8",
                )
                selected.chmod(0o755)
                env = self._env(root)
                env.update(
                    {
                        "AAS_RUNTIME_PYTHON": str(selected),
                        pointer: str(authority),
                    }
                )
                relative = f"skills/{skill}/{wrapper_name}"
                completed = subprocess.run(
                    ["bash", str(runner), relative, "selftest"],
                    check=False,
                    text=True,
                    capture_output=True,
                    env=env,
                    timeout=30,
                )
                if accepted:
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    runtime_value = json.loads(completed.stdout)["runtime"]
                    self.assertRegex(runtime_value, r"^/(?:proc/self|dev)/fd/[0-9]+$")
                else:
                    self.assertEqual(completed.returncode, 127)
                self.assertFalse(marker.exists())

    def test_secret_pointer_without_resolved_python_uses_trusted_system_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = self._stage_entrypoint(
                root,
                skill="kaggle-research-compute",
                wrapper="run_kaggle_research_compute.sh",
                python_entrypoint="kaggle_research_compute.py",
            )
            secrets = self._private_file(
                root,
                "compute.env",
                "KAGGLE_API_TOKEN=must-not-reach-path-python\n",
            )
            hostile_bin = root / "hostile-bin"
            hostile_bin.mkdir()
            marker = root / "hostile-python-ran"
            hostile_python = hostile_bin / "python3"
            hostile_python.write_text(
                f"#!/bin/sh\ntouch {marker}\nexit 99\n",
                encoding="utf-8",
            )
            hostile_python.chmod(0o755)
            env = self._env(root)
            env.pop("AAS_RUNTIME_PYTHON")
            env["PATH"] = f"{hostile_bin}:/usr/bin:/bin"
            env["AAS_COMPUTE_SECRETS_FILE"] = str(secrets)

            completed = subprocess.run(
                ["bash", str(wrapper), "doctor"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            child = json.loads(completed.stdout)
            self.assertEqual(
                child["KAGGLE_API_TOKEN"],
                "must-not-reach-path-python",
            )
            self.assertFalse(marker.exists())

    def test_secret_launch_ignores_hostile_home_python_closure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = self._stage_entrypoint(
                root,
                skill="kaggle-research-compute",
                wrapper="run_kaggle_research_compute.sh",
                python_entrypoint="kaggle_research_compute.py",
            )
            secrets = self._private_file(
                root,
                "compute.env",
                "KAGGLE_API_TOKEN=restored-kaggle\n",
            )
            hostile_home = root / "caller-selected-home"
            hostile_python = (
                hostile_home
                / ".local/share/coding-system/python-closure/shared/bin/python3"
            )
            hostile_python.parent.mkdir(parents=True)
            marker = root / "hostile-home-python-ran"
            hostile_python.write_text(
                f"#!/bin/sh\ntouch {marker}\nexit 99\n",
                encoding="utf-8",
            )
            hostile_python.chmod(0o755)
            env = self._env(root)
            env.pop("AAS_RUNTIME_PYTHON")
            env.update(
                {
                    "HOME": str(hostile_home),
                    "AAS_COMPUTE_SECRETS_FILE": str(secrets),
                }
            )

            completed = subprocess.run(
                ["bash", str(wrapper), "doctor"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(marker.exists())
            self.assertEqual(json.loads(completed.stdout)["KAGGLE_API_TOKEN"], "restored-kaggle")

    def test_secret_entrypoints_have_no_caller_home_interpreter_trust_root(self) -> None:
        paths = [
            RUNTIME_SOURCE / "runners/run_skill.sh",
            RUNTIME_SOURCE / "skills/hetzner-research-compute/run_hetzner_research_compute.sh",
            RUNTIME_SOURCE / "skills/hetzner-research-compute/run_hetzner_reaper.sh",
            RUNTIME_SOURCE / "skills/modal-research-compute/run_modal_research_compute.sh",
            RUNTIME_SOURCE / "skills/kaggle-research-compute/run_kaggle_research_compute.sh",
            RUNTIME_SOURCE / "skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh",
            RUNTIME_SOURCE / "skills/autonomous-research-loop-runtime/force-loop/run_force_loop.sh",
        ]
        for path in paths:
            with self.subTest(path=path):
                body = path.read_text(encoding="utf-8")
                self.assertNotIn(
                    "${HOME:-}/.local/share/coding-system/python-closure",
                    body,
                )
                self.assertIn("bind_selected_python_inode" if path.name != "run_skill.sh" else "bind_regular_file", body)

    def test_secret_pointer_rejects_hostile_absolute_python_before_execution(self) -> None:
        cases = (
            (
                "modal-research-compute",
                "run_modal_research_compute.sh",
                "modal_research_compute.py",
                "AAS_COMPUTE_SECRETS_FILE",
                "HCLOUD_TOKEN=must-not-reach-hostile-python\n",
                ["doctor"],
            ),
            (
                "kaggle-research-compute",
                "run_kaggle_research_compute.sh",
                "kaggle_research_compute.py",
                "AAS_COMPUTE_SECRETS_FILE",
                "KAGGLE_API_TOKEN=must-not-reach-hostile-python\n",
                ["doctor"],
            ),
            (
                "hetzner-research-compute",
                "run_hetzner_research_compute.sh",
                "hetzner_research_compute.py",
                "AAS_COMPUTE_SECRETS_FILE",
                "HCLOUD_TOKEN=must-not-reach-hostile-python\n",
                ["doctor"],
            ),
            (
                "hetzner-research-compute",
                "run_hetzner_reaper.sh",
                "hetzner_reaper.py",
                "AAS_COMPUTE_SECRETS_FILE",
                "HCLOUD_TOKEN=must-not-reach-hostile-python\n",
                ["reap"],
            ),
            (
                "autonomous-research-loop-runtime",
                "run_autonomous_research_loop.sh",
                "autonomous_research_loop_runtime.py",
                "AAS_PROVIDER_SECRETS_FILE",
                "OPENAI_API_KEY=must-not-reach-hostile-python\n",
                ["drive"],
            ),
            (
                "autonomous-research-loop-runtime/force-loop",
                "run_force_loop.sh",
                "force_loop_cli.py",
                "AAS_PROVIDER_SECRETS_FILE",
                "OPENAI_API_KEY=must-not-reach-hostile-python\n",
                ["start"],
            ),
        )
        for skill, wrapper_name, entrypoint, pointer, body, arguments in cases:
            with self.subTest(wrapper=wrapper_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                wrapper = self._stage_entrypoint(
                    root,
                    skill=skill,
                    wrapper=wrapper_name,
                    python_entrypoint=entrypoint,
                )
                secrets = self._private_file(root, "authority.env", body)
                marker = root / "hostile-absolute-python-ran"
                hostile_python = root / "hostile-python"
                hostile_python.write_text(
                    f"#!/bin/sh\ntouch {marker}\nexit 99\n",
                    encoding="utf-8",
                )
                hostile_python.chmod(0o755)
                env = self._env(root)
                env.update(
                    {
                        "AAS_RUNTIME_PYTHON": str(hostile_python),
                        pointer: str(secrets),
                    }
                )

                completed = subprocess.run(
                    ["bash", str(wrapper), *arguments],
                    check=False,
                    text=True,
                    capture_output=True,
                    env=env,
                    timeout=30,
                )

                self.assertEqual(completed.returncode, 127)
                self.assertIn("trusted", completed.stderr.lower())
                self.assertFalse(marker.exists())
                self.assertNotIn(
                    "must-not-reach-hostile-python",
                    completed.stdout + completed.stderr,
                )

    def test_outer_runner_and_hetzner_wrapper_drop_broad_skill_authority_and_hostile_startup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = self._stage_entrypoint(
                root,
                skill="hetzner-research-compute",
                wrapper="run_hetzner_research_compute.sh",
                python_entrypoint="hetzner_research_compute.py",
            )
            runtime = wrapper.parents[3]
            runner = runtime / "run_skill.sh"
            self._copy_test_runner(runner)
            runner.chmod(0o755)
            skill_secrets = self._private_file(
                root,
                "skill.env",
                "AXLE_API_KEY=must-not-reach-hetzner\n"
                "PATENTSVIEW_API_KEY=must-not-reach-hetzner-patents\n"
                "ZENODO_TOKEN=must-not-reach-hetzner-either\n",
            )
            compute_secrets = self._private_file(
                root,
                "compute.env",
                "HCLOUD_TOKEN=restored-hetzner\n"
                "HCLOUD_SSH_KEYS=research-key\n"
                "KAGGLE_API_TOKEN=must-not-cross-lane\n",
            )
            hostile = root / "hostile"
            hostile.mkdir()
            preload_marker = root / "sitecustomize-ran.jsonl"
            (hostile / "sitecustomize.py").write_text(
                "import json, os\n"
                "from pathlib import Path\n"
                "Path(os.environ['PYTHON_PRELOAD_MARKER']).open('a', encoding='utf-8').write(\n"
                "    json.dumps({\n"
                "        'pointer': os.environ.get('AAS_COMPUTE_SECRETS_FILE'),\n"
                "        'skill': os.environ.get('ZENODO_TOKEN'),\n"
                "        'token': os.environ.get('HCLOUD_TOKEN'),\n"
                "    }) + '\\n'\n"
                ")\n",
                encoding="utf-8",
            )
            hostile_bin = root / "hostile-bin"
            hostile_bin.mkdir()
            tool_marker = root / "hostile-tool-ran"
            for name in ("hcloud", "ssh", "scp", "rsync"):
                candidate = hostile_bin / name
                candidate.write_text(
                    f"#!/bin/sh\ntouch {tool_marker}\nexit 99\n",
                    encoding="utf-8",
                )
                candidate.chmod(0o755)
            env = self._env(root)
            env.update(
                {
                    "AAS_SKILL_SECRETS_FILE": str(skill_secrets),
                    "AAS_COMPUTE_SECRETS_FILE": str(compute_secrets),
                    "PATH": f"{hostile_bin}:/usr/bin:/bin",
                    "PYTHONHOME": str(root / "bogus-python-home"),
                    "PYTHONPATH": str(hostile),
                    "PYTHON_PRELOAD_MARKER": str(preload_marker),
                    "ZENODO_TOKEN": "stale-ambient-skill",
                    "PATENTSVIEW_API_KEY": "stale-ambient-patentsview",
                }
            )

            completed = subprocess.run(
                [
                    "bash",
                    str(runner),
                    "skills/hetzner-research-compute/run_hetzner_research_compute.sh",
                    "doctor",
                ],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            child = json.loads(completed.stdout)
            self.assertEqual(child["HCLOUD_TOKEN"], "restored-hetzner")
            self.assertEqual(child["HCLOUD_SSH_KEYS"], "research-key")
            for key in (SKILL_KEYS | (COMPUTE_KEYS - HCLOUD_KEYS) | PROVIDER_KEYS):
                self.assertIsNone(child[key], key)
            for pointer in (
                "AAS_SKILL_SECRETS_FILE",
                "AAS_COMPUTE_SECRETS_FILE",
                "AAS_PROVIDER_SECRETS_FILE",
            ):
                self.assertIsNone(child[pointer], pointer)
            self.assertIsNone(child["PYTHONHOME"])
            self.assertIsNone(child["PYTHONPATH"])
            for pin in (
                "AAS_HETZNER_HCLOUD_BIN",
                "AAS_HETZNER_SCP_BIN",
                "AAS_HETZNER_SSH_BIN",
                "AAS_HETZNER_RSYNC_BIN",
            ):
                if child[pin] is not None:
                    self.assertNotEqual(Path(child[pin]).parent, hostile_bin, pin)
            self.assertFalse(preload_marker.exists())
            self.assertFalse(tool_marker.exists())

    def test_direct_drive_loads_provider_and_compute_but_status_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = self._stage_entrypoint(
                root,
                skill="autonomous-research-loop-runtime",
                wrapper="run_autonomous_research_loop.sh",
                python_entrypoint="autonomous_research_loop_runtime.py",
            )
            compute = self._private_file(
                root,
                "compute.env",
                "HCLOUD_TOKEN=restored-hetzner\n"
                "KAGGLE_API_TOKEN=restored-kaggle\n",
            )
            providers = self._private_file(
                root,
                "providers.env",
                "OPENAI_API_KEY=restored-openai\n"
                "KIMI_API_KEY=restored-kimi\n"
                "COPILOT_PROVIDER_API_KEY=restored-copilot-api\n"
                "COPILOT_PROVIDER_BEARER_TOKEN=restored-copilot-bearer\n",
            )
            env = self._env(root)
            env.update(
                {
                    "AAS_COMPUTE_SECRETS_FILE": str(compute),
                    "AAS_PROVIDER_SECRETS_FILE": str(providers),
                }
            )

            driven = subprocess.run(
                ["bash", str(wrapper), "drive"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )
            self.assertEqual(driven.returncode, 0, driven.stderr)
            child = json.loads(driven.stdout)
            self.assertEqual(child["HCLOUD_TOKEN"], "restored-hetzner")
            self.assertEqual(child["KAGGLE_API_TOKEN"], "restored-kaggle")
            self.assertEqual(child["OPENAI_API_KEY"], "restored-openai")
            self.assertEqual(child["KIMI_API_KEY"], "restored-kimi")
            self.assertEqual(
                child["COPILOT_PROVIDER_API_KEY"], "restored-copilot-api"
            )
            self.assertEqual(
                child["COPILOT_PROVIDER_BEARER_TOKEN"],
                "restored-copilot-bearer",
            )
            self.assertIsNone(child["AAS_COMPUTE_SECRETS_FILE"])
            self.assertIsNone(child["AAS_PROVIDER_SECRETS_FILE"])

            status = subprocess.run(
                ["bash", str(wrapper), "status"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            status_child = json.loads(status.stdout)
            for key in COMPUTE_KEYS | PROVIDER_KEYS:
                self.assertIsNone(status_child[key], key)

    def test_kaggle_doctor_resolves_canonical_access_token_without_cross_lane_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "runtime" / "workspace"
            skill_dir = workspace / "skills" / "kaggle-research-compute"
            shutil.copytree(
                RUNTIME_SOURCE / "skills" / "kaggle-research-compute",
                skill_dir,
            )
            shutil.copytree(
                RUNTIME_SOURCE / "workspace" / "research_compute",
                workspace / "research_compute",
            )
            config_dir = workspace / "config"
            config_dir.mkdir()
            config = config_dir / "research-compute.toml"
            shutil.copy2(
                RUNTIME_SOURCE / "workspace" / "config" / "research-compute.example.toml",
                config,
            )
            wrapper = skill_dir / "run_kaggle_research_compute.sh"
            wrapper.chmod(0o755)
            home = root / "home"
            token_path = home / ".kaggle" / "access_token"
            token_path.parent.mkdir(parents=True)
            token_path.write_text("offline-canonical-kaggle-token\n", encoding="utf-8")
            token_path.chmod(0o600)
            env = {
                "HOME": str(home),
                "PATH": "/usr/bin:/bin",
                "AAS_RUNTIME_PYTHON": "/usr/bin/python3",
                "OPENCLAW_WORKSPACE": str(workspace),
                "PYTHONDONTWRITEBYTECODE": "1",
                "AAS_PROVIDER_SECRETS_FILE": "/provider-pointer-must-not-cross",
                "REMOTE_BRIDGE_SECRETS_FILE": "/remote-pointer-must-not-cross",
                "OPENAI_API_KEY": "provider-token-must-not-cross",
                "ZULIP_API_KEY": "zulip-token-must-not-cross",
            }
            completed = subprocess.run(
                ["/bin/bash", str(wrapper), "--config", str(config), "doctor"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["api_token_present"])
            self.assertEqual(
                payload["network_probe"],
                "skipped (doctor performs no network calls)",
            )
            surfaced = completed.stdout + completed.stderr
            for canary in (
                "offline-canonical-kaggle-token",
                "provider-pointer-must-not-cross",
                "remote-pointer-must-not-cross",
                "provider-token-must-not-cross",
                "zulip-token-must-not-cross",
            ):
                self.assertNotIn(canary, surfaced)

    def test_entrypoints_reject_unsafe_compute_files_before_child_without_leak(self) -> None:
        cases = (
            (
                "modal-research-compute",
                "run_modal_research_compute.sh",
                "modal_research_compute.py",
                "doctor",
            ),
            (
                "hetzner-research-compute",
                "run_hetzner_research_compute.sh",
                "hetzner_research_compute.py",
                "doctor",
            ),
            (
                "hetzner-research-compute",
                "run_hetzner_reaper.sh",
                "hetzner_reaper.py",
                "reap",
            ),
            (
                "kaggle-research-compute",
                "run_kaggle_research_compute.sh",
                "kaggle_research_compute.py",
                "doctor",
            ),
        )
        for skill, wrapper_name, python_entrypoint, command in cases:
            with self.subTest(wrapper=wrapper_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                wrapper = self._stage_entrypoint(
                    root,
                    skill=skill,
                    wrapper=wrapper_name,
                    python_entrypoint=python_entrypoint,
                )
                unsafe = self._private_file(
                    root,
                    "compute.env",
                    "ZULIP_API_KEY=must-not-leak\n",
                )
                public = self._private_file(
                    root,
                    "public.env",
                    "HCLOUD_TOKEN=must-not-leak\n",
                )
                public.chmod(0o640)
                linked = root / "linked.env"
                linked.symlink_to(public)

                for candidate in (unsafe, public, linked):
                    with self.subTest(candidate=candidate.name):
                        marker = root / f"{candidate.name}.child-ran"
                        env = self._env(root)
                        env.update(
                            {
                                "AAS_COMPUTE_SECRETS_FILE": str(candidate),
                                "TEST_CHILD_MARKER": str(marker),
                            }
                        )
                        completed = subprocess.run(
                            ["bash", str(wrapper), command],
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

    def test_direct_drive_rejects_bad_provider_authority_without_value_leak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = self._stage_entrypoint(
                root,
                skill="autonomous-research-loop-runtime",
                wrapper="run_autonomous_research_loop.sh",
                python_entrypoint="autonomous_research_loop_runtime.py",
            )
            providers = self._private_file(
                root,
                "providers.env",
                "HCLOUD_TOKEN=must-not-leak\n",
            )
            marker = root / "child-ran"
            env = self._env(root)
            env.update(
                {
                    "AAS_PROVIDER_SECRETS_FILE": str(providers),
                    "TEST_CHILD_MARKER": str(marker),
                }
            )
            completed = subprocess.run(
                ["bash", str(wrapper), "drive"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertFalse(marker.exists())
            self.assertIn("unsupported key HCLOUD_TOKEN", completed.stderr)
            self.assertNotIn("must-not-leak", completed.stdout + completed.stderr)

    def test_pointer_text_is_never_evaluated_as_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = self._stage_entrypoint(
                root,
                skill="hetzner-research-compute",
                wrapper="run_hetzner_research_compute.sh",
                python_entrypoint="hetzner_research_compute.py",
            )
            marker = root / "pointer-injection-ran"
            env = self._env(root)
            env["AAS_COMPUTE_SECRETS_FILE"] = f"$(touch {marker})"
            completed = subprocess.run(
                ["bash", str(wrapper), "doctor"],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertFalse(marker.exists())


class SecretEntrypointStaticTests(unittest.TestCase):
    def test_windows_runner_uses_minimal_environment_and_holds_command_guard(self) -> None:
        runner = (RUNTIME_SOURCE / "runners" / "run_skill.ps1").read_text(
            encoding="utf-8"
        )

        clear = runner.index("[Environment]::GetEnvironmentVariables(")
        compile_boundary = runner.index('Add-Type -TypeDefinition @\'')
        self.assertLess(clear, compile_boundary)
        self.assertIn("$credentialEnvironment = @{}", runner)
        for unknown in (
            "AWS_SECRET_ACCESS_KEY",
            "DATABASE_URL",
            "CLOUDFLARE_API_TOKEN",
        ):
            self.assertNotIn(unknown, runner)
        self.assertIn("[System.IO.FileShare]::Read", runner)
        opened = runner.index(
            "$commandGuard = Open-AasGuardedRuntimeFile -Path $commandResolved"
        )
        invoked = runner.index("& $commandResolved @SkillArgs")
        disposed = runner.index("$commandGuard.Stream.Dispose()")
        self.assertLess(opened, invoked)
        self.assertLess(invoked, disposed)

    def test_loader_detects_in_place_mutation_even_when_mtime_is_restored(self) -> None:
        loader_path = RUNTIME_SOURCE / "runners" / "load_secret_env.py"
        spec = importlib.util.spec_from_file_location(
            "aas_test_load_secret_env_ctime",
            loader_path,
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "skill.env"
            secret.write_text("AXLE_API_KEY=first-value\n", encoding="utf-8")
            secret.chmod(0o600)
            original_stat = secret.stat()
            original_read = os.read
            mutated = False

            def mutate_after_read(descriptor: int, size: int) -> bytes:
                nonlocal mutated
                payload = original_read(descriptor, size)
                if payload and not mutated:
                    mutated = True
                    with secret.open("r+b") as handle:
                        handle.write(b"AXLE_API_KEY=other-value\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.utime(
                        secret,
                        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                    )
                return payload

            from unittest import mock

            with mock.patch.object(module.os, "read", side_effect=mutate_after_read):
                with self.assertRaisesRegex(
                    module.SecretEnvError,
                    "changed while reading",
                ):
                    module.read_protected_secret_env(str(secret))

    def test_loader_can_validate_a_shared_authority_but_export_a_target_subset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = root / "providers.env"
            secret.write_text(
                "GH_TOKEN=restored-copilot\nOPENAI_API_KEY=restored-openai\n",
                encoding="utf-8",
            )
            secret.chmod(0o600)
            child = root / "child.py"
            child.write_text(
                "import json, os\n"
                "print(json.dumps({\n"
                "  'gh': os.environ.get('GH_TOKEN'),\n"
                "  'openai': os.environ.get('OPENAI_API_KEY'),\n"
                "  'pointer': os.environ.get('AAS_PROVIDER_SECRETS_FILE'),\n"
                "  'aws': os.environ.get('AWS_SECRET_ACCESS_KEY'),\n"
                "  'database': os.environ.get('DATABASE_URL'),\n"
                "  'cloudflare': os.environ.get('CLOUDFLARE_API_TOKEN'),\n"
                "}))\n",
                encoding="utf-8",
            )
            loader = RUNTIME_SOURCE / "runners" / "load_secret_env.py"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(loader),
                    "--pointer-env",
                    "AAS_PROVIDER_SECRETS_FILE",
                    "--allow-key",
                    "GH_TOKEN",
                    "--allow-key",
                    "OPENAI_API_KEY",
                    "--export-key",
                    "GH_TOKEN",
                    "--",
                    sys.executable,
                    str(child),
                ],
                check=False,
                text=True,
                capture_output=True,
                env={
                    "AAS_PROVIDER_SECRETS_FILE": str(secret),
                    "GH_TOKEN": "stale-ambient-gh",
                    "OPENAI_API_KEY": "stale-ambient-openai",
                    "AWS_SECRET_ACCESS_KEY": "ambient-aws-secret",
                    "DATABASE_URL": "ambient-database-secret",
                    "CLOUDFLARE_API_TOKEN": "ambient-cloudflare-secret",
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                },
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(completed.stdout),
                {
                    "gh": "restored-copilot",
                    "openai": None,
                    "pointer": None,
                    "aws": None,
                    "database": None,
                    "cloudflare": None,
                },
            )

    def test_loader_explicit_empty_subset_scrubs_allowed_ambient_and_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = root / "compute.env"
            secret.write_text("HCLOUD_TOKEN=restored-hetzner\n", encoding="utf-8")
            secret.chmod(0o600)
            loader = RUNTIME_SOURCE / "runners" / "load_secret_env.py"
            probe = (
                "import json, os; print(json.dumps({"
                "'token': os.environ.get('HCLOUD_TOKEN'), "
                "'pointer': os.environ.get('AAS_COMPUTE_SECRETS_FILE'), "
                "'protected_ids': os.environ.get('AAS_PROTECTED_SECRET_FILE_IDS')}))"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(loader),
                    "--pointer-env",
                    "AAS_COMPUTE_SECRETS_FILE",
                    "--allow-key",
                    "HCLOUD_TOKEN",
                    "--export-subset",
                    "--",
                    sys.executable,
                    "-c",
                    probe,
                ],
                check=False,
                text=True,
                capture_output=True,
                env={
                    "AAS_COMPUTE_SECRETS_FILE": str(secret),
                    "HCLOUD_TOKEN": "stale-ambient-token",
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                },
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(completed.stdout),
                {
                    "token": None,
                    "pointer": None,
                    "protected_ids": f"{secret.stat().st_dev}:{secret.stat().st_ino}",
                },
            )

    def test_loader_default_scrubs_ambient_siblings_and_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = root / "providers.env"
            secret.write_text("GH_TOKEN=restored-gh\n", encoding="utf-8")
            secret.chmod(0o600)
            loader = RUNTIME_SOURCE / "runners" / "load_secret_env.py"
            probe = (
                "import json, os; print(json.dumps({"
                "'gh': os.environ.get('GH_TOKEN'), "
                "'openai': os.environ.get('OPENAI_API_KEY'), "
                "'pointer': os.environ.get('AAS_PROVIDER_SECRETS_FILE')}))"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(loader),
                    "--pointer-env",
                    "AAS_PROVIDER_SECRETS_FILE",
                    "--allow-key",
                    "GH_TOKEN",
                    "--allow-key",
                    "OPENAI_API_KEY",
                    "--",
                    sys.executable,
                    "-c",
                    probe,
                ],
                check=False,
                text=True,
                capture_output=True,
                env={
                    "AAS_PROVIDER_SECRETS_FILE": str(secret),
                    "GH_TOKEN": "stale-gh",
                    "OPENAI_API_KEY": "legacy-ambient-openai",
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                },
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(completed.stdout),
                {
                    "gh": "restored-gh",
                    "openai": None,
                    "pointer": None,
                },
            )

    def test_loader_rejects_export_keys_outside_the_validation_allowlist(self) -> None:
        loader = RUNTIME_SOURCE / "runners" / "load_secret_env.py"
        completed = subprocess.run(
            [
                sys.executable,
                str(loader),
                "--pointer-env",
                "AAS_PROVIDER_SECRETS_FILE",
                "--allow-key",
                "GH_TOKEN",
                "--export-key",
                "OPENAI_API_KEY",
                "--",
                sys.executable,
                "-c",
                "raise SystemExit(99)",
            ],
            check=False,
            text=True,
            capture_output=True,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("subset", completed.stderr)

    def test_entrypoint_allowlists_are_exact(self) -> None:
        expected_lane_exports = {
            "modal-research-compute": set(),
            "hetzner-research-compute": HCLOUD_KEYS,
            "kaggle-research-compute": KAGGLE_KEYS,
        }
        for skill in (
            "modal-research-compute",
            "hetzner-research-compute",
            "kaggle-research-compute",
        ):
            with self.subTest(skill=skill, platform="posix"):
                text = (
                    RUNTIME_SOURCE / "skills" / skill / f"run_{skill.replace('-', '_')}.sh"
                ).read_text(encoding="utf-8")
                self.assertEqual(
                    set(re.findall(r"--allow-key ([A-Z][A-Z0-9_]*)", text)),
                    COMPUTE_KEYS,
                )
                self.assertEqual(
                    set(re.findall(r"--export-key ([A-Z][A-Z0-9_]*)", text)),
                    expected_lane_exports[skill],
                )
                if skill == "modal-research-compute":
                    self.assertIn("--export-subset", text)

            with self.subTest(skill=skill, platform="windows"):
                text = (
                    RUNTIME_SOURCE / "skills" / skill / f"run_{skill.replace('-', '_')}.ps1"
                ).read_text(encoding="utf-8")
                import_tail = text.split(
                    'Import-AasSecretEnvFile -PointerEnv "AAS_COMPUTE_SECRETS_FILE"',
                    1,
                )[1]
                block = import_tail.split(") -Export", 1)[0]
                self.assertEqual(
                    set(re.findall(r'"([A-Z][A-Z0-9_]*)"', block)),
                    COMPUTE_KEYS,
                )
                export_keys = set()
                if ") -ExportKeys @(" in import_tail:
                    export_block = import_tail.split(") -ExportKeys @(", 1)[1].split(")", 1)[0]
                    export_keys = set(
                        re.findall(r'"([A-Z][A-Z0-9_]*)"', export_block)
                    )
                self.assertEqual(export_keys, expected_lane_exports[skill])
                if skill == "modal-research-compute":
                    self.assertIn(") -ExportSubset", import_tail)
                self.assertIn("run_python.ps1", text)
                self.assertIn("AAS_RUNTIME_SCRIPT", text)
                self.assertLess(
                    text.index("-ResolveOnly"),
                    text.index("Import-AasSecretEnvFile"),
                )

        reaper_sh = (
            RUNTIME_SOURCE
            / "skills"
            / "hetzner-research-compute"
            / "run_hetzner_reaper.sh"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            set(re.findall(r"--allow-key ([A-Z][A-Z0-9_]*)", reaper_sh)),
            COMPUTE_KEYS,
        )
        self.assertEqual(
            set(re.findall(r"--export-key ([A-Z][A-Z0-9_]*)", reaper_sh)),
            HCLOUD_KEYS,
        )
        reaper_ps1 = (
            RUNTIME_SOURCE
            / "skills"
            / "hetzner-research-compute"
            / "run_hetzner_reaper.ps1"
        ).read_text(encoding="utf-8")
        reaper_ps1_tail = reaper_ps1.split(
            'Import-AasSecretEnvFile -PointerEnv "AAS_COMPUTE_SECRETS_FILE"',
            1,
        )[1]
        reaper_ps1_block = reaper_ps1_tail.split(") -Export", 1)[0]
        self.assertEqual(
            set(re.findall(r'"([A-Z][A-Z0-9_]*)"', reaper_ps1_block)),
            COMPUTE_KEYS,
        )
        reaper_export_block = reaper_ps1_tail.split(") -ExportKeys @(", 1)[1].split(")", 1)[0]
        self.assertEqual(
            set(re.findall(r'"([A-Z][A-Z0-9_]*)"', reaper_export_block)),
            HCLOUD_KEYS,
        )
        self.assertIn("Remove-Item Env:AAS_COMPUTE_SECRETS_FILE", reaper_ps1)
        self.assertIn("run_python.ps1", reaper_ps1)
        self.assertIn('"hetzner_reaper.py"', reaper_ps1)

        arl_sh = (
            RUNTIME_SOURCE
            / "skills"
            / "autonomous-research-loop-runtime"
            / "run_autonomous_research_loop.sh"
        ).read_text(encoding="utf-8")
        compute_block = arl_sh.split(
            "--pointer-env AAS_COMPUTE_SECRETS_FILE", 1
        )[1].split('-- "${command[@]}"', 1)[0]
        provider_block = arl_sh.split(
            "--pointer-env AAS_PROVIDER_SECRETS_FILE", 1
        )[1].split('-- "${command[@]}"', 1)[0]
        self.assertEqual(
            set(re.findall(r"--allow-key ([A-Z][A-Z0-9_]*)", compute_block)),
            COMPUTE_KEYS,
        )
        self.assertEqual(
            set(re.findall(r"--export-key ([A-Z][A-Z0-9_]*)", compute_block)),
            COMPUTE_KEYS,
        )
        self.assertEqual(
            set(re.findall(r"--allow-key ([A-Z][A-Z0-9_]*)", provider_block)),
            PROVIDER_KEYS,
        )
        self.assertEqual(
            set(re.findall(r"--export-key ([A-Z][A-Z0-9_]*)", provider_block)),
            PROVIDER_KEYS,
        )

        arl_ps1 = (
            RUNTIME_SOURCE
            / "skills"
            / "autonomous-research-loop-runtime"
            / "run_autonomous_research_loop.ps1"
        ).read_text(encoding="utf-8")
        compute_ps1_tail = arl_ps1.split(
            'Import-AasSecretEnvFile -PointerEnv "AAS_COMPUTE_SECRETS_FILE"',
            1,
        )[1]
        provider_ps1_tail = arl_ps1.split(
            'Import-AasSecretEnvFile -PointerEnv "AAS_PROVIDER_SECRETS_FILE"',
            1,
        )[1]
        compute_ps1 = compute_ps1_tail.split(") -Export", 1)[0]
        provider_ps1 = provider_ps1_tail.split(") -Export", 1)[0]
        self.assertEqual(
            set(re.findall(r'"([A-Z][A-Z0-9_]*)"', compute_ps1)),
            COMPUTE_KEYS,
        )
        self.assertEqual(
            set(re.findall(r'"([A-Z][A-Z0-9_]*)"', provider_ps1)),
            PROVIDER_KEYS,
        )
        compute_ps1_exports = compute_ps1_tail.split(") -ExportKeys @(", 1)[1].split(")", 1)[0]
        provider_ps1_exports = provider_ps1_tail.split(") -ExportKeys @(", 1)[1].split(")", 1)[0]
        self.assertEqual(
            set(re.findall(r'"([A-Z][A-Z0-9_]*)"', compute_ps1_exports)),
            COMPUTE_KEYS,
        )
        self.assertEqual(
            set(re.findall(r'"([A-Z][A-Z0-9_]*)"', provider_ps1_exports)),
            PROVIDER_KEYS,
        )
        self.assertIn("Remove-Item Env:AAS_COMPUTE_SECRETS_FILE", arl_ps1)
        self.assertIn("Remove-Item Env:AAS_PROVIDER_SECRETS_FILE", arl_ps1)
        self.assertIn("run_python.ps1", arl_ps1)
        self.assertIn("AAS_RUNTIME_SCRIPT", arl_ps1)

    def test_windows_compute_wrappers_are_manifested(self) -> None:
        manifest = json.loads(
            (REPO / "manifest" / "runtime.yaml").read_text(encoding="utf-8")
        )
        for skill in (
            "modal-research-compute",
            "hetzner-research-compute",
            "kaggle-research-compute",
        ):
            target = (
                f"workspace/skills/{skill}/run_{skill.replace('-', '_')}.ps1"
            )
            entries = {
                entry["target"]: entry
                for entry in manifest["skills"][skill]["files"]
            }
            self.assertIn(target, entries)
            self.assertEqual(entries[target]["platforms"], ["windows"])

    def test_reaper_wrappers_are_manifested_per_platform(self) -> None:
        manifest = json.loads(
            (REPO / "manifest" / "runtime.yaml").read_text(encoding="utf-8")
        )
        target = (
            "workspace/skills/hetzner-research-compute/run_hetzner_reaper.sh"
        )
        entries = {
            entry["target"]: entry
            for entry in manifest["skills"]["hetzner-research-compute"]["files"]
        }
        self.assertIn(target, entries)
        self.assertEqual(entries[target]["platforms"], ["linux", "macos", "wsl"])
        self.assertEqual(entries[target]["mode"], "0755")
        windows_target = (
            "workspace/skills/hetzner-research-compute/run_hetzner_reaper.ps1"
        )
        self.assertIn(windows_target, entries)
        self.assertEqual(entries[windows_target]["platforms"], ["windows"])
        self.assertEqual(entries[windows_target]["newline"], "crlf")

    def test_reaper_guide_uses_root_lease_and_user_credential_projection(self) -> None:
        guide = (
            REPO
            / "canonical"
            / "skills"
            / "hetzner-research-compute"
            / "references"
            / "reaper-deployment.md"
        ).read_text(encoding="utf-8")
        for expected in (
            "AAS_COMPUTE_SECRETS_FILE",
            "run_hetzner_reaper.sh",
            "run_hetzner_reaper.ps1",
            "/etc/ai-agents-skills/hetzner-reaper-lease.json",
            "root-owned mode `0644`",
            "ExecStartPost=",
            "--scheduler-id hetzner-reaper.timer",
            "/usr/sbin/runuser --user REPLACE_AGENT_USER",
            "root-owned runtime: `/opt/ai-agents-skills/runtime`",
            "`&&` is deliberate",
            "Native Windows status (recovery only)",
            "Live `up` and `oneshot` fail closed",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, guide)
        self.assertNotIn("AAS_HETZNER_DURABLE_REAPER_ATTESTED", guide)


if __name__ == "__main__":
    unittest.main()
