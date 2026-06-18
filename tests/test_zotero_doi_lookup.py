from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_PATH = REPO_ROOT / "canonical" / "runtime" / "skills" / "zotero" / "lib" / "zotero_client.py"
_MISSING = object()


def _install_fake_pyzotero():
    fake_pyzotero = types.ModuleType("pyzotero")
    fake_zotero = types.ModuleType("pyzotero.zotero")
    fake_errors = types.ModuleType("pyzotero.zotero_errors")

    class FakeHTTPError(Exception):
        pass

    fake_zotero.Zotero = object
    fake_errors.HTTPError = FakeHTTPError
    fake_pyzotero.zotero = fake_zotero

    previous = {
        "pyzotero": sys.modules.get("pyzotero", _MISSING),
        "pyzotero.zotero": sys.modules.get("pyzotero.zotero", _MISSING),
        "pyzotero.zotero_errors": sys.modules.get("pyzotero.zotero_errors", _MISSING),
    }
    sys.modules["pyzotero"] = fake_pyzotero
    sys.modules["pyzotero.zotero"] = fake_zotero
    sys.modules["pyzotero.zotero_errors"] = fake_errors
    return previous


def _restore_modules(previous):
    for name, module in previous.items():
        if module is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def load_client_module():
    spec = importlib.util.spec_from_file_location("canonical_zotero_client", CLIENT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    previous_modules = _install_fake_pyzotero()
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
        _restore_modules(previous_modules)
    return module


class FakeZoteroApi:
    def __init__(self, *, query_results=None, scan_pages=None):
        self.query_results = query_results or {}
        self.scan_pages = scan_pages or {}
        self.calls = []

    def top(self, **kwargs):
        self.calls.append(kwargs)
        if "q" in kwargs:
            return self.query_results.get(kwargs["q"], [])
        return self.scan_pages.get(kwargs.get("start", 0), [])


class ZoteroDoiLookupTests(unittest.TestCase):
    def _client(self, fake_api):
        module = load_client_module()
        client = module.ZoteroClient.__new__(module.ZoteroClient)
        client.zot = fake_api
        client._max_retries = 0
        client._base_delay = 0
        return module, client

    def test_normalize_doi_query_accepts_url_and_trailing_period(self) -> None:
        module = load_client_module()

        self.assertEqual(
            module.normalize_doi_query("https://doi.org/10.1007/S00373-023-02644-W."),
            "10.1007/s00373-023-02644-w",
        )

    def test_search_falls_back_to_exact_doi_scan_when_zotero_q_misses_doi(self) -> None:
        item = {
            "key": "Y82TWAFR",
            "data": {
                "title": "On Reconfiguration Graphs of Independent Sets Under Token Sliding",
                "DOI": "10.1007/S00373-023-02644-W",
            },
        }
        fake_api = FakeZoteroApi(
            query_results={
                "10.1007/s00373-023-02644-w": [],
                "s00373-023-02644-w": [],
            },
            scan_pages={0: [item]},
        )
        _, client = self._client(fake_api)

        self.assertEqual(
            client.search("10.1007/s00373-023-02644-w"),
            [item],
        )
        self.assertIn({"q": "10.1007/s00373-023-02644-w", "qmode": "everything", "limit": 10}, fake_api.calls)
        self.assertIn({"limit": 100, "start": 0}, fake_api.calls)

    def test_title_search_stays_on_query_path(self) -> None:
        item = {
            "key": "KKA9MYWK",
            "data": {
                "title": "Gallai graphs and anti-Gallai graphs",
                "DOI": "10.1016/0012-365X(95)00109-A",
            },
        }
        fake_api = FakeZoteroApi(query_results={"Gallai graphs": [item]})
        _, client = self._client(fake_api)

        self.assertEqual(client.search("Gallai graphs", limit=3), [item])
        self.assertEqual(fake_api.calls, [{"q": "Gallai graphs", "limit": 3}])


if __name__ == "__main__":
    unittest.main()

