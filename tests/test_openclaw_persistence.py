from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.cli import main
from installer.ai_agents_skills.openclaw_inventory import build_inventory
from installer.ai_agents_skills.openclaw_manifest import build_manifest, canonical_manifest_payload, stable_digest
from installer.ai_agents_skills.openclaw_persistence import check_persistence_manifest
from tests.test_openclaw_inventory import make_source_root
from tests.test_openclaw_manifest import CREATED_AT, write_inventory


def refresh_manifest_id(manifest: dict[str, object]) -> None:
    manifest["manifest_id"] = f"manifest_{stable_digest(canonical_manifest_payload(manifest))}"


class OpenClawPersistenceTests(unittest.TestCase):
    def test_hook_metadata_remains_inert_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "fake-openclaw"
            target_root = Path(tmp) / "fake-home"
            source_root.mkdir()
            target_root.mkdir()
            make_source_root(source_root)
            manifest = build_manifest(build_inventory(source_root), target_root, target_agents=["codex"], created_at=CREATED_AT)

            result = check_persistence_manifest(manifest)

            self.assertEqual(result["status"], "inert-only")
            self.assertTrue(result["inert_actions"])
            self.assertEqual(result["persistent_actions"], [])

    def test_persistent_action_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "fake-openclaw"
            target_root = Path(tmp) / "fake-home"
            source_root.mkdir()
            target_root.mkdir()
            make_source_root(source_root)
            manifest = build_manifest(build_inventory(source_root), target_root, target_agents=["codex"], created_at=CREATED_AT)
            hook_action = next(action for action in manifest["actions"] if "hook" in action["target"]["relative_path"])
            hook_action["operation"] = "create-file"
            refresh_manifest_id(manifest)

            result = check_persistence_manifest(manifest)

            self.assertEqual(result["status"], "blocked")
            self.assertTrue(result["persistent_actions"])

    def test_cli_persistence_check_accepts_inert_manifest_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_root = Path(tmp) / "fake-openclaw"
            target_root = Path(tmp) / "fake-home"
            manifest_path = Path(tmp) / "manifest.json"
            source_root.mkdir()
            target_root.mkdir()
            make_source_root(source_root)
            manifest = build_manifest(build_inventory(source_root), target_root, target_agents=["codex"], created_at=CREATED_AT)
            write_inventory(manifest_path, manifest)
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                exit_code = main(["--json", "openclaw-persistence-check", "--manifest", str(manifest_path)])

            self.assertEqual(exit_code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "inert-only")


if __name__ == "__main__":
    unittest.main()
