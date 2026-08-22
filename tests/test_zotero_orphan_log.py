"""Offline test for the Zotero orphan log's timestamp.

`zot._log_orphan` is the only writer of orphaned-keys.log, which `doctor` reads back
to report attachments that need cleanup.  What is pinned here is the stamp it writes.
"""

from __future__ import annotations

import datetime
import importlib.util
import sys
import tempfile
import types
import unittest
import warnings
from pathlib import Path

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
ZOTERO_DIR = REPO_ROOT / "canonical" / "runtime" / "skills" / "zotero"
_MISSING = object()


def _load_zot():
    """Import zot.py with pyzotero stubbed and the skill dir only briefly on path."""

    names = ("pyzotero", "pyzotero.zotero", "pyzotero.zotero_errors")
    previous = {name: sys.modules.get(name, _MISSING) for name in names}
    fake = types.ModuleType("pyzotero")
    fake_zotero = types.ModuleType("pyzotero.zotero")
    fake_errors = types.ModuleType("pyzotero.zotero_errors")
    fake_zotero.Zotero = object
    fake_errors.HTTPError = type("FakeHTTPError", (Exception,), {})
    fake.zotero = fake_zotero
    sys.modules.update(
        {
            "pyzotero": fake,
            "pyzotero.zotero": fake_zotero,
            "pyzotero.zotero_errors": fake_errors,
        }
    )
    sys.path.insert(0, str(ZOTERO_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "aas_zot_under_test", ZOTERO_DIR / "zot.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(ZOTERO_DIR))
        for name, module_or_missing in previous.items():
            if module_or_missing is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module_or_missing


zot = _load_zot()


class OrphanLogTimestampTests(unittest.TestCase):
    def _log_once(self) -> tuple[str, list[warnings.WarningMessage]]:
        with tempfile.TemporaryDirectory() as tmp:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                zot._log_orphan({"workspace": tmp}, "ABCD1234", "attachment is gone")
            log = Path(tmp) / "data" / "research" / "zotero" / "orphaned-keys.log"
            return log.read_text(encoding="utf-8").strip(), list(caught)

    def test_the_stamp_is_aware_utc_like_every_other_stamp_in_the_tree(self) -> None:
        """A naive `utcnow()` stamp cannot be told apart from a local-time one.

        Every other UTC timestamp in the runtime is `datetime.now(timezone.utc)`, and
        carries `+00:00`; this one wrote none.
        """

        line, _caught = self._log_once()
        stamp = line.split(" ", 1)[0]
        parsed = datetime.datetime.fromisoformat(stamp)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.utcoffset(), datetime.timedelta(0))

    def test_writing_a_line_raises_no_deprecation_warning(self) -> None:
        """`datetime.utcnow()` is deprecated on the interpreter this repo targets."""

        _line, caught = self._log_once()
        self.assertEqual(
            [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)],
            [],
        )

    def test_the_key_and_message_are_still_the_rest_of_the_line(self) -> None:
        """The doctor reads this file; the fields after the stamp keep their shape."""

        line, _caught = self._log_once()
        _stamp, key, message = line.split(" ", 2)
        self.assertEqual(key, "ABCD1234")
        self.assertEqual(message, "attachment is gone")


if __name__ == "__main__":
    unittest.main()
