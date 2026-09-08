from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "canonical" / "runtime" / "skills"
STDLIB_ONLY_SKILLS = (
    "vnthuquan",
    "send-email",
    "remote-bridge",
    "submission-venue-selector",
    "lean-research-library",
    "axiom-axle-mcp",
    "autonomous-research-loop-runtime",
)


def _imports(path: Path) -> list[tuple[int, str]]:
    imports = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name.split(".")[0]) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append((node.lineno, node.module.split(".")[0]))
            elif node.level:
                imports.extend((node.lineno, alias.name) for alias in node.names)
    return imports


def _local_modules(root: Path) -> set[str]:
    names = set()
    for path in root.rglob("*.py"):
        names.add(path.stem)
        names.update(path.relative_to(root).parts[:-1])
    return names


def _delivery_modules() -> set[Path]:
    root = SKILLS / "zotero"
    pending = [root / "send_queue.py"]
    found = set()
    while pending:
        path = pending.pop()
        if path in found:
            continue
        found.add(path)
        for _, module in _imports(path):
            sibling = path.parent / f"{module}.py"
            package = path.parent / module
            if sibling.is_file():
                pending.append(sibling)
            if package.is_dir():
                pending.extend(package.rglob("*.py"))
    return found


class StdlibOnlySkillsTests(unittest.TestCase):
    def test_stdlib_only_skills_import_no_third_party_module(self) -> None:
        roots_and_paths = [
            (SKILLS / skill, set((SKILLS / skill).rglob("*.py")))
            for skill in STDLIB_ONLY_SKILLS
        ]
        roots_and_paths.append((SKILLS / "zotero", _delivery_modules()))
        failures = []
        for root, paths in roots_and_paths:
            local = _local_modules(root)
            for path in sorted(paths):
                for line_number, module in _imports(path):
                    if module not in sys.stdlib_module_names and module not in local:
                        failures.append(f"{path.relative_to(REPO)}:{line_number}: {module}")
        self.assertEqual(failures, [], "third-party imports in stdlib-only skills:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
