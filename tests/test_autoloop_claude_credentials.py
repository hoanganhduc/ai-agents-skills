"""Synthetic-only checks for private native Claude OAuth checkpoints."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

RUNTIME_DIR = (
    Path(__file__).resolve().parents[1]
    / "canonical"
    / "runtime"
    / "skills"
    / "autonomous-research-loop-runtime"
)
sys.path.insert(0, str(RUNTIME_DIR))

import claude_credentials as credentials  # noqa: E402


@unittest.skipUnless(os.name == "posix", "native credential session uses POSIX flock")
class ClaudeCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source_directory = self.root / "source"
        self.checkpoint = self.root / "checkpoint"
        self.role = self.root / "role"
        self.next_role = self.root / "next-role"
        for directory in (self.source_directory, self.checkpoint, self.role, self.next_role):
            directory.mkdir(mode=0o700)
        self.source = self.source_directory / credentials.CREDENTIAL_NAME
        self.initial = {
            "claudeAiOauth": {
                "accessToken": "old-access",
                "refreshToken": "old-refresh",
                "expiresAt": 1,
                "scopes": ["profile"],
            },
            "unrelatedAccount": "omit-me",
        }
        self.write(self.source, self.initial)
        self.source_snapshot = (self.source.read_bytes(), self.source.stat().st_mtime_ns)

    def write(self, path, payload):
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(0o600)

    def session(self, role=None):
        return credentials.credential_session(self.source, self.checkpoint, role or self.role)

    def assert_source_unchanged(self):
        self.assertEqual((self.source.read_bytes(), self.source.stat().st_mtime_ns), self.source_snapshot)

    def test_rotation_is_used_by_next_role_without_global_writeback(self):
        refreshed = {"claudeAiOauth": {"accessToken": "new-access", "refreshToken": "new-refresh", "expiresAt": 2}}
        with self.session() as path:
            self.assertEqual(path, self.role / credentials.CREDENTIAL_NAME)
            self.assertEqual(json.loads(path.read_text()), {"claudeAiOauth": self.initial["claudeAiOauth"]})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.write(path, refreshed)
        with self.session(self.next_role) as path:
            self.assertEqual(json.loads(path.read_text()), refreshed)
        self.assert_source_unchanged()
        self.assertEqual(
            {path.name for path in self.checkpoint.iterdir()},
            {credentials.CREDENTIAL_NAME, credentials.LOCK_NAME},
        )
        for path in self.checkpoint.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_native_nonzero_exit_still_preserves_refresh(self):
        child = """
import json
import pathlib
import sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({"claudeAiOauth": {"accessToken": "renewed"}}))
sys.exit(7)
"""
        with self.session() as path:
            result = subprocess.run(
                [sys.executable, "-B", "-c", child, str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
            self.assertEqual(result.returncode, 7)
        with self.session(self.next_role) as path:
            self.assertEqual(json.loads(path.read_text())["claudeAiOauth"]["accessToken"], "renewed")
        self.assert_source_unchanged()

    def test_body_exception_still_promotes_refresh_and_is_preserved(self):
        primary = ValueError("native call failed")
        with self.assertRaises(ValueError) as caught:
            with self.session() as path:
                self.write(path, {"claudeAiOauth": {"accessToken": "renewed"}})
                raise primary
        self.assertIs(caught.exception, primary)
        with self.session(self.next_role) as path:
            self.assertEqual(json.loads(path.read_text())["claudeAiOauth"]["accessToken"], "renewed")
        self.assert_source_unchanged()

    def test_promotion_failure_preserves_primary_with_redacted_note(self):
        primary = RuntimeError("native call failed")
        with self.assertRaises(RuntimeError) as caught:
            with self.session() as path:
                path.write_text("secret-data malformed json", encoding="utf-8")
                raise primary
        self.assertIs(caught.exception, primary)
        self.assertEqual(primary.__notes__, ["credential checkpoint promotion failed; credential details redacted"])
        self.assertFalse((self.checkpoint / credentials.CREDENTIAL_NAME).exists())
        self.assert_source_unchanged()

    def test_invalid_checkpoint_is_not_replaced_from_source(self):
        checkpoint = self.checkpoint / credentials.CREDENTIAL_NAME
        checkpoint.write_text("secret-data malformed json", encoding="utf-8")
        checkpoint.chmod(0o600)
        try:
            with self.session():
                self.fail("invalid checkpoint was accepted")
        except credentials.CredentialSessionError as exc:
            self.assertNotIn("secret-data", "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        self.assertFalse((self.role / credentials.CREDENTIAL_NAME).exists())
        self.assert_source_unchanged()

    def test_invalid_json_oauth_and_oversized_documents_are_rejected(self):
        values = [
            b"not-json", b"\xff", b"{}", b"[]", b"x" * (credentials.MAX_CREDENTIAL_BYTES + 1),
            {"claudeAiOauth": {"accessToken": ""}},
            {"claudeAiOauth": {"accessToken": 1}},
            {"claudeAiOauth": {"accessToken": "valid", "refreshToken": 1}},
            {"claudeAiOauth": {"accessToken": "valid", "expiresAt": True}},
            {"claudeAiOauth": {"accessToken": "valid", "expiresAt": "tomorrow"}},
            {"claudeAiOauth": {"accessToken": "valid", "expiresAt": float("nan")}},
        ]
        for value in values:
            with self.subTest(kind=type(value).__name__):
                self.source.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
                with self.assertRaises(credentials.CredentialSessionError):
                    with self.session():
                        self.fail("invalid credentials were accepted")
                self.assertFalse((self.checkpoint / credentials.CREDENTIAL_NAME).exists())

    def test_unsafe_file_modes_and_hard_links_are_rejected(self):
        for name in ("source", "checkpoint", "role", "lock"):
            path = {
                "source": self.source,
                "checkpoint": self.checkpoint / credentials.CREDENTIAL_NAME,
                "role": self.role / credentials.CREDENTIAL_NAME,
                "lock": self.checkpoint / credentials.LOCK_NAME,
            }[name]
            for kind in ("mode", "hardlink"):
                with self.subTest(file=name, kind=kind):
                    self.write(path, self.initial)
                    link = self.root / "extra-link"
                    if kind == "mode":
                        path.chmod(0o644)
                    else:
                        os.link(path, link)
                    with self.assertRaises(credentials.CredentialSessionError):
                        with self.session():
                            self.fail("unsafe credential file was accepted")
                    if link.exists():
                        link.unlink()
                    path.unlink()
                    self.write(self.source, self.initial)

    def test_symlink_files_and_directory_components_are_rejected(self):
        for path in (
            self.checkpoint / credentials.CREDENTIAL_NAME,
            self.role / credentials.CREDENTIAL_NAME,
            self.checkpoint / credentials.LOCK_NAME,
        ):
            with self.subTest(name=path.name, parent=path.parent.name):
                if path.exists():
                    path.unlink()
                path.symlink_to(self.source)
                with self.assertRaises(credentials.CredentialSessionError):
                    with self.session():
                        self.fail("symlink was accepted")
                path.unlink()
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(self.source_directory, target_is_directory=True)
        with self.assertRaises(credentials.CredentialSessionError):
            with credentials.credential_session(linked_parent / self.source.name, self.checkpoint, self.role):
                self.fail("symlink parent was accepted")
        actual_source = self.source_directory / "actual.json"
        self.source.rename(actual_source)
        self.source.symlink_to(actual_source)
        with self.assertRaises(credentials.CredentialSessionError):
            with self.session():
                self.fail("symlink source was accepted")

    def test_private_directories_distinct_paths_and_file_owner_are_required(self):
        for directory in (self.source_directory, self.checkpoint, self.role):
            directory.chmod(0o755)
            with self.assertRaises(credentials.CredentialSessionError):
                with self.session():
                    self.fail("nonprivate directory was accepted")
            directory.chmod(0o700)
        with self.assertRaises(credentials.CredentialSessionError):
            with credentials.credential_session(self.source, self.source_directory, self.role):
                self.fail("source directory was used as a checkpoint")
        info = list(self.source.stat())
        info[4] += 1  # st_uid; no privileged chown needed for this boundary check.
        with self.assertRaises(credentials.CredentialSessionError):
            credentials._check_file(os.stat_result(info))
        with mock.patch.object(credentials.os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(credentials.CredentialSessionError):
                with self.session():
                    self.fail("other owner's directory was accepted")

    def test_second_process_fails_fast_and_lock_releases_after_session(self):
        child = """
import pathlib
import sys
sys.path.insert(0, sys.argv[1])
from claude_credentials import CredentialSessionError, credential_session
try:
    with credential_session(*(pathlib.Path(arg) for arg in sys.argv[2:])):
        sys.exit(1)
except CredentialSessionError as exc:
    print(str(exc))
    sys.exit(23)
"""
        with self.session():
            result = subprocess.run(
                [sys.executable, "-B", "-c", child, str(RUNTIME_DIR), str(self.source), str(self.checkpoint), str(self.next_role)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=3,
                check=False,
            )
            self.assertEqual(result.returncode, 23)
            self.assertEqual(result.stdout.strip(), "credential session already active")
        with self.session(self.next_role) as path:
            self.assertTrue(path.is_file())

    def test_failed_atomic_promotion_keeps_previous_checkpoint_and_cleans_temp(self):
        checkpoint = self.checkpoint / credentials.CREDENTIAL_NAME
        self.write(checkpoint, self.initial)
        previous = checkpoint.read_bytes()
        with mock.patch.object(credentials.os, "replace", wraps=credentials.os.replace) as replace:
            with self.assertRaises(credentials.CredentialSessionError):
                with self.session() as path:
                    self.write(path, {"claudeAiOauth": {"accessToken": "renewed"}})
                    replace.side_effect = OSError("secret-data")
        self.assertEqual(checkpoint.read_bytes(), previous)
        self.assertEqual({p.name for p in self.checkpoint.iterdir()}, {credentials.CREDENTIAL_NAME, credentials.LOCK_NAME})

    def test_missing_posix_lock_fails_closed(self):
        with mock.patch.object(credentials, "fcntl", None):
            with self.assertRaises(credentials.CredentialSessionError):
                with self.session():
                    self.fail("session without flock was accepted")


if __name__ == "__main__":
    unittest.main()
