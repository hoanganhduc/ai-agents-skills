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
import copy
import os
import re
import tempfile
import unittest
from pathlib import Path

from installer.ai_agents_skills import manifest as runtime_manifest
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


class RuntimeSmokeContractValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.source = self.root / "fixture.txt"
        self.source.write_text("fixture content", encoding="utf-8")
        self.source.chmod(0o600)

    @staticmethod
    def _contract(kind: str = "smoke", **overrides: object) -> dict:
        contract = {
            "schema": {
                "smoke": "runtime-smoke.v1",
                "functional_smoke": "runtime-functional-smoke.v1",
                "live_check": "runtime-live-check.v1",
            }[kind],
            "command": "workspace/skills/example/run_example.sh",
            "args": ["doctor"],
            "timeout_seconds": 30,
            "expect": {"exit_code": 0},
        }
        if kind == "smoke":
            contract.update({
                "mode": "offline", "writes": [],
                "safety": {key: "forbidden" for key in (
                    "network", "live_api", "package_install", "server_start", "config_write", "real_secrets",
                )},
            })
        elif kind == "functional_smoke":
            contract.update({"requires_python_modules": [], "safety": {"network": "forbidden"}})
        else:
            contract.update({"read_only": True, "requires": {"network": True}})
        contract.update(overrides)
        return contract

    def _validate(self, contract: object, kind: str = "smoke", *, skill: str = "example") -> None:
        validator = getattr(runtime_manifest, {
            "smoke": "validate_runtime_smoke_contract",
            "functional_smoke": "validate_runtime_functional_smoke_contract",
            "live_check": "validate_runtime_live_check_contract",
        }[kind])
        validator(skill, contract, self.root)

    def _refuses(self, contract: object, kind: str = "smoke", message: str = ".") -> None:
        with self.assertRaisesRegex(ManifestError, message):
            self._validate(contract, kind)

    def test_each_contract_kind_accepts_its_declared_shape(self) -> None:
        for kind in ("smoke", "functional_smoke", "live_check"):
            with self.subTest(kind=kind):
                self._validate(self._contract(kind), kind)
        smoke = self._contract()
        smoke["safety"].update({"provider_cli": "forbidden", "subagent_spawn": "forbidden"})
        smoke["env_canaries"] = {"AAS_EXAMPLE_TOKEN": "synthetic-canary"}
        smoke["secret_file_canaries"] = {
            "pointer_env": "AAS_SKILL_SECRETS_FILE", "format": "env", "values": {"AXLE_API_KEY": "synthetic-canary"},
        }
        self._validate(smoke)
        self._validate(self._contract("functional_smoke", safety={"network": "loopback"}), "functional_smoke")

    def test_smoke_rejects_an_unknown_top_level_key(self) -> None:
        # Keep the existing two-argument API in this regression: with manifest.py
        # reverted, the old validator silently accepts this undeclared key.
        with self.assertRaisesRegex(ManifestError, "unknown keys"):
            runtime_manifest.validate_runtime_smoke_contract("example", self._contract(undeclared=True))

    def test_every_kind_has_a_closed_top_level_key_set(self) -> None:
        for kind in ("smoke", "functional_smoke", "live_check"):
            with self.subTest(kind=kind):
                self._refuses(self._contract(kind, undeclared=True), kind, "unknown keys")
                for invalid in (None, [], "contract"):
                    self._refuses(invalid, kind, "must be an object")
        for field in ("env", "fixtures", "safety", "writes", "env_canaries"):
            self._refuses(self._contract("live_check", **{field: {}}), "live_check", "unknown keys")
        for field in ("mode", "writes", "env_canaries", "secret_file_canaries"):
            self._refuses(self._contract("functional_smoke", **{field: {}}), "functional_smoke", "unknown keys")

    def test_every_kind_requires_expect_and_its_own_schema(self) -> None:
        for kind in ("smoke", "functional_smoke", "live_check"):
            with self.subTest(kind=kind):
                contract = self._contract(kind)
                del contract["expect"]
                self._refuses(contract, kind, "requires expect")
                self._refuses(self._contract(kind, expect=None), kind, "must be an object")
                self._refuses(self._contract(kind, schema="another-schema"), kind, "schema")

    def test_commands_remain_under_workspace(self) -> None:
        self._validate(self._contract(command={"linux": "workspace/skills/example/run.sh", "windows_ps1": "workspace/skills/example/run.ps1"}))
        for command in (None, {}, [], "", "skills/example/run.sh", "workspace/../run.sh", "workspace/C:/run.sh", "workspace/\\run.sh", "workspace/", {"linux": 1}, {1: "workspace/skills/example/run.sh"}):
            with self.subTest(command=command):
                self._refuses(self._contract(command=command), message="command")

    def test_args_are_strings_and_timeout_limits_depend_on_kind(self) -> None:
        for kind, maximum in (("smoke", 120), ("functional_smoke", 300), ("live_check", 600)):
            for timeout in (1, maximum):
                self._validate(self._contract(kind, timeout_seconds=timeout), kind)
            for timeout in (None, 0, -1, True, 1.5, maximum + 1):
                with self.subTest(kind=kind, timeout=timeout):
                    self._refuses(self._contract(kind, timeout_seconds=timeout), kind, "timeout_seconds")
            for args in ("doctor", None, [False], [1], [None]):
                self._refuses(self._contract(kind, args=args), kind, "args")

    def test_smoke_safety_keeps_six_required_and_two_optional_forbidden_keys(self) -> None:
        contract = self._contract()
        for field in contract["safety"]:
            invalid = copy.deepcopy(contract)
            del invalid["safety"][field]
            self._refuses(invalid, message="safety")
        for field in (*contract["safety"], "provider_cli", "subagent_spawn"):
            invalid = copy.deepcopy(contract)
            invalid["safety"][field] = "allowed"
            self._refuses(invalid, message="safety")
        self._refuses(self._contract(safety={**contract["safety"], "unknown": "forbidden"}), message="unknown keys")
        self._refuses(self._contract(mode="live"), message="mode")
        self._refuses(self._contract("functional_smoke", safety={"network": "allowed"}), "functional_smoke", "network")
        self._refuses(self._contract("functional_smoke", safety={"network": "forbidden", "live_api": "forbidden"}), "functional_smoke", "unknown keys")

    def test_writes_are_only_relative_scratch_paths(self) -> None:
        self._validate(self._contract(writes=["output.json", "{smoke_dir}/nested/report.md"]))
        for writes in ({"workspace_scratch": True}, None, "output", ["../outside"], ["/outside"], ["C:/outside"], ["a\\outside"], ["{workspace}/data"], [False]):
            self._refuses(self._contract(writes=writes), message="writes")

    def test_env_rejects_reserved_and_secret_shaped_names(self) -> None:
        self._validate(self._contract(env={"AAS_AUTOLOOP_COMPUTE_WORKSPACE": "{smoke_dir}/compute", "EXAMPLE": "{workspace}/file"}))
        reserved = (
            "HOME", "PATH", "AAS_RUNTIME_ROOT", "AAS_RUNTIME_WORKSPACE", "AAS_ALLOW_EXTERNAL_RUNTIME_WORKSPACE",
            "AAS_SKILL_VENV", "AAS_RUNTIME_PYTHON_PREFIX", "OPENCLAW_WORKSPACE", "AAS_RUNTIME_PYTHON",
            "DOCLING_PYTHON", "PYTHONPATH", "VIRTUAL_ENV",
        )
        secret_names = ("EXAMPLE_TOKEN", "EXAMPLE_SECRET", "EXAMPLE_PASSWORD", "EXAMPLE_CREDENTIAL", "EXAMPLE_API_KEY", "EXAMPLE_AUTH")
        for key in (*reserved, *secret_names):
            with self.subTest(key=key):
                self._refuses(self._contract(env={key: "value"}), message="reserved or secret-shaped")
        for env in ([], {"lowercase": "value"}, {"BAD-NAME": "value"}, {"NAME": 1}, {1: "value"}):
            self._refuses(self._contract(env=env), message="env")
        self._refuses(self._contract(env_canaries={"HOME": "synthetic-canary"}), message="reserved")

    def test_canary_shapes_preserve_the_exact_authority_channel(self) -> None:
        valid = {"pointer_env": "AAS_SKILL_SECRETS_FILE", "format": "env", "values": {"AXLE_API_KEY": "synthetic-canary"}}
        for canary in ([], {**valid, "unknown": 1}, {**valid, "pointer_env": "OPENAI_API_KEY"}, {**valid, "pointer_env": []}, {**valid, "format": "yaml"}, {**valid, "values": {}}, {**valid, "values": {"AXLE_API_KEY": 1}}):
            self._refuses(self._contract(secret_file_canaries=canary), message="secret_file_canaries")

    def test_fixture_content_and_runtime_source_copy_are_accepted(self) -> None:
        fixtures = [
            {"to": "{smoke_dir}/data/request.json", "content": '{"workspace": "{workspace}"}'},
            {"to": "data/copied.txt", "copy_from": "fixture.txt"},
        ]
        self._validate(self._contract(fixtures=fixtures))
        self._validate(self._contract("functional_smoke", fixtures=fixtures), "functional_smoke")

    def test_fixture_shapes_and_paths_are_closed(self) -> None:
        for fixture in ({}, {"to": "output"}, {"to": "output", "content": "", "copy_from": "fixture.txt"}, {"to": "output", "content": 1}, {"to": "output", "copy_from": "missing.txt"}, {"to": "output", "copy_from": "../fixture.txt"}, {"to": "output", "copy_from": str(self.source)}, {"to": "../outside", "content": ""}, {"to": "/outside", "content": ""}, {"to": "{workspace}/data", "content": ""}, {"to": "output", "content": "", "unknown": True}):
            with self.subTest(fixture=fixture):
                self._refuses(self._contract(fixtures=[fixture]), message="fixtures")
        self._refuses(self._contract(fixtures={}), message="fixtures")

    @unittest.skipIf(os.name == "nt", "symlink creation is privileged on Windows")
    def test_copy_from_symlink_cannot_escape_runtime_source(self) -> None:
        with tempfile.TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir) / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            (self.root / "linked.txt").symlink_to(outside)
            self._refuses(self._contract(fixtures=[{"to": "output", "copy_from": "linked.txt"}]), message="under canonical/runtime")

    def test_functional_modules_use_python_module_name_grammar(self) -> None:
        self._validate(self._contract("functional_smoke", requires_python_modules=["requests", "google.oauth2", "_private.module_1"]), "functional_smoke")
        for modules in (None, "requests", ["google-auth"], ["1module"], ["requests."], [".requests"], [False]):
            self._refuses(self._contract("functional_smoke", requires_python_modules=modules), "functional_smoke", "requires_python_modules")

    def test_live_requirements_are_closed_and_read_only_is_literal(self) -> None:
        pointers = ["AAS_SKILL_SECRETS_FILE", "AAS_COMPUTE_SECRETS_FILE", "AAS_PROVIDER_SECRETS_FILE", "AAS_ZOTERO_SECRETS_FILE", "AAS_CALIBRE_SECRETS_FILE", "AAS_FILE_DELIVERY_SECRETS_FILE", "SEND_EMAIL_SECRETS_FILE", "REMOTE_BRIDGE_SECRETS_FILE"]
        self._validate(self._contract("live_check", requires={"network": True, "pointer_env": pointers, "config_files": ["~/.config/example.json"]}), "live_check")
        for literal in (False, 1, "true", None):
            self._refuses(self._contract("live_check", read_only=literal), "live_check", "read_only")
        for requires in (None, {"network": 1}, {"network": False}, {"network": True, "unknown": True}, {"network": True, "pointer_env": ["OPENAI_API_KEY"]}, {"network": True, "pointer_env": "AAS_SKILL_SECRETS_FILE"}, {"network": True, "pointer_env": [[]]}, {"network": True, "config_files": [""]}, {"network": True, "config_files": "~/.config/example.json"}):
            self._refuses(self._contract("live_check", requires=requires), "live_check", "requires")

    def test_live_checks_refuse_each_per_skill_mutating_verb(self) -> None:
        verbs = {
            "zotero": ("add", "update", "trash", "delete", "send", "purge"),
            "calibre": ("add", "update", "add-tag", "remove-tag", "remove", "sync", "clean", "convert"),
            "docling": ("ocrspace-smoke",), "kaggle-research-compute": ("push", "run", "fetch"),
            "hetzner-research-compute": ("up", "down", "kill", "reap"),
            "modal-research-compute": ("submit", "run", "cancel", "deploy"),
            "send-email": ("send",), "remote-bridge": ("send", "approve"),
            "submission-venue-selector": ("run", "select"), "vnthuquan": ("download",),
        }
        for skill, blocked in verbs.items():
            for verb in blocked:
                with self.subTest(skill=skill, verb=verb), self.assertRaisesRegex(ManifestError, "mutating verb"):
                    self._validate(self._contract("live_check", args=[verb]), "live_check", skill=skill)
        self._validate(self._contract("live_check", args=["reap", "--dry-run"]), "live_check", skill="hetzner-research-compute")
        with self.assertRaisesRegex(ManifestError, "mutating verb"):
            self._validate(self._contract("live_check", args=["kill", "--dry-run"]), "live_check", skill="hetzner-research-compute")

    def test_case_collections_require_named_contracts_and_preserve_order(self) -> None:
        for kind in ("functional_smoke", "live_check"):
            cases = {"second": self._contract(kind), "first": self._contract(kind)}
            runtime_manifest.validate_runtime_case_collection("example", cases, kind, self.root)
            self.assertEqual(list(cases), ["second", "first"])
            for invalid in ({}, [], {"": self._contract(kind)}, {1: self._contract(kind)}, {"case": {}}):
                with self.assertRaises(ManifestError):
                    runtime_manifest.validate_runtime_case_collection("example", invalid, kind, self.root)


class RuntimeExpectValidationTests(unittest.TestCase):
    def _refuses(self, expect: object, message: str = ".") -> None:
        with self.assertRaisesRegex(ManifestError, message):
            runtime_manifest.validate_runtime_expect(expect)

    def test_every_json_operator_accepts_its_declared_value(self) -> None:
        for operator, value in (
            ("equals", {"value": [1, True, None]}), ("regex", "value.*"), ("exists", True), ("absent", True),
            ("min_len", 0), ("equals_path", "other.items[0]"), ("contains", {"name": "entry"}),
            ("minimum", 1), ("minimum", 1.5), ("set_equals", ["first", "second"]),
        ):
            with self.subTest(operator=operator):
                runtime_manifest.validate_runtime_expect({"stdout_json": [{"path": "items[*].value", operator: value}]})
        for name in ("null", "boolean", "object", "array", "number", "integer", "string"):
            runtime_manifest.validate_runtime_expect({"stdout_json": [{"path": "", "type": name}]})

    def test_json_assertions_require_exactly_one_operator(self) -> None:
        for assertion in ({}, {"path": "value"}, {"equals": 1}, {"path": "value", "equals": 1, "exists": True}, {"path": "value", "unknown": True}, []):
            self._refuses({"stdout_json": [assertion]})
        self._refuses({"stdout_json": {"path": "value", "exists": True}}, "must be a list")

    def test_json_paths_include_root_indices_and_wildcards(self) -> None:
        for path in ("", "value", "nested.items[0].name", "items[*].name", "items[0][1]", "[0][1].name", "[*].name", "hyphen-key.value"):
            runtime_manifest.validate_runtime_expect({"stdout_json": [{"path": path, "exists": True}]})
        for path in (None, 1, ".value", "value.", "value..name", "items[-1]", "items[x]", "items[", "items]", "[0]name", "value name"):
            self._refuses({"stdout_json": [{"path": path, "exists": True}]}, "JSON path")
            self._refuses({"stdout_json": [{"path": "value", "equals_path": path}]}, "JSON path")

    def test_operator_values_reject_wrong_types_and_non_json_values(self) -> None:
        for operator, values in (
            ("exists", (False, 1, "true")), ("absent", (False, 1, "true")),
            ("min_len", (-1, True, 1.0, "1")), ("type", ("int", "bool", None, [])),
            ("minimum", (True, "1", None, float("nan"), float("inf"))),
            ("set_equals", ({}, "items", [float("nan")])),
            ("equals", ({1: "bad-key"}, {"nested": {1, 2}}, float("nan"))),
            ("contains", ({1, 2}, float("inf"))),
        ):
            for value in values:
                with self.subTest(operator=operator, value=value):
                    self._refuses({"stdout_json": [{"path": "value", operator: value}]})

    def test_regex_fields_compile_every_declared_expression(self) -> None:
        for field in ("stdout_regex", "stderr_regex", "stdout_not_regex", "stderr_not_regex"):
            runtime_manifest.validate_runtime_expect({field: ["^first$", "second"]})
            runtime_manifest.validate_runtime_expect({field: "^single$"})
            for value in ("[", ["valid", "["], [1], None):
                self._refuses({field: value}, "regex")
        self._refuses({"stdout_json": [{"path": "value", "regex": "["}]}, "regex")

    def test_exit_codes_are_integer_or_nonempty_integer_list(self) -> None:
        for expect in ({}, {"exit_code": 0}, {"exit_code": -9}, {"exit_code": [0, 1]}):
            runtime_manifest.validate_runtime_expect(expect)
        for codes in ([], True, "0", [0, False], [0, "1"], None, 1.0):
            self._refuses({"exit_code": codes}, "exit_code")

    def test_expect_and_alternatives_have_closed_shapes(self) -> None:
        runtime_manifest.validate_runtime_expect({"alternatives": [{"exit_code": 0, "stdout_regex": "ok"}, {"exit_code": 2, "stdout_json": [{"path": "error", "exists": True}]}]})
        for expect in ([], {"unknown": 1}, {"alternatives": []}, {"alternatives": {}}, {"alternatives": [None]}, {"alternatives": [{"alternatives": [{}]}]}):
            self._refuses(expect)

    def test_files_accept_existence_regex_json_and_type(self) -> None:
        runtime_manifest.validate_runtime_expect({"files": [
            "report.txt", {"path": "{smoke_dir}/report.txt", "regex": "marker"},
            {"path": "state.json", "json": [{"path": "", "type": "object"}, {"path": "count", "minimum": 1}]},
            {"path": "report.txt", "type": "file"}, {"path": "delegation", "type": "directory"},
        ]})
        for entry in ({"path": "report.txt"}, {"path": "report.txt", "regex": "["}, {"path": "report.txt", "json": {}}, {"path": "report.txt", "regex": "marker", "json": []}, {"path": "report.txt", "type": "symlink"}, {"path": "report.txt", "unknown": True}, "../outside", "{smoke_dir}/../outside", "/outside", "C:/outside", "a\\outside", {"path": "../outside", "json": []}):
            self._refuses({"files": [entry]})
        self._refuses({"files": "report.txt"}, "files must be a list")


class RuntimeSmokeCoverageValidationTests(unittest.TestCase):
    @staticmethod
    def _spec(status: str, **contracts: object) -> dict:
        return {"smoke_coverage": {"status": status, "reason": "test coverage"}, **contracts}

    def test_offline_smoke_presence_is_equivalent_to_offline_status(self) -> None:
        runtime_manifest.validate_runtime_smoke_coverage("example", self._spec("offline-smoke", smoke={}))
        for spec in (self._spec("offline-smoke"), self._spec("doctor-only", smoke={}), self._spec("venv-smoke", smoke={}, functional_smoke={"case": {}})):
            with self.assertRaises(ManifestError):
                runtime_manifest.validate_runtime_smoke_coverage("example", spec)

    def test_venv_smoke_requires_functional_contracts(self) -> None:
        runtime_manifest.validate_runtime_smoke_coverage("example", self._spec("venv-smoke", functional_smoke={"help": {}}, python={}))
        runtime_manifest.validate_runtime_smoke_coverage("example", self._spec("offline-smoke", smoke={}, functional_smoke={"help": {}}))
        for spec in (self._spec("venv-smoke"), self._spec("venv-smoke", functional_smoke={}), self._spec("doctor-only", functional_smoke={"help": {}})):
            with self.assertRaises(ManifestError):
                runtime_manifest.validate_runtime_smoke_coverage("example", spec)

    def test_live_checks_are_allowed_with_every_coverage_status(self) -> None:
        for status in ("offline-smoke", "venv-smoke", "doctor-only", "manual-native", "static-only", "unsupported", "not-applicable"):
            spec = self._spec(status, live_check={"doctor": {}})
            if status == "offline-smoke":
                spec["smoke"] = {}
            elif status == "venv-smoke":
                spec["functional_smoke"] = {"help": {}}
            runtime_manifest.validate_runtime_smoke_coverage("example", spec)


class RuntimeSmokeManifestTests(unittest.TestCase):
    def test_every_contract_has_expect(self) -> None:
        manifests = load_manifests()
        seen = 0
        for skill, spec in manifests["runtime"]["skills"].items():
            contracts = ([spec["smoke"]] if "smoke" in spec else [])
            for kind in ("functional_smoke", "live_check"):
                contracts.extend(spec.get(kind, {}).values())
            for contract in contracts:
                self.assertIn("expect", contract, skill)
                seen += 1
        self.assertGreaterEqual(seen, 19)

    def test_manifest_validation_rejects_a_bad_functional_or_live_case(self) -> None:
        manifests = load_manifests()
        for kind in ("functional_smoke", "live_check"):
            runtime = copy.deepcopy(manifests["runtime"])
            skill = next(name for name, spec in runtime["skills"].items() if "smoke" in spec)
            runtime["skills"][skill][kind] = {
                "invalid": RuntimeSmokeContractValidationTests._contract(kind, unknown=True),
            }
            with self.subTest(kind=kind), self.assertRaisesRegex(ManifestError, "unknown keys"):
                runtime_manifest.validate_manifests(
                    manifests["skills"], manifests["profiles"], manifests["dependencies"],
                    manifests["artifacts"], manifests["system_dependencies"], runtime,
                    manifests["delegation"], external_dependencies=manifests["external_dependencies"],
                )

    def test_loaded_contracts_use_the_matching_validator(self) -> None:
        manifests = load_manifests()
        for skill, spec in manifests["runtime"]["skills"].items():
            if "smoke" in spec:
                runtime_manifest.validate_runtime_smoke_contract(skill, spec["smoke"])
            for kind in ("functional_smoke", "live_check"):
                if kind in spec:
                    runtime_manifest.validate_runtime_case_collection(skill, spec[kind], kind)


if __name__ == "__main__":
    unittest.main()
