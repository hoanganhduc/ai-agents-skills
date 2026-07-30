"""Guard the explicit text encoding every suite module needs on Windows.

``Path.read_text`` and ``open`` decode with the interpreter's locale encoding,
which is cp1252 on the Windows runner and UTF-8 everywhere else the suite runs.
The runtime writes its artifacts as UTF-8, so a read left on the default turns
every non-ASCII character into mojibake and any assertion that compares the file
against the runtime's own rendering fails on that platform alone. The locale
default comes from the running interpreter and cannot be patched from a POSIX
host, so the call sites are checked in the source instead.

The encoding has to be named rather than passed positionally, because the
position differs between ``read_text``, ``write_text`` and ``open``, and one
spelling keeps the convention greppable. ``os.open`` is excluded because it
takes integer flags and returns a descriptor rather than decoding anything.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent


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
            if isinstance(func.value, ast.Name) and func.value.id == "os":
                return False
            return "b" not in _mode_argument(call, 0)
        return False
    if isinstance(func, ast.Name) and func.id == "open":
        return "b" not in _mode_argument(call, 1)
    return False


class SuiteTextEncodingTests(unittest.TestCase):
    def test_suite_text_io_names_its_encoding(self) -> None:
        offenders: list[str] = []
        for path in sorted(TESTS_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not _decodes_text(node):
                    continue
                if any(keyword.arg == "encoding" for keyword in node.keywords):
                    continue
                offenders.append(f"{path.name}:{node.lineno}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
