from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.dont_write_bytecode = True
import hashlib
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "canonical/runtime/skills/zenodo-artifact/zenodo_artifact.py"
spec = importlib.util.spec_from_file_location("zenodo_artifact", PATH)
assert spec and spec.loader
z = importlib.util.module_from_spec(spec); spec.loader.exec_module(z)


class MetadataTests(unittest.TestCase):
    def metadata(self):
        return {"title": "Order-pattern benchmark", "version": "1.0.0", "upload_type": "software",
                "license": "Apache-2.0", "creators": [{"name": "Example, Researcher"}], "description": "A local verification artifact."}

    def test_valid_metadata(self):
        self.assertEqual(z.validate_metadata(self.metadata()), [])

    def test_placeholder_refused(self):
        m = self.metadata(); m["title"] = "<PAPER-TITLE>"
        self.assertTrue(z.validate_metadata(m))

    def test_empty_creator_refused(self):
        m = self.metadata(); m["creators"] = []
        self.assertTrue(z.validate_metadata(m))

    def test_citation_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "CITATION.cff"
            p.write_text('title: "Other title"\nversion: "1.0.0"\nlicense: "Apache-2.0"\n', encoding="utf-8")
            self.assertTrue(z.check_citation(p, self.metadata()))

    def test_no_network_in_publication_plan(self):
        r = z.publication_plan(self.metadata())
        self.assertFalse(r["publication_enabled"])
        self.assertEqual(r["status"], "proposal")

    def test_bundle_checksum_change_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); (p / "source.zip").write_bytes(b"before")
            z.write_checksums(p)
            (p / "source.zip").write_bytes(b"after")
            self.assertFalse(z.validate_bundle(p)["ok"])

    def bundle(self, root):
        with zipfile.ZipFile(root/'source.zip', 'w') as archive: archive.writestr('Result.lean', 'example : True := trivial\n')
        cert={'source':{'commit':'a'*40},'source_digest':z.source_digest(root/'source.zip'),
              'machine_status':'passed','closure_status':'closed','dependency_digests':{}}
        (root/'verification-summary.json').write_text(json.dumps(cert), encoding="utf-8")
        (root/'provenance.json').write_text(json.dumps({'source_commit':'a'*40,'verification_sha256':hashlib.sha256((root/'verification-summary.json').read_bytes()).hexdigest()}), encoding="utf-8")
        (root/'metadata.json').write_text(json.dumps(self.metadata()), encoding="utf-8")
        (root/'dependency-inventory.json').write_text('{}', encoding="utf-8")
        (root/'REPRODUCE.md').write_text('Restore then verify the fixed scope.\n', encoding="utf-8")
        z.write_checksums(root)

    def test_archive_unsafe_paths_and_git_control_files_refused(self):
        for name in ['../escape', '.git/config', 'x/.Git/hooks/pre-commit', 'CON', 'x/../y']:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                p=Path(d)/'source.zip'
                with zipfile.ZipFile(p,'w') as archive: archive.writestr(name,'bad')
                self.assertTrue(z.check_zip(p))

    def test_restore_has_no_git_and_rejects_mutable_bundle_swap(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);bundle=root/'bundle';bundle.mkdir();self.bundle(bundle)
            self.assertTrue(z.validate_bundle(bundle)['ok'])
            self.assertEqual(z.restore(bundle,root/'restored')['status'],'restored')
            self.assertFalse((root/'restored/.git').exists())
            validated=z.validate_bundle(bundle)
            with zipfile.ZipFile(bundle/'source.zip','w') as archive: archive.writestr('../escape','bad')
            with patch.object(z,'validate_bundle',return_value=validated):
                with self.assertRaises(ValueError):z.restore(bundle,root/'attacked')
            self.assertFalse((root/'escape').exists())

    def test_failed_certificate_cannot_be_admitted_by_rehashing_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.bundle(root)
            p=root/'verification-summary.json';r=json.loads(p.read_text(encoding="utf-8"));r['machine_status']='failed';p.write_text(json.dumps(r), encoding="utf-8")
            prov=root/'provenance.json';r=json.loads(prov.read_text(encoding="utf-8"));r['verification_sha256']=hashlib.sha256(p.read_bytes()).hexdigest();prov.write_text(json.dumps(r), encoding="utf-8")
            z.write_checksums(root)
            self.assertFalse(z.validate_bundle(root)['ok'])

    def test_provenance_cannot_promote_pending_semantics(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.bundle(root)
            p=root/'provenance.json';r=json.loads(p.read_text(encoding="utf-8"));r['semantic_status']='accepted';p.write_text(json.dumps(r), encoding="utf-8")
            z.write_checksums(root)
            self.assertIn('semantic status contradicts verification report',z.validate_bundle(root)['issues'])


if __name__ == "__main__": unittest.main()
