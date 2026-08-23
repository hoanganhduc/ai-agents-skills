from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
ZOTERO_ROOT = REPO_ROOT / "canonical" / "runtime" / "skills" / "zotero"
CONFIG_PATH = ZOTERO_ROOT / "lib" / "config.py"
DOWNLOADER_PATH = ZOTERO_ROOT / "lib" / "downloader.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _load_downloader():
    fake_lib = types.ModuleType("lib")
    fake_lib.__path__ = []
    fake_verifier = types.ModuleType("lib.verifier")
    fake_verifier.verify = lambda *_args, **_kwargs: {}
    missing = object()
    previous_modules = {
        "lib": sys.modules.get("lib", missing),
        "lib.verifier": sys.modules.get("lib.verifier", missing),
    }
    sys.modules["lib"] = fake_lib
    sys.modules["lib.verifier"] = fake_verifier
    try:
        return _load_module("zotero_secure_downloader", DOWNLOADER_PATH)
    finally:
        for name, previous in previous_modules.items():
            if previous is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class ZoteroSecretConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _load_module("zotero_secure_config", CONFIG_PATH)
        cls.downloader = _load_downloader()

    def _config_file(self, root: Path, **extra: str) -> Path:
        path = root / "config.json"
        path.write_text(
            json.dumps({"zotero_user_id": "12345", **extra}),
            encoding="utf-8",
        )
        return path

    def test_windows_direct_loader_allows_an_absent_optional_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            with mock.patch.object(self.config.os, "name", "nt"):
                self.assertIsNone(self.config._read_private_secret_bytes(missing))

    def test_windows_direct_loader_rejects_an_existing_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / "selected.json"
            selected.write_text("{}\n", encoding="utf-8")
            with mock.patch.object(self.config.os, "name", "nt"):
                with self.assertRaisesRegex(ValueError, "managed projection runner"):
                    self.config._read_private_secret_bytes(selected)

    @unittest.skipIf(os.name == "nt", "native Windows secret files require the managed projection runner")
    def test_environment_secret_overrides_and_removes_legacy_config_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._config_file(
                root,
                semantic_scholar_api_key="legacy-config-value",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "SEMANTIC_SCHOLAR_API_KEY": "restored-environment-value",
                    "AAS_RUNTIME_WORKSPACE": str(root / "workspace"),
                },
                clear=True,
            ):
                loaded = self.config.load_config(config_path=config_path)

            self.assertEqual(
                loaded["SEMANTIC_SCHOLAR_API_KEY"],
                "restored-environment-value",
            )
            self.assertNotIn("semantic_scholar_api_key", loaded)

    @unittest.skipIf(os.name == "nt", "native Windows secret files require the managed projection runner")
    def test_legacy_config_field_is_not_a_credential_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._config_file(
                root,
                semantic_scholar_api_key="must-not-be-authority",
            )
            with mock.patch.dict(
                os.environ,
                {"AAS_RUNTIME_WORKSPACE": str(root / "workspace")},
                clear=True,
            ):
                loaded = self.config.load_config(config_path=config_path)

            self.assertNotIn("semantic_scholar_api_key", loaded)
            self.assertNotIn("SEMANTIC_SCHOLAR_API_KEY", loaded)

    def test_downloader_uses_only_uppercase_secret_authority(self) -> None:
        response = mock.Mock(status_code=404)
        with mock.patch.object(
            self.downloader.requests,
            "get",
            return_value=response,
        ) as request:
            result = self.downloader._semantic_scholar(
                "/unused-staging",
                "10.1000/example",
                {
                    "SEMANTIC_SCHOLAR_API_KEY": "restored-environment-value",
                    "semantic_scholar_api_key": "legacy-config-value",
                },
            )

        self.assertIsNone(result)
        self.assertEqual(
            request.call_args.kwargs["headers"],
            {"x-api-key": "restored-environment-value"},
        )

    def test_non_secret_example_contains_no_semantic_scholar_key(self) -> None:
        example = json.loads((ZOTERO_ROOT / "config.json.example").read_text(encoding="utf-8"))
        self.assertNotIn("semantic_scholar_api_key", example)
        self.assertNotIn("SEMANTIC_SCHOLAR_API_KEY", example)

    @unittest.skipIf(os.name == "nt", "native Windows secret files require the managed projection runner")
    def test_private_exact_secret_projection_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._config_file(root)
            secrets = root / "zotero-secrets.json"
            secrets.write_text(
                json.dumps({"ZOTERO_API_KEY": "selected-api-key"}),
                encoding="utf-8",
            )
            secrets.chmod(0o600)
            with mock.patch.dict(os.environ, {
                "AAS_ZOTERO_SECRETS_FILE": str(secrets),
                "AAS_RUNTIME_WORKSPACE": str(root / "workspace"),
            }, clear=True):
                loaded = self.config.load_config(config_path=config_path)
        self.assertEqual(loaded["ZOTERO_API_KEY"], "selected-api-key")

    @unittest.skipIf(os.name == "nt", "POSIX descriptor and mode semantics")
    def test_secret_projection_rejects_symlink_hardlink_permissive_and_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._config_file(root)
            private = root / "private.json"
            private.write_text('{"ZOTERO_API_KEY":"safe-value"}', encoding="utf-8")
            private.chmod(0o600)
            symlink = root / "symlink.json"
            symlink.symlink_to(private)
            hardlink = root / "hardlink.json"
            os.link(private, hardlink)
            permissive = root / "permissive.json"
            permissive.write_text('{"ZOTERO_API_KEY":"unsafe-value"}', encoding="utf-8")
            permissive.chmod(0o644)
            oversized = root / "oversized.json"
            oversized.write_bytes(b"{" + b" " * (self.config.MAX_SECRETS_FILE_BYTES + 1) + b"}")
            oversized.chmod(0o600)

            for candidate in (symlink, hardlink, permissive, oversized):
                with self.subTest(candidate=candidate.name):
                    with mock.patch.dict(os.environ, {
                        "AAS_ZOTERO_SECRETS_FILE": str(candidate),
                        "AAS_RUNTIME_WORKSPACE": str(root / "workspace"),
                    }, clear=True):
                        with self.assertRaises(ValueError) as raised:
                            self.config.load_config(config_path=config_path)
                    self.assertNotIn(str(root), str(raised.exception))
                    self.assertNotIn("safe-value", str(raised.exception))

    @unittest.skipIf(os.name == "nt", "POSIX descriptor identity semantics")
    def test_secret_projection_replacement_between_stat_and_open_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._config_file(root)
            selected = root / "selected.json"
            selected.write_text('{"ZOTERO_API_KEY":"safe-value"}', encoding="utf-8")
            selected.chmod(0o600)
            saved = root / "saved.json"
            real_open = self.config.os.open
            replaced = False

            def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal replaced
                if dir_fd is not None and path == selected.name and not replaced:
                    replaced = True
                    selected.rename(saved)
                    selected.write_text(
                        '{"ZOTERO_API_KEY":"CANARY_REPLACEMENT"}', encoding="utf-8"
                    )
                    selected.chmod(0o600)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with mock.patch.dict(os.environ, {
                "AAS_ZOTERO_SECRETS_FILE": str(selected),
                "AAS_RUNTIME_WORKSPACE": str(root / "workspace"),
            }, clear=True):
                with mock.patch.object(self.config.os, "open", side_effect=swapping_open):
                    with self.assertRaises(ValueError) as raised:
                        self.config.load_config(config_path=config_path)

        self.assertTrue(replaced)
        self.assertNotIn("CANARY_REPLACEMENT", str(raised.exception))
        self.assertNotIn(str(root), str(raised.exception))

    def test_secret_projection_rejects_duplicate_unknown_and_nonstring_values(self) -> None:
        hostile = (
            '{"ZOTERO_API_KEY":"one","ZOTERO_API_KEY":"two"}',
            '{"UNSUPPORTED":"value"}',
            '{"ZOTERO_API_KEY":{"nested":"value"}}',
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._config_file(root)
            for index, payload in enumerate(hostile):
                selected = root / f"hostile-{index}.json"
                selected.write_text(payload, encoding="utf-8")
                selected.chmod(0o600)
                with self.subTest(payload=payload):
                    with mock.patch.dict(os.environ, {
                        "AAS_ZOTERO_SECRETS_FILE": str(selected),
                        "AAS_RUNTIME_WORKSPACE": str(root / "workspace"),
                    }, clear=True):
                        with self.assertRaises(ValueError):
                            self.config.load_config(config_path=config_path)


if __name__ == "__main__":
    unittest.main()
