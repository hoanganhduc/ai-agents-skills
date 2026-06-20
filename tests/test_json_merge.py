from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.json_merge import (
    MANAGED_BY,
    extract_hook_entry,
    load_json_object,
    merge_hook_entry,
    remove_hook_entry,
)

ENTRY = {"hooks": [{"type": "command", "command": "autoloop hook-check"}]}
MID = "autoloop-stop"


class JsonMergeTests(unittest.TestCase):
    def test_merge_into_empty_settings(self) -> None:
        merged, changed, _ = merge_hook_entry({}, "Stop", ENTRY, MID)
        self.assertTrue(changed)
        entry = extract_hook_entry(merged, "Stop", MID)
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry["_managedBy"], MANAGED_BY)
        self.assertEqual(entry["_id"], MID)
        self.assertEqual(entry["hooks"], ENTRY["hooks"])

    def test_merge_preserves_existing_user_hooks(self) -> None:
        user = {
            "model": "opus",
            "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "user-stop"}]}],
                "PreToolUse": [{"matcher": "Bash", "hooks": []}],
            },
        }
        merged, changed, _ = merge_hook_entry(user, "Stop", ENTRY, MID)
        self.assertTrue(changed)
        self.assertEqual(merged["model"], "opus")
        self.assertEqual(merged["hooks"]["PreToolUse"], user["hooks"]["PreToolUse"])
        stop = merged["hooks"]["Stop"]
        self.assertEqual(len(stop), 2)
        # the pre-existing user entry stays first and untouched
        self.assertEqual(stop[0], user["hooks"]["Stop"][0])
        self.assertEqual(stop[1]["_managedBy"], MANAGED_BY)

    def test_merge_is_idempotent(self) -> None:
        once, _, _ = merge_hook_entry({}, "Stop", ENTRY, MID)
        twice, changed, _ = merge_hook_entry(once, "Stop", ENTRY, MID)
        self.assertFalse(changed)
        self.assertEqual(once, twice)
        self.assertEqual(len(twice["hooks"]["Stop"]), 1)

    def test_merge_upserts_changed_entry(self) -> None:
        once, _, _ = merge_hook_entry({}, "Stop", ENTRY, MID)
        updated = {"hooks": [{"type": "command", "command": "new-cmd"}]}
        twice, changed, _ = merge_hook_entry(once, "Stop", updated, MID)
        self.assertTrue(changed)
        self.assertEqual(len(twice["hooks"]["Stop"]), 1)
        entry = extract_hook_entry(twice, "Stop", MID)
        assert entry is not None
        self.assertEqual(entry["hooks"], updated["hooks"])

    def test_remove_leaves_user_hooks(self) -> None:
        user = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "user-stop"}]}]}}
        merged, _, created = merge_hook_entry(user, "Stop", ENTRY, MID)
        removed, changed = remove_hook_entry(merged, "Stop", MID, created)
        self.assertTrue(changed)
        self.assertEqual(removed, user)

    def test_full_round_trip_restores_original_populated_settings(self) -> None:
        original = {
            "model": "opus",
            "permissions": {"allow": ["Bash(git *)"]},
            "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "user-stop"}]}],
                "PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "fmt"}]}],
            },
        }
        merged, m_changed, created = merge_hook_entry(original, "Stop", ENTRY, MID)
        self.assertTrue(m_changed)
        restored, r_changed = remove_hook_entry(merged, "Stop", MID, created)
        self.assertTrue(r_changed)
        self.assertEqual(restored, original)

    def test_round_trip_when_no_hooks_key(self) -> None:
        original = {"model": "opus"}
        merged, _, created = merge_hook_entry(original, "Stop", ENTRY, MID)
        self.assertIn("hooks", merged)
        restored, _ = remove_hook_entry(merged, "Stop", MID, created)
        self.assertEqual(restored, original)
        self.assertNotIn("hooks", restored)

    def test_round_trip_preserves_user_empty_hooks_object(self) -> None:
        # A user-authored empty `hooks: {}` must survive install+uninstall.
        original = {"hooks": {}, "model": "opus"}
        merged, _, created = merge_hook_entry(original, "Stop", ENTRY, MID)
        restored, _ = remove_hook_entry(merged, "Stop", MID, created)
        self.assertEqual(restored, original)

    def test_round_trip_preserves_user_empty_event_list(self) -> None:
        # A user-authored empty `hooks.Stop: []` must survive install+uninstall.
        original = {"hooks": {"Stop": []}}
        merged, _, created = merge_hook_entry(original, "Stop", ENTRY, MID)
        restored, _ = remove_hook_entry(merged, "Stop", MID, created)
        self.assertEqual(restored, original)

    def test_remove_keeps_sibling_event(self) -> None:
        original = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]}}
        merged, _, created = merge_hook_entry(original, "Stop", ENTRY, MID)
        restored, changed = remove_hook_entry(merged, "Stop", MID, created)
        self.assertTrue(changed)
        self.assertEqual(restored, original)

    def test_remove_is_noop_when_absent(self) -> None:
        settings = {"hooks": {"Stop": [{"hooks": []}]}}
        removed, changed = remove_hook_entry(settings, "Stop", MID)
        self.assertFalse(changed)
        self.assertEqual(removed, settings)

    def test_load_rejects_non_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{ not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_json_object(path)

    def test_load_rejects_non_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_json_object(path)

    def test_load_missing_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data, existed = load_json_object(Path(tmp) / "nope.json")
            self.assertEqual(data, {})
            self.assertFalse(existed)

    def test_merge_rejects_non_dict_hooks(self) -> None:
        with self.assertRaises(ValueError):
            merge_hook_entry({"hooks": "oops"}, "Stop", ENTRY, MID)

    def test_merge_rejects_non_list_event(self) -> None:
        with self.assertRaises(ValueError):
            merge_hook_entry({"hooks": {"Stop": "oops"}}, "Stop", ENTRY, MID)


if __name__ == "__main__":
    unittest.main()
