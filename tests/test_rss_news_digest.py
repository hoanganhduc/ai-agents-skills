"""Unit tests for rss-news-digest stub writing (offline, no network)."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[1]
RSS = REPO / "canonical" / "runtime" / "skills" / "rss-news-digest" / "rss_news_digest.py"


def _mod():
    spec = importlib.util.spec_from_file_location("aas_rss_news_digest_test", RSS)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _items(count: int) -> list[dict]:
    # `key` is the id the writer slugs the filename from; an item without one
    # is skipped before the write, so a fixture keyed on anything else would
    # never reach the code under test.
    return [
        {
            "key": f"item-{n}",
            "title": f"Title {n}",
            "link": f"https://example.invalid/{n}",
            "summary": "summary",
            "tag": "research",
            "feed_title": "Feed",
            "published": "2026-01-01",
            "score": 1,
        }
        for n in range(count)
    ]


class DigestStubWriteFailuresAreReported(unittest.TestCase):
    """A digest that silently drops items reports work it never did."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name) / "ws"
        self.papers = self.workspace / "memory" / "papers"
        self.papers.mkdir(parents=True)
        self._saved = os.environ.get("AAS_RUNTIME_WORKSPACE")
        os.environ["AAS_RUNTIME_WORKSPACE"] = str(self.workspace)

    def tearDown(self) -> None:
        os.chmod(self.papers, 0o700)
        if self._saved is None:
            os.environ.pop("AAS_RUNTIME_WORKSPACE", None)
        else:
            os.environ["AAS_RUNTIME_WORKSPACE"] = self._saved
        self.tmp.cleanup()

    def test_a_writable_workspace_writes_every_stub_and_reports_nothing(self) -> None:
        """Anchors the failure test: the fixture does reach the write."""
        mod = _mod()
        self.assertEqual(mod._write_digest_stubs(_items(3)), [])
        self.assertEqual(len(list(self.papers.glob("digest_*.md"))), 3)

    @unittest.skipUnless(os.name == "posix", "directory modes are POSIX")
    @unittest.skipIf(os.getuid() == 0, "root ignores directory write permission")
    def test_an_unwritable_papers_directory_names_every_dropped_item(self) -> None:
        mod = _mod()
        os.chmod(self.papers, 0o500)
        unwritten = mod._write_digest_stubs(_items(3))
        self.assertEqual(len(unwritten), 3, unwritten)
        for n in range(3):
            self.assertTrue(
                any(problem.startswith(f"item-{n}:") for problem in unwritten),
                f"item-{n} was dropped without being named: {unwritten}",
            )
        self.assertEqual(len(list(self.papers.glob("digest_*.md"))), 0)

    @unittest.skipUnless(os.name == "posix", "directory modes are POSIX")
    @unittest.skipIf(os.getuid() == 0, "root ignores directory write permission")
    def test_an_unwritable_ledger_is_reported_so_the_next_run_is_not_a_surprise(self) -> None:
        mod = _mod()
        library = self.workspace / "data" / "library"
        library.mkdir(parents=True)
        os.chmod(library, 0o500)
        try:
            unwritten = mod._write_digest_stubs(_items(2))
        finally:
            os.chmod(library, 0o700)
        # The stubs themselves land; only the ledger write fails.
        self.assertEqual(len(list(self.papers.glob("digest_*.md"))), 2)
        self.assertEqual(len(unwritten), 1, unwritten)
        self.assertTrue(unwritten[0].startswith("ingested ledger:"), unwritten)


if __name__ == "__main__":
    unittest.main()
