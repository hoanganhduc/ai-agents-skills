"""Guard the link-free temp directory that runtime path checks require.

The autonomous-research-loop runtime rejects loop and artifact paths that cross a
symlink, and ``tests/__init__.py`` resolves the process-wide temp directory so
every test satisfies that check. Importing the package here makes the
normalization an explicit precondition of the suite instead of a side effect of
whichever collected module happens to import the package first.
"""

from __future__ import annotations

import os
import tempfile
import unittest

import tests  # noqa: F401  # imported for its temp-directory normalization


def _is_fully_resolved(path: str) -> bool:
    return os.path.normcase(path) == os.path.normcase(os.path.realpath(path))


class TempDirectoryNormalizationTests(unittest.TestCase):
    def test_default_temp_directory_is_fully_resolved(self) -> None:
        temp_dir = tempfile.gettempdir()
        self.assertTrue(
            _is_fully_resolved(temp_dir),
            f"default temp directory is not fully resolved: {temp_dir}",
        )

    def test_created_temp_directory_is_fully_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            self.assertTrue(
                _is_fully_resolved(raw),
                f"created temp directory is not fully resolved: {raw}",
            )
