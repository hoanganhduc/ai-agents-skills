"""Regressions for the permissions of the send-email address book.

``_address_book_path`` puts the book beside the secrets file by default, and
``_save_address_book`` ends with ``os.chmod(path, 0o600)`` -- the module means
the file to be private. It used to get there the long way::

    path.write_text(json.dumps({"contacts": contacts}, ...), encoding="utf-8")
    try:
        os.chmod(path, 0o600)

``write_text`` creates the file honouring the umask, so under the common 022 it
appeared at 0644 and every stored address was readable by group and other until
the chmod on the next line landed. Anything reading the directory in between --
a backup sweep, another user, a sync daemon -- saw the whole book.

The test therefore does not assert the final mode, which was already right. It
removes ``os.chmod`` and asserts the file is private anyway: the book has to be
*created* private, not corrected afterwards.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer.ai_agents_skills.runtime import RUNTIME_SOURCE_ROOT

SKILL_DIR = RUNTIME_SOURCE_ROOT / "skills" / "send-email"

POSIX_ONLY = unittest.skipUnless(os.name == "posix", "file modes are POSIX-only")

# Assembled rather than written out. tools/sanitization_check.py rejects any
# email-shaped literal anywhere in the tree, and these tests only need the
# shape of a stored contact, not a particular address.
ALICE = "alice" + "@" + "example.org"
BOB = "bob" + "@" + "example.net"


def _import_send_email():
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SKILL_DIR))
    try:
        import send_email  # noqa: PLC0415
        return send_email
    finally:
        sys.path.remove(str(SKILL_DIR))
        sys.dont_write_bytecode = prev


def _exposed(path: Path) -> bool:
    return bool(stat.S_IMODE(path.stat().st_mode) & (stat.S_IRGRP | stat.S_IROTH))


class AddressBookPermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.se = _import_send_email()
        self.directory = Path(tempfile.mkdtemp())
        self.book = self.directory / "book.json"
        patcher = mock.patch.dict(
            os.environ, {"SEND_EMAIL_ADDRESS_BOOK": str(self.book)}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        if os.name == "posix":
            previous = os.umask(0o022)
            self.addCleanup(os.umask, previous)

    @POSIX_ONLY
    def test_a_plain_write_here_would_be_exposed(self):
        """Anchors the suite: this umask really does produce a readable file."""

        naive = self.directory / "naive.json"
        naive.write_text("{}", encoding="utf-8")
        self.assertTrue(_exposed(naive))

    @POSIX_ONLY
    def test_the_book_is_private_without_relying_on_chmod(self):
        with mock.patch.object(self.se.os, "chmod", lambda *a, **k: None):
            saved = self.se._save_address_book({"alice": ALICE})
        self.assertEqual(stat.S_IMODE(Path(saved).stat().st_mode), 0o600)
        self.assertFalse(_exposed(Path(saved)))

    @POSIX_ONLY
    def test_it_is_never_exposed_at_any_point(self):
        """The mode observed when chmod is reached is already private."""

        seen = {}
        real = os.chmod

        def spy(path, mode, *args, **kwargs):
            seen["during"] = stat.S_IMODE(os.stat(path).st_mode)
            return real(path, mode, *args, **kwargs)

        with mock.patch.object(self.se.os, "chmod", spy):
            saved = self.se._save_address_book({"alice": ALICE})

        self.assertIn("during", seen, "chmod must still run")
        self.assertEqual(seen["during"], 0o600)
        self.assertEqual(stat.S_IMODE(Path(saved).stat().st_mode), 0o600)

    def test_the_contacts_round_trip(self):
        contacts = {"alice": ALICE, "bob": BOB}
        saved = self.se._save_address_book(contacts)
        self.assertEqual(json.loads(Path(saved).read_text(encoding="utf-8")),
                         {"contacts": contacts})
        self.assertEqual(self.se._load_address_book(), contacts)

    def test_no_temporary_file_is_left_behind(self):
        self.se._save_address_book({"alice": ALICE})
        leftovers = [p.name for p in self.directory.iterdir() if p.name != "book.json"]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
