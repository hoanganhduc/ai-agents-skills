"""The per-skill ``python`` block of ``manifest/runtime.yaml``.

A skill that imports third-party modules declares them in a ``python`` block:
the requirements files the provisioner installs into the shared venv, the
module names the skill imports (checked after provisioning), and whether the
skill is provisioned by default or only on request.  ``validate_runtime_python_block``
refuses a block whose requirements file is missing or escapes
``canonical/runtime``, whose module names are not importable names, or whose
provision value is unknown.  The manifest-level tests pin the eight blocks the
plan declares and check the three facts the validator cannot: every declared
module is really imported by the skill's sources, every declared module has a
requirements line that installs it, and every requirements file is enrolled in
the skill's ``files`` list so the installer ships it.

Every fixture is created under ``umask 077`` in a 0700 root, because the host
umask decides what ``mkdir`` and ``open`` would otherwise leave behind.
"""

from __future__ import annotations

import ast
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills.manifest import (
    REPO_ROOT,
    ManifestError,
    load_manifests,
    validate_runtime_python_block,
)

RUNTIME_SOURCE_ROOT = REPO_ROOT / "canonical" / "runtime"

# skill -> (modules exactly as the skill imports them, provision)
EXPECTED_PYTHON_BLOCKS = {
    "zotero": (["pyzotero", "requests", "PyPDF2", "pdfplumber", "googleapiclient", "google.oauth2"], "default"),
    "calibre": (["PyPDF2", "requests", "ebooklib", "googleapiclient", "google.oauth2"], "default"),
    "docling": (["docling", "pypdfium2"], "opt-in"),
    "research-digest-wrapper": (["feedparser", "requests"], "default"),
    "lean-explore-mcp": (["lean_explore"], "opt-in"),
    "modal-research-compute": (["modal"], "default"),
    "kaggle-research-compute": (["kagglehub"], "default"),
    "hetzner-research-compute": ([], "default"),
}

# Requirements files added for the venv; the other blocks reuse files that
# already existed and were already enrolled.
NEW_REQUIREMENTS_FILES = {
    "research-digest-wrapper": "skills/research-digest-wrapper/requirements.txt",
    "lean-explore-mcp": "skills/lean-explore-mcp/requirements.txt",
    "modal-research-compute": "workspace/research_compute/requirements-modal.txt",
    "kaggle-research-compute": "workspace/research_compute/requirements-kaggle.txt",
    "hetzner-research-compute": "skills/hetzner-research-compute/requirements.txt",
}

# import name -> distribution name where the two differ; identity otherwise.
MODULE_DISTRIBUTIONS = {
    "googleapiclient": "google-api-python-client",
    "google.oauth2": "google-auth",
    "lean_explore": "lean-explore",
    "PyPDF2": "PyPDF2",
}

# The compute skills import their modules from the shared research_compute
# package rather than from files enrolled under their own name.
COMPUTE_PACKAGE_SKILLS = {"modal-research-compute", "kaggle-research-compute", "hetzner-research-compute"}

ALL_PLATFORMS = ["linux", "macos", "windows", "wsl"]


def normalize_distribution(name: str) -> str:
    return name.lower().replace("_", "-")


def requirement_project_names(path: Path) -> set[str]:
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        names.add(normalize_distribution(re.split(r"[=<>!~;\[ ]", stripped, maxsplit=1)[0]))
    return names


def imported_names(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
    return names


def imports_module(names: set[str], module: str) -> bool:
    return any(name == module or name.startswith(module + ".") for name in names)


def python_block_skills(manifests: dict) -> dict:
    return {skill: spec for skill, spec in manifests["runtime"]["skills"].items() if "python" in spec}


class RuntimePythonBlockValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        previous = os.umask(0o077)
        self.addCleanup(os.umask, previous)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.tmp.chmod(0o700)
        self.root = self.tmp / "runtime"
        (self.root / "skills" / "example").mkdir(parents=True, mode=0o700)
        self.root.chmod(0o700)
        (self.root / "skills").chmod(0o700)
        self.requirements = self.root / "skills" / "example" / "requirements.txt"
        self.requirements.write_text("requests>=2.28.0\n", encoding="utf-8")

    def _block(self, **overrides: object) -> dict:
        block = {
            "requirements": ["skills/example/requirements.txt"],
            "modules": ["requests"],
            "provision": "default",
        }
        block.update(overrides)
        return block

    def _refuses(self, block: object, message: str) -> None:
        with self.assertRaisesRegex(ManifestError, message):
            validate_runtime_python_block("example", block, self.root)

    def test_a_well_formed_block_is_accepted(self) -> None:
        validate_runtime_python_block("example", self._block(), self.root)
        validate_runtime_python_block("example", self._block(provision="opt-in"), self.root)
        validate_runtime_python_block("example", self._block(modules=[]), self.root)
        validate_runtime_python_block("example", self._block(modules=["google.oauth2", "_x1.y_2"]), self.root)
        block = self._block()
        del block["provision"]
        validate_runtime_python_block("example", block, self.root)

    def test_a_non_mapping_block_is_refused(self) -> None:
        self._refuses("skills/example/requirements.txt", "runtime skill example python must be an object")
        self._refuses(["skills/example/requirements.txt"], "runtime skill example python must be an object")

    def test_a_missing_requirements_file_is_refused(self) -> None:
        self._refuses(
            self._block(requirements=["skills/example/missing.txt"]),
            r"runtime skill example python requirements file does not exist: skills/example/missing\.txt",
        )
        self._refuses(
            self._block(requirements=["skills/example"]),
            "runtime skill example python requirements file does not exist: skills/example",
        )

    def test_an_escaping_requirements_path_is_refused(self) -> None:
        outside = self.tmp / "outside.txt"
        outside.write_text("requests\n", encoding="utf-8")
        for requirement in (
            "../outside.txt",
            "skills/../../outside.txt",
            str(outside),
            "/skills/example/requirements.txt",
            "skills//example/requirements.txt",
            "skills/example/requirements.txt/",
        ):
            with self.subTest(requirement=requirement):
                self._refuses(
                    self._block(requirements=[requirement]),
                    "runtime skill example python requirements must be relative paths under canonical/runtime",
                )

    @unittest.skipIf(os.name == "nt", "symlink creation is privileged on Windows")
    def test_a_symlink_resolving_outside_the_runtime_root_is_refused(self) -> None:
        outside = self.tmp / "outside.txt"
        outside.write_text("requests\n", encoding="utf-8")
        (self.root / "skills" / "example" / "linked.txt").symlink_to(outside)
        self._refuses(
            self._block(requirements=["skills/example/linked.txt"]),
            "runtime skill example python requirements must stay under canonical/runtime",
        )

    def test_empty_requirements_are_refused(self) -> None:
        self._refuses(self._block(requirements=[]), "runtime skill example python requirements must be a non-empty list")
        self._refuses(
            self._block(requirements="skills/example/requirements.txt"),
            "runtime skill example python requirements must be a non-empty list",
        )
        block = self._block()
        del block["requirements"]
        self._refuses(block, "runtime skill example python requirements must be a non-empty list")
        self._refuses(
            self._block(requirements=[""]),
            "runtime skill example python requirements entries must be non-empty strings",
        )
        self._refuses(
            self._block(requirements=[None]),
            "runtime skill example python requirements entries must be non-empty strings",
        )

    def test_a_bad_module_name_is_refused(self) -> None:
        for module in ("google-auth", "1abc", "requests.", ".requests", "", "os.path\n", "a b", "x..y"):
            with self.subTest(module=module):
                self._refuses(
                    self._block(modules=[module]),
                    "runtime skill example python modules entry is not a module name",
                )
        self._refuses(self._block(modules=[42]), "runtime skill example python modules entry is not a module name")
        self._refuses(self._block(modules="requests"), "runtime skill example python modules must be a list")
        block = self._block()
        del block["modules"]
        self._refuses(block, "runtime skill example python modules must be a list")

    def test_a_bad_provision_is_refused(self) -> None:
        for provision in ("always", "Default", "", None, True, ["default"]):
            with self.subTest(provision=provision):
                self._refuses(
                    self._block(provision=provision),
                    "runtime skill example python provision must be default or opt-in",
                )

    def test_an_unknown_key_is_refused(self) -> None:
        self._refuses(self._block(extras=["pdf"]), "runtime skill example python has unknown keys: extras")
        self._refuses(
            self._block(venv="~/.agents_skills_venv", extras=["pdf"]),
            "runtime skill example python has unknown keys: extras, venv",
        )


class RuntimePythonBlockManifestTests(unittest.TestCase):
    def test_credential_runtime_manifest_lists_every_armed_command(self) -> None:
        source = (RUNTIME_SOURCE_ROOT / "runners/run_skill.sh").read_text(encoding="utf-8")
        armed = set()
        for match in re.finditer(r"^  (skills/[^\n]+)\)\n(.*?)(?=^    ;;)", source, re.MULTILINE | re.DOTALL):
            if "credential_contract=1" in match.group(2):
                armed.update(match.group(1).split("|"))
        manifest = json.loads((REPO_ROOT / "manifest/credential-runtime.json").read_text(encoding="utf-8"))
        declared = {command for consumer in manifest["consumers"] for command in consumer["commands"]}
        self.assertEqual(len(armed), 25)
        self.assertEqual(armed, declared)

    def test_every_declared_skill_has_the_planned_modules_and_provision(self) -> None:
        manifests = load_manifests()
        skills = python_block_skills(manifests)
        self.assertEqual(sorted(skills), sorted(EXPECTED_PYTHON_BLOCKS))
        for skill, (modules, provision) in EXPECTED_PYTHON_BLOCKS.items():
            with self.subTest(skill=skill):
                block = skills[skill]["python"]
                self.assertEqual(block["modules"], modules)
                self.assertEqual(block["provision"], provision)
                self.assertEqual(set(block), {"requirements", "modules", "provision"})
                self.assertTrue(block["requirements"], skill)
                keys = list(skills[skill])
                self.assertEqual(keys.index("python"), keys.index("smoke_coverage") + 1, keys)

    def test_runtime_python_modules_are_imported_by_the_skill(self) -> None:
        manifests = load_manifests()
        compute_sources = sorted((RUNTIME_SOURCE_ROOT / "workspace" / "research_compute").glob("*.py"))
        for skill, spec in python_block_skills(manifests).items():
            sources = [
                RUNTIME_SOURCE_ROOT / entry["source"]
                for entry in spec.get("files", [])
                if entry["source"].endswith(".py")
            ]
            if skill in COMPUTE_PACKAGE_SKILLS:
                sources.extend(compute_sources)
            self.assertTrue(sources, skill)
            names = set()
            for source in sources:
                names.update(imported_names(source))
            for module in spec["python"]["modules"]:
                with self.subTest(skill=skill, module=module):
                    self.assertTrue(
                        imports_module(names, module),
                        f"{skill} declares {module} but none of its sources import it",
                    )

    def test_declared_modules_have_a_requirements_line(self) -> None:
        manifests = load_manifests()
        for skill, spec in python_block_skills(manifests).items():
            block = spec["python"]
            projects = set()
            for requirement in block["requirements"]:
                projects.update(requirement_project_names(RUNTIME_SOURCE_ROOT / requirement))
            self.assertTrue(projects, skill)
            for module in block["modules"]:
                distribution = MODULE_DISTRIBUTIONS.get(module, module)
                with self.subTest(skill=skill, module=module):
                    self.assertIn(
                        normalize_distribution(distribution),
                        projects,
                        f"{skill} imports {module} but {block['requirements']} has no {distribution} line",
                    )

    def test_new_requirements_files_are_enrolled_in_runtime_files(self) -> None:
        manifests = load_manifests()
        skills = python_block_skills(manifests)
        for skill, requirement in NEW_REQUIREMENTS_FILES.items():
            with self.subTest(skill=skill):
                self.assertTrue((RUNTIME_SOURCE_ROOT / requirement).is_file(), requirement)
                self.assertEqual(skills[skill]["python"]["requirements"], [requirement])
        for skill, spec in skills.items():
            entries = {entry["source"]: entry for entry in spec.get("files", [])}
            for requirement in spec["python"]["requirements"]:
                with self.subTest(skill=skill, requirement=requirement):
                    self.assertIn(requirement, entries, f"{skill} does not enrol {requirement} in files")
                    target = requirement if requirement.startswith("workspace/") else f"workspace/{requirement}"
                    self.assertEqual(
                        entries[requirement],
                        {
                            "source": requirement,
                            "target": target,
                            "platforms": ALL_PLATFORMS,
                            "type": "text",
                            "newline": "lf",
                            "mode": "0644",
                        },
                    )


if __name__ == "__main__":
    unittest.main()
