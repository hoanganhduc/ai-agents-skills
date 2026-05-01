from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.cli import main
from installer.ai_agents_skills.openclaw_inventory import build_inventory
from installer.ai_agents_skills.openclaw_manifest import build_manifest
from tests.test_openclaw_inventory import CANARY_TEXT, fake_root_path, make_source_root, snapshot


CREATED_AT = "2026-05-01T00:00:00Z"


def write_inventory(path: Path, inventory: dict[str, object]) -> None:
    path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class OpenClawDryRunManifestTests(unittest.TestCase):
    def test_manifest_is_review_only_content_addressed_and_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = fake_root_path(tmp, "fake-openclaw")
            target_root = fake_root_path(tmp, "fake-home")
            source_root.mkdir()
            target_root.mkdir()
            make_source_root(source_root)
            before_source = snapshot(source_root)
            before_target = snapshot(target_root)

            inventory = build_inventory(source_root)
            manifest = build_manifest(
                inventory,
                target_root,
                target_agents=["codex", "claude"],
                created_at=CREATED_AT,
            )

            self.assertEqual(snapshot(source_root), before_source)
            self.assertEqual(snapshot(target_root), before_target)
            self.assertEqual(manifest["manifest_schema_version"], "openclaw.apply-manifest.v1")
            self.assertTrue(manifest["manifest_id"].startswith("manifest_"))
            self.assertEqual(manifest["source_inventory_id"], inventory["inventory_id"])
            self.assertEqual(manifest["target_agent_refs"], ["claude", "codex"])
            self.assertEqual(manifest["approval"], {"review_status": "unreviewed"})
            self.assertTrue(manifest["apply_policy"]["no_recompute"])
            self.assertTrue(manifest["apply_policy"]["content_addressed"])
            self.assertTrue(manifest["apply_policy"]["fail_closed_on_drift"])

            serialized = json.dumps(manifest, sort_keys=True)
            self.assertNotIn(str(source_root), serialized)
            self.assertNotIn(str(target_root), serialized)
            self.assertNotIn(CANARY_TEXT, serialized)
            self.assertNotIn("<FAKE_OPENCLAW_ROOT>", serialized)
            self.assertTrue(manifest["actions"])
            self.assertEqual(
                len({action["action_id"] for action in manifest["actions"]}),
                len(manifest["actions"]),
            )
            self.assertTrue(
                all(action["target"]["containment_policy"] == "must-stay-under-target-root" for action in manifest["actions"])
            )
            self.assertTrue(
                all(action["precondition"]["owner_policy"] == "expected-absent" for action in manifest["actions"])
            )
            self.assertTrue(any(action["operation"] == "create-reference-doc" for action in manifest["actions"]))
            self.assertTrue(any(action["operation"] == "create-template" for action in manifest["actions"]))
            self.assertTrue(any(action["operation"] == "no-op" for action in manifest["actions"]))

    def test_manifest_generation_is_deterministic_for_fixed_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = fake_root_path(tmp, "fake-openclaw")
            target_root = fake_root_path(tmp, "fake-home")
            source_root.mkdir()
            target_root.mkdir()
            make_source_root(source_root)
            inventory = build_inventory(source_root)

            first = build_manifest(inventory, target_root, target_agents=["codex"], created_at=CREATED_AT)
            second = build_manifest(inventory, target_root, target_agents=["codex"], created_at=CREATED_AT)

            self.assertEqual(first, second)

    def test_existing_target_path_becomes_no_op_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = fake_root_path(tmp, "fake-openclaw")
            target_root = fake_root_path(tmp, "fake-home")
            source_root.mkdir()
            target_root.mkdir()
            make_source_root(source_root)
            inventory = build_inventory(source_root)
            clean = build_manifest(inventory, target_root, target_agents=["codex"], created_at=CREATED_AT)
            target = Path(clean["actions"][0]["target"]["relative_path"])
            existing = target_root / target
            existing.parent.mkdir(parents=True)
            existing.write_text("user-owned existing target\n", encoding="utf-8")

            collided = build_manifest(inventory, target_root, target_agents=["codex"], created_at=CREATED_AT)
            collided_action = next(
                action
                for action in collided["actions"]
                if action["target"]["relative_path"] == target.as_posix()
            )

            self.assertEqual(collided_action["operation"], "no-op")
            self.assertEqual(collided_action["collision"]["policy"], "skip-report")
            self.assertEqual(collided_action["precondition"]["owner_policy"], "unmanaged-preserve")

    def test_manifest_refuses_unsafe_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target_root = fake_root_path(tmp, "fake-home")
            target_root.mkdir()
            inventory = {
                "schema_version": "openclaw.inventory.v1",
                "inventory_id": "inv_unsafe",
                "source_root": {"explicit_input": True},
                "denylist_version": "openclaw.denylist.v1",
                "redaction_version": "openclaw.redaction.v1",
                "content_read_policy": "deny-by-default",
                "contains_raw_paths": True,
                "items": [],
                "denied_categories": [],
            }

            with self.assertRaisesRegex(ValueError, "raw paths"):
                build_manifest(inventory, target_root, created_at=CREATED_AT)

            inventory["contains_raw_paths"] = False
            inventory["denied_categories"] = [
                {
                    "category_id": "source-root-symlink-prefix-denied",
                    "reason_code": "source-root-symlink-prefix-denied",
                    "count": 1,
                    "read_policy": "lstat-only",
                }
            ]
            with self.assertRaisesRegex(ValueError, "critical denial"):
                build_manifest(inventory, target_root, created_at=CREATED_AT)

    def test_cli_openclaw_dry_run_manifest_reads_inventory_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = fake_root_path(tmp, "fake-openclaw")
            target_root = fake_root_path(tmp, "fake-home")
            inventory_path = fake_root_path(tmp, "inventory.json")
            source_root.mkdir()
            target_root.mkdir()
            make_source_root(source_root)
            write_inventory(inventory_path, build_inventory(source_root))
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "--json",
                        "openclaw-dry-run-manifest",
                        "--inventory",
                        str(inventory_path),
                        "--target-root",
                        str(target_root),
                        "--target-agents",
                        "codex",
                        "--created-at",
                        CREATED_AT,
                    ]
                )

            self.assertEqual(exit_code, 0)
            manifest = json.loads(output.getvalue())
            self.assertEqual(manifest["target_agent_refs"], ["codex"])
            self.assertEqual(manifest["approval"]["review_status"], "unreviewed")
            serialized = json.dumps(manifest, sort_keys=True)
            self.assertNotIn(str(source_root), serialized)
            self.assertNotIn(str(target_root), serialized)
            self.assertNotIn(CANARY_TEXT, serialized)


if __name__ == "__main__":
    unittest.main()
