from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WEBDAV_PATH = REPO_ROOT / "canonical" / "runtime" / "skills" / "zotero" / "lib" / "webdav.py"
_MISSING = object()


class _FakeAuth:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs


def _install_fake_requests():
    fake_requests = types.ModuleType("requests")
    fake_requests.request = lambda *args, **kwargs: None

    fake_auth = types.ModuleType("requests.auth")
    fake_auth.HTTPBasicAuth = _FakeAuth
    fake_auth.HTTPDigestAuth = _FakeAuth
    fake_requests.auth = fake_auth

    previous = {
        "requests": sys.modules.get("requests", _MISSING),
        "requests.auth": sys.modules.get("requests.auth", _MISSING),
    }
    sys.modules["requests"] = fake_requests
    sys.modules["requests.auth"] = fake_auth
    return previous


def _restore_modules(previous):
    for name, module in previous.items():
        if module is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def load_webdav_module():
    spec = importlib.util.spec_from_file_location("canonical_zotero_webdav", WEBDAV_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    previous_modules = _install_fake_requests()
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
        _restore_modules(previous_modules)
    return module


class ZoteroWebDAVMetadataTests(unittest.TestCase):
    def test_file_sync_metadata_uses_md5_and_mtime_milliseconds(self) -> None:
        webdav = load_webdav_module()
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"zotero attachment bytes")
            path = f.name
        try:
            os.utime(path, (1700000000.125, 1700000000.125))
            metadata = webdav.file_sync_metadata(path)
            self.assertEqual(metadata["md5"], "40c15acff47969ba6efd1f4ea12afd01")
            self.assertEqual(metadata["mtime"], 1700000000125)
        finally:
            os.remove(path)

    def test_populate_imported_file_attachment_preserves_existing_fields(self) -> None:
        webdav = load_webdav_module()
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"abc")
            path = f.name
        try:
            template = {
                "itemType": "attachment",
                "linkMode": "imported_file",
                "filename": "sample.pdf",
            }
            result = webdav.populate_imported_file_attachment(template, path)
            self.assertIs(result, template)
            self.assertEqual(result["filename"], "sample.pdf")
            self.assertEqual(result["md5"], "900150983cd24fb0d6963f7d28e17f72")
            self.assertIsInstance(result["mtime"], int)
            self.assertGreater(result["mtime"], 0)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
