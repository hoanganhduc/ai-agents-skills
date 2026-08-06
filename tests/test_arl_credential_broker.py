"""Offline tests for the ARL credential broker's provider config projection."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNERS_DIR = REPO_ROOT / "canonical" / "runtime" / "runners"
sys.path.insert(0, str(RUNNERS_DIR))

import arl_credential_broker as broker  # noqa: E402


def _bare_state(provider_config: dict[str, str]) -> broker.CredentialState:
    """Build a state without __init__: the projection only reads provider_config."""
    state = broker.CredentialState.__new__(broker.CredentialState)
    state.provider_config = provider_config
    return state


class PrepareConfigProjectionTests(unittest.TestCase):
    def test_seed_state_provider_gets_writable_seeded_copy_without_mounts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "real-grok"
            child_home = root / "child-home"
            source.mkdir()
            child_home.mkdir()
            (source / "auth.json").write_text("{}", encoding="utf-8")
            (source / "config.toml").write_text("", encoding="utf-8")
            (source / "sessions.log").write_text("private", encoding="utf-8")
            state = _bare_state({"GROK_CONFIG_DIR": str(source)})
            projected, mounts = state._prepare_config_projection("grok", child_home)
            target = child_home / ".grok"
            self.assertEqual(projected, {"GROK_CONFIG_DIR": str(target)})
            self.assertEqual(mounts, {})
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            self.assertEqual(
                sorted(item.name for item in target.iterdir()),
                ["auth.json", "config.toml"],
            )
            for name in ("auth.json", "config.toml"):
                self.assertEqual(
                    stat.S_IMODE((target / name).stat().st_mode), 0o600
                )
            self.assertTrue(os.access(target, os.W_OK))

    def test_symlinked_seed_state_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "real-grok"
            child_home = root / "child-home"
            source.mkdir()
            child_home.mkdir()
            secret = root / "outside-secret"
            secret.write_text("{}", encoding="utf-8")
            (source / "auth.json").symlink_to(secret)
            state = _bare_state({"GROK_CONFIG_DIR": str(source)})
            with self.assertRaisesRegex(ValueError, "seed state"):
                state._prepare_config_projection("grok", child_home)

    def test_non_seed_provider_keeps_read_only_mount_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "real-codewhale"
            child_home = root / "child-home"
            source.mkdir()
            child_home.mkdir()
            state = _bare_state({"CODEWHALE_HOME": str(source)})
            projected, mounts = state._prepare_config_projection(
                "codewhale", child_home
            )
            target = child_home / ".codewhale"
            self.assertEqual(projected, {"CODEWHALE_HOME": str(target)})
            self.assertEqual(mounts, {str(target): str(source)})
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
