from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
METADATA_PATH = REPO_ROOT / "canonical" / "runtime" / "skills" / "zotero" / "lib" / "metadata.py"
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


def load_metadata_module():
    spec = importlib.util.spec_from_file_location("canonical_zotero_metadata", METADATA_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class ZoteroMetadataTests(unittest.TestCase):
    def test_url_fetch_falls_back_to_translation_server_after_wsl_error(self) -> None:
        metadata_module = load_metadata_module()
        calls = []

        def fake_wsl(url, config):
            calls.append(("wsl", url))
            raise RuntimeError("wsl command not found")

        def fake_translation_server(lookup_url, translation_server):
            calls.append(("translation_server", lookup_url, translation_server))
            return {"itemType": "webpage", "title": "Translated URL"}

        metadata_module._fetch_url_via_wsl = fake_wsl
        metadata_module._fetch_via_translation_server = fake_translation_server

        metadata, input_type, normalized = metadata_module.fetch_metadata(
            "https://example.test/article",
            "http://localhost:1969",
            {},
        )

        self.assertEqual(input_type, "url")
        self.assertEqual(normalized, "https://example.test/article")
        self.assertEqual(metadata["title"], "Translated URL")
        self.assertEqual(metadata["_input_type"], "url")
        self.assertEqual(calls[0], ("wsl", "https://example.test/article"))
        self.assertEqual(
            calls[1],
            ("translation_server", "https://example.test/article", "http://localhost:1969"),
        )


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

    def test_upload_uses_basename_inside_webdav_zip(self) -> None:
        webdav = load_webdav_module()
        uploads = []

        class FakeResponse:
            status_code = 201

        def fake_request(method, url, auth=None, **kwargs):
            uploads.append({
                "method": method,
                "url": url,
                "data": kwargs.get("data"),
            })
            return FakeResponse()

        webdav.requests.request = fake_request
        client = webdav.WebDAVClient({
            "webdav_url": "https://dav.example",
            "webdav_user": "user",
            "WEBDAV_PASSWORD": "password",
        })

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4\n")
            path = f.name
        try:
            self.assertTrue(client.upload("ABC123", path, "/tmp/Expected_Name.pdf"))
            self.assertEqual(uploads[0]["method"], "PUT")
            self.assertEqual(uploads[0]["url"], "https://dav.example/zotero/ABC123.zip")
            with zipfile.ZipFile(io.BytesIO(uploads[0]["data"])) as zf:
                self.assertEqual(zf.namelist(), ["Expected_Name.pdf"])
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
