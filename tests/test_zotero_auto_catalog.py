"""Regression for auto-catalog's handling of a failed collection lookup.

``auto-catalog.py`` matches each digest paper's topics against the collections
already in the library. That lookup used to be wrapped like this::

    try:
        coll_result = subprocess.run([... "list-collections" ...])
        collections = json.loads(coll_result.stdout).get("collections", [])
        coll_names = _flatten_collection_names(collections)
    except Exception:
        coll_names = []

A non-zero exit was never inspected at all, so a revoked API key -- ``zot``
printing ``403 Forbidden`` and exiting 1 -- produced an empty ``coll_names``
and the run carried on. Every paper then landed in ``Auto-cataloged`` alone,
and the script still printed ``"status": "ok"`` with a summary claiming the
papers were cataloged. A library that could not be read looked exactly like a
library with no collections in it, and the cron entry reported success either
way.

The lookup is still non-fatal -- ``Auto-cataloged`` is a real destination --
but the failure is now named on stderr and in the JSON report, and the status
degrades to ``partial``. The tests below pin the distinction the caller needs:
broken, empty, and healthy libraries must not print the same thing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG = REPO_ROOT / "canonical" / "runtime" / "skills" / "zotero" / "scripts" / "auto-catalog.py"

# Two papers whose titles carry topics the collection names below match on.
BRIDGE_STUB = """import json
print(json.dumps({"papers": [
    {"identifier": "10.1000/graph-recolouring", "title": "Token Sliding on Chordal Graphs"},
    {"identifier": "10.1000/complexity", "title": "Reconfiguration Complexity Dichotomy"},
]}))
"""

COLLECTIONS = [
    {"key": "AAAA1111", "name": "Graph Reconfiguration", "numItems": 12,
     "parentCollection": False, "children": [
         {"key": "BBBB2222", "name": "Token Sliding", "numItems": 5,
          "parentCollection": "AAAA1111", "children": []}]},
    {"key": "CCCC3333", "name": "Complexity Theory", "numItems": 7,
     "parentCollection": False, "children": []},
]

ZOT_HEAD = """import json, sys
if "list-collections" in sys.argv:
"""
ZOT_TAIL = """
print(json.dumps({"status": "ok", "action": "add", "key": "ZZZZ9999", "title": "x"}))
"""
# What a real zot.py does when the key is revoked: a message on stderr, exit 1.
ZOT_FAILING = """    print("Error: Zotero API request failed: 403 Forbidden", file=sys.stderr)
    sys.exit(1)
"""
ZOT_UNPARSEABLE = """    print("<html>Service Unavailable</html>")
    sys.exit(0)
"""


class AutoCatalogCollectionLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name) / "ws"
        bridge_dir = self.workspace / "skills" / "digest-bridge"
        bridge_dir.mkdir(parents=True)
        (bridge_dir / "digest_bridge.py").write_text(BRIDGE_STUB, encoding="utf-8")
        self.zot_dir = self.workspace / "skills" / "zotero"
        self.zot_dir.mkdir(parents=True)
        (self.zot_dir / "config.json").write_text('{"zotero_user_id": "000000"}', encoding="utf-8")

    def _install_zot(self, body: str) -> None:
        (self.zot_dir / "zot.py").write_text(ZOT_HEAD + body + ZOT_TAIL, encoding="utf-8")

    def _install_listing(self, collections: list) -> None:
        body = "    print(json.dumps({'status': 'ok', 'collections': %r}))\n    sys.exit(0)\n" % (
            collections,)
        self._install_zot(body)

    def _run(self) -> tuple[dict, str, int]:
        env = dict(os.environ)
        env["AAS_RUNTIME_WORKSPACE"] = str(self.workspace)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, str(CATALOG), "--dry-run"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=120, check=False,
        )
        self.assertNotIn("Traceback", result.stderr)
        return json.loads(result.stdout), result.stderr, result.returncode

    def test_a_readable_library_matches_topics_and_reports_ok(self) -> None:
        self._install_listing(COLLECTIONS)
        report, stderr, code = self._run()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "ok")
        self.assertNotIn("collection_error", report)
        self.assertEqual(
            [p["collections"] for p in report["papers"]],
            [["Graph Reconfiguration", "Token Sliding", "Auto-cataloged"],
             ["Graph Reconfiguration", "Complexity Theory", "Auto-cataloged"]],
        )
        self.assertNotIn("Collection lookup failed", stderr)

    def test_a_failing_lookup_is_named_rather_than_swallowed(self) -> None:
        self._install_zot(ZOT_FAILING)
        report, stderr, code = self._run()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["code"], "collection_lookup_failed")
        self.assertIn("403 Forbidden", report["collection_error"])
        self.assertIn("Topic matching skipped", report["message"])
        self.assertIn("Collection lookup failed", stderr)

    def test_an_unparseable_listing_is_named_too(self) -> None:
        """The exit code is 0 here; only the payload is wrong."""
        self._install_zot(ZOT_UNPARSEABLE)
        report, stderr, code = self._run()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["code"], "collection_lookup_failed")
        self.assertIn("Collection lookup failed", stderr)

    def test_an_empty_library_is_distinguishable_from_an_unreadable_one(self) -> None:
        """The defect in one sentence: these two used to print the same report."""
        self._install_listing([])
        empty_report, empty_stderr, _ = self._run()
        self._install_zot(ZOT_FAILING)
        broken_report, broken_stderr, _ = self._run()

        # Both fall back to the same destination -- that part is by design.
        fallback = [["Auto-cataloged"], ["Auto-cataloged"]]
        self.assertEqual([p["collections"] for p in empty_report["papers"]], fallback)
        self.assertEqual([p["collections"] for p in broken_report["papers"]], fallback)

        self.assertEqual(empty_report["status"], "ok")
        self.assertEqual(broken_report["status"], "partial")
        self.assertNotEqual(empty_report["message"], broken_report["message"])
        self.assertNotIn("Collection lookup failed", empty_stderr)
        self.assertIn("Collection lookup failed", broken_stderr)


if __name__ == "__main__":
    unittest.main()
