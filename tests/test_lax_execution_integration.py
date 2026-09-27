"""Explicit native Docker/Lean checks; excluded from default offline test runs."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'canonical/runtime/skills/lax-formalization'))
from lax_formalization import digest, tree_inventory
from lax_executor import verify


@unittest.skipUnless(os.environ.get('AAS_LAX_EXECUTOR_TEST') == '1', 'explicit native Docker/Lean qualification only')
class NativeLaxTests(unittest.TestCase):
    def fixture(self, base, proof_body):
        project=base/'project';project.mkdir();home=base/'home';home.mkdir();lh=base/'lax-home';lh.mkdir()
        (lh/'warm').symlink_to(Path.home()/'.lax/warm',target_is_directory=True)
        env={'PATH':os.environ['PATH'],'HOME':str(home),'LAX_HOME':str(lh),'ELAN_HOME':str(Path.home()/'.elan'),
             'LAX_DISABLE_UPDATE_CHECK':'1','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null'}
        def run(args):
            return subprocess.run(args,cwd=project,env=env,check=True,capture_output=True,text=True,encoding="utf-8").stdout
        run(['git','init','--quiet']);run(['lax','init','submission','--title','Independent executor fixture'])
        sub=project/'submission';sid=re.search(r'^id: (lax-\d+)',(sub/'manifest.yaml').read_text(encoding="utf-8"),re.M).group(1);mod='Lax'+sid[4:]
        concept=sub/'concepts';proof=sub/'proofs';(concept/mod).mkdir(exist_ok=True);(proof/(mod+'Proofs')).mkdir(exist_ok=True)
        (concept/mod/'Check.lean').write_text(f'import Mathlib.Logic.Basic\n/-!\n---\ntitle: Executor fixture\ntype: theorem\n---\nA local fixture about the true proposition.\n-/\nnamespace {mod}.Check\naxiom target : True\nend {mod}.Check\n', encoding="utf-8")
        (concept/(mod+'.lean')).write_text(f'import {mod}.Check\n', encoding="utf-8")
        annotation=f'/--\n---\nconclusion: {mod}.Check.target\n---\n-/\n'
        content=proof_body.replace('ANNOTATION',annotation)
        (proof/(mod+'Proofs')/'Check.lean').write_text(f'import {mod}.Check\nnamespace {mod}Proofs\n{content}\nend {mod}Proofs\n', encoding="utf-8")
        (proof/(mod+'Proofs.lean')).write_text(f'import {mod}Proofs.Check\n', encoding="utf-8")
        run(['git','add','.']);run(['git','-c','user.name=Local test','-c','user.email=local'+'@'+'example.invalid','commit','--quiet','-m','Local executor fixture'])
        challenge=base/'challenge';shutil.copytree(concept,challenge,ignore=shutil.ignore_patterns('.lake','lake-manifest.json'))
        targets=[mod+'.Check.target']
        req={'schema_version':'lax-request.v1','project_root':str(project),'submission':'submission','database_root':str(Path.home()/'.lax/lax-database'),
             'environment':'v4.33.0','targets':targets,'challenge_root':str(challenge),
             'semantic_review':{'status':'accepted','reviewer':'fixture-evaluator','challenge_sha256':tree_inventory(challenge)['sha256'],'scope_digest':digest(targets)}}
        p=base/'request.json';p.write_text(json.dumps(req), encoding="utf-8");return p

    def test_full_fresh_replay_and_open_obligation(self):
        for body,expected in [('ANNOTATIONtheorem target : True := trivial','closed'),('', 'open')]:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory(prefix='aas-native-lax-') as tmp:
                root=Path(tmp);request=self.fixture(root,body);report=verify(request,root/'evidence')
                self.assertEqual(report['closure_status'],expected)
                self.assertTrue(all(p['teardown_confirmed'] for p in report['phases']))
                self.assertEqual(report['semantic_status'],'accepted')

    def test_well_typed_rogue_axiom_and_native_proof_are_rejected(self):
        for body in ['axiom bad : True\nANNOTATIONtheorem target : True := bad',
                     'ANNOTATIONtheorem target : True := by decide +native']:
            with self.subTest(body=body), tempfile.TemporaryDirectory(prefix='aas-native-lax-') as tmp:
                root=Path(tmp);request=self.fixture(root,body)
                with self.assertRaises(ValueError):verify(request,root/'evidence')
                compiled=json.loads((root/'evidence/execution/result-compile/compile.json').read_text(encoding="utf-8"))
                self.assertTrue(any(v['phase']=='inspect' and 'axiom' in v['rule'] for v in compiled['violations']),compiled)


if __name__=='__main__':unittest.main()
