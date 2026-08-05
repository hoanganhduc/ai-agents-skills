"""Agent-facing Zotero delivery wrapper security checks."""

from __future__ import annotations

import json
import os
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEND_FILE = ROOT / "canonical/runtime/skills/zotero/send_file.sh"
SEND_TELEGRAM = ROOT / "canonical/runtime/skills/zotero/send_telegram.sh"
SEND_WORKER = ROOT / "canonical/runtime/skills/zotero/send_queue_worker.sh"
SEND_QUEUE = ROOT / "canonical/runtime/skills/zotero/send_queue.py"


@unittest.skipIf(os.name == "nt", "POSIX Zotero delivery wrapper")
class ZoteroSendFileTests(unittest.TestCase):
    def _layout(self, root: Path) -> tuple[Path, Path]:
        workspace = root / "workspace"
        for relative in (
            "data/exports",
            "data/research/zotero/staging",
            "data/calibre/staging",
        ):
            path = workspace / relative
            path.mkdir(parents=True, mode=0o700)
            path.chmod(0o700)
        (workspace / "data").chmod(0o700)
        authority = root / ".config/ai-agents-skills/file-delivery-queue.json"
        authority.parent.mkdir(parents=True, mode=0o700)
        replay = root / ".local/state/ai-agents-skills/file-delivery-replay"
        replay.mkdir(parents=True, mode=0o700)
        authority.write_text(
            json.dumps(
                {
                    "version": 1,
                    "hmac_key_hex": "11" * 32,
                    "allowed": {"telegram": ["target"]},
                    "max_job_age_seconds": 60,
                    "max_media_bytes": 1024,
                    "replay_ledger_dir": "aas-host-state:file-delivery-replay",
                    "replay_retention_seconds": 300,
                    "max_replay_entries": 100,
                }
            ),
            encoding="utf-8",
        )
        authority.chmod(0o600)
        return workspace, authority

    def test_all_channels_route_exclusively_through_authenticated_queue(self) -> None:
        body = SEND_FILE.read_text(encoding="utf-8")

        self.assertIn('exec "$PYTHON" -I -c "$secure_loader"', body)
        self.assertIn("/usr/bin/python3", body)
        self.assertIn('getattr(os,"O_NOFOLLOW",0)', body)
        self.assertIn("(q.st_dev,q.st_ino)==(b.st_dev,b.st_ino)", body)
        self.assertIn("export PATH=/usr/bin:/bin", body)
        self.assertNotIn("curl", body)
        self.assertNotIn("TELEGRAM_SECRETS_FILE", body)
        self.assertNotIn("ZULIP_SECRETS_FILE", body)
        self.assertNotIn("command -v", body)

    def test_direct_wrapper_rejects_outside_root_and_ignores_hostile_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, authority = self._layout(root)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            marker = root / "hostile-tool-ran"
            for name in ("python3", "curl", "dirname"):
                executable = fake_bin / name
                executable.write_text(
                    f"#!/bin/sh\n: > {marker!s}\nexit 97\n",
                    encoding="utf-8",
                )
                executable.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(root),
                    "AAS_RUNTIME_WORKSPACE": str(workspace),
                    "AAS_FILE_DELIVERY_SECRETS_FILE": str(authority),
                    "PATH": str(fake_bin),
                    "AAS_SECRETS_FILE": str(root / "must-not-be-read"),
                    "OPENCLAW_SECRETS_FILE": str(root / "must-not-be-read"),
                    "REMOTE_BRIDGE_SECRETS_FILE": str(root / "must-not-be-read"),
                }
            )

            completed = subprocess.run(
                ["/bin/bash", str(SEND_FILE)],
                env=env,
                input=json.dumps(
                    {
                        "channel": "telegram",
                        "target": "target",
                        "media": "/etc/passwd",
                        "caption": "",
                    }
                ),
                check=False,
                text=True,
                capture_output=True,
                timeout=15,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("outside the authorized export roots", completed.stdout)
            self.assertFalse(marker.exists())

    def test_send_telegram_delegates_without_loading_credentials(self) -> None:
        body = SEND_TELEGRAM.read_text(encoding="utf-8")

        self.assertIn('exec /usr/bin/python3 -I -c "$secure_shell_loader"', body)
        self.assertIn("os.memfd_create", body)
        self.assertIn("request_fd=os.dup(0)", body)
        self.assertIn('getattr(os,"O_NOFOLLOW",0)', body)
        self.assertIn("unset AAS_SECRETS_FILE OPENCLAW_SECRETS_FILE", body)
        self.assertIn("TELEGRAM_BOT_TOKEN", body)
        self.assertNotIn("curl", body)
        self.assertNotIn("TELEGRAM_SECRETS_FILE", body)

    def test_direct_wrapper_is_privileged_bash_and_scrubs_credential_families(self) -> None:
        body = SEND_FILE.read_text(encoding="utf-8")

        self.assertTrue(body.startswith("#!/bin/bash -p\n"))
        for key in (
            "AAS_SECRETS_FILE",
            "OPENCLAW_SECRETS_FILE",
            "AAS_SKILL_SECRETS_FILE",
            "AAS_PROVIDER_SECRETS_FILE",
            "AAS_COMPUTE_SECRETS_FILE",
            "AAS_ZOTERO_SECRETS_FILE",
            "REMOTE_BRIDGE_SECRETS_FILE",
            "HCLOUD_TOKEN",
            "TELEGRAM_BOT_TOKEN",
            "ZULIP_API_KEY",
        ):
            self.assertIn(key, body)

    def test_producer_child_receives_only_allowlisted_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staged = root / "producer"
            staged.mkdir(mode=0o700)
            wrapper = staged / "send_file.sh"
            probe = staged / "send_queue.py"
            shutil.copy2(SEND_FILE, wrapper)
            wrapper.chmod(0o755)
            probe.write_text(
                "import json, os\n"
                "print(json.dumps(dict(os.environ), sort_keys=True))\n",
                encoding="utf-8",
            )
            probe.chmod(0o600)
            authority = root / "authority.json"
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(root),
                    "AAS_RUNTIME_WORKSPACE": str(root / "workspace"),
                    "AAS_FILE_DELIVERY_SECRETS_FILE": str(authority),
                    "UNRECOGNIZED_PROVIDER_TOKEN": "must-not-reach-producer-child",
                }
            )

            completed = subprocess.run(
                ["/bin/bash", str(wrapper)],
                env=env,
                input="{}",
                check=False,
                text=True,
                capture_output=True,
                timeout=15,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            child_env = json.loads(completed.stdout)
            self.assertNotIn("UNRECOGNIZED_PROVIDER_TOKEN", child_env)
            self.assertEqual(
                child_env.get("AAS_FILE_DELIVERY_SECRETS_FILE"), str(authority)
            )
            self.assertEqual(child_env.get("PATH"), "/usr/bin:/bin")

    def test_worker_prelude_binds_python_and_ignores_hostile_path_and_ambient_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, authority = self._layout(root)
            staged = root / "worker"
            staged.mkdir()
            worker = staged / "send_queue_worker.sh"
            queue = staged / "send_queue.py"
            shutil.copy2(SEND_WORKER, worker)
            shutil.copy2(SEND_QUEUE, queue)
            worker.chmod(0o755)
            queue.chmod(0o644)
            hostile = root / "hostile-bin"
            hostile.mkdir()
            marker = root / "hostile-prelude-ran"
            for name in ("python3", "dirname", "pwd", "stat", "id", "readlink"):
                candidate = hostile / name
                candidate.write_text(
                    "#!/bin/sh\n"
                    f"printf '%s|%s\\n' \"${{AAS_FILE_DELIVERY_SECRETS_FILE:-}}\" "
                    f"\"${{OPENAI_API_KEY:-}}\" > {marker}\n"
                    "exit 97\n",
                    encoding="utf-8",
                )
                candidate.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(root),
                    "PATH": str(hostile),
                    "AAS_RUNTIME_WORKSPACE": str(workspace),
                    "AAS_FILE_DELIVERY_SECRETS_FILE": str(authority),
                    "OPENAI_API_KEY": "ambient-must-not-reach-prelude",
                }
            )

            completed = subprocess.run(
                ["/bin/bash", str(worker), "--once"],
                env=env,
                check=False,
                text=True,
                capture_output=True,
                timeout=15,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["status"], "idle")
            self.assertFalse(marker.exists())
            body = SEND_WORKER.read_text(encoding="utf-8")
            self.assertTrue(body.startswith("#!/bin/bash -p\n"))
            self.assertIn('exec "$PYTHON" -I -c "$secure_loader"', body)
            self.assertIn('getattr(os,"O_NOFOLLOW",0)', body)
            self.assertNotIn("command -v", body)
            self.assertIn("unset AAS_FILE_DELIVERY_SECRETS_FILE", body)

    def test_worker_child_receives_no_unknown_or_queue_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staged = root / "worker"
            staged.mkdir(mode=0o700)
            worker = staged / "send_queue_worker.sh"
            probe = staged / "send_queue.py"
            shutil.copy2(SEND_WORKER, worker)
            worker.chmod(0o755)
            probe.write_text(
                "import json, os\n"
                "print(json.dumps(dict(os.environ), sort_keys=True))\n",
                encoding="utf-8",
            )
            probe.chmod(0o600)
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(root),
                    "AAS_FILE_DELIVERY_SECRETS_FILE": str(root / "authority.json"),
                    "UNRECOGNIZED_PROVIDER_TOKEN": "must-not-reach-worker-child",
                }
            )

            completed = subprocess.run(
                ["/bin/bash", str(worker), "--once"],
                env=env,
                check=False,
                text=True,
                capture_output=True,
                timeout=15,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            child_env = json.loads(completed.stdout)
            self.assertNotIn("UNRECOGNIZED_PROVIDER_TOKEN", child_env)
            self.assertNotIn("AAS_FILE_DELIVERY_SECRETS_FILE", child_env)
            self.assertEqual(child_env.get("PATH"), "/usr/bin:/bin")


if __name__ == "__main__":
    unittest.main()
