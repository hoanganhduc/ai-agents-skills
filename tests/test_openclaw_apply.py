from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.ai_agents_skills.cli import main
from installer.ai_agents_skills.openclaw_apply import apply_manifest, uninstall_manifest
from installer.ai_agents_skills.openclaw_inventory import build_inventory
from installer.ai_agents_skills.openclaw_manifest import approve_manifest, build_manifest
from tests.test_openclaw_inventory import CANARY_TEXT, make_source_root, snapshot
from tests.test_openclaw_manifest import CREATED_AT


REVIEWED_AT = "2026-05-01T00:01:00Z"


def approved_manifest(source_root: Path, target_root: Path, agents: list[str] | None = None) -> dict[str, object]:
    inventory = build_inventory(source_root)
    manifest = build_manifest(
        inventory,
        target_root,
        target_agents=agents or ["codex"],
        created_at=CREATED_AT,
    )
    return approve_manifest(manifest, reviewer="phase3-test", reviewed_at=REVIEWED_AT)


def write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class OpenClawApplyTests(unittest.TestCase):
    def test_apply_requires_approved_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "fake-openclaw"
            target_root = Path(tmp) / "fake-home"
            source_root.mkdir()
            target_root.mkdir()
            make_source_root(source_root)
            inventory = build_inventory(source_root)
            manifest = build_manifest(inventory, target_root, target_agents=["codex"], created_at=CREATED_AT)

            dry_run = apply_manifest(manifest, target_root, dry_run=True)
            self.assertTrue(dry_run["dry_run"])

            with self.assertRaisesRegex(ValueError, "approved"):
                apply_manifest(manifest, target_root, dry_run=False)

    def test_apply_and_uninstall_roundtrip_restores_fake_target_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "fake-openclaw"
            target_root = Path(tmp) / "fake-home"
            source_root.mkdir()
            target_root.mkdir()
            make_source_root(source_root)
            before_source = snapshot(source_root)
            before_target = snapshot(target_root)
            manifest = approved_manifest(source_root, target_root, ["codex", "claude"])

            applied = apply_manifest(manifest, target_root, dry_run=False)

            self.assertFalse(applied["dry_run"])
            self.assertTrue(any(action["applied"] for action in applied["actions"]))
            serialized_target = json.dumps(snapshot(target_root), sort_keys=True, default=str)
            self.assertNotIn(CANARY_TEXT, serialized_target)
            self.assertEqual(snapshot(source_root), before_source)

            dry_uninstall = uninstall_manifest(target_root, manifest_id=manifest["manifest_id"], dry_run=True)
            self.assertTrue(dry_uninstall["dry_run"])
            self.assertTrue(dry_uninstall["actions"])

            removed = uninstall_manifest(target_root, manifest_id=manifest["manifest_id"], dry_run=False)

            self.assertFalse(removed["dry_run"])
            self.assertTrue(removed["removed"])
            self.assertEqual(snapshot(target_root), before_target)
            self.assertEqual(snapshot(source_root), before_source)

    def test_apply_dry_run_and_apply_actions_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "fake-openclaw"
            target_root = Path(tmp) / "fake-home"
            source_root.mkdir()
            target_root.mkdir()
            make_source_root(source_root)
            manifest = approved_manifest(source_root, target_root, ["codex"])

            dry_run = apply_manifest(manifest, target_root, dry_run=True)
            applied = apply_manifest(manifest, target_root, dry_run=False)

            dry_actions = [
                (action["key"], action["operation"], action["relative_path"], action["reason"], action["blocked"])
                for action in dry_run["actions"]
            ]
            applied_actions = [
                (action["key"], action["operation"], action["relative_path"], action["reason"], action["blocked"])
                for action in applied["actions"]
            ]
            self.assertEqual(applied_actions, dry_actions)

    def test_apply_refuses_symlinked_state_dir_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "fake-openclaw"
            target_root = Path(tmp) / "fake-home"
            outside = Path(tmp) / "outside-state"
            source_root.mkdir()
            target_root.mkdir()
            outside.mkdir()
            (target_root / ".ai-agents-skills").symlink_to(outside, target_is_directory=True)
            make_source_root(source_root)
            before_target = snapshot(target_root)
            manifest = approved_manifest(source_root, target_root, ["codex"])

            with self.assertRaisesRegex(ValueError, "installer state"):
                apply_manifest(manifest, target_root, dry_run=False)

            self.assertEqual(snapshot(target_root), before_target)
            self.assertEqual(snapshot(outside), {})

    def test_apply_refuses_symlinked_target_parent_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "fake-openclaw"
            target_root = Path(tmp) / "fake-home"
            outside = Path(tmp) / "outside-codex"
            source_root.mkdir()
            target_root.mkdir()
            outside.mkdir()
            make_source_root(source_root)
            manifest = approved_manifest(source_root, target_root, ["codex"])
            (target_root / ".codex").symlink_to(outside, target_is_directory=True)
            before_target = snapshot(target_root)

            dry_run = apply_manifest(manifest, target_root, dry_run=True)
            self.assertIn("target-parent-is-symlink", {action["reason"] for action in dry_run["actions"]})
            with self.assertRaisesRegex(ValueError, "target-parent-is-symlink"):
                apply_manifest(manifest, target_root, dry_run=False)

            self.assertEqual(snapshot(target_root), before_target)
            self.assertEqual(snapshot(outside), {})

    def test_apply_refuses_real_system_target_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "fake-openclaw"
            target_root = Path(tmp) / "fake-home"
            source_root.mkdir()
            target_root.mkdir()
            make_source_root(source_root)
            manifest = approved_manifest(source_root, target_root, ["codex"])

            with patch("installer.ai_agents_skills.openclaw_apply.is_real_system_root", return_value=True):
                with self.assertRaisesRegex(ValueError, "real-system target roots"):
                    apply_manifest(manifest, target_root, dry_run=True)

    def test_apply_accepts_windows_shaped_fake_target_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "fake-openclaw"
            target_root = Path(tmp) / "C" / "Users" / "agent"
            source_root.mkdir()
            target_root.mkdir(parents=True)
            make_source_root(source_root)
            manifest = approved_manifest(source_root, target_root, ["codex"])

            applied = apply_manifest(manifest, target_root, dry_run=False)
            removed = uninstall_manifest(target_root, manifest_id=manifest["manifest_id"], dry_run=False)

            self.assertTrue(any(action["applied"] for action in applied["actions"]))
            self.assertTrue(removed["removed"])

    def test_apply_fails_closed_on_target_drift_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "fake-openclaw"
            target_root = Path(tmp) / "fake-home"
            source_root.mkdir()
            target_root.mkdir()
            make_source_root(source_root)
            manifest = approved_manifest(source_root, target_root, ["codex"])
            writable = next(action for action in manifest["actions"] if action["operation"] != "no-op")
            drift_path = target_root / writable["target"]["relative_path"]
            drift_path.parent.mkdir(parents=True)
            drift_path.write_text("drift before apply\n", encoding="utf-8")
            before_target = snapshot(target_root)

            with self.assertRaisesRegex(ValueError, "preflight"):
                apply_manifest(manifest, target_root, dry_run=False)

            self.assertEqual(snapshot(target_root), before_target)

    def test_uninstall_preserves_changed_generated_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "fake-openclaw"
            target_root = Path(tmp) / "fake-home"
            source_root.mkdir()
            target_root.mkdir()
            make_source_root(source_root)
            manifest = approved_manifest(source_root, target_root, ["codex"])
            applied = apply_manifest(manifest, target_root, dry_run=False)
            changed = next(action for action in applied["actions"] if action.get("applied"))
            changed_path = target_root / changed["relative_path"]
            changed_path.write_text("user changed generated review file\n", encoding="utf-8")

            result = uninstall_manifest(target_root, manifest_id=manifest["manifest_id"], dry_run=False)

            skipped = [action for action in result["actions"] if action["operation"] == "skip-conflict"]
            self.assertTrue(skipped)
            self.assertTrue(changed_path.exists())

    def test_cli_openclaw_approve_apply_uninstall_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "fake-openclaw"
            target_root = Path(tmp) / "fake-home"
            manifest_path = Path(tmp) / "manifest.json"
            approved_path = Path(tmp) / "approved.json"
            source_root.mkdir()
            target_root.mkdir()
            make_source_root(source_root)
            manifest = build_manifest(build_inventory(source_root), target_root, target_agents=["codex"], created_at=CREATED_AT)
            write_json(manifest_path, manifest)

            approved_output = io.StringIO()
            with contextlib.redirect_stdout(approved_output):
                exit_code = main(
                    [
                        "--json",
                        "openclaw-approve-manifest",
                        "--manifest",
                        str(manifest_path),
                        "--reviewer",
                        "phase3-test",
                        "--reviewed-at",
                        REVIEWED_AT,
                    ]
                )
            self.assertEqual(exit_code, 0)
            approved = json.loads(approved_output.getvalue())
            self.assertEqual(approved["approval"]["review_status"], "approved")
            write_json(approved_path, approved)

            apply_output = io.StringIO()
            with contextlib.redirect_stdout(apply_output):
                exit_code = main(
                    [
                        "--json",
                        "openclaw-apply-manifest",
                        "--manifest",
                        str(approved_path),
                        "--target-root",
                        str(target_root),
                        "--apply",
                    ]
                )
            self.assertEqual(exit_code, 0)
            applied = json.loads(apply_output.getvalue())
            self.assertFalse(applied["dry_run"])

            uninstall_output = io.StringIO()
            with contextlib.redirect_stdout(uninstall_output):
                exit_code = main(
                    [
                        "--json",
                        "openclaw-uninstall-manifest",
                        "--target-root",
                        str(target_root),
                        "--manifest-id",
                        approved["manifest_id"],
                        "--apply",
                    ]
                )
            self.assertEqual(exit_code, 0)
            uninstalled = json.loads(uninstall_output.getvalue())
            self.assertFalse(uninstalled["dry_run"])
            self.assertEqual(snapshot(target_root), {})


if __name__ == "__main__":
    unittest.main()
