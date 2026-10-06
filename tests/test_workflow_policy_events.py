"""An idempotency key cannot silently erase a different event."""
import sys
import tempfile
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1] / "canonical/runtime/skills/autonomous-research-loop-runtime"
sys.path.insert(0, str(PACK))
from state_transaction import commit_transaction, TransactionError


class EventIdentityTests(unittest.TestCase):
    def test_reused_id_with_changed_content_is_refused_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commit_transaction(root, jsonl_appends={"events.jsonl": [{"event_id": "E1", "status": "pending"}]})
            before = (root / "events.jsonl").read_bytes()
            with self.assertRaises(TransactionError):
                commit_transaction(root, json_files={"result.json": {"accepted": True}},
                    jsonl_appends={"events.jsonl": [{"event_id": "E1", "status": "accepted"}]})
            self.assertEqual((root / "events.jsonl").read_bytes(), before)
            self.assertFalse((root / "result.json").exists())

    def test_identical_canonical_event_deduplicates_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for event in ({"event_id": "E1", "status": "pending"}, {"status": "pending", "event_id": "E1"}):
                commit_transaction(root, jsonl_appends={"events.jsonl": [event]})
            self.assertEqual(len((root / "events.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_implicit_digest_identity_still_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for _ in range(2):
                commit_transaction(root, jsonl_appends={"events.jsonl": [{"status": "pending"}]})
            self.assertEqual(len((root / "events.jsonl").read_text(encoding="utf-8").splitlines()), 1)
