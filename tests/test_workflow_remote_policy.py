"""Remote formal requests cannot widen the run's compute authority."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from unittest import mock

from tests import test_workflow_remote_formal as fixtures

remote, broker = fixtures.remote, fixtures.broker


class RemotePolicyTests(fixtures.RemoteFormalTests):
    # Reuse only the fixture, not the parent test collection.
    def test_existing_job_deny_prevents_submission(self):
        for name, document in (
            ("compute_policy.json", {"policy": {"backends": ["local"], "forbidden_services": ["kaggle"]}}),
            ("loop_state.json", {"standing_orders": {"compute": {"backends": ["local"]}}}),
            ("current_plan.json", {"plan_revision": 3, "compute_policy": {"allowed_services": []}}),
        ):
            with self.subTest(name=name):
                (self.run / name).write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "compute|permitted|policy"):
                    self.advance()
                (self.run / name).unlink()
        self.assertEqual(self.backend.pushes, 0)

    def test_blocked_marker_prevents_remote_submission(self):
        (self.run / "BLOCKED").write_text("operator", encoding="utf-8")
        result = self.advance()
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(self.backend.pushes, 0)

    def test_policy_change_between_preparation_and_push_is_refused(self):
        (self.run / "compute_policy.json").write_text(json.dumps({"backends": ["kaggle"]}), encoding="utf-8")
        prepare = remote.prepare_bundle
        def narrowed(*args, **kwargs):
            result = prepare(*args, **kwargs)
            (self.run / "compute_policy.json").write_text(json.dumps({"backends": ["local"]}), encoding="utf-8")
            return result
        with mock.patch.object(remote, "prepare_bundle", side_effect=narrowed):
            with self.assertRaisesRegex(ValueError, "compute|permitted|policy"):
                self.advance()
        self.assertEqual(self.backend.pushes, 0)

    def test_allowed_explicit_remote_choice_preserved(self):
        (self.run / "compute_policy.json").write_text(json.dumps({"backends": ["local", "kaggle"]}), encoding="utf-8")
        (self.run / "current_plan.json").write_text(json.dumps({"plan_revision": 2, "compute_policy": {"allowed_services": ["kaggle"]}}), encoding="utf-8")
        self.assertEqual(self.advance()["status"], "verification_pending")
        self.assertEqual(self.backend.pushes, 1)

    def test_registration_refuses_job_deny_before_authority_mutation(self):
        (self.run / "compute_policy.json").write_text(json.dumps({"backends": ["local"]}), encoding="utf-8")
        state = broker.CredentialState.__new__(broker.CredentialState)
        state.lock = threading.Lock()
        state.private_root = self.root
        state.formal_authority_root = self.root / "host-authority"
        state.formal_registrations = {}
        state.formal_locks = {}
        state.formal_checkpoint_locks = {}
        with self.assertRaisesRegex(ValueError, "compute|permitted|policy"):
            state.formal_register({"operation": "formal_register", "run_dir": str(self.run), "project": str(self.project), "pin": self.pin})
        self.assertFalse(state.formal_authority_root.exists())


# The shared fixture's unrelated tests run in its own suite.
for _name in list(vars(fixtures.RemoteFormalTests)):
    if _name.startswith("test_"):
        setattr(RemotePolicyTests, _name, None)
