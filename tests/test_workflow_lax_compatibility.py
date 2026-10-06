"""Deployed Lax extensions remain typed and cannot certify dependency-only work."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'canonical/runtime/skills/lax-formalization'))
import workflow_check


class VerificationCompatibilityTests(unittest.TestCase):
    def report(self):
        return {'schema_version': 'lax-verification.v1', 'backend': 'lax',
                'status': 'passed', 'machine_status': 'passed', 'closure_status': 'closed',
                'source': {'repository': 'https://github.com/example/paper', 'commit': 'a' * 40},
                'submission_id': 'lax-1', 'source_digest': 'b' * 64, 'scope_digest': 'c' * 64,
                'targets': ['Lax1.Target.t']}

    def test_legacy_and_current_theorem_summary(self):
        for fields in [{}, {'verification_kind': 'theorems', 'compile_timeout_seconds': 1200}]:
            with self.subTest(fields=fields):
                self.assertEqual(workflow_check.public_summary({**self.report(), **fields})['machine_status'], 'passed')

    def test_definition_only_never_becomes_public_theorem_summary(self):
        report = {**self.report(), 'verification_kind': 'definitions-only',
                  'compile_timeout_seconds': 1200, 'targets': []}
        with self.assertRaises(ValueError):
            workflow_check.public_summary(report)

    def test_malformed_or_partial_extension_refused(self):
        for fields in [{'verification_kind': 'theorems'}, {'compile_timeout_seconds': 1200},
                       {'verification_kind': 'theorems', 'compile_timeout_seconds': True},
                       {'verification_kind': 'theorems', 'compile_timeout_seconds': 3601},
                       {'verification_kind': 'unverified', 'compile_timeout_seconds': 1200}]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                workflow_check.public_summary({**self.report(), **fields})

    def test_request_schema_accepts_only_defined_extension_shapes(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest('optional schema test dependency unavailable')
        schema = json.loads((ROOT / 'canonical/schemas/lax/request.schema.json').read_text(encoding="utf-8"))
        validate = Draft202012Validator(schema)
        request = {'schema_version': 'lax-request.v1', 'project_root': '/project',
                   'database_root': '/database', 'challenge_root': '/challenge',
                   'environment': 'v4.33.0', 'targets': ['Lax1.Target.t']}
        validate.validate(request)
        validate.validate({**request, 'compile_timeout_seconds': 2400})
        validate.validate({**request, 'verification_kind': 'definitions-only', 'targets': []})
        for bad in [{**request, 'targets': []}, {**request, 'compile_timeout_seconds': True},
                    {**request, 'verification_kind': 'definitions-only'}, {**request, 'execute': True}]:
            self.assertTrue(list(validate.iter_errors(bad)))


if __name__ == '__main__':
    unittest.main()
