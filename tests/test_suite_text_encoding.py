"""Guard the explicit text encoding every module needs on Windows.

``Path.read_text`` and ``open`` decode with the interpreter's locale encoding,
which is cp1252 on the Windows runner and UTF-8 everywhere else this repository
runs. The runtime writes its artifacts as UTF-8, so a read left on the default
turns every non-ASCII character into mojibake: a suite assertion that compares a
file against the runtime's own rendering fails on that platform alone, and a
runtime skill silently corrupts an author name, a feed title or a ledger row.
The locale default comes from the running interpreter and cannot be patched from
a POSIX host, so the call sites are checked in the source instead.

The encoding has to be named rather than passed positionally, because the
position differs between ``read_text``, ``write_text`` and ``open``, and one
spelling keeps the convention greppable.

Two kinds of ``open`` decode nothing and are excluded structurally rather than
by name. ``os.open`` takes integer flags and returns a descriptor; ``fitz``,
``Image`` and ``pdfplumber`` take a path and return a parsed binary document.
And a call carrying a keyword that ``open`` does not accept is some other
``open`` entirely, which is how urllib's opener -- it takes ``timeout`` -- stays
out of the count.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
PRODUCTION_ROOTS = ("canonical", "installer", "tools")

BINARY_OPENERS = frozenset({"os", "fitz", "Image", "pdfplumber"})
OPEN_KEYWORDS = frozenset(
    {"mode", "buffering", "encoding", "errors", "newline", "closefd", "opener"}
)


def _mode_argument(call: ast.Call, position: int) -> str:
    if len(call.args) > position and isinstance(call.args[position], ast.Constant):
        value = call.args[position].value
        if isinstance(value, str):
            return value
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    return "r"


def _decodes_text(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute):
        if func.attr in {"read_text", "write_text"}:
            return True
        if func.attr == "open":
            if isinstance(func.value, ast.Name) and func.value.id in BINARY_OPENERS:
                return False
            if any(
                keyword.arg is not None and keyword.arg not in OPEN_KEYWORDS
                for keyword in call.keywords
            ):
                return False
            return "b" not in _mode_argument(call, 0)
        return False
    if isinstance(func, ast.Name) and func.id == "open":
        return "b" not in _mode_argument(call, 1)
    return False


def _offenders(paths: list[Path]) -> list[str]:
    found: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _decodes_text(node):
                continue
            if any(keyword.arg == "encoding" for keyword in node.keywords):
                continue
            found.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    return found


class SuiteTextEncodingTests(unittest.TestCase):
    def test_suite_text_io_names_its_encoding(self) -> None:
        self.assertEqual(_offenders(sorted(TESTS_DIR.glob("*.py"))), [])


class RuntimeTextEncodingTests(unittest.TestCase):
    """Hold the shipped code to the rule the suite already follows.

    A skill that reads its own cache or config with the locale encoding is the
    same defect as a test that does, except nothing on a POSIX host reports it:
    the failure surfaces only on the user's Windows machine, in whatever the
    skill wrote last. Scan the trees that are installed onto a target.
    """

    def test_runtime_text_io_names_its_encoding(self) -> None:
        paths = [
            path
            for root in PRODUCTION_ROOTS
            for path in sorted((REPO_ROOT / root).rglob("*.py"))
            if "__pycache__" not in path.parts
        ]
        self.assertEqual(_offenders(paths), [])


if __name__ == "__main__":
    unittest.main()
