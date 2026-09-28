"""Definitions-only dependencies must never certify skipped theorem targets."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "canonical/runtime/skills/lax-formalization"))
import lax_executor as executor


class DefinitionDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ["project", "database", "challenge"]:
            (self.root / name).mkdir()
        self.request = {
            "schema_version": "lax-request.v1",
            "project_root": str(self.root / "project"),
            "database_root": str(self.root / "database"),
            "challenge_root": str(self.root / "challenge"),
            "environment": "v4.33.0",
            "submission": ".",
            "targets": [],
            "verification_kind": "definitions-only",
            "dependencies": {},
        }

    def save(self):
        path = self.root / "request.json"
        path.write_text(json.dumps(self.request), encoding="utf-8")
        return path

    def test_explicit_definition_request_is_admitted(self):
        self.assertEqual(executor.validate_request(self.save()), self.request)

    def test_ordinary_request_still_requires_targets(self):
        del self.request["verification_kind"]
        with self.assertRaises(ValueError):
            executor.validate_request(self.save())

    def test_explicit_theorem_request_still_requires_targets(self):
        self.request["verification_kind"] = "theorems"
        with self.assertRaises(ValueError):
            executor.validate_request(self.save())

    def test_definition_request_cannot_name_a_theorem(self):
        self.request["targets"] = ["Lax1.Open.target"]
        with self.assertRaises(ValueError):
            executor.validate_request(self.save())

    def test_unknown_verification_kind_is_rejected(self):
        self.request["verification_kind"] = "trust-registered"
        self.request["targets"] = ["Lax1.Open.target"]
        with self.assertRaises(ValueError):
            executor.validate_request(self.save())

    def test_definition_mode_is_dependency_only_before_executor_setup(self):
        with patch.object(executor, "settings") as setup:
            with self.assertRaisesRegex(ValueError, "dependency"):
                executor.verify(self.save(), self.root / "output")
            setup.assert_not_called()

    def test_empty_checked_statement_inventory_is_allowed_for_definitions(self):
        executor.validate_checked_scope(self.request, set())

    def test_definition_check_cannot_become_a_publication_plan(self):
        with self.assertRaisesRegex(ValueError, "dependency"):
            executor.publication_plan(self.save(), self.root / "publication")
        self.assertFalse((self.root / "publication").exists())

    def test_definition_mode_cannot_hide_any_checked_statement(self):
        with self.assertRaisesRegex(ValueError, "statement"):
            executor.validate_checked_scope(self.request, {"Lax1.Open.target"})

    def test_default_theorem_scope_requires_every_target(self):
        request = {"targets": ["Lax1.Result.target"]}
        with self.assertRaisesRegex(ValueError, "target"):
            executor.validate_checked_scope(request, set())
        executor.validate_checked_scope(request, {"Lax1.Result.target"})


if __name__ == "__main__":
    unittest.main()