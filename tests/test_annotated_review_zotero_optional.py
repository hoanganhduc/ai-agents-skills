"""Regressions for annotated-review's optional dependency on the zotero skill.

``zotero_note`` borrows ``load_config`` and ``ZoteroClient`` from the zotero
skill by bare name, relying on a ``sys.path`` entry pointing at its sibling
directory::

    _ZOTERO_SKILL_DIR = os.path.join(_SKILLS_DIR, "zotero")

Nothing installs that sibling for it. ``manifest/runtime.yaml`` gives
annotated-review no ``runtime_requires``, and no profile pairs the two, so
``install --skills annotated-review`` produces a tree with no ``zotero``
directory at all. The imports sat inside the three functions that need them, so
the skill installed and imported clean and only failed once a review actually
tried to write its note -- with ``ModuleNotFoundError: No module named 'lib'``,
which names neither the skill that is missing nor the directory searched for it.

The fix must stay narrow. ``lib.zotero_client`` imports ``pyzotero``, so a
tree that *has* the zotero skill but lacks that package raises ``ImportError``
too; blaming the skill there sends the reader after something already
installed. Only a missing ``lib`` is the sibling skill's absence.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path

from tests.test_never_matching_predicates import RUNTIME_SKILLS, load_module


SKILL_DIR = RUNTIME_SKILLS / "annotated-review"
ZOTERO_DIR = RUNTIME_SKILLS / "zotero"

# Modules the import under test pulls in, or that shadow it. Left in
# sys.modules they leak a `lib` package into every later test in the run.
_VOLATILE = ("lib", "lib.config", "lib.zotero_client", "pyzotero")


@contextmanager
def _isolated(stub_pyzotero: bool):
    """Run with sys.path and sys.modules restored afterwards.

    ``zotero_note`` inserts the zotero skill directory into ``sys.path`` at
    import time and never removes it, so the caller has to. The canonical
    zotero directory is stripped on entry as well: importing ``zot.py`` leaks
    the same entry, and any earlier test in the run that did so would otherwise
    satisfy ``from lib.config import ...`` here and quietly turn the
    missing-sibling cases into no-ops.
    """

    saved_path = list(sys.path)
    sys.path[:] = [entry for entry in sys.path if entry != str(ZOTERO_DIR)]
    saved_modules = {
        name: mod
        for name, mod in sys.modules.items()
        if name in _VOLATILE or name.startswith("pyzotero.")
    }
    for name in list(saved_modules):
        del sys.modules[name]
    if stub_pyzotero:
        for name, attrs in _pyzotero_stub().items():
            module = types.ModuleType(name)
            for attr, value in attrs.items():
                setattr(module, attr, value)
            sys.modules[name] = module
        sys.modules["pyzotero"].zotero = sys.modules["pyzotero.zotero"]
    try:
        yield
    finally:
        sys.path[:] = saved_path
        for name in list(sys.modules):
            if name in _VOLATILE or name.startswith("pyzotero."):
                del sys.modules[name]
        sys.modules.update(saved_modules)


def _pyzotero_stub() -> dict:
    """The pyzotero surface lib/zotero_client.py imports at module level."""

    class Zotero:
        def __init__(self, *args, **kwargs):
            pass

    class HTTPError(Exception):
        pass

    return {
        "pyzotero": {},
        "pyzotero.zotero": {"Zotero": Zotero},
        "pyzotero.zotero_errors": {"HTTPError": HTTPError},
    }


def _tree_without_zotero(tmp: Path) -> Path:
    """A skills tree holding annotated-review and no sibling zotero skill.

    Mirrors what ``install --skills annotated-review`` materialises.
    """

    skills = tmp / "skills"
    shutil.copytree(SKILL_DIR, skills / "annotated-review")
    assert not (skills / "zotero").exists()
    return skills / "annotated-review"


class ZoteroSkillPresentTests(unittest.TestCase):
    """Anchors the suite: the helper really does resolve when it can."""

    def test_the_helper_returns_the_real_zotero_objects(self):
        with _isolated(stub_pyzotero=True):
            module = load_module(
                "zotero_note_present", SKILL_DIR / "zotero_note.py", extra_syspath=SKILL_DIR
            )
            self.assertTrue(ZOTERO_DIR.is_dir(), "canonical tree ships the zotero skill")
            load_config, client = module._zotero_lib()

        self.assertEqual(load_config.__module__, "lib.config")
        self.assertEqual(load_config.__name__, "load_config")
        self.assertEqual(client.__module__, "lib.zotero_client")
        self.assertEqual(client.__name__, "ZoteroClient")


class ZoteroSkillMissingTests(unittest.TestCase):
    def test_the_helper_names_the_missing_skill(self):
        with tempfile.TemporaryDirectory() as raw:
            skill = _tree_without_zotero(Path(raw))
            with _isolated(stub_pyzotero=True):
                module = load_module(
                    "zotero_note_absent", skill / "zotero_note.py", extra_syspath=skill
                )
                with self.assertRaises(RuntimeError) as caught:
                    module._zotero_lib()
                searched = module._ZOTERO_SKILL_DIR

        message = str(caught.exception)
        self.assertIn("zotero skill", message)
        self.assertIn(searched, message)
        self.assertIsInstance(caught.exception.__cause__, ImportError)

    def test_every_entry_point_reports_it(self):
        """All three functions reach the helper before touching their arguments."""

        with tempfile.TemporaryDirectory() as raw:
            skill = _tree_without_zotero(Path(raw))
            with _isolated(stub_pyzotero=True):
                module = load_module(
                    "zotero_note_entry", skill / "zotero_note.py", extra_syspath=skill
                )
                calls = {
                    "create_zotero_note": lambda: module.create_zotero_note(
                        "ABCD1234", "<p>note</p>", "2026-01-01", "config.json"
                    ),
                    "get_existing_review_notes": lambda: module.get_existing_review_notes(
                        "ABCD1234", "config.json"
                    ),
                    "tag_parent_item": lambda: module.tag_parent_item(
                        "ABCD1234", "config.json"
                    ),
                }
                for name, call in calls.items():
                    with self.subTest(entry_point=name):
                        with self.assertRaises(RuntimeError) as caught:
                            call()
                        self.assertNotIsInstance(caught.exception, ImportError)
                        self.assertIn("zotero skill", str(caught.exception))


class SharedLoaderIsolationTests(unittest.TestCase):
    """The missing-sibling premise only holds if no loader leaks the path.

    ``load_zot_module`` executes ``zot.py``, which inserts its own skill
    directory into ``sys.path``. When that entry survived the call, every later
    test in the run could import ``lib.config`` from the canonical tree, and the
    cases above stopped testing anything while still reporting success.
    """

    def test_loading_the_zotero_cli_leaves_sys_path_unchanged(self):
        from tests.test_zotero_webdav_metadata import load_zot_module

        before = list(sys.path)
        try:
            load_zot_module()
        finally:
            leaked = [entry for entry in sys.path if entry not in before]
            sys.path[:] = before

        self.assertEqual(leaked, [], "a loader must not leak sys.path entries")

    def test_the_leak_would_have_been_visible(self):
        """Anchor: the canonical zotero directory really is what gets added."""

        self.assertTrue((ZOTERO_DIR / "zot.py").is_file())
        self.assertIn(
            f'sys.path.insert(0, "{ZOTERO_DIR}")'.split("(", 1)[0],
            (ZOTERO_DIR / "zot.py").read_text(encoding="utf-8"),
            "zot.py no longer mutates sys.path; this guard needs rewriting",
        )


class UnrelatedImportFailureTests(unittest.TestCase):
    def test_a_missing_package_is_not_blamed_on_the_skill(self):
        """pyzotero absent is the package's absence, not the skill's."""

        with _isolated(stub_pyzotero=False):
            module = load_module(
                "zotero_note_nopyz", SKILL_DIR / "zotero_note.py", extra_syspath=SKILL_DIR
            )
            self.assertTrue(ZOTERO_DIR.is_dir(), "the skill it would blame is present")
            self.assertIsNone(
                sys.modules.get("pyzotero"), "pyzotero must be absent for this to mean anything"
            )
            with self.assertRaises(ImportError) as caught:
                module._zotero_lib()

        self.assertEqual(caught.exception.name, "pyzotero")
        self.assertNotIn("zotero skill", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
