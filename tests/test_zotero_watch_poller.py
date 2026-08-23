"""Regressions for the zotero watch poller.

Both defects below are silent, and both leave the four-hourly cron entry
doing nothing. The first is in how the poller read ``list-watches``.

``gsp_openclaw_helper list-watches`` answers with the watch store itself --
``read_json(store, {"items": []})`` -- so stdout is ``{"items": [...]}``. The
poller used to bind that envelope straight to the record list::

    watches = json.loads(result.stdout)
    ...
    for watch in watches:
        watch_id = watch.get("id", "")

Iterating a dict walks its keys, so every record arrived as the string
``"items"`` and the loop died on ``watch.get`` before reading a single status.
The cron entry runs every four hours, and each run ended the same way: no PDF
attached, no expired mapping cleared, no status line printed.

The empty case failed just as quietly. An empty store is ``{"items": []}``,
which is a *non-empty* dict, so the ``if not watches`` guard never fired and
even a store with nothing in it reached the crash.

Each test drives the real script through a fake workspace, because the defect
lives in what the script does with the helper's output rather than in any value
it returns.

``watch-keys.json`` carried a second, independent defect. ``load_watch_keys``
parsed it with no guard, and ``save_watch_keys`` wrote it with a plain
``open(..., "w")``, which truncates before ``json.dump`` writes a byte. An
interrupt in that window left an empty file, and nothing rewrites the map
before it is read, so every later run raised out of the poller and no watch was
ever attached again. The write is now staged beside the target and renamed, and
the read reports an unusable map rather than dying on it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_never_matching_predicates import load_module

REPO_ROOT = Path(__file__).resolve().parents[1]
POLLER = REPO_ROOT / "canonical" / "runtime" / "skills" / "zotero" / "scripts" / "watch-poller.py"

FOUND_WATCH = {
    "id": "watch-1700000000-abcdef12",
    "watch_key": "abcdef12",
    "kind": "paper",
    "identifier_type": "doi",
    "identifier": "10.1000/test",
    "status": "found",
    "created_at": 1700000000,
    "updated_at": 1700000100,
    "sent_file_hashes": [],
    "check_count": 3,
}
EXPIRED_WATCH = dict(FOUND_WATCH, id="watch-1700000001-beefcafe",
                     watch_key="beefcafe", status="expired")

# The helper stub prints the store verbatim; zot.py reports a successful
# attach. Neither needs the real skill, and both keep the run offline.
HELPER_STUB = """import os, sys
sys.stdout.write(open(os.environ["FAKE_WATCH_STORE"], encoding="utf-8").read())
"""
ZOT_STUB = """import json, sys
print(json.dumps({"status": "ok", "message": "attached " + sys.argv[2]}))
"""


class _PollerFixture:
    """The fake workspace both defects are exercised in."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.workspace = root / "ws"
        # The manifest installs the helper under an underscored directory name,
        # so the poller's own path constant is what the layout has to match.
        helper_dir = self.workspace / "skills" / "getscipapers_requester"
        helper_dir.mkdir(parents=True)
        (helper_dir / "gsp_openclaw_helper.py").write_text(HELPER_STUB, encoding="utf-8")
        zot_dir = self.workspace / "skills" / "zotero"
        zot_dir.mkdir(parents=True)
        (zot_dir / "zot.py").write_text(ZOT_STUB, encoding="utf-8")
        self.keys_path = self.workspace / "data" / "research" / "zotero" / "watch-keys.json"
        self.keys_path.parent.mkdir(parents=True)
        self.store = root / "store.json"

    def _write_keys(self, mapping: dict) -> None:
        self.keys_path.write_text(json.dumps(mapping), encoding="utf-8")

    def _run(self, items: list) -> subprocess.CompletedProcess:
        self.store.write_text(json.dumps({"items": items}), encoding="utf-8")
        env = dict(os.environ)
        env["AAS_RUNTIME_WORKSPACE"] = str(self.workspace)
        env["FAKE_WATCH_STORE"] = str(self.store)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(POLLER)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=60, check=False,
        )

class WatchPollerTests(_PollerFixture, unittest.TestCase):
    """Reading the ``list-watches`` envelope."""

    def test_an_empty_store_is_reported_not_crashed(self) -> None:
        self._write_keys({})
        result = self._run([])
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.returncode, 0)
        self.assertIn("No active watches", result.stderr)

    def test_a_found_watch_is_attached(self) -> None:
        self._write_keys({FOUND_WATCH["id"]: "ZKEY1234", EXPIRED_WATCH["id"]: "ZKEY5678"})
        result = self._run([FOUND_WATCH])
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {"status": "ok", "action": "watch_poll",
             "attached": 1, "cleared": 1, "remaining": 1},
        )
        remaining = json.loads(self.keys_path.read_text(encoding="utf-8"))
        self.assertEqual(remaining, {EXPIRED_WATCH["id"]: "ZKEY5678"})

    def test_an_expired_watch_clears_its_mapping(self) -> None:
        self._write_keys({FOUND_WATCH["id"]: "ZKEY1234", EXPIRED_WATCH["id"]: "ZKEY5678"})
        result = self._run([EXPIRED_WATCH])
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {"status": "ok", "action": "watch_poll",
             "attached": 0, "cleared": 1, "remaining": 1},
        )
        remaining = json.loads(self.keys_path.read_text(encoding="utf-8"))
        self.assertEqual(remaining, {FOUND_WATCH["id"]: "ZKEY1234"})

    def test_a_watch_without_a_key_mapping_is_skipped(self) -> None:
        self._write_keys({})
        result = self._run([FOUND_WATCH])
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.returncode, 0)
        self.assertIn("no Zotero key mapping", result.stderr)
        self.assertEqual(json.loads(result.stdout)["attached"], 0)

    def test_the_stub_really_speaks_the_helper_envelope(self) -> None:
        """Anchors the suite: a bare list would not exercise the defect."""
        self.store.write_text(json.dumps({"items": [FOUND_WATCH]}), encoding="utf-8")
        env = dict(os.environ)
        env["FAKE_WATCH_STORE"] = str(self.store)
        helper = self.workspace / "skills" / "getscipapers_requester" / "gsp_openclaw_helper.py"
        emitted = subprocess.run(
            [sys.executable, str(helper), "list-watches"],
            capture_output=True, text=True, encoding="utf-8", env=env,
            timeout=60, check=True,
        ).stdout
        payload = json.loads(emitted)
        self.assertIsInstance(payload, dict)
        self.assertEqual([w["id"] for w in payload["items"]], [FOUND_WATCH["id"]])


class WatchKeyStoreTests(_PollerFixture, unittest.TestCase):
    """Reading and writing ``watch-keys.json``."""

    def _load_poller(self):
        """Import the script against this test's workspace.

        WATCH_KEYS_FILE is bound at import time from the environment, so the
        variable has to be in place before the module is executed.
        """
        with mock.patch.dict(os.environ, {"AAS_RUNTIME_WORKSPACE": str(self.workspace)}):
            module = load_module("watch_poller_under_test", POLLER)
        self.assertEqual(module.WATCH_KEYS_FILE, str(self.keys_path))
        return module

    def test_a_truncated_key_map_does_not_stop_the_poll(self) -> None:
        self.keys_path.write_text("", encoding="utf-8")
        result = self._run([FOUND_WATCH])
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Ignoring unreadable", result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ok")

    def test_a_partial_key_map_does_not_stop_the_poll(self) -> None:
        self.keys_path.write_text('{"watch-1700000000-abcdef12": "ZKEY', encoding="utf-8")
        result = self._run([FOUND_WATCH])
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Ignoring unreadable", result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ok")

    def test_a_failed_write_leaves_the_previous_map_intact(self) -> None:
        """The property the rename buys: no window where the map is truncated."""
        module = self._load_poller()
        self._write_keys({FOUND_WATCH["id"]: "ZKEY1234"})
        before = self.keys_path.read_text(encoding="utf-8")
        with mock.patch.object(module.json, "dump", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                module.save_watch_keys({EXPIRED_WATCH["id"]: "ZKEY5678"})
        self.assertEqual(self.keys_path.read_text(encoding="utf-8"), before)
        self.assertEqual(json.loads(before), {FOUND_WATCH["id"]: "ZKEY1234"})

    def test_a_successful_write_replaces_the_map_and_leaves_no_residue(self) -> None:
        module = self._load_poller()
        self._write_keys({FOUND_WATCH["id"]: "ZKEY1234"})
        module.save_watch_keys({EXPIRED_WATCH["id"]: "ZKEY5678"})
        self.assertEqual(
            json.loads(self.keys_path.read_text(encoding="utf-8")),
            {EXPIRED_WATCH["id"]: "ZKEY5678"},
        )
        leftovers = sorted(p.name for p in self.keys_path.parent.iterdir())
        self.assertEqual(leftovers, ["watch-keys.json"])


if __name__ == "__main__":
    unittest.main()
