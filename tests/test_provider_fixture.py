from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from tests._provider_fixture import (
    FIXTURE_PREFIX,
    STALE_AFTER_SECONDS,
    ProviderAttestationFixture,
    sweep_stale_fixtures,
)


class SweepStaleFixturesTests(unittest.TestCase):
    """The sweep deletes directories, so every guard it relies on is pinned here."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.parent = Path(self._temporary.name)

    def _aged(self, name: str, age_seconds: float) -> Path:
        path = self.parent / name
        path.mkdir()
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def test_removes_only_trees_past_the_stale_window(self) -> None:
        old = self._aged(f"{FIXTURE_PREFIX}old", STALE_AFTER_SECONDS + 60)
        fresh = self._aged(f"{FIXTURE_PREFIX}fresh", 60)
        removed = sweep_stale_fixtures(self.parent)
        self.assertEqual(removed, [old])
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_leaves_unrelated_names_alone(self) -> None:
        other = self._aged(".ssh", STALE_AFTER_SECONDS + 60)
        self.assertEqual(sweep_stale_fixtures(self.parent), [])
        self.assertTrue(other.exists())

    def test_ignores_symlinks_that_carry_the_prefix(self) -> None:
        target = self.parent / "target"
        target.mkdir()
        link = self.parent / f"{FIXTURE_PREFIX}link"
        link.symlink_to(target)
        stale_now = link.lstat().st_mtime + STALE_AFTER_SECONDS + 60
        self.assertEqual(sweep_stale_fixtures(self.parent, now=stale_now), [])
        self.assertTrue(target.exists())
        self.assertTrue(link.is_symlink())

    def test_ignores_plain_files(self) -> None:
        stray = self.parent / f"{FIXTURE_PREFIX}note"
        stray.write_text("", encoding="utf-8")
        os.utime(stray, (0, 0))
        self.assertEqual(sweep_stale_fixtures(self.parent), [])
        self.assertTrue(stray.exists())

    def test_missing_parent_is_not_an_error(self) -> None:
        self.assertEqual(sweep_stale_fixtures(self.parent / "absent"), [])


class ProviderAttestationFixtureTests(unittest.TestCase):
    def test_attests_every_requested_family_and_cleans_up(self) -> None:
        with ProviderAttestationFixture({"codex": "openai"}) as fixture:
            root = fixture.root
            self.assertTrue(root.name.startswith(FIXTURE_PREFIX))
            self.assertTrue(fixture.paths["codex"].exists())
            self.assertEqual(
                fixture.environment["AAS_AUTOLOOP_ATTESTED_UPSTREAM_CODEX"], "openai"
            )
            self.assertEqual(
                fixture.environment["AAS_AUTOLOOP_ATTESTED_BIN_CODEX"],
                str(fixture.paths["codex"]),
            )
        self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
