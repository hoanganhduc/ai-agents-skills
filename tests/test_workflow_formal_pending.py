"""Pending remote proof checks preserve the exact reviewed candidate."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest import mock

import test_goal_focus as fixtures


class PendingVerificationTests(fixtures._AttestedGoalFocusTestCase):
    def staged(self, root):
        fixtures._initialize(root)
        plan = fixtures._activate(root)
        candidate = fixtures._stage_enforced_candidate(root, plan, {
            'output': 'formal result', 'evidence_ids': ['proof.json'],
            'formal_terminal_state': {'terminal_state': 'sorry_free_artifact', 'source_digest': 'a'*64},
        })
        return candidate['candidate'], fixtures._bound_review(candidate)

    def test_pending_check_retains_review_and_does_not_bank(self):
        gf = fixtures.gf
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); candidate, review = self.staged(root)
            result = gf.finalize_candidate(root, accepted=True, review=review,
                formal_reverifier=lambda _: {'status': 'verification_pending', 'ok': False})
            self.assertEqual(result['status'], 'verification_pending')
            self.assertEqual(gf._read_iteration_rows(root), [])
            self.assertEqual(gf.pending_formal_review(root, candidate), review)
            final = gf.finalize_candidate(root, accepted=True, review=review,
                formal_reverifier=lambda _: {'status': 'reverified', 'ok': True,
                    'staged': {'source_digest': 'a'*64}, 'observed': {'source_digest': 'a'*64}})
            self.assertEqual(final['record']['bank_status'], 'accepted')

    def test_stop_during_reverification_cannot_resume_or_bank(self):
        gf = fixtures.gf
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); _, review = self.staged(root)
            def stop(_):
                (root / 'STOP_REQUESTED').write_text('user stop', encoding="utf-8")
                return {'status': 'reverified', 'ok': True,
                    'staged': {'source_digest': 'a'*64}, 'observed': {'source_digest': 'a'*64}}
            with self.assertRaises(gf.RevisionConflict):
                gf.finalize_candidate(root, accepted=True, review=review, formal_reverifier=stop)
            self.assertEqual(gf._read_iteration_rows(root), [])
            self.assertTrue((root / 'STOP_REQUESTED').exists())

    def test_refreshed_host_verdict_cannot_replace_candidate_formal_source(self):
        gf = fixtures.gf
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); candidate, review = self.staged(root)
            gf.finalize_candidate(root, accepted=True, review=review,
                formal_reverifier=lambda _: {'status': 'verification_pending', 'ok': False})
            cached = gf.pending_formal_review(root, candidate)
            with self.assertRaisesRegex(ValueError, 'candidate.*formal|formal.*candidate'):
                gf.finalize_candidate(root, accepted=True, review=cached,
                    formal_reverifier=lambda _: {'status': 'reverified', 'ok': True,
                        'staged': {'source_digest': 'b'*64}, 'observed': {'source_digest': 'b'*64}})
            self.assertEqual(gf._read_iteration_rows(root), [])

    def test_shutdown_with_pending_candidate_preserves_certified_stamp(self):
        rt = fixtures.rt
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.staged(root)
            stamp = root / 'formal/terminal_state.json'; stamp.parent.mkdir(exist_ok=True)
            stamp.write_text('retained host evidence', encoding="utf-8")
            with mock.patch.object(rt, 'load_formal_terminal_state', return_value={'terminal_state': 'sorry_free_artifact'}), mock.patch.object(rt, 'evaluate_formal_terminal_state', side_effect=AssertionError('shutdown must not evaluate')):
                result = rt.formal_shutdown_verdict(root, root=root, policy=object(), pin={},
                    reason='formal_verification_pending', integrity={})
            self.assertEqual(result['terminal_state'], 'sorry_free_artifact')
            self.assertEqual(stamp.read_text(encoding="utf-8"), 'retained host evidence')

    def test_enforce_pending_honors_poll_cap_without_relaunching_primary(self):
        rt, gf = fixtures.rt, fixtures.gf
        base = self.provider_fixture.root / 'bounded-formal-wait'
        project = base / 'project'; project.mkdir(parents=True, mode=0o700)
        root = project / 'loop'; root.mkdir(mode=0o700)
        _, review = self.staged(root)
        registry = self.provider_fixture.root / 'formal-wait-registry'; registry.mkdir(mode=0o700)
        args = rt.selftest_drive_args(root, registry, 'unused')
        args.root = str(project); args.cmd = None; args.provider = 'codex'; args.max_review_waits = 1
        with mock.patch.dict(os.environ, {'AAS_AUTOLOOP_PROVIDER_TRANSPORT': 'trusted-local'}), \
             mock.patch.object(rt, 'preflight_resource_backend', return_value=rt.provider_resource_limits(60, role='primary')), \
             mock.patch.object(rt, 'run_panel_phase_for_drive', return_value={}) as panel, \
             mock.patch.object(rt, '_result_review_from_panel', return_value={'status': 'accepted', 'review': review}), \
             mock.patch.object(gf, '_default_formal_reverifier', return_value={'status': 'verification_pending', 'ok': False}), \
             mock.patch.object(rt, 'interruptible_sleep', side_effect=AssertionError('poll cap must stop before sleep')), \
             mock.patch.object(rt, 'run_primary_subprocess', side_effect=AssertionError('pending verification must not relaunch primary')):
            result = rt.drive_command(args)
        self.assertEqual(result['reason'], 'formal_verification_pending', result)
        self.assertEqual(result['exit_code'], 19)
        panel.assert_called_once()
        self.assertEqual(gf._read_iteration_rows(root), [])


if __name__ == '__main__': unittest.main()
