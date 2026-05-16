from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


def load_import_tool():
    path = Path(__file__).resolve().parents[1] / "tools" / "import_canonical_skills.py"
    spec = importlib.util.spec_from_file_location("import_canonical_skills_tool", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load import_canonical_skills.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ImportCanonicalSkillsTests(unittest.TestCase):
    def test_write_skill_rejects_dest_outside_repo_without_explicit_override(self) -> None:
        tool = load_import_tool()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source" / "demo"
            source.mkdir(parents=True)
            skill_file = source / "SKILL.md"
            skill_file.write_text("---\nname: demo\n---\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "destination outside canonical skills root"):
                tool.write_skill(root / "dest", "demo", source, [skill_file])

    def test_selected_files_rejects_symlinked_source_files(self) -> None:
        tool = load_import_tool()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            outside = root / "outside.md"
            outside.write_text("secret", encoding="utf-8")
            link = source / "SKILL.md"
            try:
                link.symlink_to(outside)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"file symlink unavailable: {exc}")

            with self.assertRaisesRegex(ValueError, "symlinked source file"):
                tool.selected_files(source)


if __name__ == "__main__":
    unittest.main()
