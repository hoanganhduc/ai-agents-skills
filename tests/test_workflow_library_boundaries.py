from __future__ import annotations

from argparse import Namespace
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
import zipfile

import test_zotero_webdav_metadata as fixtures


class LibraryBoundaryTests(unittest.TestCase):
    def archive(self, entries):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for name, data in entries:
                archive.writestr(name, data)
        return stream.getvalue()

    def webdav(self, entries):
        module = fixtures.load_webdav_module()
        client = module.WebDAVClient.__new__(module.WebDAVClient)
        client.zotero_url = 'https://example.invalid/zotero'
        client._request = mock.Mock(return_value=Namespace(status_code=200, content=self.archive(entries)))
        return client

    def test_multiple_pdf_members_require_explicit_selection(self):
        client = self.webdav([('paper.pdf', b'paper'), ('private-review.pdf', b'review')])
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            client.download('ATTACH01', tmp)

    def test_explicit_member_does_not_extract_other_content(self):
        client = self.webdav([('private-review.pdf', b'review'), ('paper.pdf', b'paper')])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(client.download('ATTACH01', tmp, expected_member='paper.pdf'))
            self.assertEqual(path.read_bytes(), b'paper')
            self.assertFalse(any(Path(tmp).rglob('private-review.pdf')))

    def test_traversal_is_refused_before_extraction(self):
        client = self.webdav([('../escaped.pdf', b'private')])
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            client.download('ATTACH01', tmp)

    def test_multiple_attachments_do_not_touch_local_pdf(self):
        module = fixtures.load_zot_module()
        parent = {'key': 'PARENT01', 'data': {'itemType': 'journalArticle', 'title': 'Paper'}}
        attachments = [{'key': name, 'data': {'itemType': 'attachment', 'contentType': 'application/pdf', 'filename': name+'.pdf'}} for name in ['ATTACH01','ATTACH02']]
        client = Namespace(search=lambda *_a, **_k: [parent], children=lambda _k: attachments)
        args = Namespace(link=False, query='paper', index=None, local_storage=True)
        with mock.patch.object(module, 'load_config', return_value={}), mock.patch.object(module, 'ZoteroClient', return_value=client), mock.patch.object(module, '_output') as out, mock.patch.object(module, '_find_local_attachment_pdf') as read:
            module.cmd_get(args)
        read.assert_not_called()
        self.assertEqual(out.call_args.args[0]['status'], 'multiple_attachments')

    def test_bibliography_objects_are_serialized_not_printed(self):
        module = fixtures.load_zot_module()
        db = Namespace(entries=[{'ENTRYTYPE':'article','ID':'key','title':'Paper'}])
        with mock.patch.dict('sys.modules', {'bibtexparser': Namespace(dumps=lambda obj: '@article{key, title={Paper}}')}):
            self.assertTrue(module.serialize_bibtex(db).startswith('@article{'))
        with self.assertRaises(ValueError):
            module.serialize_bibtex(object())

    def test_explicit_archive_member_cannot_return_other_local_attachment(self):
        module = fixtures.load_zot_module()
        parent = {'key': 'PARENT01', 'data': {'itemType': 'journalArticle', 'title': 'Paper'}}
        att = {'key': 'ATTACH01', 'data': {'itemType': 'attachment', 'contentType': 'application/pdf', 'filename': 'main.pdf'}}
        client = Namespace(search=lambda *_a, **_k: [parent], children=lambda _k: [att])
        args = Namespace(link=False, query='paper', index=None, local_storage=True, archive_member='supplement.pdf')
        with mock.patch.object(module, 'load_config', return_value={}), mock.patch.object(module, 'ZoteroClient', return_value=client), mock.patch.object(module, '_output'), mock.patch.object(module, '_find_local_attachment_pdf') as local:
            module.cmd_get(args)
        local.assert_not_called()

    def test_download_staging_cleanup_handles_only_owned_layout(self):
        module = fixtures.load_zot_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); owned = root / ('ATTACH01-' + 'a'*20); owned.mkdir()
            other = root / 'user-directory'; other.mkdir()
            for folder in (owned, other):
                pdf = folder / 'paper.pdf'; pdf.write_bytes(b'%PDF-example')
                os.utime(pdf, (time.time()-90000, time.time()-90000))
            with mock.patch.object(module, 'load_config', return_value={'staging_dir': tmp}), mock.patch('builtins.print'):
                module.cmd_clean_staging(Namespace())
            self.assertFalse(owned.exists())
            self.assertTrue((other/'paper.pdf').exists())


if __name__ == '__main__':
    unittest.main()
