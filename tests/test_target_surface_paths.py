"""The published surface table has to name directories the installer writes to.

``docs-check`` compares the generated documents against ``TARGET_SURFACES`` and
reports ``ok`` whenever they agree, which says nothing about whether the table
agrees with the installer.  It did not: the Antigravity migration moved the skill
and plugin trees to ``.gemini/config`` and the table went on publishing
``.gemini/antigravity-cli`` for both, so on a migrated home every path in it was
one no managed file was written to.  The checks here are what the docs gate
cannot be: they resolve the real directories against real homes in both layouts
and require the note to name them.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.agents import (
    antigravity_layout_paths,
    antigravity_plugin_root,
    antigravity_skills_dir,
    target_for,
)
from installer.ai_agents_skills.target_surfaces import TARGET_SURFACES


# Surfaces whose note names a directory, and which of the composed paths it is.
DOCUMENTED_DIRECTORIES = {
    "skill-file": "skills",
    "entrypoint-alias": "skills",
    "agent-persona": "plugin-package",
    "plugin": "plugin-package",
    "settings-file": "settings",
}
# Paths the migration left alone and that are not part of the composed layout.
# GEMINI.md is the Gemini global context file, shared with the Gemini CLI.
FIXED_PATHS = {"~/.gemini/GEMINI.md"}

HOME_PATH = re.compile(r"~/[\w./<>-]*[\w>]")


def antigravity_notes() -> dict[str, str]:
    return {
        surface.surface: surface.notes
        for surface in TARGET_SURFACES
        if surface.target == "antigravity"
    }


def home_paths(text: str) -> set[str]:
    return {match.rstrip(".") for match in HOME_PATH.findall(text)}


def unmigrated_home(root: Path) -> None:
    (root / ".gemini" / "antigravity-cli").mkdir(parents=True)


def migrated_home(root: Path) -> None:
    """Reproduce the layout the vendor's own migration leaves behind.

    ``skills`` becomes a compatibility symlink to the migrated tree while
    ``plugins`` is copied and both copies are left real -- the asymmetry the
    installer's own layout functions are written around.
    """
    config = root / ".gemini" / "config"
    (config / "plugins").mkdir(parents=True)
    (config / "skills").mkdir(parents=True)
    (config / ".migrated").touch()
    (root / ".gemini" / "antigravity-cli").mkdir(parents=True)
    (root / ".gemini" / "antigravity-cli" / "skills").symlink_to(config / "skills")


class AntigravitySurfacePathTests(unittest.TestCase):
    def test_no_note_names_a_path_the_layout_does_not_compose(self) -> None:
        """Every ``~`` path in an Antigravity note must be one of the real ones."""
        composed = set()
        for migrated in (False, True):
            for path in antigravity_layout_paths(Path("~"), migrated=migrated).values():
                composed.add(path.as_posix())
        allowed = composed | FIXED_PATHS
        for surface, notes in antigravity_notes().items():
            for path in home_paths(notes):
                # The notes name files inside a composed directory too, such as
                # ``<skill>.md``; those are checked by their directory.
                with self.subTest(surface=surface, path=path):
                    self.assertTrue(
                        any(path == entry or path.startswith(entry + "/") for entry in allowed),
                        f"{surface} documents {path}, which no Antigravity layout composes",
                    )

    def test_both_layouts_are_documented_for_every_directory_surface(self) -> None:
        """A note must cover the migrated home as well as the unmigrated one.

        Naming only one layout is how this broke: the pre-migration spelling was
        correct when it was written and silently became wrong for every migrated
        home.
        """
        notes = antigravity_notes()
        for surface, key in DOCUMENTED_DIRECTORIES.items():
            with self.subTest(surface=surface):
                self.assertIn(surface, notes)
                for migrated in (False, True):
                    path = antigravity_layout_paths(Path("~"), migrated=migrated)[key]
                    self.assertIn(path.as_posix(), notes[surface])

    def test_the_documented_directories_are_the_ones_a_real_home_resolves(self) -> None:
        """Tie the table to the functions an install actually calls.

        The composer is shared, so this is what would catch a layout function
        changing which of the two it selects without the note following.
        """
        for label, prepare in (("unmigrated", unmigrated_home), ("migrated", migrated_home)):
            with self.subTest(home=label):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    prepare(root)
                    notes = antigravity_notes()
                    resolved = {
                        "skills": antigravity_skills_dir(root),
                        "plugin-package": target_for(root, "antigravity").target_dir_for("plugin"),
                        "settings": root / ".gemini" / "antigravity-cli" / "settings.json",
                    }
                    self.assertEqual(
                        antigravity_plugin_root(root), resolved["plugin-package"].parent
                    )
                    for surface, key in DOCUMENTED_DIRECTORIES.items():
                        documented = "~/" + resolved[key].relative_to(root).as_posix()
                        self.assertIn(
                            documented,
                            notes[surface],
                            f"{label} home writes {key} to {documented}, "
                            f"which the {surface} note does not name",
                        )


if __name__ == "__main__":
    unittest.main()
