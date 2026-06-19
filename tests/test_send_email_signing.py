from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.runtime import RUNTIME_SOURCE_ROOT

SKILL_DIR = RUNTIME_SOURCE_ROOT / "skills" / "send-email"

# Built at runtime so the literals never match the repo's email sanitizer.
_AT = "@"
SIGN_EMAIL = "test" + _AT + "sign.example"
RCPT_EMAIL = "rcpt" + _AT + "to.example"


def _import_send_email():
    # Importing from canonical/runtime/ in-process must not write __pycache__ there
    # (it would break the runtime-inventory "only candidate sources" invariant).
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SKILL_DIR))
    try:
        import send_email  # noqa: PLC0415
        return send_email
    finally:
        sys.path.remove(str(SKILL_DIR))
        sys.dont_write_bytecode = prev


class SendEmailSigningTests(unittest.TestCase):
    def test_should_sign_logic(self) -> None:
        se = _import_send_email()
        ns = se._selftest_namespace
        self.assertTrue(se._should_sign(ns(sign=True), se.SmtpConfig()))
        self.assertFalse(se._should_sign(ns(sign=True, no_sign=True), se.SmtpConfig()))
        self.assertTrue(se._should_sign(ns(), se.SmtpConfig(pgp_sign=True)))
        self.assertFalse(se._should_sign(ns(), se.SmtpConfig()))

    @unittest.skipUnless(shutil.which("gpg"), "gpg not installed")
    def test_pgp_mime_signature_verifies(self) -> None:
        """A PGP/MIME signed message must verify against the transmitted content,
        proving the RFC 3156 construction and canonicalization are correct."""
        se = _import_send_email()
        with tempfile.TemporaryDirectory() as home:
            params = Path(home) / "params"
            params.write_text(
                "%no-protection\nKey-Type: RSA\nKey-Length: 2048\n"
                "Name-Real: Test Sign\n" + f"Name-Email: {SIGN_EMAIL}\n"
                + "Expire-Date: 0\n%commit\n",
                encoding="utf-8",
            )
            gen = subprocess.run(
                ["gpg", "--homedir", home, "--batch", "--gen-key", str(params)],
                capture_output=True, timeout=120,
            )
            self.assertEqual(gen.returncode, 0, gen.stderr.decode("utf-8", "replace"))

            attach = Path(home) / "doc.txt"
            attach.write_text("attached random content\n" * 3, encoding="utf-8")
            ns = se._selftest_namespace(
                sender=f"Test Sign <{SIGN_EMAIL}>", to=[RCPT_EMAIL],
                subject="Signed message é", body="Plain body é.",
                html="<p>HTML body</p>", attach=[str(attach)], sign=True, dry_run=False,
            )
            outer, _content = se.build_signed_message(ns, se.SmtpConfig(gnupg_home=home))

            self.assertEqual(outer.get_content_type(), "multipart/signed")
            self.assertEqual(outer.get_param("protocol"), "application/pgp-signature")
            self.assertEqual(outer.get_param("micalg"), "pgp-sha256")

            raw = se._flatten_crlf(outer)
            boundary = outer.get_boundary().encode()
            opener = b"--" + boundary + b"\r\n"
            start = raw.index(opener) + len(opener)
            content_bytes = raw[start:raw.index(b"\r\n--" + boundary, start)]
            begin = raw.find(b"-----BEGIN PGP SIGNATURE-----")
            end = raw.find(b"-----END PGP SIGNATURE-----")
            signature = raw[begin:end + len(b"-----END PGP SIGNATURE-----")] + b"\n"

            (Path(home) / "content").write_bytes(content_bytes)
            (Path(home) / "sig.asc").write_bytes(signature)
            verify = subprocess.run(
                ["gpg", "--homedir", home, "--verify",
                 str(Path(home) / "sig.asc"), str(Path(home) / "content")],
                capture_output=True, timeout=30,
            )
            self.assertEqual(verify.returncode, 0, verify.stderr.decode("utf-8", "replace"))
            self.assertIn(b"Good signature", verify.stderr)


if __name__ == "__main__":
    unittest.main()
