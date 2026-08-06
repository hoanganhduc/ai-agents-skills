"""Credential-selector regression tests for the Calibre runtime."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CALIBRE_ROOT = ROOT / "canonical/runtime/skills/calibre"
CONFIG_PATH = CALIBRE_ROOT / "lib/config.py"


def _load_config_module():
    spec = importlib.util.spec_from_file_location("calibre_secure_config", CONFIG_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class CalibreConfigSecretsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _load_config_module()

    def _write_public_config(self, root: Path) -> None:
        (root / "config.json").write_text(json.dumps({
            "staging_dir": str(root / "staging"),
            "cache_path": str(root / "cache" / "library.json"),
            "db_local_path": str(root / "cache" / "metadata.db"),
        }), encoding="utf-8")
        self.config.SKILL_DIR = str(root)

    @unittest.skipIf(os.name == "nt", "native Windows secret files require the managed projection runner")
    def test_config_prefers_target_neutral_aas_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            aas = root / "aas.json"
            aas.write_text(
                json.dumps(
                    {
                        "GDRIVE_CREDENTIALS": "selected-credentials",
                        "CALIBRE_GDRIVE_FOLDER_ID": "selected-folder",
                    }
                ),
                encoding="utf-8",
            )
            aas.chmod(0o600)
            legacy = root / "legacy.json"
            legacy.write_text(
                json.dumps(
                    {
                        "GDRIVE_CREDENTIALS": "legacy-credentials",
                        "CALIBRE_GDRIVE_FOLDER_ID": "legacy-folder",
                    }
                ),
                encoding="utf-8",
            )
            legacy.chmod(0o600)
            workspace = root / "workspace"
            workspace.mkdir()
            code = (
                "import json,sys; sys.path.insert(0,sys.argv[1]); "
                "from lib.config import load_config; c=load_config(); "
                "print(json.dumps({'credentials': c.get('GDRIVE_CREDENTIALS'), "
                "'folder': c.get('gdrive_folder_id')}))"
            )
            env = os.environ.copy()
            env.update(
                {
                    "AAS_RUNTIME_WORKSPACE": str(workspace),
                    "AAS_CALIBRE_SECRETS_FILE": str(aas),
                    "AAS_SECRETS_FILE": str(legacy),
                    "OPENCLAW_SECRETS_FILE": str(legacy),
                }
            )
            completed = subprocess.run(
                ["python3", "-I", "-B", "-c", code, str(CALIBRE_ROOT)],
                cwd=CALIBRE_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            value = json.loads(completed.stdout)
            self.assertEqual(value["credentials"], "selected-credentials")
            self.assertEqual(value["folder"], "selected-folder")

    def test_direct_wrapper_uses_only_dedicated_calibre_selector(self) -> None:
        wrapper = (CALIBRE_ROOT / "run_cal.sh").read_text(encoding="utf-8")
        self.assertIn("AAS_CALIBRE_SECRETS_FILE", wrapper)
        self.assertIn("unset AAS_SECRETS_FILE OPENCLAW_SECRETS_FILE", wrapper)
        self.assertNotIn('export OPENCLAW_SECRETS_FILE=', wrapper)

    @unittest.skipIf(os.name == "nt", "POSIX descriptor and mode semantics")
    def test_secret_projection_rejects_symlink_hardlink_permissive_and_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_public_config(root)
            private = root / "private.json"
            private.write_text('{"GDRIVE_CREDENTIALS":"safe-value"}', encoding="utf-8")
            private.chmod(0o600)
            symlink = root / "symlink.json"
            symlink.symlink_to(private)
            hardlink = root / "hardlink.json"
            os.link(private, hardlink)
            permissive = root / "permissive.json"
            permissive.write_text('{"GDRIVE_CREDENTIALS":"unsafe-value"}', encoding="utf-8")
            permissive.chmod(0o644)
            oversized = root / "oversized.json"
            oversized.write_bytes(b"{" + b" " * (self.config.MAX_SECRETS_FILE_BYTES + 1) + b"}")
            oversized.chmod(0o600)

            for candidate in (symlink, hardlink, permissive, oversized):
                with self.subTest(candidate=candidate.name):
                    with mock.patch.dict(os.environ, {
                        "AAS_CALIBRE_SECRETS_FILE": str(candidate),
                    }, clear=True):
                        with self.assertRaises(ValueError) as raised:
                            self.config.load_config()
                    self.assertNotIn(str(root), str(raised.exception))
                    self.assertNotIn("safe-value", str(raised.exception))

    @unittest.skipIf(os.name == "nt", "POSIX descriptor identity semantics")
    def test_secret_projection_replacement_between_stat_and_open_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_public_config(root)
            selected = root / "selected.json"
            selected.write_text('{"GDRIVE_CREDENTIALS":"safe-value"}', encoding="utf-8")
            selected.chmod(0o600)
            saved = root / "saved.json"
            real_open = self.config.os.open
            replaced = False

            def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal replaced
                if dir_fd is not None and path == selected.name and not replaced:
                    replaced = True
                    selected.rename(saved)
                    selected.write_text(
                        '{"GDRIVE_CREDENTIALS":"CANARY_REPLACEMENT"}', encoding="utf-8"
                    )
                    selected.chmod(0o600)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with mock.patch.dict(os.environ, {
                "AAS_CALIBRE_SECRETS_FILE": str(selected),
            }, clear=True):
                with mock.patch.object(self.config.os, "open", side_effect=swapping_open):
                    with self.assertRaises(ValueError) as raised:
                        self.config.load_config()

        self.assertTrue(replaced)
        self.assertNotIn("CANARY_REPLACEMENT", str(raised.exception))
        self.assertNotIn(str(root), str(raised.exception))

    def test_secret_projection_rejects_duplicate_unknown_and_nonstring_values(self) -> None:
        hostile = (
            '{"GDRIVE_CREDENTIALS":"one","GDRIVE_CREDENTIALS":"two"}',
            '{"UNSUPPORTED":"value"}',
            '{"GDRIVE_CREDENTIALS":{"nested":"value"}}',
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_public_config(root)
            for index, payload in enumerate(hostile):
                selected = root / f"hostile-{index}.json"
                selected.write_text(payload, encoding="utf-8")
                selected.chmod(0o600)
                with self.subTest(payload=payload):
                    with mock.patch.dict(os.environ, {
                        "AAS_CALIBRE_SECRETS_FILE": str(selected),
                    }, clear=True):
                        with self.assertRaises(ValueError):
                            self.config.load_config()


if __name__ == "__main__":
    unittest.main()
