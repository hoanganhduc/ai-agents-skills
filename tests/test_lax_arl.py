from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True
import tempfile
import unittest

RUNTIME = Path(__file__).resolve().parents[1] / 'canonical/runtime/skills/autonomous-research-loop-runtime'
sys.path.insert(0, str(RUNTIME))
import formal_policy as fp


class LaxTerminalTests(unittest.TestCase):
    def setup_case(self, base):
        project = base / 'project'; project.mkdir()
        for kind in ['concepts', 'proofs']:
            (project/kind).mkdir(); (project/kind/'lakefile.toml').write_text('name = "fixture"\n', encoding="utf-8")
        (project/'manifest.yaml').write_text('id: lax-1\n', encoding="utf-8")
        request = base/'request.json'
        request.write_text(json.dumps({'project_root': str(project), 'submission': '.', 'targets': ['Lax1.X.t']}), encoding="utf-8")
        policy = fp.FormalPolicy(policy='force', project=str(project), lax_request=str(request))
        pin = fp.pin_privileged_policy(policy)
        report = {'schema_version': 'lax-verification.v1', 'backend': 'lax', 'machine_status': 'passed',
                  'closure_status': 'closed', 'closure': {'open': []}, 'semantic_status': 'accepted',
                  'source': {'commit': 'a'*40}, 'source_digest':'a'*64, 'scope_digest':'b'*64,
                  'challenge_digest':'c'*64, 'request_digest':pin['lax_request_sha256'],
                  'database_commit':'d'*40, 'database_digest':'e'*64, 'tools': {'environment':'v4.33.0'}, 'dependency_digests':{}}
        return project, request, policy, pin, report

    def test_closed_reviewed_scope_has_terminal_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            root, _, _, pin, report = self.setup_case(Path(d))
            r=fp._evaluate_lax_terminal(root, lambda *_: {'ok':True,'report':report}, pin)
            self.assertEqual(r['terminal_state'],'sorry_free_artifact')

    def test_registration_or_machine_pass_cannot_replace_semantic_review(self):
        with tempfile.TemporaryDirectory() as d:
            root, _, _, pin, report = self.setup_case(Path(d)); report['semantic_status']='pending'
            r=fp._evaluate_lax_terminal(root, lambda *_: {'ok':True,'report':report}, pin)
            self.assertEqual(r['terminal_state'],'indeterminate')

    def test_open_obligation_stays_open(self):
        with tempfile.TemporaryDirectory() as d:
            root, _, _, pin, report = self.setup_case(Path(d)); report['closure_status']='open';report['closure']={'open':['X']}
            r=fp._evaluate_lax_terminal(root, lambda *_: {'ok':False,'report':report}, pin)
            self.assertEqual(r['terminal_state'],'open_ledger')

    def test_modified_host_request_is_not_executed(self):
        with tempfile.TemporaryDirectory() as d:
            root, request, _, pin, _ = self.setup_case(Path(d));request.write_text('{}', encoding="utf-8")
            def forbidden(*_): self.fail('changed request was executed')
            r=fp._evaluate_lax_terminal(root, forbidden, pin)
            self.assertEqual(r['detail'],'lax_request_changed')

    def test_database_change_changes_reverification_binding(self):
        with tempfile.TemporaryDirectory() as d:
            root, _, _, pin, report = self.setup_case(Path(d))
            r1=fp._evaluate_lax_terminal(root, lambda *_: {'ok':True,'report':report}, pin)
            report['database_digest']='f'*64
            r2=fp._evaluate_lax_terminal(root, lambda *_: {'ok':True,'report':report}, pin)
            self.assertNotEqual(r1['gate']['scan']['source_digest'],r2['gate']['scan']['source_digest'])


if __name__=='__main__': unittest.main()
