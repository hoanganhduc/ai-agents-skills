"""Publication admission limits, complete scans, and stable path regression checks."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
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

import publication_scan  # noqa: E402
from publication_scan import (  # noqa: E402
    DEFAULT_TEXT_LIMIT,
    LEDGER_LIMIT,
    VERIFIED_TEXT_LIMIT,
    PublicationScanError,
    scan_public_text,
)


@unittest.skipUnless(os.name == "posix", "POSIX no-follow path admission")
class PublicationScanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # macOS's default temporary directory can itself have symlink parents.
        self.directory = Path(temporary.name).resolve()
        self.path = self.directory / "artifact.txt"

    def write(self, data):
        self.path.write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def rejected(self, **kwargs):
        with self.assertRaises(PublicationScanError) as caught:
            scan_public_text(self.path, **kwargs)
        self.assertEqual(str(caught.exception), "publication text scan rejected artifact")
        self.assertTrue(caught.exception.__suppress_context__)

    def test_small_utf8_text_and_empty_file(self):
        for data in (b"", "finite text: caf\u00e9 \U0001f989\n".encode()):
            with self.subTest(length=len(data)):
                self.write(data)
                self.assertEqual(scan_public_text(self.path), data)

    def test_verified_artifact_above_default_limit(self):
        data = b"a" * (DEFAULT_TEXT_LIMIT + 117)
        digest = self.write(data)
        self.rejected()
        self.assertEqual(scan_public_text(self.path, expected_sha256=digest), data)
        self.rejected(expected_sha256="0" * 64)

    def test_hash_required_to_match_even_for_small_files_and_after_shrinking(self):
        digest = self.write(b"a" * (DEFAULT_TEXT_LIMIT + 1))
        self.write(b"small changed content")
        self.rejected(expected_sha256=digest)
        current = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(scan_public_text(self.path, expected_sha256=current.upper()), self.path.read_bytes())

    def test_invalid_expected_digest_rejected_before_open(self):
        for digest in ("", "0" * 63, "0" * 65, "g" * 64, "0" * 64 + "\n", b"0" * 64, False):
            with self.subTest(digest_type=type(digest).__name__, length=len(digest) if digest else 0):
                with mock.patch.object(publication_scan.os, "open") as opened:
                    self.rejected(expected_sha256=digest)
                opened.assert_not_called()

    def test_exact_default_and_verified_caps(self):
        for limit, verified in ((DEFAULT_TEXT_LIMIT, False), (VERIFIED_TEXT_LIMIT, True)):
            with self.subTest(limit=limit):
                data = b"a" * limit
                digest = self.write(data)
                self.assertEqual(scan_public_text(self.path, expected_sha256=digest if verified else None), data)
                self.write(data + b"a")
                self.rejected(expected_sha256=digest if verified else None)

    def test_oversized_verified_file_rejected_before_read(self):
        self.write(b"a" * (VERIFIED_TEXT_LIMIT + 1))
        with mock.patch.object(publication_scan.os, "read") as read:
            self.rejected(expected_sha256="0" * 64)
        read.assert_not_called()

    def test_every_existing_credential_pattern(self):
        credentials = [
            b"sk" + b"-" + b"x" * 20,
            b"sk" + b"-proj-" + b"x" * 20,
            b"sk" + b"-svcacct-" + b"x" * 20,
            b"github" + b"_pat_" + b"x" * 20,
            *(b"gh" + prefix + b"_" + b"x" * 25 for prefix in (b"p", b"o", b"u", b"s", b"r")),
            *(b"-----BEGIN " + prefix + b"PRIVATE KEY-----" for prefix in (b"", b"RSA ", b"EC ", b"OPENSSH ")),
            b"ey" + b"J" + b"x" * 20 + b"." + b"y" * 20 + b".",
            *(name + b'="' + b"x" * 16 + b'"' for name in (b"api_key", b"access_token", b"refresh_token", b"password")),
        ]
        for index, credential in enumerate(credentials):
            with self.subTest(pattern=index):
                digest = self.write(b"public note\n" + credential)
                self.rejected(expected_sha256=digest)

    def test_credentials_beyond_default_limit_and_across_read_boundary(self):
        credential = b"sk" + b"-proj-" + b"x" * 25
        for offset in (DEFAULT_TEXT_LIMIT + 30, DEFAULT_TEXT_LIMIT + publication_scan._READ_SIZE - 4):
            with self.subTest(offset=offset):
                digest = self.write(b"a" * offset + credential + b"\n")
                self.rejected(expected_sha256=digest)

    def test_supplied_secret_bytes_scanned_across_read_boundary(self):
        secret = b"private" + b"-supplied-value-with-punctuation!"
        digest = self.write(b"a" * (DEFAULT_TEXT_LIMIT + publication_scan._READ_SIZE - 4) + secret)
        self.rejected(expected_sha256=digest, secret_values=(secret,))

    def test_binary_and_invalid_utf8_rejected_even_with_matching_hash(self):
        for suffix in (b"\0", b"\xff", b"\xc3"):
            with self.subTest(suffix_size=len(suffix)):
                digest = self.write(b"a" * (DEFAULT_TEXT_LIMIT + 1) + suffix)
                self.rejected(expected_sha256=digest)

    def test_ledger_keeps_its_separate_limit_and_requires_json_objects(self):
        data = b'{"message":"' + b"a" * (DEFAULT_TEXT_LIMIT + 1) + b'"}\n \n{}\n'
        self.write(data)
        self.assertEqual(scan_public_text(self.path, ledger=True), data)
        for invalid in (b"[]\n", b"{}\ntrue\n", b"{bad}\n", b"null\n", b"{}\n42\n"):
            with self.subTest(size=len(invalid)):
                self.write(invalid)
                self.rejected(ledger=True)
        data = b'{"message":"' + b"a" * (LEDGER_LIMIT - 15) + b'"}\n'
        self.assertEqual(len(data), LEDGER_LIMIT)
        digest = self.write(data)
        self.assertEqual(scan_public_text(self.path, ledger=True), data)
        self.write(data + b" ")
        self.rejected(ledger=True)
        self.rejected(ledger=True, expected_sha256=digest)

    def test_ledger_still_runs_hash_and_credential_checks(self):
        self.write(b"{}\n")
        self.rejected(ledger=True, expected_sha256="0" * 64)
        credential = b"gh" + b"p_" + b"x" * 25
        self.write(b'{"value":"' + credential + b'"}\n')
        self.rejected(ledger=True)

    def test_ledger_preserves_unicode_line_separator_inside_json_string(self):
        data = '{"message":"line\u2028paragraph\u2029"}\n{}\n'.encode()
        self.write(data)
        self.assertEqual(scan_public_text(self.path, ledger=True), data)

    def test_symlink_file_and_parent_rejected(self):
        self.write(b"valid text")
        link = self.directory / "link"
        link.symlink_to(self.path)
        with self.assertRaises(PublicationScanError):
            scan_public_text(link)
        parent_link = self.directory / "parent-link"
        parent_link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(PublicationScanError):
            scan_public_text(parent_link / self.path.name)
        with self.assertRaises(PublicationScanError):
            scan_public_text(parent_link / ".." / self.directory.name / self.path.name)

    def test_directory_fifo_missing_and_device_rejected(self):
        fifo = self.directory / "fifo"
        os.mkfifo(fifo)
        for path in (self.directory, fifo, self.path, Path("/dev/null")):
            with self.subTest(kind=path.name):
                with self.assertRaises(PublicationScanError):
                    scan_public_text(path)

    def test_same_size_rewrite_during_read_rejected(self):
        self.write(b"original")
        read = os.read
        changed = False

        def rewrite(descriptor, count):
            nonlocal changed
            data = read(descriptor, count)
            if not changed:
                changed = True
                self.path.write_bytes(b"modified")
            return data

        with mock.patch.object(publication_scan.os, "read", side_effect=rewrite):
            self.rejected()

    def test_replacement_after_read_rejected(self):
        self.write(b"original")
        replacement = self.directory / "replacement"
        replacement.write_bytes(b"modified")
        read = os.read
        changed = False

        def replace(descriptor, count):
            nonlocal changed
            data = read(descriptor, count)
            if not data and not changed:
                changed = True
                os.replace(replacement, self.path)
            return data

        with mock.patch.object(publication_scan.os, "read", side_effect=replace):
            self.rejected()

    def test_parent_replacement_after_read_rejected(self):
        parent = self.directory / "parent"
        parent.mkdir()
        self.path = parent / "artifact.txt"
        self.write(b"original")
        read = os.read
        changed = False

        def replace_parent(descriptor, count):
            nonlocal changed
            data = read(descriptor, count)
            if not data and not changed:
                changed = True
                parent.rename(self.directory / "old-parent")
                parent.mkdir()
                self.path.write_bytes(b"modified")
            return data

        with mock.patch.object(publication_scan.os, "read", side_effect=replace_parent):
            self.rejected()

    def test_growing_file_read_is_bounded(self):
        self.write(b"")
        total = 0

        def growing_read(descriptor, count):
            nonlocal total
            total += count
            return b"a" * count

        with mock.patch.object(publication_scan.os, "read", side_effect=growing_read):
            self.rejected()
        self.assertEqual(total, DEFAULT_TEXT_LIMIT + 1)

    def test_invalid_supplied_secrets_have_redacted_failure(self):
        self.write(b"public text")
        for values in ((b"",), ("private value",), None):
            with self.subTest(value_type=type(values).__name__):
                self.rejected(secret_values=values)


if __name__ == "__main__":
    unittest.main()
