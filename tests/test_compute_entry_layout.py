"""Offline tests for the compute-lane entry scripts' layout and workspace logic."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "canonical" / "runtime" / "skills"
ENTRY_SCRIPTS = {
    "kaggle": SKILLS_DIR / "kaggle-research-compute" / "kaggle_research_compute.py",
    "modal": SKILLS_DIR / "modal-research-compute" / "modal_research_compute.py",
    "hetzner": SKILLS_DIR / "hetzner-research-compute" / "hetzner_research_compute.py",
}
WORKSPACE_KEYS = ("CODEX_RUNTIME_WORKSPACE", "OPENCLAW_WORKSPACE")
PIN_KEY = "AAS_AUTOLOOP_COMPUTE_WORKSPACE"


def _load_entry(lane: str):
    path = ENTRY_SCRIPTS[lane]
    spec = importlib.util.spec_from_file_location(f"entry_{lane}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CodeWorkspaceRootTests(unittest.TestCase):
    def test_installed_layout_uses_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "research_compute").mkdir()
            skill_dir = root / "skills" / "kaggle-research-compute"
            skill_dir.mkdir(parents=True)
            for lane in ENTRY_SCRIPTS:
                module = _load_entry(lane)
                self.assertEqual(module._code_workspace_root(skill_dir), root)

    def test_source_layout_descends_into_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "workspace" / "research_compute").mkdir(parents=True)
            skill_dir = root / "skills" / "kaggle-research-compute"
            skill_dir.mkdir(parents=True)
            for lane in ENTRY_SCRIPTS:
                module = _load_entry(lane)
                self.assertEqual(
                    module._code_workspace_root(skill_dir), root / "workspace"
                )

    def test_unrecognized_layout_keeps_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill_dir = root / "skills" / "kaggle-research-compute"
            skill_dir.mkdir(parents=True)
            module = _load_entry("kaggle")
            self.assertEqual(module._code_workspace_root(skill_dir), root)


class NormalizeDataWorkspaceEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            key: os.environ.get(key) for key in (*WORKSPACE_KEYS, PIN_KEY)
        }
        for key in (*WORKSPACE_KEYS, PIN_KEY):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_overrides_without_config_are_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            os.environ["OPENCLAW_WORKSPACE"] = temporary
            module = _load_entry("modal")
            module._normalize_data_workspace_env()
            self.assertNotIn("OPENCLAW_WORKSPACE", os.environ)

    def test_overrides_with_config_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config").mkdir()
            (root / "config" / "research-compute.toml").write_text(
                "", encoding="utf-8"
            )
            os.environ["OPENCLAW_WORKSPACE"] = temporary
            module = _load_entry("modal")
            module._normalize_data_workspace_env()
            self.assertEqual(os.environ["OPENCLAW_WORKSPACE"], temporary)

    def test_valid_pin_overrides_both_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config").mkdir()
            (root / "config" / "research-compute.toml").write_text(
                "", encoding="utf-8"
            )
            os.environ[PIN_KEY] = temporary
            os.environ["OPENCLAW_WORKSPACE"] = "/nonexistent-workspace"
            module = _load_entry("kaggle")
            module._normalize_data_workspace_env()
            resolved = str(root.resolve())
            for key in WORKSPACE_KEYS:
                self.assertEqual(os.environ[key], resolved)

    def test_invalid_pin_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            os.environ[PIN_KEY] = temporary
            module = _load_entry("hetzner")
            with self.assertRaises(SystemExit) as caught:
                module._normalize_data_workspace_env()
            self.assertEqual(caught.exception.code, 2)

    def test_relative_pin_fails_closed(self) -> None:
        os.environ[PIN_KEY] = "relative/workspace"
        module = _load_entry("hetzner")
        with self.assertRaises(SystemExit) as caught:
            module._normalize_data_workspace_env()
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
