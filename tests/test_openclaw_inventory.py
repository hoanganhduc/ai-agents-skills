from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.ai_agents_skills.cli import main
from installer.ai_agents_skills.openclaw_inventory import build_inventory


CANARY_TEXT = "OPENCLAW_CANARY_VALUE_DO_NOT_LEAK"


def fake_root_path(tmp: str, name: str) -> Path:
    return Path(tmp).resolve() / name


def snapshot(root: Path) -> dict[str, tuple[str, bytes | str | None]]:
    state: dict[str, tuple[str, bytes | str | None]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            state[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            state[relative] = ("dir", None)
        elif path.is_file():
            state[relative] = ("file", path.read_bytes())
        else:
            state[relative] = ("other", None)
    return state


def make_source_root(root: Path) -> None:
    (root / "skills" / "source-research").mkdir(parents=True)
    (root / "skills" / "source-research" / "SKILL.md").write_text(
        f"name: source-research\ncanary: {CANARY_TEXT}\n",
        encoding="utf-8",
    )
    (root / "templates").mkdir()
    (root / "templates" / "report.md").write_text("template body\n", encoding="utf-8")
    (root / "aliases").mkdir()
    (root / "aliases" / "source.json").write_text('{"alias": "source"}\n', encoding="utf-8")
    (root / "hooks").mkdir()
    (root / "hooks" / "post-install.sh").write_text("echo should-not-run\n", encoding="utf-8")
    (root / "auth").mkdir()
    (root / "auth" / "token.txt").write_text(CANARY_TEXT, encoding="utf-8")
    (root / ".env").write_text(f"OPENCLAW_TEST_CANARY={CANARY_TEXT}\n", encoding="utf-8")


class OpenClawInventoryTests(unittest.TestCase):
    def test_inventory_is_read_only_sanitized_and_schema_shaped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_root_path(tmp, "fake-openclaw")
            root.mkdir()
            make_source_root(root)
            before = snapshot(root)

            inventory = build_inventory(root, evidence_class="fixture-only")

            self.assertEqual(snapshot(root), before)
            self.assertEqual(inventory["schema_version"], "openclaw.inventory.v1")
            self.assertEqual(inventory["source_root"]["explicit_input"], True)
            self.assertEqual(inventory["source_root"]["display_label"], "<FAKE_OPENCLAW_ROOT>")
            self.assertEqual(inventory["content_read_policy"], "deny-by-default")
            self.assertFalse(inventory["contains_raw_paths"])

            serialized = json.dumps(inventory, sort_keys=True)
            self.assertNotIn(str(root), serialized)
            self.assertNotIn(CANARY_TEXT, serialized)
            self.assertNotIn("OPENCLAW_TEST_CANARY", serialized)

            categories = {item["category"] for item in inventory["items"]}
            self.assertIn("skill-metadata", categories)
            self.assertIn("template-metadata", categories)
            self.assertIn("alias-metadata", categories)
            self.assertIn("hook-metadata-detected-only", categories)
            self.assertTrue(all(item["metadata"]["read_policy"] == "lstat-only" for item in inventory["items"]))
            self.assertTrue(all(item["relative_path_token"].startswith("<OPENCLAW_ROOT>/") for item in inventory["items"]))

            denied = {item["category_id"]: item["count"] for item in inventory["denied_categories"]}
            self.assertGreaterEqual(denied.get("private-category-denied", 0), 2)

    def test_absent_source_root_produces_sanitized_denial_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_root_path(tmp, "missing-openclaw")

            inventory = build_inventory(root)

            self.assertEqual(inventory["items"], [])
            self.assertEqual(
                inventory["denied_categories"],
                [
                    {
                        "category_id": "source-root-absent",
                        "reason_code": "source-root-absent",
                        "count": 1,
                        "read_policy": "lstat-only",
                    }
                ],
            )
            self.assertNotIn(str(root), json.dumps(inventory, sort_keys=True))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support is required")
    def test_symlink_source_root_is_denied_without_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            real_root = base / "real-openclaw"
            real_root.mkdir()
            (real_root / "skills").mkdir()
            link_root = base / "linked-openclaw"
            try:
                link_root.symlink_to(real_root, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")

            inventory = build_inventory(link_root)

            self.assertEqual(inventory["items"], [])
            denied = {item["category_id"] for item in inventory["denied_categories"]}
            self.assertIn("source-root-symlink-prefix-denied", denied)

    def test_cli_openclaw_inventory_ignores_hostile_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_root_path(tmp, "fake-openclaw")
            root.mkdir()
            make_source_root(root)
            output = io.StringIO()
            env = {
                "OPENCLAW_WORKSPACE": f"/tmp/{CANARY_TEXT}",
                "DEEPSEEK_CONFIG": f"/tmp/{CANARY_TEXT}",
                "MODEL_PROVIDER_OVERRIDE": CANARY_TEXT,
            }

            with patch.dict(os.environ, env, clear=False), contextlib.redirect_stdout(output):
                exit_code = main(["--json", "openclaw-inventory", "--source-root", str(root)])

            self.assertEqual(exit_code, 0)
            inventory = json.loads(output.getvalue())
            serialized = json.dumps(inventory, sort_keys=True)
            self.assertNotIn(CANARY_TEXT, serialized)
            self.assertNotIn("DEEPSEEK_CONFIG", serialized)
            self.assertNotIn(str(root), serialized)

    def test_inventory_max_entries_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_root_path(tmp, "fake-openclaw")
            (root / "skills").mkdir(parents=True)
            for index in range(5):
                (root / "skills" / f"skill-{index}.md").write_text("metadata\n", encoding="utf-8")

            inventory = build_inventory(root, max_entries=3)

            self.assertLessEqual(len(inventory["items"]), 3)
            denied = {item["category_id"] for item in inventory["denied_categories"]}
            self.assertIn("max-entries-exceeded", denied)


if __name__ == "__main__":
    unittest.main()
