from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from installer.ai_agents_skills import runtime as rt
from installer.ai_agents_skills.apply import base_result


def _entry() -> dict:
    return {
        "source": "tool.py",
        "target": "workspace/skills/demo/tool.py",
        "type": "text",
        "newline": "lf",
        "mode": "0644",
        "platforms": ["linux", "macos", "wsl", "windows"],
    }


class RuntimeSourceIntegrityTest(unittest.TestCase):
    """P0: apply_runtime_file_action must verify the live source against the
    approved source_sha256 before writing (approve-A-apply-B / poisoned checkout)."""

    def _build(self, tmp: Path):
        src_root = tmp / "src"
        src_root.mkdir()
        (src_root / "tool.py").write_text("print('v1')\n", encoding="utf-8")
        root = tmp / "root"
        root.mkdir()
        # Runtime root lives under the install root in production (e.g. ~/.local/share/...).
        rroot = root / ".local" / "share" / "ai-agents-skills" / "runtime"
        rroot.mkdir(parents=True)
        with patch.object(rt, "RUNTIME_SOURCE_ROOT", src_root):
            action = rt.runtime_file_action(
                root=root,
                runtime_root=rroot,
                entry=_entry(),
                skill="demo",
                artifact_name="tool.py",
                backup_replace=False,
                seen_targets={},
            )
        return src_root, root, rroot, action

    def test_apply_writes_when_source_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src_root, root, rroot, action = self._build(Path(tmp))
            self.assertEqual(action["operation"], "create")
            self.assertTrue(action["source_sha256"])
            result = rt.apply_runtime_file_action(root, "run1", action, base_result("run1", action))
            self.assertTrue(result["applied"])
            written = rroot / "workspace" / "skills" / "demo" / "tool.py"
            self.assertTrue(written.exists())
            self.assertEqual(written.read_text(encoding="utf-8"), "print('v1')\n")

    def test_apply_refuses_when_source_changed_after_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src_root, root, rroot, action = self._build(Path(tmp))
            # Poison the source after the action (= approved manifest) was built.
            (src_root / "tool.py").write_text("print('PWNED')\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "runtime source content changed"):
                rt.apply_runtime_file_action(root, "run1", action, base_result("run1", action))
            written = rroot / "workspace" / "skills" / "demo" / "tool.py"
            self.assertFalse(written.exists())

    def test_helper_matches_expected_for_unchanged_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src_root, root, rroot, action = self._build(Path(tmp))
            live = rt.runtime_source_content_hash(Path(action["source_path"]), action)
            self.assertEqual(live, action["source_sha256"])


if __name__ == "__main__":
    unittest.main()
