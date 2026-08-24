from __future__ import annotations

import importlib.util
import hashlib
import http.client
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests._slow_http import slow_http_server

REPO_ROOT = Path(__file__).resolve().parents[1]
GSP_ROOT = REPO_ROOT / "canonical" / "runtime" / "skills" / "getscipapers-requester"


def _load_module(name: str, filename: str):
    path = GSP_ROOT / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Do not write __pycache__ into the canonical runtime source tree; stray
    # .pyc files there are flagged as denied by the runtime inventory check.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _watch_record(**updates):
    record = {
        "id": "watch-one",
        "kind": "paper",
        "identifier_type": "doi",
        "identifier": "10.1234/example",
        "services": ["all"],
        "status": "active",
        "created_at": 1_700_000_000,
        "updated_at": 1_700_000_000,
        "deadline_ts": None,
        "sent_file_hashes": [],
        "check_count": 0,
    }
    record.update(updates)
    if "watch_key" not in updates:
        services = sorted(record["services"])
        identity = json.dumps(
            [
                record["kind"],
                record["identifier_type"],
                record["identifier"],
                services,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        record["watch_key"] = hashlib.sha1(identity.encode("utf-8")).hexdigest()
    return record


class GetSciPapersSetupTests(unittest.TestCase):
    def test_requirements_pins_fork_master_branch_by_url(self) -> None:
        text = (GSP_ROOT / "requirements.txt").read_text(encoding="utf-8")
        # The fork's default branch is master, and the distribution is named
        # getscipapers-hoanganhduc, so a PEP 508 ``getscipapers @`` name prefix
        # triggers a pip name-mismatch error. Install by URL only.
        self.assertIn(
            "git+https://github.com/hoanganhduc/getscipapers.git@master",
            text,
        )
        self.assertNotIn("getscipapers @ git+", text)

    def test_venv_python_branches_on_os(self) -> None:
        setup = _load_module("gsp_setup_under_test", "run_gsp_setup.py")
        venv = Path("/tmp/example-venv")
        with mock.patch.object(setup.os, "name", "posix"):
            self.assertEqual(setup._venv_python(venv), venv / "bin" / "python")
            self.assertEqual(setup._venv_getscipapers(venv), venv / "bin" / "getscipapers")
        with mock.patch.object(setup.os, "name", "nt"):
            self.assertEqual(setup._venv_python(venv), venv / "Scripts" / "python.exe")
            self.assertEqual(setup._venv_getscipapers(venv), venv / "Scripts" / "getscipapers.exe")

    def test_venv_dir_honors_env_override(self) -> None:
        setup = _load_module("gsp_setup_under_test", "run_gsp_setup.py")
        with mock.patch.dict(os.environ, {"GETSCIPAPERS_VENV": "/custom/gsp"}, clear=False):
            self.assertEqual(setup._venv_dir(), Path("/custom/gsp"))
        env = {k: v for k, v in os.environ.items() if k != "GETSCIPAPERS_VENV"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(setup._venv_dir().name, ".getscipapers_venv")

    def test_setup_creates_venv_then_installs_requirements(self) -> None:
        setup = _load_module("gsp_setup_under_test", "run_gsp_setup.py")
        calls: list[list[str]] = []

        def fake_run(argv, check=False, **kwargs):
            calls.append([str(a) for a in argv])

            class _Done:
                returncode = 0

            return _Done()

        with (
            mock.patch.object(setup.subprocess, "run", side_effect=fake_run),
            mock.patch.object(setup, "_venv_dir", return_value=Path("/tmp/gsp-venv")),
            mock.patch.object(setup, "_emit"),
        ):
            rc = setup.cmd_setup(mock.Mock())

        self.assertEqual(rc, 0)
        # First action creates the venv with the launching interpreter.
        self.assertEqual(calls[0][1:3], ["-m", "venv"])
        self.assertEqual(calls[0][3], str(Path("/tmp/gsp-venv")))
        # Final action installs the fork from the skill's requirements.txt.
        install = calls[-1]
        self.assertEqual(install[1:4], ["-m", "pip", "install"])
        self.assertIn("-r", install)
        self.assertTrue(install[-1].endswith("requirements.txt"))
        joined = " ".join(" ".join(call) for call in calls)
        self.assertIn("ensurepip", joined)


class GetSciPapersResolverTests(unittest.TestCase):
    def test_env_var_takes_priority(self) -> None:
        helper = _load_module("gsp_helper_under_test", "gsp_openclaw_helper.py")
        with (
            mock.patch.dict(os.environ, {"GETSCIPAPERS_BIN": "/opt/gsp/getscipapers"}, clear=False),
            mock.patch.object(helper.Path, "is_file", return_value=True),
            mock.patch.object(helper.os, "access", return_value=True),
        ):
            self.assertEqual(helper.find_getscipapers(), "/opt/gsp/getscipapers")

    def test_windows_scripts_candidate_resolves_without_x_ok(self) -> None:
        helper = _load_module("gsp_helper_under_test", "gsp_openclaw_helper.py")

        # Stub the module's Path so the resolver never instantiates a real
        # WindowsPath while os.name is forced to "nt": pathlib refuses to build
        # WindowsPath on a POSIX host, which would error this test on Linux/macOS.
        class _FakePath:
            def __init__(self, raw: object) -> None:
                self._p = str(raw)

            def __truediv__(self, other: object) -> "_FakePath":
                return _FakePath(f"{self._p}/{other}")

            def __str__(self) -> str:
                return self._p

            def is_file(self) -> bool:
                # Only the venv Scripts/*.exe candidate "exists".
                return self._p.endswith("getscipapers.exe") and ".getscipapers_venv" in self._p

            @classmethod
            def home(cls) -> "_FakePath":
                return _FakePath("/fake/home")

        env = {k: v for k, v in os.environ.items() if k != "GETSCIPAPERS_BIN"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(helper, "Path", _FakePath),
            mock.patch.object(helper.os, "name", "nt"),
            mock.patch.object(helper.shutil, "which", return_value=None),
            # X_OK must be skipped on Windows; force it False to prove it is not consulted.
            mock.patch.object(helper.os, "access", return_value=False),
        ):
            resolved = helper.find_getscipapers()

        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.endswith("getscipapers.exe"))
        self.assertIn(".getscipapers_venv", resolved)

    def test_missing_everywhere_returns_none(self) -> None:
        helper = _load_module("gsp_helper_under_test", "gsp_openclaw_helper.py")
        env = {k: v for k, v in os.environ.items() if k != "GETSCIPAPERS_BIN"}
        env.setdefault("HOME", str(Path.home()))
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(helper.shutil, "which", return_value=None),
            mock.patch.object(helper.Path, "is_file", return_value=False),
        ):
            self.assertIsNone(helper.find_getscipapers())


class GetSciPapersRunnerTests(unittest.TestCase):
    def test_getpapers_defaults_to_no_proxy(self) -> None:
        helper = _load_module("gsp_helper_under_test", "gsp_openclaw_helper.py")
        apply = helper._apply_runner_proxy_default
        # getpapers gets --no-proxy appended (a stale proxy breaks doi.org resolution).
        self.assertEqual(
            apply(["getpapers", "--doi", "10.1/x"]),
            ["getpapers", "--doi", "10.1/x", "--no-proxy"],
        )
        # An explicit proxy flag is respected, not overridden.
        for flag in ("--proxy", "--no-proxy", "--auto-proxy"):
            self.assertEqual(
                apply(["getpapers", "--doi", "10.1/x", flag]),
                ["getpapers", "--doi", "10.1/x", flag],
            )
        # Other modules and empty argv are untouched.
        self.assertEqual(apply(["zlib", "--search", "x"]), ["zlib", "--search", "x"])
        self.assertEqual(apply([]), [])


class _MetadataResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_length: str | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self.offset = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def getcode(self) -> int:
        return self.status

    def read(self, size: int) -> bytes:
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


class _MetadataOpener:
    def __init__(self, response: _MetadataResponse) -> None:
        self.response = response
        self.requests = []

    def open(self, request, timeout: int):
        self.requests.append((request, timeout))
        return self.response


class GetSciPapersMetadataTests(unittest.TestCase):
    def test_metadata_http_refuses_redirects_and_cross_origin_urls(self) -> None:
        helper = _load_module("gsp_helper_metadata_origin_test", "gsp_openclaw_helper.py")
        redirected = _MetadataResponse(b"{}", status=302)
        opener = _MetadataOpener(redirected)
        handlers = []

        def fake_build(*values):
            handlers.extend(values)
            return opener

        with (
            mock.patch.object(helper, "build_opener", side_effect=fake_build),
            self.assertRaises(helper.MetadataSourceError),
        ):
            helper._http_json_bytes_in_process(
                "https://api.crossref.org/works?q=test",
                expected_origin=helper.CROSSREF_ORIGIN,
            )

        self.assertTrue(redirected.closed)
        self.assertEqual(len(opener.requests), 1)
        self.assertTrue(
            any(isinstance(value, helper._NoRedirectHandler) for value in handlers)
        )

        never_opened = _MetadataOpener(_MetadataResponse(b"{}"))
        with self.assertRaises(helper.MetadataSourceError):
            helper.http_json(
                "https://example.invalid/redirect-target",
                expected_origin=helper.CROSSREF_ORIGIN,
                opener=never_opened,
            )
        self.assertEqual(never_opened.requests, [])

    def test_metadata_http_bounds_declared_and_streamed_bodies(self) -> None:
        helper = _load_module("gsp_helper_metadata_cap_test", "gsp_openclaw_helper.py")
        responses = (
            _MetadataResponse(b"{}", content_length="6"),
            _MetadataResponse(b"123456"),
        )
        for response in responses:
            with self.subTest(response=response), self.assertRaises(helper.MetadataSourceError):
                helper.http_json(
                    "https://api.crossref.org/works?q=test",
                    expected_origin=helper.CROSSREF_ORIGIN,
                    opener=_MetadataOpener(response),
                    max_bytes=5,
                )
            self.assertTrue(response.closed)

        valid = _MetadataResponse(b'{"ok":true}', content_length="11")
        self.assertEqual(
            helper.http_json(
                "https://api.crossref.org/works?q=test",
                expected_origin=helper.CROSSREF_ORIGIN,
                opener=_MetadataOpener(valid),
                max_bytes=11,
            ),
            {"ok": True},
        )
        self.assertTrue(valid.closed)

    def test_metadata_http_rejects_short_declared_valid_json_body(self) -> None:
        helper = _load_module(
            "gsp_helper_metadata_truncated_framing_test",
            "gsp_openclaw_helper.py",
        )

        class TruncatedResponse(_MetadataResponse):
            def read1(self, size: int) -> bytes:
                return super().read(size)

            def read(self, size: int) -> bytes:
                partial = super().read(size)
                raise http.client.IncompleteRead(partial, 100 - len(partial))

        response = TruncatedResponse(
            b'{"message":{"items":[]}}',
            content_length="100",
        )
        with self.assertRaisesRegex(
            helper.MetadataSourceError,
            "framing is incomplete",
        ):
            helper.http_json(
                "https://api.crossref.org/works?q=test",
                expected_origin=helper.CROSSREF_ORIGIN,
                opener=_MetadataOpener(response),
            )

        self.assertTrue(response.closed)

    def test_metadata_worker_isolates_short_declared_valid_json_body(self) -> None:
        helper = _load_module(
            "gsp_helper_metadata_worker_truncated_framing_test",
            "gsp_openclaw_helper.py",
        )
        body = b'{"message":{"items":[]}}'
        with slow_http_server(
            body=body,
            drip_seconds=0,
            content_length=len(body) + 10,
        ) as url:
            parsed = helper.urlsplit(url)
            expected_origin = ("http", parsed.hostname, parsed.port)
            with self.assertRaisesRegex(
                helper.MetadataSourceError,
                "framing is incomplete",
            ):
                helper.http_json(
                    url,
                    timeout=2,
                    expected_origin=expected_origin,
                    deadline_seconds=2,
                )

    def test_metadata_slow_drip_deadline_closes_the_response(self) -> None:
        helper = _load_module(
            "gsp_helper_metadata_deadline_test",
            "gsp_openclaw_helper.py",
        )
        with slow_http_server(body=b"{" + b" " * 99, drip_seconds=0.1) as url:
            parsed = helper.urlsplit(url)
            expected_origin = ("http", parsed.hostname, parsed.port)
            started = time.monotonic()
            with self.assertRaisesRegex(
                helper.MetadataSourceError,
                "wall-clock deadline",
            ):
                helper.http_json(
                    url,
                    timeout=2,
                    expected_origin=expected_origin,
                    deadline_seconds=0.6,
                )
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 3.0)

    def test_metadata_http_rejects_nonstandard_nonfinite_json_numbers(self) -> None:
        helper = _load_module("gsp_helper_metadata_json_number_test", "gsp_openclaw_helper.py")
        for constant in ("NaN", "Infinity", "-Infinity"):
            response = _MetadataResponse(
                f'{{"score":{constant}}}'.encode("ascii")
            )
            with self.subTest(constant=constant), self.assertRaises(
                helper.MetadataSourceError
            ):
                helper.http_json(
                    "https://api.crossref.org/works?q=test",
                    expected_origin=helper.CROSSREF_ORIGIN,
                    opener=_MetadataOpener(response),
                )
            self.assertTrue(response.closed)

    def test_metadata_searches_bound_overreturn_and_tolerate_bad_shapes(self) -> None:
        helper = _load_module("gsp_helper_metadata_results_test", "gsp_openclaw_helper.py")
        crossref = {
            "message": {
                "items": [
                    {"DOI": f"10.1234/{index}", "title": [f"Paper {index}"]}
                    for index in range(10)
                ]
            }
        }
        google = {
            "items": [
                {
                    "volumeInfo": {
                        "title": f"Book {index}",
                        "industryIdentifiers": [
                            {"type": "ISBN_13", "identifier": "9780262046305"}
                        ],
                    }
                }
                for index in range(10)
            ]
        }
        openlibrary = {
            "docs": [
                {"title": f"Open book {index}", "isbn": ["9780262046305"]}
                for index in range(10)
            ]
        }
        with mock.patch.object(
            helper,
            "http_json",
            side_effect=[crossref, google, openlibrary],
        ) as request_json:
            results = (
                helper.search_crossref("bounded", limit=2),
                helper.search_google_books("bounded", limit=2),
                helper.search_openlibrary("bounded", limit=2),
            )

        self.assertEqual([len(value) for value in results], [2, 2, 2])
        self.assertEqual(
            [call.kwargs["expected_origin"] for call in request_json.call_args_list],
            [
                helper.CROSSREF_ORIGIN,
                helper.GOOGLE_BOOKS_ORIGIN,
                helper.OPENLIBRARY_ORIGIN,
            ],
        )
        with mock.patch.object(helper, "http_json", return_value={"items": "bad"}):
            self.assertEqual(helper.search_google_books("shape"), [])

    def test_metadata_candidates_require_valid_identifiers_and_scalars(self) -> None:
        helper = _load_module("gsp_helper_metadata_candidate_test", "gsp_openclaw_helper.py")
        crossref_payload = {
            "message": {
                "items": [
                    {
                        "DOI": "not-a-doi",
                        "title": ["Exact title"],
                        "score": 100,
                    },
                    {
                        "DOI": "10.1234/valid",
                        "title": ["Exact title"],
                        "issued": {"date-parts": [["2025"]]},
                        "type": {"not": "text"},
                        "score": math.nan,
                    },
                ]
            }
        }
        with mock.patch.object(helper, "http_json", return_value=crossref_payload):
            papers = helper.search_crossref("Exact title")
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["doi"], "10.1234/valid")
        self.assertIsNone(papers[0]["year"])
        self.assertEqual(papers[0]["type"], "")
        self.assertIsNone(papers[0]["score"])

        google_payload = {
            "items": [{
                "volumeInfo": {
                    "title": "Exact book",
                    "industryIdentifiers": [
                        {
                            "type": "ISBN_13",
                            "identifier": "978-0-306-40615-7evil",
                        }
                    ],
                }
            }]
        }
        openlibrary_payload = {
            "docs": [{
                "title": "Exact book",
                "isbn": ["garbage/9780💣306406157/trailer"],
            }]
        }
        with mock.patch.object(helper, "http_json", return_value=google_payload):
            self.assertEqual(helper.search_google_books("Exact book"), [])
        with mock.patch.object(helper, "http_json", return_value=openlibrary_payload):
            self.assertEqual(helper.search_openlibrary("Exact book"), [])

    def test_remote_metadata_text_is_normalized_before_candidate_output(self) -> None:
        helper = _load_module("gsp_helper_metadata_text_test", "gsp_openclaw_helper.py")
        self.assertEqual(
            helper._metadata_text("&lt;b&gt;Ｆｕｌｌ&lt;/b&gt;\n\u202eＷｉｄｔｈ\x00"),
            "Full Width",
        )

        crossref_payload = {
            "message": {
                "items": [{
                    "DOI": "10.1234/safe",
                    "title": ["&lt;b&gt;Ｆｕｌｌ&lt;/b&gt;\n\u202eＷｉｄｔｈ"],
                    "container-title": ["<i>Journal</i>\r\nName"],
                    "author": [{
                        "given": "<em>Ａｄａ</em>",
                        "family": "\u202eＬｏｖｅｌａｃｅ\x00",
                    }],
                    "issued": {"date-parts": [[2025]]},
                    "type": "<b>journal-article</b>\n",
                    "score": 100,
                }]
            }
        }
        with mock.patch.object(helper, "http_json", return_value=crossref_payload):
            papers = helper.search_crossref("Full Width")
        self.assertEqual(papers[0]["title"], "Full Width")
        self.assertEqual(papers[0]["container"], "Journal Name")
        self.assertEqual(papers[0]["authors"], ["Ada Lovelace"])
        self.assertEqual(papers[0]["type"], "journal-article")

        google_payload = {
            "items": [{
                "volumeInfo": {
                    "title": "<b>Ｂｏｏｋ</b>\nOne",
                    "authors": ["<i>Ａｕｔｈｏｒ</i>\u202e One"],
                    "publisher": "&lt;em&gt;Ｐｒｅｓｓ&lt;/em&gt;\x00 House",
                    "publishedDate": "２０２５\n",
                    "industryIdentifiers": [{
                        "type": "ISBN_13",
                        "identifier": "9780262046305",
                    }],
                }
            }]
        }
        with mock.patch.object(helper, "http_json", return_value=google_payload):
            books = helper.search_google_books("Book One")
        self.assertEqual(books[0]["title"], "Book One")
        self.assertEqual(books[0]["authors"], ["Author One"])
        self.assertEqual(books[0]["publisher"], "Press House")
        self.assertEqual(books[0]["publishedDate"], "2025")

        openlibrary_payload = {
            "docs": [{
                "title": "&lt;b&gt;Ｏｐｅｎ&lt;/b&gt;\nBook",
                "author_name": ["<i>Ａｕｔｈｏｒ</i>\u202e Two"],
                "publisher": ["<em>Ｏｐｅｎ</em>\x00 Press"],
                "first_publish_year": 2024,
                "isbn": ["9780262046305"],
            }]
        }
        with mock.patch.object(helper, "http_json", return_value=openlibrary_payload):
            open_books = helper.search_openlibrary("Open Book")
        self.assertEqual(open_books[0]["title"], "Open Book")
        self.assertEqual(open_books[0]["authors"], ["Author Two"])
        self.assertEqual(open_books[0]["publisher"], "Open Press")

    def test_resolution_filters_mocked_invalid_identifiers_and_nonfinite_scores(self) -> None:
        helper = _load_module("gsp_helper_metadata_resolution_test", "gsp_openclaw_helper.py")
        invalid_paper = {
            "doi": "not-a-doi",
            "title": "Exact title",
            "authors": [],
            "year": 2025,
            "type": "article",
            "score": 100,
        }
        with mock.patch.object(helper, "search_crossref", return_value=[invalid_paper]):
            paper = helper.choose_best_identifier("paper", "Exact title")
        self.assertIsNone(paper["selected"])
        self.assertEqual(paper["ranked_identifiers"], [])

        invalid_book = {
            "title": "Exact book",
            "authors": [],
            "isbn": [""],
        }
        with (
            mock.patch.object(helper, "search_google_books", return_value=[invalid_book]),
            mock.patch.object(helper, "search_openlibrary", return_value=[]),
        ):
            book = helper.choose_best_identifier("book", "Exact book")
        self.assertIsNone(book["selected"])
        self.assertEqual(book["ranked_identifiers"], [])

        finite_candidate = {
            **invalid_paper,
            "doi": "10.1234/finite",
            "score": math.nan,
        }
        with mock.patch.object(
            helper,
            "search_crossref",
            return_value=[finite_candidate],
        ):
            finite = helper.choose_best_identifier("paper", "Exact title")
        self.assertTrue(math.isfinite(finite["ranked_identifiers"][0]["score"]))
        json.dumps(finite, allow_nan=False)


class GetSciPapersManifestBoundsTests(unittest.TestCase):
    @staticmethod
    def _settings(helper, root: Path):
        return helper.Settings(
            download_dir=root / "downloads",
            state_dir=root / "state",
            manifest_dir=root / "manifests",
            telegram_max_bytes=1024,
        )

    def test_manifest_rejects_oversized_stdin_and_regular_file_before_writing(self) -> None:
        helper = _load_module("gsp_helper_manifest_source_cap_test", "gsp_openclaw_helper.py")
        oversized = "x" * (helper.MAX_TEXT_SOURCE_BYTES + 1)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = self._settings(helper, root)
            source_file = root / "oversized.txt"
            source_file.write_text(oversized, encoding="utf-8")

            with (
                mock.patch.object(helper.sys, "stdin", io.StringIO(oversized)),
                self.assertRaisesRegex(ValueError, "stdin source exceeds"),
            ):
                helper.build_manifest("paper", "-", settings)
            with self.assertRaisesRegex(ValueError, "source file exceeds"):
                helper.build_manifest("paper", str(source_file), settings)

            self.assertFalse(settings.manifest_dir.exists())

    def test_doi_grammar_is_ascii_and_case_canonical(self) -> None:
        helper = _load_module(
            "gsp_helper_manifest_doi_ascii_test",
            "gsp_openclaw_helper.py",
        )
        self.assertEqual(helper.valid_doi("10.1234/ABC"), "10.1234/abc")
        self.assertEqual(
            helper.valid_doi("10.48550/ARXIV.1706.03762v7"),
            "10.48550/arXiv.1706.03762",
        )
        for pseudo_doi in (
            "10.1234/xİ",
            "10.1234/xı",
            "10.1234/xſ",
            "10.1234/xK",
        ):
            with self.subTest(pseudo_doi=pseudo_doi):
                self.assertEqual(helper.valid_doi(pseudo_doi), "")
                self.assertEqual(helper.extract_dois(pseudo_doi), [])

    def test_structured_doi_validation_preserves_valid_suffix_characters(self) -> None:
        helper = _load_module(
            "gsp_helper_manifest_doi_suffix_test",
            "gsp_openclaw_helper.py",
        )
        for suffix in (";", ".", ")"):
            expected = f"10.1234/x{suffix}"
            with self.subTest(suffix=suffix):
                self.assertEqual(helper.valid_doi(expected), expected)
                self.assertEqual(helper.extract_dois(expected), [expected])
                candidate = helper._sanitize_resolver_candidate({
                    "doi": expected,
                    "title": "Exact remote paper",
                }, "paper")
                self.assertIsNotNone(candidate)
                self.assertEqual(candidate["doi"], expected)

        self.assertEqual(
            helper.extract_dois("Read 10.1234/x; then continue."),
            ["10.1234/x"],
        )

    def test_isbn_grammar_rejects_garbage_and_unicode_digit_aliases(self) -> None:
        helper = _load_module(
            "gsp_helper_manifest_isbn_ascii_test",
            "gsp_openclaw_helper.py",
        )
        self.assertEqual(helper.valid_isbn("9780306406157"), "9780306406157")
        self.assertEqual(helper.valid_isbn("978-0-306-40615-7"), "9780306406157")
        self.assertEqual(helper.valid_isbn("0 306 40615 2"), "0306406152")
        for malformed in (
            "978-0-306-40615-7evil",
            "garbage/9780💣306406157/trailer",
            "9780A306406157",
            "9780١306406157",
        ):
            with self.subTest(malformed=malformed):
                self.assertEqual(helper.valid_isbn(malformed), "")
                self.assertEqual(helper.extract_isbns(malformed), [])

        self.assertIsNone(helper._sanitize_resolver_candidate({
            "title": "Malformed remote book",
            "isbn": ["garbage/9780💣306406157/trailer"],
        }, "book"))
        accepted = helper._sanitize_resolver_candidate({
            "title": "Valid remote book",
            "isbn": ["978-0-306-40615-7"],
        }, "book")
        self.assertIsNotNone(accepted)
        self.assertEqual(accepted["isbn"], ["9780306406157"])

    def test_manifest_rejects_too_many_lines_and_items_before_resolution(self) -> None:
        helper = _load_module("gsp_helper_manifest_count_cap_test", "gsp_openclaw_helper.py")
        with tempfile.TemporaryDirectory() as raw:
            settings = self._settings(helper, Path(raw))
            too_many_lines = "\n".join(
                f"10.1234/line-{index}" for index in range(helper.MAX_MANIFEST_LINES + 1)
            )
            with self.assertRaisesRegex(ValueError, "line limit"):
                helper.build_manifest("paper", too_many_lines, settings)

            too_many_items = "\n".join(
                f"10.1234/a-{index} 10.1234/b-{index}"
                for index in range((helper.MAX_MANIFEST_ITEMS // 2) + 1)
            )
            with self.assertRaisesRegex(ValueError, "item limit"):
                helper.build_manifest("paper", too_many_items, settings)

            self.assertFalse(settings.manifest_dir.exists())

    def test_manifest_accepts_the_bridge_maximum_identifier_batch(self) -> None:
        helper = _load_module("gsp_helper_manifest_bridge_capacity_test", "gsp_openclaw_helper.py")
        source = "\n".join(
            f"10.1234/bridge-{index}" for index in range(helper.MAX_MANIFEST_ITEMS)
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result = helper.build_manifest("paper", source, self._settings(helper, root))

            self.assertEqual(result["counts"]["total_items"], helper.MAX_MANIFEST_ITEMS)
            self.assertLessEqual(
                Path(result["manifest_path"]).stat().st_size,
                4 * 1024 * 1024,
            )

    def test_overlong_inline_title_is_not_probed_as_a_filesystem_path(self) -> None:
        helper = _load_module("gsp_helper_manifest_long_inline_test", "gsp_openclaw_helper.py")
        with tempfile.TemporaryDirectory() as raw:
            settings = self._settings(helper, Path(raw))
            with self.assertRaisesRegex(ValueError, "source line 1 exceeds"):
                helper.build_manifest("paper", "x" * 3_000, settings)
            self.assertFalse(settings.manifest_dir.exists())

    def test_manifest_rejects_symlink_sources(self) -> None:
        helper = _load_module("gsp_helper_manifest_symlink_source_test", "gsp_openclaw_helper.py")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "source.txt"
            target.write_text("10.1234/example\n", encoding="utf-8")
            source = root / "source-link.txt"
            source.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "regular, non-symlink"):
                helper.build_manifest("paper", str(source), self._settings(helper, root))

    def test_resolvers_never_reinterpret_literal_queries_as_local_files(self) -> None:
        helper = _load_module("gsp_helper_literal_query_test", "gsp_openclaw_helper.py")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            sentinel = root / "credential.txt"
            sentinel.write_text("DO-NOT-SEND\n", encoding="utf-8")
            captured = []

            def capture(query, limit=helper.MAX_METADATA_RESULTS):
                del limit
                captured.append(query)
                return []

            with mock.patch.object(helper, "search_crossref", side_effect=capture):
                helper.resolve_auto("paper", str(sentinel))

                manifest_source = root / "manifest-input.txt"
                manifest_source.write_text(str(sentinel) + "\n", encoding="utf-8")
                helper.build_manifest(
                    "paper",
                    str(manifest_source),
                    self._settings(helper, root),
                )

            self.assertEqual(captured, [str(sentinel), str(sentinel)])
            self.assertNotIn("DO-NOT-SEND", captured)

    def test_manifest_never_publishes_a_mocked_invalid_resolution(self) -> None:
        helper = _load_module("gsp_helper_manifest_identifier_test", "gsp_openclaw_helper.py")
        invalid_selections = (
            {"identifier_type": "doi", "identifier": "not-a-doi"},
            {"identifier_type": "isbn", "identifier": "9780000000000"},
        )
        for selected in invalid_selections:
            with self.subTest(selected=selected), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                resolution = {
                    "selected": {
                        **selected,
                        "confidence": "very_high",
                        "score": math.nan,
                    },
                    "selection_status": "auto",
                }
                with mock.patch.object(
                    helper,
                    "choose_best_identifier",
                    return_value=resolution,
                ):
                    manifest = helper.build_manifest(
                        "auto",
                        "A title needing resolution",
                        self._settings(helper, root),
                    )

                self.assertEqual(len(manifest["items"]), 1)
                self.assertIsNone(manifest["items"][0]["identifier"])
                self.assertEqual(manifest["items"][0]["status"], "auto")
                json.dumps(manifest, allow_nan=False)

    def test_manifest_recleans_selected_and_ranked_metadata_before_publish(self) -> None:
        helper = _load_module("gsp_helper_manifest_metadata_test", "gsp_openclaw_helper.py")
        selected = {
            "identifier_type": "doi",
            "identifier": "10.1234/safe",
            "source": "<b>crossref</b>\n",
            "score": 0.9,
            "confidence": "high",
            "title": "&lt;i&gt;Ｓａｆｅ&lt;/i&gt;\n\u202e Title",
            "authors": ["<em>Ａｄａ</em>\x00 Lovelace"],
            "container": "<b>Journal</b>\r\nName",
            "publisher": "<i>Ｐｒｅｓｓ</i>\u202e House",
            "type": "<b>article</b>",
            "year": "２０２５\n",
            "untrusted_extra": {"html": "<script>ignored</script>"},
        }
        ranked = {
            "identifier_type": "isbn",
            "identifier": "9780262046305",
            "source": "<b>google_books</b>",
            "score": 0.7,
            "confidence": "medium",
            "title": "<b>Ｂｏｏｋ</b>\nTitle",
            "authors": ["\u202eＡｕｔｈｏｒ\x00 One"],
            "publisher": "&lt;i&gt;Ｐｒｅｓｓ&lt;/i&gt;\r\nHouse",
            "publishedDate": "２０２４\n",
        }
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with mock.patch.object(
                helper,
                "choose_best_identifier",
                side_effect=[
                    {"selected": selected, "selection_status": "auto"},
                    {
                        "selected": None,
                        "selection_status": "ambiguous",
                        "ranked_identifiers": [ranked],
                    },
                ],
            ):
                manifest = helper.build_manifest(
                    "auto",
                    "Selected metadata title\nAmbiguous metadata title",
                    self._settings(helper, root),
                )

            resolution = manifest["items"][0]["resolution"]
            self.assertEqual(resolution["source"], "crossref")
            self.assertEqual(resolution["title"], "Safe Title")
            self.assertEqual(resolution["authors"], ["Ada Lovelace"])
            self.assertEqual(resolution["container"], "Journal Name")
            self.assertEqual(resolution["publisher"], "Press House")
            self.assertEqual(resolution["type"], "article")
            self.assertEqual(resolution["year"], "2025")
            self.assertNotIn("untrusted_extra", resolution)

            ranked_output = manifest["items"][1]["ranked_identifiers"][0]
            self.assertEqual(ranked_output["source"], "google_books")
            self.assertEqual(ranked_output["title"], "Book Title")
            self.assertEqual(ranked_output["authors"], ["Author One"])
            self.assertEqual(ranked_output["publisher"], "Press House")
            self.assertEqual(ranked_output["publishedDate"], "2024")

            persisted = json.loads(
                Path(manifest["manifest_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(persisted["items"], manifest["items"])
            output_text = json.dumps(persisted["items"], ensure_ascii=False)
            self.assertNotIn("<", output_text)
            self.assertNotIn("\u202e", output_text)
            self.assertFalse(any(
                char in "\r\n\x00" for char in output_text
            ))


class GetSciPapersWatchStoreTests(unittest.TestCase):
    def test_windows_reparse_points_are_rejected_by_strict_config_admission(self) -> None:
        helper = _load_module(
            "gsp_helper_windows_reparse_test",
            "gsp_openclaw_helper.py",
        )
        reparse = SimpleNamespace(
            st_mode=helper.stat.S_IFREG,
            st_file_attributes=0x400,
            st_size=2,
        )
        ordinary = SimpleNamespace(st_mode=helper.stat.S_IFREG)

        self.assertTrue(helper._is_link_like_stat(reparse))
        self.assertFalse(helper._is_link_like_stat(ordinary))
        with mock.patch.object(
            helper.os,
            "lstat",
            return_value=reparse,
        ), self.assertRaisesRegex(SystemExit, "config is unsafe"):
            helper._load_settings_data(
                Path("junction-config.json"),
                explicit=True,
                strict=True,
            )

        settings = helper.Settings(
            download_dir=Path("downloads"),
            state_dir=Path("junction-state"),
            manifest_dir=Path("manifests"),
            telegram_max_bytes=1,
        )
        with mock.patch.object(
            helper.os,
            "lstat",
            return_value=SimpleNamespace(
                st_mode=helper.stat.S_IFDIR,
                st_file_attributes=0x400,
            ),
        ), self.assertRaisesRegex(SystemExit, "configured getscipapers storage"):
            helper.ensure_settings_dirs(
                settings,
                allow_fallback=False,
                required=("state_dir",),
            )

    def test_configured_storage_symlinks_are_never_followed(self) -> None:
        helper = _load_module(
            "gsp_helper_storage_symlink_test",
            "gsp_openclaw_helper.py",
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for label, live in (("live", True), ("broken", False)):
                with self.subTest(case=label):
                    target = root / f"target-{label}"
                    if live:
                        target.mkdir()
                        sentinel = target / "sentinel.txt"
                        sentinel.write_bytes(b"keep\n")
                    state_link = root / f"state-{label}"
                    state_link.symlink_to(target, target_is_directory=True)
                    settings = helper.Settings(
                        download_dir=root / f"downloads-{label}",
                        state_dir=state_link,
                        manifest_dir=root / f"manifests-{label}",
                        telegram_max_bytes=1,
                    )

                    with self.assertRaisesRegex(
                        SystemExit,
                        "configured getscipapers storage",
                    ):
                        helper.ensure_settings_dirs(
                            settings,
                            allow_fallback=False,
                            required=("state_dir",),
                        )

                    self.assertTrue(state_link.is_symlink())
                    if live:
                        self.assertEqual(
                            {path.name: path.read_bytes() for path in target.iterdir()},
                            {"sentinel.txt": b"keep\n"},
                        )
                    else:
                        self.assertFalse(target.exists())

    def test_fallback_storage_root_symlink_is_never_followed(self) -> None:
        helper = _load_module(
            "gsp_helper_fallback_storage_symlink_test",
            "gsp_openclaw_helper.py",
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            unavailable = root / "configured-state"
            unavailable.write_text("not a directory\n", encoding="utf-8")
            outside = root / "outside-fallback"
            outside.mkdir()
            sentinel = outside / "sentinel.txt"
            sentinel.write_bytes(b"keep\n")
            fallback_root = root / "fallback-root"
            fallback_root.symlink_to(outside, target_is_directory=True)
            settings = helper.Settings(
                download_dir=root / "downloads",
                state_dir=unavailable,
                manifest_dir=root / "manifests",
                telegram_max_bytes=1,
            )

            with mock.patch.dict(
                helper.os.environ,
                {"GETSCIPAPERS_FALLBACK_ROOT": str(fallback_root)},
            ), self.assertRaisesRegex(OSError, "fallback storage root is unsafe"):
                helper.ensure_settings_dirs(
                    settings,
                    allow_fallback=True,
                    required=("state_dir",),
                )

            self.assertTrue(fallback_root.is_symlink())
            self.assertEqual(
                {path.name: path.read_bytes() for path in outside.iterdir()},
                {"sentinel.txt": b"keep\n"},
            )

    def test_configless_wrapper_uses_default_storage_but_explicit_missing_config_fails(self) -> None:
        wrapper = GSP_ROOT / "run_gsp_helper.sh"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            env = {
                key: value
                for key, value in os.environ.items()
                if key != "GETSCIPAPERS_SKILL_CONFIG"
            }
            env.update({
                "AAS_RUNTIME_WORKSPACE": str(root),
                "PYTHONDONTWRITEBYTECODE": "1",
            })
            command = [
                "bash",
                str(wrapper),
                "make-manifest",
                "paper",
                "10.1234/configless",
            ]

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            manifest = Path(payload["manifest_path"])
            self.assertTrue(manifest.is_file())
            self.assertTrue(manifest.is_relative_to(root))

            explicit_env = dict(
                env,
                GETSCIPAPERS_SKILL_CONFIG=str(root / "missing-config.json"),
            )
            failed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=explicit_env,
                timeout=10,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("explicit getscipapers config is missing", failed.stderr)

    def test_default_config_symlinks_fail_before_watch_mutation(self) -> None:
        wrapper = GSP_ROOT / "run_gsp_helper.sh"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state_dir = root / "data" / "research" / "getscipapers_bot" / "state"
            state_dir.mkdir(parents=True)
            config = state_dir / "config.json"
            env = {
                key: value
                for key, value in os.environ.items()
                if key != "GETSCIPAPERS_SKILL_CONFIG"
            }
            env.update({
                "AAS_RUNTIME_WORKSPACE": str(root),
                "PYTHONDONTWRITEBYTECODE": "1",
            })
            command = [
                "bash",
                str(wrapper),
                "create-watch",
                "--kind",
                "paper",
                "--label=config-symlink",
                "--identifier-type",
                "doi",
                "--identifier",
                "10.1234/config-symlink",
                "--services",
                "all",
            ]
            cases = (
                ("broken", root / "missing-config.json", None),
                ("live", root / "real-config.json", b"{}"),
            )

            for label, target, content in cases:
                with self.subTest(label=label):
                    if content is not None:
                        target.write_bytes(content)
                    config.symlink_to(target)
                    original_target = os.readlink(config)

                    result = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=env,
                        timeout=10,
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("config is unsafe or oversized", result.stderr)
                    self.assertTrue(config.is_symlink())
                    self.assertEqual(os.readlink(config), original_target)
                    self.assertFalse((state_dir / "watches.json").exists())
                    if content is None:
                        self.assertFalse(target.exists())
                    else:
                        self.assertEqual(target.read_bytes(), content)
                    config.unlink()

    def test_strict_config_rejects_nonfinite_constants_before_durable_writes(self) -> None:
        helper_script = GSP_ROOT / "gsp_openclaw_helper.py"
        commands = (
            ["make-manifest", "paper", "10.1234/config-poison"],
            [
                "create-watch",
                "--kind",
                "paper",
                "--label=config-poison",
                "--identifier-type",
                "doi",
                "--identifier",
                "10.1234/config-poison",
            ],
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for field in ("unknown_future_field", "telegram_max_bytes"):
                with self.subTest(field=field):
                    case_root = root / field
                    case_root.mkdir()
                    state_dir = case_root / "state"
                    manifest_dir = case_root / "manifests"
                    config = case_root / "config.json"
                    prefix = json.dumps({
                        "state_dir": str(state_dir),
                        "manifest_dir": str(manifest_dir),
                    })[:-1]
                    config.write_text(
                        f'{prefix}, "{field}": NaN}}',
                        encoding="utf-8",
                    )
                    env = dict(
                        os.environ,
                        GETSCIPAPERS_SKILL_CONFIG=str(config),
                        PYTHONDONTWRITEBYTECODE="1",
                    )

                    for arguments in commands:
                        result = subprocess.run(
                            [sys.executable, "-B", str(helper_script), *arguments],
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            env=env,
                            timeout=10,
                        )
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("config is unreadable or invalid", result.stderr)
                        self.assertFalse(state_dir.exists())
                        self.assertFalse(manifest_dir.exists())

    def test_strict_config_bounds_telegram_max_bytes(self) -> None:
        helper = _load_module("gsp_helper_config_limit_test", "gsp_openclaw_helper.py")
        invalid_values = (
            True,
            0,
            -1,
            helper.MAX_TELEGRAM_MAX_BYTES + 1,
            1.5,
            "1024",
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "config.json"
            for value in invalid_values:
                with self.subTest(value=value):
                    config.write_text(
                        json.dumps({"telegram_max_bytes": value}),
                        encoding="utf-8",
                    )
                    with mock.patch.dict(
                        helper.os.environ,
                        {"GETSCIPAPERS_SKILL_CONFIG": str(config)},
                    ), self.assertRaisesRegex(
                        SystemExit,
                        "telegram_max_bytes must be an integer",
                    ):
                        helper.load_settings(strict_config=True)

            for value in (1, helper.MAX_TELEGRAM_MAX_BYTES):
                with self.subTest(valid=value):
                    config.write_text(
                        json.dumps({"telegram_max_bytes": value}),
                        encoding="utf-8",
                    )
                    with mock.patch.dict(
                        helper.os.environ,
                        {"GETSCIPAPERS_SKILL_CONFIG": str(config)},
                    ):
                        settings = helper.load_settings(strict_config=True)
                    self.assertEqual(settings.telegram_max_bytes, value)

    def test_strict_config_rejects_duplicate_members_before_manifest_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first_manifest = root / "hidden-manifests"
            second_manifest = root / "visible-manifests"
            config = root / "config.json"
            config.write_text(
                "{" +
                f'"manifest_dir":{json.dumps(str(first_manifest))},' +
                f'"manifest_dir":{json.dumps(str(second_manifest))}' +
                "}",
                encoding="utf-8",
            )
            original = config.read_bytes()
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(GSP_ROOT / "gsp_openclaw_helper.py"),
                    "make-manifest",
                    "paper",
                    "10.1234/duplicate-config",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=dict(
                    os.environ,
                    GETSCIPAPERS_SKILL_CONFIG=str(config),
                    PYTHONDONTWRITEBYTECODE="1",
                ),
                timeout=10,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("config is unreadable or invalid", result.stderr)
            self.assertEqual(config.read_bytes(), original)
            self.assertFalse(first_manifest.exists())
            self.assertFalse(second_manifest.exists())

    def test_watch_ids_do_not_collide_for_legacy_sha1_prefix_pair(self) -> None:
        helper = _load_module("gsp_helper_watch_id_test", "gsp_openclaw_helper.py")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = helper.Settings(
                download_dir=root / "downloads",
                state_dir=root / "state",
                manifest_dir=root / "state" / "manifests",
                telegram_max_bytes=1024,
            )
            settings.state_dir.mkdir(parents=True)
            payloads = [
                {
                    "kind": "paper",
                    "identifier_type": "doi",
                    "identifier": identifier,
                    "services": ["all"],
                }
                for identifier in (
                    "10.1234/collision-30809",
                    "10.1234/collision-49972",
                )
            ]
            self.assertEqual(
                helper._legacy_watch_key(payloads[0])[:8],
                helper._legacy_watch_key(payloads[1])[:8],
            )
            self.assertNotEqual(
                helper._watch_key(payloads[0]),
                helper._watch_key(payloads[1]),
            )

            with mock.patch.object(helper.time, "time", return_value=1_700_000_000):
                first = helper.create_watch(settings, payloads[0])
                second = helper.create_watch(settings, payloads[1])
            self.assertNotEqual(first["id"], second["id"])

            helper.update_watch(settings, second["id"], {"status": "found"})
            stored = {
                item["id"]: item
                for item in helper.read_watch_store(
                    settings.state_dir / "watches.json"
                )["items"]
            }
            self.assertEqual(stored[first["id"]]["status"], "active")
            self.assertEqual(stored[second["id"]]["status"], "found")

    def test_watch_identity_serialization_is_unambiguous_and_independent(self) -> None:
        helper = _load_module("gsp_helper_watch_identity_test", "gsp_openclaw_helper.py")
        payloads = [
            {
                "kind": "paper",
                "identifier_type": "search",
                "identifier": "x|y",
                "services": ["z"],
            },
            {
                "kind": "paper",
                "identifier_type": "search",
                "identifier": "x",
                "services": ["y|z"],
            },
        ]
        self.assertEqual(
            helper._legacy_watch_key(payloads[0]),
            helper._legacy_watch_key(payloads[1]),
        )
        self.assertNotEqual(
            helper._watch_key(payloads[0]),
            helper._watch_key(payloads[1]),
        )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = helper.Settings(
                download_dir=root / "downloads",
                state_dir=root / "state",
                manifest_dir=root / "state" / "manifests",
                telegram_max_bytes=1024,
            )
            settings.state_dir.mkdir(parents=True)
            first = helper.create_watch(settings, payloads[0])
            second = helper.create_watch(settings, payloads[1])
            self.assertNotEqual(first["id"], second["id"])
            self.assertNotEqual(first["watch_key"], second["watch_key"])

            helper.update_watch(settings, second["id"], {"status": "found"})
            stored = {
                item["id"]: item
                for item in helper.read_watch_store(
                    settings.state_dir / "watches.json"
                )["items"]
            }
            self.assertEqual(stored[first["id"]]["status"], "active")
            self.assertEqual(stored[second["id"]]["status"], "found")

    def test_matching_legacy_watch_key_is_migrated_without_duplication(self) -> None:
        helper = _load_module("gsp_helper_watch_legacy_key_test", "gsp_openclaw_helper.py")
        payload = {
            "kind": "paper",
            "identifier_type": "doi",
            "identifier": "10.1234/legacy-key",
            "services": ["all"],
        }
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = helper.Settings(
                download_dir=root / "downloads",
                state_dir=root / "state",
                manifest_dir=root / "state" / "manifests",
                telegram_max_bytes=1024,
            )
            settings.state_dir.mkdir(parents=True)
            original = helper.create_watch(settings, payload)
            store = settings.state_dir / "watches.json"
            data = helper.read_watch_store(store)
            data["items"][0]["watch_key"] = helper._legacy_watch_key(payload)
            helper.write_watch_store(store, data)

            reused = helper.create_watch(settings, payload)
            stored = helper.read_watch_store(store)["items"]

            self.assertTrue(reused["reused"])
            self.assertEqual(reused["id"], original["id"])
            self.assertEqual(reused["watch_key"], helper._watch_key(payload))
            self.assertEqual(len(stored), 1)

    def test_matching_found_watch_is_reused_without_duplication(self) -> None:
        helper = _load_module("gsp_helper_watch_found_reuse_test", "gsp_openclaw_helper.py")
        payload = {
            "kind": "paper",
            "identifier_type": "doi",
            "identifier": "10.1234/already-found",
            "services": ["all"],
        }
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = helper.Settings(
                download_dir=root / "downloads",
                state_dir=root / "state",
                manifest_dir=root / "state" / "manifests",
                telegram_max_bytes=1024,
            )
            settings.state_dir.mkdir(parents=True)
            original = helper.create_watch(settings, payload)
            found = helper.update_watch(
                settings,
                original["id"],
                {"status": "found"},
            )
            reused = helper.create_watch(settings, payload)
            stored = helper.read_watch_store(
                settings.state_dir / "watches.json"
            )["items"]

            self.assertTrue(reused["reused"])
            self.assertEqual(reused["id"], original["id"])
            self.assertEqual(reused["status"], "found")
            self.assertEqual(reused["updated_at"], found["updated_at"])
            self.assertEqual(len(stored), 1)

    def test_watch_update_rejects_oversized_last_note_without_mutation(self) -> None:
        helper = _load_module("gsp_helper_watch_last_note_test", "gsp_openclaw_helper.py")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = helper.Settings(
                download_dir=root / "downloads",
                state_dir=root / "state",
                manifest_dir=root / "state" / "manifests",
                telegram_max_bytes=1024,
            )
            settings.state_dir.mkdir(parents=True)
            watch = helper.create_watch(settings, {
                "kind": "paper",
                "label": "bounded",
                "identifier_type": "doi",
                "identifier": "10.1234/bounded-note",
                "services": ["all"],
                "notes": "",
            })
            store = settings.state_dir / "watches.json"
            before = store.read_bytes()

            with self.assertRaisesRegex(SystemExit, "watch store update is invalid"):
                helper.update_watch(
                    settings,
                    watch["id"],
                    {"last_note": "x" * (helper.MAX_WATCH_NOTE_CHARS + 1)},
                )

            self.assertEqual(store.read_bytes(), before)
            updated = helper.update_watch(
                settings,
                watch["id"],
                {"last_note": "x" * helper.MAX_WATCH_NOTE_CHARS},
            )
            self.assertEqual(len(updated["last_note"]), helper.MAX_WATCH_NOTE_CHARS)

    def test_watch_store_validates_history_note_and_timestamp_shapes(self) -> None:
        helper = _load_module("gsp_helper_watch_history_shape_test", "gsp_openclaw_helper.py")
        base = {
            "items": [_watch_record(id="watch-1")],
        }
        invalid_history = (
            [{"ts": -1, "note": "negative"}],
            [{"ts": helper.MAX_WATCH_TIMESTAMP + 1, "note": "too late"}],
            [{"ts": True, "note": "boolean"}],
            [{"ts": 1, "note": "x" * (helper.MAX_WATCH_NOTE_CHARS + 1)}],
            [{"ts": 1, "note": "multiline\nnote"}],
            [{"ts": 1}],
        )
        for history in invalid_history:
            with self.subTest(history=history), self.assertRaisesRegex(
                ValueError,
                "notes history",
            ):
                helper._validate_watch_store({
                    "items": [{**base["items"][0], "notes_history": history}],
                })

    def test_watch_store_rejects_strings_the_bridge_cannot_consume(self) -> None:
        helper = _load_module("gsp_helper_watch_string_schema_test", "gsp_openclaw_helper.py")
        valid = _watch_record()
        invalid = (
            {"id": "watch\nother"},
            {"watch_key": "key\u2060other"},
            {"identifier": "10.1234/control\rvalue"},
            {"label": "line one\nline two"},
            {"notes": "tab\tvalue"},
            {"last_note": "hidden\u200bvalue"},
            {"services": [" "]},
            {"services": ["all\nother"]},
            {"sent_file_hashes": ["hash\nother"]},
        )
        for update in invalid:
            with self.subTest(update=update), self.assertRaises(ValueError):
                helper._validate_watch_store({"items": [{**valid, **update}]})

    def test_relative_config_storage_is_stable_across_working_directories(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config_dir = root / "config"
            config_dir.mkdir()
            config = config_dir / "config.json"
            config.write_text(
                json.dumps({
                    "download_dir": "downloads",
                    "state_dir": "state",
                    "manifest_dir": "manifests",
                }),
                encoding="utf-8",
            )
            first_cwd = root / "first-cwd"
            second_cwd = root / "second-cwd"
            first_cwd.mkdir()
            second_cwd.mkdir()
            env = dict(
                os.environ,
                GETSCIPAPERS_SKILL_CONFIG=str(config),
                PYTHONDONTWRITEBYTECODE="1",
            )

            created = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(GSP_ROOT / "gsp_openclaw_helper.py"),
                    "create-watch",
                    "--kind",
                    "paper",
                    "--label=cross-cwd",
                    "--identifier-type",
                    "doi",
                    "--identifier",
                    "10.1234/cross-cwd",
                ],
                cwd=first_cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=10,
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            watch_id = json.loads(created.stdout)["id"]

            listed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(GSP_ROOT / "gsp_openclaw_helper.py"),
                    "list-watches",
                ],
                cwd=second_cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=10,
            )
            self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
            self.assertEqual(
                [item["id"] for item in json.loads(listed.stdout)["items"]],
                [watch_id],
            )
            self.assertTrue((config_dir / "state" / "watches.json").is_file())
            self.assertFalse((first_cwd / "state").exists())
            self.assertFalse((second_cwd / "state").exists())

    def test_watch_store_rejects_incomplete_or_wrong_typed_baseline_records(self) -> None:
        helper = _load_module("gsp_helper_watch_id_validation_test", "gsp_openclaw_helper.py")
        valid = _watch_record()
        required = (
            "id",
            "watch_key",
            "kind",
            "identifier_type",
            "identifier",
            "services",
            "status",
            "created_at",
            "updated_at",
            "sent_file_hashes",
            "check_count",
        )
        cases = [
            [{key: value for key, value in valid.items() if key != field}]
            for field in required
        ]
        cases.extend((
            [valid, dict(valid)],
            [{**valid, "created_at": "not-an-integer"}],
            [{**valid, "check_count": True}],
            [{**valid, "status": "invented"}],
        ))
        for items in cases:
            with self.subTest(items=items), self.assertRaises(ValueError):
                helper._validate_watch_store({"items": items})

    def test_duplicate_watch_store_members_block_mutation_and_preserve_bytes(self) -> None:
        helper = _load_module(
            "gsp_helper_watch_duplicate_member_test",
            "gsp_openclaw_helper.py",
        )
        first = _watch_record(id="watch-hidden", identifier="10.1234/hidden")
        second = _watch_record(id="watch-visible", identifier="10.1234/visible")
        original = (
            b'{"items":['
            + json.dumps(first).encode("utf-8")
            + b'],"items":['
            + json.dumps(second).encode("utf-8")
            + b']}')
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = helper.Settings(
                download_dir=root / "downloads",
                state_dir=root / "state",
                manifest_dir=root / "state" / "manifests",
                telegram_max_bytes=1024,
            )
            settings.state_dir.mkdir(parents=True)
            store = settings.state_dir / "watches.json"
            store.write_bytes(original)
            operations = (
                lambda: helper.create_watch(settings, {
                    "kind": "paper",
                    "identifier_type": "doi",
                    "identifier": "10.1234/new",
                    "services": ["all"],
                }),
                lambda: helper.update_watch(
                    settings,
                    second["id"],
                    {"status": "waiting"},
                ),
            )
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaisesRegex(
                    SystemExit,
                    "watch store is unreadable or invalid",
                ):
                    operation()
                self.assertEqual(store.read_bytes(), original)

    def test_watch_store_requires_canonical_identity_key_and_timestamp_order(self) -> None:
        helper = _load_module(
            "gsp_helper_watch_semantic_identity_test",
            "gsp_openclaw_helper.py",
        )
        valid = _watch_record()
        invalid = (
            {**valid, "identifier": "not-a-doi"},
            {**valid, "identifier": "HTTPS://DOI.ORG/10.1234/example"},
            {**valid, "watch_key": "0" * 40},
            {**valid, "updated_at": valid["created_at"] - 1},
            {**valid, "deadline_ts": valid["created_at"] - 1},
            {
                **valid,
                "last_checked_at": valid["updated_at"] + 1,
            },
        )

        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                helper._validate_watch_store({"items": [item]})

        legacy = dict(valid)
        legacy["watch_key"] = helper._legacy_watch_key(legacy)
        self.assertIs(
            helper._validate_watch_store({"items": [legacy]})["items"][0],
            legacy,
        )

        search = _watch_record(
            kind="paper",
            identifier_type="search",
            identifier="graph reconfiguration",
        )
        self.assertEqual(
            helper._validate_watch_store({"items": [search]})["items"],
            [search],
        )
        padded_search = {**search, "identifier": " graph reconfiguration "}
        padded_search["watch_key"] = helper._watch_key(padded_search)
        with self.assertRaisesRegex(ValueError, "not canonical"):
            helper._validate_watch_store({"items": [padded_search]})

    def test_future_watch_events_block_reuse_and_update_without_mutation(self) -> None:
        helper = _load_module(
            "gsp_helper_watch_future_timestamp_test",
            "gsp_openclaw_helper.py",
        )
        future = (
            int(time.time()) + helper.MAX_WATCH_FUTURE_SKEW_SECONDS + 86_400
        )
        payload = {
            "kind": "paper",
            "identifier_type": "doi",
            "identifier": "10.1234/future-watch",
            "services": ["all"],
        }
        record = _watch_record(
            id="watch-future",
            identifier=payload["identifier"],
            services=payload["services"],
            created_at=future,
            updated_at=future,
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = helper.Settings(
                download_dir=root / "downloads",
                state_dir=root / "state",
                manifest_dir=root / "state" / "manifests",
                telegram_max_bytes=1024,
            )
            settings.state_dir.mkdir(parents=True)
            store = settings.state_dir / "watches.json"
            store.write_text(
                json.dumps({"items": [record]}),
                encoding="utf-8",
            )
            original = store.read_bytes()

            operations = (
                lambda: helper.create_watch(settings, payload),
                lambda: helper.update_watch(
                    settings,
                    record["id"],
                    {"status": "waiting"},
                ),
            )
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaisesRegex(
                    SystemExit,
                    "watch store is unreadable or invalid",
                ):
                    operation()
                self.assertEqual(store.read_bytes(), original)

            for field_update in (
                {"last_checked_at": future},
                {"notes_history": [{"ts": future, "note": "future"}]},
            ):
                candidate = {
                    **record,
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                    **field_update,
                }
                with self.subTest(field=field_update), self.assertRaisesRegex(
                    ValueError,
                    "in the future",
                ):
                    helper._validate_watch_store({"items": [candidate]})

    def test_within_skew_future_watch_remains_mutable_monotonically(self) -> None:
        helper = _load_module(
            "gsp_helper_watch_within_skew_test",
            "gsp_openclaw_helper.py",
        )
        future = int(time.time()) + 1
        payload = {
            "kind": "paper",
            "identifier_type": "doi",
            "identifier": "10.1234/within-skew",
            "services": ["all"],
        }
        record = _watch_record(
            id="watch-within-skew",
            identifier=payload["identifier"],
            services=payload["services"],
            created_at=future,
            updated_at=future,
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = helper.Settings(
                download_dir=root / "downloads",
                state_dir=root / "state",
                manifest_dir=root / "state" / "manifests",
                telegram_max_bytes=1024,
            )
            settings.state_dir.mkdir(parents=True)
            store = settings.state_dir / "watches.json"
            store.write_text(json.dumps({"items": [record]}), encoding="utf-8")

            reused = helper.create_watch(settings, payload)
            self.assertTrue(reused["reused"])
            self.assertGreaterEqual(reused["updated_at"], future)
            self.assertGreaterEqual(reused["notes_history"][-1]["ts"], future)
            updated = helper.update_watch(
                settings,
                record["id"],
                {"status": "waiting", "last_checked_at": int(time.time())},
                bump_check=True,
            )

            self.assertEqual(updated["status"], "waiting")
            self.assertGreaterEqual(updated["updated_at"], future)
            self.assertGreaterEqual(updated["last_checked_at"], future)
            self.assertEqual(helper.read_watch_store(store)["items"], [updated])

    def test_wrong_shape_watch_store_is_preserved_without_create_mutation(self) -> None:
        helper = _load_module("gsp_helper_watch_shape_preservation_test", "gsp_openclaw_helper.py")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = helper.Settings(
                download_dir=root / "downloads",
                state_dir=root / "state",
                manifest_dir=root / "state" / "manifests",
                telegram_max_bytes=1024,
            )
            settings.state_dir.mkdir(parents=True)
            store = settings.state_dir / "watches.json"
            original = b'{"items":[{"id":"only"}]}'
            store.write_bytes(original)

            with self.assertRaisesRegex(SystemExit, "watch store is unreadable or invalid"):
                helper.create_watch(settings, {
                    "kind": "paper",
                    "identifier_type": "doi",
                    "identifier": "10.1234/new",
                    "services": ["all"],
                })

            self.assertEqual(store.read_bytes(), original)

    def test_nonfinite_unknown_watch_field_is_preserved_without_create_mutation(self) -> None:
        helper = _load_module(
            "gsp_helper_watch_nonfinite_preservation_test",
            "gsp_openclaw_helper.py",
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = helper.Settings(
                download_dir=root / "downloads",
                state_dir=root / "state",
                manifest_dir=root / "state" / "manifests",
                telegram_max_bytes=1024,
            )
            settings.state_dir.mkdir(parents=True)
            store = settings.state_dir / "watches.json"
            existing = _watch_record(
                kind="book",
                identifier_type="isbn",
                identifier="9780306406157",
                poison=math.nan,
            )
            original = json.dumps({"items": [existing]}).encode("utf-8")
            store.write_bytes(original)

            with self.assertRaisesRegex(
                SystemExit,
                "watch store is unreadable or invalid",
            ):
                helper.create_watch(settings, {
                    "kind": "paper",
                    "identifier_type": "doi",
                    "identifier": "10.1234/must-not-be-created",
                    "services": ["all"],
                })

            self.assertEqual(store.read_bytes(), original)
            self.assertNotIn(b"must-not-be-created", store.read_bytes())

    def test_watch_store_writer_rejects_nonfinite_unknown_field_without_mutation(self) -> None:
        helper = _load_module(
            "gsp_helper_watch_nonfinite_writer_test",
            "gsp_openclaw_helper.py",
        )
        with tempfile.TemporaryDirectory() as raw:
            store = Path(raw) / "watches.json"
            valid = {"items": [_watch_record()]}
            helper.write_watch_store(store, valid)
            original = store.read_bytes()
            poisoned = {"items": [{**_watch_record(), "poison": math.inf}]}

            with self.assertRaisesRegex(
                SystemExit,
                "watch store update is invalid",
            ):
                helper.write_watch_store(store, poisoned)

            self.assertEqual(store.read_bytes(), original)

    def test_full_bridge_batch_with_non_ascii_labels_fits_watch_store(self) -> None:
        helper = _load_module("gsp_helper_watch_capacity_test", "gsp_openclaw_helper.py")
        label = "\U0001f9ea" * helper.MAX_WATCH_LABEL_CHARS
        items = []
        for index in range(3_000):
            item = {
                "id": f"watch-1700000000-{index:040x}",
                "kind": "paper",
                "label": label,
                "identifier_type": "doi",
                "identifier": (
                    f"10.1234/{index:04d}"
                    + "x" * (
                        helper.MAX_WATCH_IDENTIFIER_CHARS
                        - len(f"10.1234/{index:04d}")
                    )
                ),
                "services": ["all"],
                "notes": "",
                "status": "active",
                "created_at": 1_700_000_000,
                "updated_at": 1_700_000_000,
                "deadline_ts": None,
                "sent_file_hashes": [],
                "check_count": 0,
            }
            item["watch_key"] = helper._watch_key(item)
            items.append(item)
        encoded = (
            json.dumps({"items": items}, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        self.assertGreater(len(encoded), 4 * 1024 * 1024)
        self.assertLessEqual(len(encoded), helper.MAX_WATCH_STORE_BYTES)

        with tempfile.TemporaryDirectory() as raw:
            store = Path(raw) / "watches.json"
            helper.write_watch_store(store, {"items": items})
            self.assertEqual(len(helper.read_watch_store(store)["items"]), 3_000)

    def test_manifest_outputs_do_not_follow_predictable_or_final_symlinks(self) -> None:
        helper = _load_module("gsp_helper_manifest_atomic_test", "gsp_openclaw_helper.py")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest_dir = root / "manifests"
            manifest_dir.mkdir()
            settings = helper.Settings(
                download_dir=root / "downloads",
                state_dir=root / "state",
                manifest_dir=manifest_dir,
                telegram_max_bytes=1024,
            )
            source = "10.1234/example"
            digest = helper.hashlib.sha256(
                f"paper\n{source}".encode("utf-8")
            ).hexdigest()
            manifest_path = manifest_dir / f"manifest-{digest}.json"
            predictable_stage = manifest_path.with_suffix(".json.tmp")
            doi_path = manifest_dir / f"manifest-{digest}.doi.txt"
            victims = [root / f"victim-{index}" for index in range(3)]
            for victim in victims:
                victim.write_text("sentinel", encoding="utf-8")
            predictable_stage.symlink_to(victims[0])
            manifest_path.symlink_to(victims[1])
            doi_path.symlink_to(victims[2])

            result = helper.build_manifest("paper", source, settings)

            self.assertEqual(result["manifest_path"], str(manifest_path))
            self.assertTrue(manifest_path.is_file())
            self.assertFalse(manifest_path.is_symlink())
            self.assertTrue(doi_path.is_file())
            self.assertFalse(doi_path.is_symlink())
            self.assertEqual(
                [victim.read_text(encoding="utf-8") for victim in victims],
                ["sentinel"] * 3,
            )

    def test_manifest_names_do_not_overwrite_on_an_equal_legacy_hash_prefix(self) -> None:
        helper = _load_module(
            "gsp_helper_manifest_digest_width_test",
            "gsp_openclaw_helper.py",
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest_dir = root / "manifests"
            manifest_dir.mkdir()
            settings = helper.Settings(
                download_dir=root / "downloads",
                state_dir=root / "state",
                manifest_dir=manifest_dir,
                telegram_max_bytes=1024,
            )

            def fake_sha256(payload):
                tail = "a" if payload.endswith(b"alpha") else "b"
                return SimpleNamespace(hexdigest=lambda: "0" * 12 + tail * 52)

            with mock.patch.object(
                helper.hashlib,
                "sha256",
                side_effect=fake_sha256,
            ):
                first = helper.build_manifest("paper", "10.1234/alpha", settings)
                second = helper.build_manifest("paper", "10.1234/beta", settings)

            first_path = Path(first["manifest_path"])
            second_path = Path(second["manifest_path"])
            self.assertNotEqual(first_path, second_path)
            self.assertTrue(first_path.is_file())
            self.assertTrue(second_path.is_file())
            self.assertIn("0" * 12 + "a" * 52, first_path.name)
            self.assertIn("0" * 12 + "b" * 52, second_path.name)

    def test_every_helper_writer_participates_in_the_watch_store_lock(self) -> None:
        helper = _load_module("gsp_helper_watch_lock_test", "gsp_openclaw_helper.py")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = helper.Settings(
                download_dir=root / "downloads",
                state_dir=root / "state",
                manifest_dir=root / "state" / "manifests",
                telegram_max_bytes=1024,
            )
            settings.state_dir.mkdir(parents=True)
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "download_dir": str(settings.download_dir),
                        "state_dir": str(settings.state_dir),
                        "manifest_dir": str(settings.manifest_dir),
                    }
                ),
                encoding="utf-8",
            )
            env = dict(
                os.environ,
                GETSCIPAPERS_SKILL_CONFIG=str(config),
                PYTHONDONTWRITEBYTECODE="1",
            )

            with helper.watch_store_lock(settings):
                store = helper.ensure_watch_store(settings)
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        str(GSP_ROOT / "gsp_openclaw_helper.py"),
                        "create-watch",
                        "--kind",
                        "paper",
                        "--label=child",
                        "--identifier-type",
                        "doi",
                        "--identifier",
                        "10.1234/child",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                )
                time.sleep(0.2)
                self.assertIsNone(child.poll())
                data = helper.read_watch_store(store)
                data["items"].append(
                    _watch_record(
                        id="parent",
                        identifier="10.1234/parent",
                    )
                )
                helper.write_watch_store(store, data)

            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, stdout + stderr)
            watches = helper.read_watch_store(store)["items"]
            self.assertEqual(len(watches), 2)
            self.assertIn("parent", {item["id"] for item in watches})
            self.assertTrue(
                any(item.get("identifier") == "10.1234/child" for item in watches)
            )


if __name__ == "__main__":
    unittest.main()
