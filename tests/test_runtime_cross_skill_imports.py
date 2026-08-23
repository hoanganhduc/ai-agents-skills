"""A skill that imports another skill's runtime package has to declare it.

`runtime.yaml` installs runtime files per selected skill, so a top-level import
that crosses a skill boundary only resolves when the *owning* skill happens to
be installed too.  `runtime_requires` exists to close that gap and pull the
owner in, but only `venue-ranking-evidence` used it.

`hetzner-research-compute` and `kaggle-research-compute` both import the shared
`research_compute` package, which is declared solely under
`modal-research-compute`.  Installed by name on their own, both applied
cleanly and then failed at launch with
`ModuleNotFoundError: No module named 'research_compute'`.  No profile pairs
them without modal, so only a by-name install reached it.

The check below is written against the manifest rather than against those two
skills, so a new skill that reaches across the same boundary fails here instead
of after someone installs it.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import PurePosixPath
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "installer"))

from ai_agents_skills.manifest import load_manifests  # noqa: E402

RUNTIME_SOURCE_ROOT = ROOT / "canonical" / "runtime"
WORKSPACE = "workspace/"


def _runtime_skills() -> dict:
    return load_manifests()["runtime"]["skills"]


def _module_owners(skills: dict) -> dict[str, str]:
    """Top-level importable name under `workspace/` -> the skill that ships it.

    Everything under `workspace/` sits on the launcher's import path, so these
    are exactly the names one skill can accidentally borrow from another.
    `workspace/skills/...` is excluded: those live behind a skill directory and
    are never importable by their neighbours.
    """

    owners: dict[str, str] = {}
    for name, spec in skills.items():
        for entry in spec.get("files") or []:
            target = entry["target"]
            if not target.startswith(WORKSPACE):
                continue
            parts = PurePosixPath(target[len(WORKSPACE):]).parts
            if not parts or parts[0] == "skills":
                continue
            if len(parts) > 1:
                owners.setdefault(parts[0], name)
            elif parts[0].endswith(".py"):
                owners.setdefault(parts[0][:-3], name)
    return owners


def _required_closure(skills: dict, skill: str) -> set[str]:
    seen: set[str] = set()
    pending = list(skills.get(skill, {}).get("runtime_requires") or [])
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(skills.get(current, {}).get("runtime_requires") or [])
    return seen


def _local_names(spec: dict) -> set[str]:
    """Names a skill can already import from among its own installed files."""

    names: set[str] = set()
    for entry in spec.get("files") or []:
        target = PurePosixPath(entry["target"])
        if target.suffix == ".py":
            names.add(target.stem)
        parts = target.parts
        if len(parts) > 3:            # workspace/skills/<skill>/<subpackage>/...
            names.add(parts[3])
    return names


def _cross_skill_imports() -> list[tuple[str, str, int, str, str]]:
    skills = _runtime_skills()
    owners = _module_owners(skills)
    found: list[tuple[str, str, int, str, str]] = []
    for name, spec in skills.items():
        local = _local_names(spec)
        for entry in spec.get("files") or []:
            if not entry["target"].endswith(".py"):
                continue
            source = RUNTIME_SOURCE_ROOT / entry["source"]
            if not source.is_file():
                continue
            tree = ast.parse(source.read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                imported: list[str] = []
                if isinstance(node, ast.Import):
                    imported = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    imported = [node.module.split(".")[0]]
                for module in imported:
                    if module in local:
                        continue
                    owner = owners.get(module)
                    if owner is None or owner == name:
                        continue
                    found.append((name, entry["source"], node.lineno, module, owner))
    return found


class CrossSkillImportsAreDeclaredTests(unittest.TestCase):
    def test_the_scan_finds_cross_skill_imports_at_all(self) -> None:
        # Non-vacuity anchor: every assertion below passes trivially if the
        # import walk stops finding anything, which is how the first version of
        # this check silently examined nothing.
        found = _cross_skill_imports()
        self.assertTrue(found, "found no cross-skill imports; the scan is not looking at anything")

    def test_workspace_packages_have_an_owning_skill(self) -> None:
        owners = _module_owners(_runtime_skills())
        self.assertIn("research_compute", owners)
        self.assertEqual(owners["research_compute"], "modal-research-compute")

    def test_every_cross_skill_import_is_covered_by_runtime_requires(self) -> None:
        skills = _runtime_skills()
        uncovered = [
            (skill, source, lineno, module, owner)
            for skill, source, lineno, module, owner in _cross_skill_imports()
            if owner not in _required_closure(skills, skill)
        ]
        self.assertEqual(
            uncovered,
            [],
            "these skills import a package another skill owns without declaring "
            "runtime_requires, so a by-name install of them cannot launch: "
            + "; ".join(f"{s} {src}:{n} imports {m} (owned by {o})" for s, src, n, m, o in uncovered),
        )

    def test_the_compute_lanes_declare_the_shared_broker(self) -> None:
        skills = _runtime_skills()
        for skill in ("hetzner-research-compute", "kaggle-research-compute"):
            with self.subTest(skill=skill):
                self.assertIn("modal-research-compute", _required_closure(skills, skill))


if __name__ == "__main__":
    unittest.main()
