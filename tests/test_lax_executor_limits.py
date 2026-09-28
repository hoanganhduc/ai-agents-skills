"""Operator-selected compile budgets remain explicit, typed and bounded."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "canonical/runtime/skills/lax-formalization"))
import lax_executor as executor


class CompileBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ["source", "challenge", "database"]:
            (self.root / name).mkdir()
        self.request = {
            "schema_version": "lax-request.v1",
            "project_root": str(self.root / "source"),
            "challenge_root": str(self.root / "challenge"),
            "database_root": str(self.root / "database"),
            "environment": "v4.33.0",
            "submission": ".",
            "targets": ["Lax1.Result.target"],
            "dependencies": {},
        }

    def validate(self):
        path = self.root / "request.json"
        path.write_text(json.dumps(self.request), encoding="utf-8")
        return executor.validate_request(path)

    def test_default_request_keeps_its_shape(self):
        self.assertEqual(self.validate(), self.request)
        self.assertNotIn("compile_timeout_seconds", self.request)

    def test_larger_explicit_budget_is_admitted(self):
        self.request["compile_timeout_seconds"] = 2400
        self.assertEqual(self.validate()["compile_timeout_seconds"], 2400)

    def test_limits_are_inclusive(self):
        for value in [60, 3600]:
            with self.subTest(value=value):
                self.request["compile_timeout_seconds"] = value
                self.assertEqual(self.validate()["compile_timeout_seconds"], value)

    def test_unbounded_or_malformed_budgets_are_rejected(self):
        for value in [0, -1, 59, 3601, True, False, "2400", 2400.0, None, []]:
            with self.subTest(value=value):
                self.request["compile_timeout_seconds"] = value
                with self.assertRaises(ValueError):
                    self.validate()

    def test_budget_does_not_admit_empty_theorem_scope(self):
        self.request["compile_timeout_seconds"] = 2400
        self.request["targets"] = []
        with self.assertRaises(ValueError):
            self.validate()

    def test_definition_dependency_can_select_its_own_budget(self):
        self.request.update(verification_kind="definitions-only", targets=[], compile_timeout_seconds=2400)
        self.assertEqual(self.validate()["verification_kind"], "definitions-only")


if __name__ == "__main__":
    unittest.main()