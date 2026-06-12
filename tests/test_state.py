from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.state import sha256_file, sha256_text, write_text_atomic


class StateFileTests(unittest.TestCase):
    def test_write_text_atomic_preserves_lf_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.txt"
            content = "alpha\nbeta\n"

            write_text_atomic(path, content)

            self.assertEqual(path.read_bytes(), content.encode("utf-8"))
            self.assertEqual(sha256_file(path), sha256_text(content))
