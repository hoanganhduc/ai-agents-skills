from __future__ import annotations

import base64
import http.server
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
U2S_ROOT = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "url-to-screenshot-runtime"
)
RUNTIME = U2S_ROOT / "url_to_screenshot_runtime.py"
if str(U2S_ROOT) not in sys.path:
    sys.path.insert(0, str(U2S_ROOT))


def _load_dispatcher():
    spec = importlib.util.spec_from_file_location("url_to_screenshot_runtime", RUNTIME)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_minimal_pdf(
    page_count: int,
    *,
    catalog_extra: bytes = b"",
    declared_count_token: bytes | None = None,
) -> bytes:
    kids = b" ".join(f"{number} 0 R".encode("ascii") for number in range(3, 3 + page_count))
    count_token = (
        declared_count_token
        if declared_count_token is not None
        else str(page_count).encode("ascii")
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R " + catalog_extra + b">>",
        b"<< /Type /Pages /Count " + count_token + b" /Kids [" + kids + b"] >>",
    ]
    for index in range(page_count):
        content_number = 3 + page_count + index
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents "
            + f"{content_number} 0 R".encode("ascii")
            + b" >>"
        )
    objects.extend(b"<< /Length 2 >> stream\nq\nendstream" for _ in range(page_count))
    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode("ascii"))
        data.extend(body)
        data.extend(b"\nendobj\n")
    xref_offset = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(data)


def _server_frame(payload: bytes, *, opcode: int, fin: bool) -> bytes:
    first = opcode | (0x80 if fin else 0)
    if len(payload) < 126:
        return bytes((first, len(payload))) + payload
    if len(payload) < 65536:
        return bytes((first, 126)) + struct.pack(">H", len(payload)) + payload
    return bytes((first, 127)) + struct.pack(">Q", len(payload)) + payload


def _buffered_websocket(data: bytes, *, max_message_bytes: int):
    from u2s import cdp

    class NoReadSocket:
        def recv(self, _size: int) -> bytes:
            raise AssertionError("test frame should be rejected or decoded from the buffer")

    websocket = object.__new__(cdp._WebSocket)
    websocket._sock = NoReadSocket()
    websocket._buf = data
    websocket._max_message_bytes = max_message_bytes
    return websocket


class PdfCliSurfaceTests(unittest.TestCase):
    def test_print_and_verify_pdf_verbs_exist(self) -> None:
        parser = _load_dispatcher().build_parser()
        subparsers = next(
            action for action in parser._actions if action.__class__.__name__ == "_SubParsersAction"
        )
        self.assertIn("print-pdf", subparsers.choices)
        self.assertIn("verify-pdf", subparsers.choices)

    def test_print_defaults_and_boolean_overrides_are_explicit(self) -> None:
        parser = _load_dispatcher().build_parser()
        defaults = parser.parse_args(["print-pdf", "--url", "https://example.com/"])
        self.assertEqual(defaults.media, "print")
        self.assertTrue(defaults.print_background)
        self.assertTrue(defaults.prefer_css_page_size)
        disabled = parser.parse_args(
            [
                "print-pdf",
                "--url",
                "https://example.com/",
                "--no-print-background",
                "--no-prefer-css-page-size",
            ]
        )
        self.assertFalse(disabled.print_background)
        self.assertFalse(disabled.prefer_css_page_size)

        strict = parser.parse_args(
            ["print-pdf", "--url", "https://example.com/", "--same-origin-only"]
        )
        self.assertTrue(strict.same_origin_only)
        strict_capture = parser.parse_args(
            ["capture", "--url", "https://example.com/", "--same-origin-only"]
        )
        self.assertTrue(strict_capture.same_origin_only)

    def test_verify_pdf_exit_zero_means_structural_only_not_final(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            structural = Path(tmp) / "structural.pdf"
            structural.write_bytes(_make_minimal_pdf(1))
            accepted = subprocess.run(
                [sys.executable, str(RUNTIME), "verify-pdf", "--pdf", str(structural)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            payload = json.loads(accepted.stdout)
            self.assertEqual(payload["status"], "STRUCTURALLY_VALID")
            self.assertEqual(payload["final_verdict"], "UNVERIFIED")

            malformed = Path(tmp) / "malformed.pdf"
            malformed.write_bytes(b"%PDF-1.4\n%%EOF\n")
            rejected = subprocess.run(
                [sys.executable, str(RUNTIME), "verify-pdf", "--pdf", str(malformed)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(rejected.returncode, 2, rejected.stdout + rejected.stderr)


class PdfPrintContractTests(unittest.TestCase):
    def test_print_params_make_defaults_explicit(self) -> None:
        from u2s import cdp

        self.assertEqual(
            cdp.build_print_to_pdf_params(),
            {
                "printBackground": True,
                "preferCSSPageSize": True,
                "transferMode": "ReturnAsBase64",
            },
        )

    def test_pdf_runner_uses_shared_guarded_navigation(self) -> None:
        from u2s import cdp

        request = cdp.CdpPrintPdfRequest(
            browser_path="/usr/bin/chromium",
            url="file:///tmp/trusted.html",
            out_path="/tmp/proof.pdf",
            allow_file_urls=True,
        )
        with mock.patch.object(cdp, "_run_guarded_cdp", return_value={"out_path": request.out_path}) as guarded:
            result = cdp.run_cdp_print_pdf(request)
        self.assertEqual(result["out_path"], request.out_path)
        guarded.assert_called_once()
        self.assertIs(guarded.call_args.args[1], cdp._print_pdf_operation)

    def test_print_operation_sets_media_and_atomically_writes(self) -> None:
        from u2s import cdp

        class FakeSession:
            def __init__(self, encoded: str):
                self.encoded = encoded
                self.calls: list[tuple[str, dict]] = []

            def call(self, method: str, params: dict | None = None, *, deadline: float):
                self.calls.append((method, params or {}))
                if method == "Page.printToPDF":
                    return {"data": self.encoded}
                return {}

        data = _make_minimal_pdf(2)
        session = FakeSession(base64.b64encode(data).decode("ascii"))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "proof.pdf"
            request = cdp.CdpPrintPdfRequest(
                browser_path="browser",
                url="file:///trusted.html",
                out_path=str(out),
                media="screen",
                max_bytes=len(data),
            )
            result = cdp._print_pdf_operation(session, request, 99.0, False)
            self.assertEqual(out.read_bytes(), data)
        self.assertEqual(session.calls[0], ("Emulation.setEmulatedMedia", {"media": "screen"}))
        self.assertEqual(
            session.calls[1][1],
            {
                "printBackground": True,
                "preferCSSPageSize": True,
                "transferMode": "ReturnAsBase64",
            },
        )
        self.assertEqual(result["bytes"], len(data))
        self.assertEqual(len(result["sha256"]), 64)

    def test_atomic_writer_leaves_existing_destination_on_limit_failure(self) -> None:
        from u2s import cdp

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "proof.pdf"
            out.write_bytes(b"previous")
            with self.assertRaises(cdp._OutputBlocked):
                cdp._atomic_write_pdf(str(out), _make_minimal_pdf(1), max_bytes=8)
            self.assertEqual(out.read_bytes(), b"previous")
            self.assertEqual(list(out.parent.glob(f".{out.name}.*.tmp")), [])


class PdfVerificationTests(unittest.TestCase):
    def test_valid_page_tree_is_structural_only_with_digest(self) -> None:
        from u2s import pdf

        result = pdf.verify_pdf(_make_minimal_pdf(2))
        self.assertFalse(result.ok)
        self.assertTrue(result.structurally_valid)
        self.assertEqual(result.final_verdict, pdf.UNVERIFIED)
        self.assertEqual(result.status, pdf.STRUCTURALLY_VALID)
        self.assertEqual(result.page_count, 2)
        self.assertEqual(len(result.sha256 or ""), 64)
        self.assertTrue(all(value == pdf.PASS for value in result.checks.values()))

    def test_signature_without_parsed_page_tree_is_never_verified(self) -> None:
        from u2s import pdf

        result = pdf.verify_pdf(b"%PDF-1.4\n/Type /Page /Count 999\n%%EOF\n")
        self.assertFalse(result.ok)
        self.assertEqual(result.final_verdict, pdf.UNVERIFIED)
        self.assertIsNone(result.page_count)

    def test_mismatched_or_zero_page_tree_is_unverified(self) -> None:
        from u2s import pdf

        mismatch = pdf.verify_pdf(_make_minimal_pdf(2).replace(b"/Count 2", b"/Count 3"))
        zero = pdf.verify_pdf(_make_minimal_pdf(0))
        self.assertFalse(mismatch.ok)
        self.assertFalse(zero.ok)

    def test_huge_decimal_token_fails_closed_without_big_int_conversion(self) -> None:
        from u2s import pdf

        result = pdf.verify_pdf(
            _make_minimal_pdf(1, declared_count_token=b"9" * 5000)
        )
        self.assertEqual(result.status, pdf.UNVERIFIED)
        self.assertEqual(result.checks["page_count"], pdf.FAIL)
        self.assertIn("numeric limit", result.detail)

    def test_oversized_pdf_is_not_read_or_verified(self) -> None:
        from u2s import pdf

        data = _make_minimal_pdf(1)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "proof.pdf"
            source.write_bytes(data)
            result = pdf.verify_pdf_file(source, max_bytes=len(data) - 1)
        self.assertFalse(result.ok)
        self.assertEqual(result.checks["bounded_size"], pdf.FAIL)
        self.assertIsNone(result.sha256)

    def test_malformed_xref_fails_closed(self) -> None:
        from u2s import pdf

        data = bytearray(b"%PDF-1.4\n")
        xref_offset = len(data)
        data.extend(b"xref\n0 1\n0000000000 65535 f \n")
        data.extend(f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
        result = pdf.verify_pdf(bytes(data))
        self.assertFalse(result.ok)
        self.assertEqual(result.checks["page_count"], pdf.FAIL)

    def test_blank_page_and_comment_forged_structure_are_unverified(self) -> None:
        from u2s import pdf

        blank = _make_minimal_pdf(1).replace(b"/Length 2", b"/Length 0").replace(b"q\nendstream", b" \nendstream")
        self.assertFalse(pdf.verify_pdf(blank).ok)

        forged = _make_minimal_pdf(1)
        forged = forged.replace(b"<< /Type /Catalog /Pages 2 0 R >>", b"%/Type /Catalog /Pages 2 0 R\nnull")
        self.assertFalse(pdf.verify_pdf(forged).ok)

    def test_declared_stream_length_and_endstream_mismatches_are_unverified(self) -> None:
        from u2s import pdf

        wrong_length = _make_minimal_pdf(1).replace(b"/Length 2", b"/Length 9")
        wrong_endstream = _make_minimal_pdf(1).replace(b"endstream", b"endstreaX")
        for malformed in (wrong_length, wrong_endstream):
            with self.subTest(malformed=malformed[-80:]):
                result = pdf.verify_pdf(malformed)
                self.assertFalse(result.ok)
                self.assertEqual(result.final_verdict, pdf.UNVERIFIED)
                self.assertEqual(result.checks["content_evidence"], pdf.FAIL)

    def test_active_content_action_is_unverified_even_with_valid_pages(self) -> None:
        from u2s import pdf

        result = pdf.verify_pdf(
            _make_minimal_pdf(1, catalog_extra=b"/OpenAction 4 0 R ")
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.checks["page_count"], pdf.PASS)
        self.assertEqual(result.checks["content_evidence"], pdf.PASS)
        self.assertEqual(result.checks["active_content_absent"], pdf.FAIL)

    def test_escaped_active_content_name_is_detected_as_active(self) -> None:
        from u2s import pdf

        escaped = pdf.verify_pdf(
            _make_minimal_pdf(1, catalog_extra=b"/Open#41ction 4 0 R ")
        )
        self.assertEqual(escaped.checks["active_content_absent"], pdf.FAIL)

        embedded_slash = pdf.verify_pdf(
            _make_minimal_pdf(1, catalog_extra=b"/Safe#2FOpenAction 4 0 R ")
        )
        self.assertEqual(embedded_slash.checks["active_content_absent"], pdf.PASS)

    def test_shared_contents_are_memoized_and_page_work_is_capped(self) -> None:
        from u2s import pdf

        shared = _make_minimal_pdf(2).replace(
            b"/Contents 6 0 R", b"/Contents 5 0 R"
        )
        original = pdf._validated_direct_stream_has_content
        calls = 0

        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        with mock.patch.object(
            pdf, "_validated_direct_stream_has_content", side_effect=counted
        ):
            result = pdf.verify_pdf(shared)
        self.assertTrue(result.structurally_valid)
        self.assertEqual(calls, 1)

        with mock.patch.object(pdf, "MAX_PDF_PAGES", 1):
            capped = pdf.verify_pdf(_make_minimal_pdf(2))
        self.assertEqual(capped.status, pdf.UNVERIFIED)
        self.assertIn("page count exceeds", capped.detail)

    def test_aggregate_content_stream_work_is_capped_before_scan(self) -> None:
        from u2s import pdf

        with mock.patch.object(pdf, "MAX_CONTENT_STREAM_BYTES", 1):
            result = pdf.verify_pdf(_make_minimal_pdf(1))
        self.assertEqual(result.status, pdf.UNVERIFIED)
        self.assertIn("aggregate work limit", result.detail)


class SidecarSafetyTests(unittest.TestCase):
    def test_sidecar_write_is_atomic_and_refuses_planted_symlink(self) -> None:
        from u2s import naming

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "proof.pdf"
            victim = root / "victim.json"
            victim.write_text("untouched", encoding="utf-8")
            sidecar = artifact.with_suffix(".result.json")
            sidecar.symlink_to(victim)
            with self.assertRaises(OSError):
                naming.write_result_sidecar(artifact, {"status": "PDF_PRINTED"})
            self.assertEqual(victim.read_text(encoding="utf-8"), "untouched")
            self.assertTrue(sidecar.is_symlink())
            self.assertEqual(list(root.glob(f".{sidecar.name}.*.tmp")), [])

            sidecar.unlink()
            written = naming.write_result_sidecar(
                artifact, {"status": "PDF_PRINTED"}
            )
            self.assertEqual(
                json.loads(written.read_text(encoding="utf-8"))["status"],
                "PDF_PRINTED",
            )


class RuntimeProvenanceTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX executable fixture")
    def test_browser_version_probe_is_time_and_output_bounded(self) -> None:
        from u2s import detect

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            browser = root / "browser"
            browser.write_text(
                "#!/bin/sh\nprintf 'Fixture Chromium 123.4\\n'\n", encoding="utf-8"
            )
            browser.chmod(0o755)
            self.assertEqual(
                detect.probe_browser_version(str(browser)),
                "Fixture Chromium 123.4",
            )

            browser.write_text(
                "#!/bin/sh\n"
                "printf '[warning] channel metadata unavailable\\n'\n"
                "printf 'Google Chrome for Testing 150.0.1\\n'\n",
                encoding="utf-8",
            )
            self.assertEqual(
                detect.probe_browser_version(str(browser)),
                "Google Chrome for Testing 150.0.1",
            )

            browser.write_text(
                "#!/bin/sh\nprintf '123456789'\n", encoding="utf-8"
            )
            self.assertEqual(
                detect.probe_browser_version(str(browser), max_output_bytes=8), ""
            )

            browser.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
            self.assertEqual(
                detect.probe_browser_version(str(browser), timeout_seconds=0.05),
                "",
            )

    def test_real_capture_and_print_payloads_attest_runtime_and_browser_version(self) -> None:
        from u2s import __version__, capture, detect, pdf

        browser = detect.BrowserInfo(path="/fixture/chromium", family="chromium")
        with tempfile.TemporaryDirectory() as tmp:
            capture_request = capture.CaptureRequest(
                url=(Path(tmp) / "page.html").as_uri(),
                out_path=str(Path(tmp) / "shot.png"),
                allow_file_urls=True,
                same_origin_only=True,
                engine="cdp",
            )
            with (
                mock.patch.object(capture, "resolve_browser", return_value=browser),
                mock.patch.object(
                    capture.detect,
                    "probe_browser_version",
                    return_value="Fixture Chromium 123.4",
                ),
                mock.patch.object(
                    capture,
                    "_run_tier",
                    return_value={"status": "CAPTURED", "out_path": capture_request.out_path},
                ),
                mock.patch.object(capture, "_write_sidecar"),
            ):
                capture_result = capture.run_capture(capture_request)
            self.assertEqual(capture_result["runtime_version"], __version__)
            self.assertEqual(
                capture_result["browser"]["version"], "Fixture Chromium 123.4"
            )
            self.assertTrue(capture_result["same_origin_only"])

            print_request = pdf.PrintPdfRequest(
                url=(Path(tmp) / "page.html").as_uri(),
                out_path=str(Path(tmp) / "proof.pdf"),
                allow_file_urls=True,
                same_origin_only=True,
            )
            with (
                mock.patch.object(pdf, "_resolve_browser", return_value=browser),
                mock.patch.object(
                    pdf.detect,
                    "probe_browser_version",
                    return_value="Fixture Chromium 123.4",
                ),
                mock.patch.object(
                    pdf.cdp,
                    "run_cdp_print_pdf",
                    return_value={"out_path": print_request.out_path},
                ) as print_runner,
            ):
                print_result = pdf.run_print_pdf(print_request)
            self.assertEqual(print_result["runtime_version"], __version__)
            self.assertEqual(
                print_result["browser"]["version"], "Fixture Chromium 123.4"
            )
            self.assertTrue(print_result["same_origin_only"])
            self.assertTrue(print_runner.call_args.args[0].same_origin_only)


class WebSocketFrameBoundaryTests(unittest.TestCase):
    def test_oversized_single_frame_is_rejected_before_payload_read(self) -> None:
        from u2s import cdp

        websocket = _buffered_websocket(
            _server_frame(b"123456789", opcode=0x1, fin=True),
            max_message_bytes=8,
        )
        with self.assertRaisesRegex(cdp.CdpError, "message exceeds"):
            websocket.recv_text()

    def test_fragmented_text_frames_are_assembled_within_limit(self) -> None:
        websocket = _buffered_websocket(
            _server_frame(b"hello ", opcode=0x1, fin=False)
            + _server_frame(b"world", opcode=0x0, fin=True),
            max_message_bytes=11,
        )
        self.assertEqual(websocket.recv_text(), "hello world")

    def test_fragmented_text_frames_enforce_cumulative_limit(self) -> None:
        from u2s import cdp

        websocket = _buffered_websocket(
            _server_frame(b"12345", opcode=0x1, fin=False)
            + _server_frame(b"6789", opcode=0x0, fin=True),
            max_message_bytes=8,
        )
        with self.assertRaisesRegex(cdp.CdpError, "message exceeds"):
            websocket.recv_text()

    def test_large_receive_uses_bounded_mutable_chunks(self) -> None:
        from u2s import cdp

        class ChunkSocket:
            def __init__(self, remaining: int):
                self.remaining = remaining
                self.calls = 0

            def settimeout(self, _timeout: float) -> None:
                pass

            def recv(self, size: int) -> bytes:
                self.calls += 1
                take = min(size, self.remaining)
                self.remaining -= take
                return b"x" * take

        websocket = object.__new__(cdp._WebSocket)
        websocket._sock = ChunkSocket(8 * 1024 * 1024)
        websocket._buf = bytearray()
        websocket._max_message_bytes = 9 * 1024 * 1024
        received = websocket._recv_exact(8 * 1024 * 1024)
        self.assertEqual(len(received), 8 * 1024 * 1024)
        self.assertEqual(websocket._sock.calls, 128)

    def test_receive_checks_deadline_between_chunks(self) -> None:
        from u2s import cdp

        class ChunkSocket:
            def settimeout(self, _timeout: float) -> None:
                pass

            def recv(self, _size: int) -> bytes:
                return b"x"

        websocket = object.__new__(cdp._WebSocket)
        websocket._sock = ChunkSocket()
        websocket._buf = bytearray()
        websocket._max_message_bytes = 8
        with (
            mock.patch("time.monotonic", side_effect=(0.0, 2.0)),
            self.assertRaisesRegex(cdp.CdpError, "deadline expired"),
        ):
            websocket._recv_exact(2, deadline=1.0)


class PdfOfflineSelftestIsolationTests(unittest.TestCase):
    def test_pdf_selftest_checks_open_no_socket_and_launch_no_browser(self) -> None:
        from u2s import selftest

        blocked = AssertionError("offline selftest crossed an I/O boundary")
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch("socket.create_connection", side_effect=blocked),
                mock.patch("socket.getaddrinfo", side_effect=blocked),
                mock.patch("subprocess.Popen", side_effect=blocked),
            ):
                checks = selftest.run_checks(Path(tmp))
        self.assertTrue(checks.passed, checks.results)


CHROMIUM = os.environ.get("URL_TO_SCREENSHOT_BROWSER") or next(
    (
        found
        for name in (
            "chrome",
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
        )
        if (found := shutil.which(name))
    ),
    None,
)


@unittest.skipUnless(CHROMIUM, "local Chromium is not installed")
class ChromiumCaptureSecurityFixtureTests(unittest.TestCase):
    def test_oneshot_png_publication_replaces_output_symlink_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = root / "page.html"
            fixture.write_text(
                "<!doctype html><style>body{background:#246}</style><h1>safe</h1>",
                encoding="utf-8",
            )
            victim = root / "victim.png"
            victim.write_bytes(b"untouched")
            out = root / "capture.png"
            out.symlink_to(victim)
            captured = subprocess.run(
                [
                    sys.executable,
                    str(RUNTIME),
                    "capture",
                    "--url",
                    fixture.as_uri(),
                    "--out",
                    str(out),
                    "--browser",
                    str(CHROMIUM),
                    "--allow-file-urls",
                    "--engine",
                    "oneshot",
                    "--consent",
                    "off",
                    "--wait",
                    "0",
                    "--timeout",
                    "30000",
                ],
                capture_output=True,
                text=True,
                timeout=45,
            )
            self.assertEqual(captured.returncode, 0, captured.stdout + captured.stderr)
            payload = json.loads(captured.stdout)
            self.assertEqual(payload["status"], "CAPTURED")
            self.assertEqual(victim.read_bytes(), b"untouched")
            self.assertFalse(out.is_symlink())
            self.assertGreater(payload["bytes"], 0)
            self.assertEqual(len(payload["sha256"]), 64)

    def test_real_redirect_chain_exceeding_cap_is_blocked(self) -> None:
        class RedirectHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
                try:
                    hop = int(self.path.strip("/") or "0")
                except ValueError:
                    hop = 0
                if hop <= 5:
                    self.send_response(302)
                    self.send_header("Location", f"/{hop + 1}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = b"<!doctype html><title>redirected</title><p>done</p>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args) -> None:
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "must-not-exist.png"
                captured = subprocess.run(
                    [
                        sys.executable,
                        str(RUNTIME),
                        "capture",
                        "--url",
                        f"http://127.0.0.1:{server.server_port}/0",
                        "--out",
                        str(out),
                        "--browser",
                        str(CHROMIUM),
                        "--allow-private-targets",
                        "--same-origin-only",
                        "--engine",
                        "cdp",
                        "--consent",
                        "off",
                        "--wait",
                        "0",
                        "--timeout",
                        "30000",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                self.assertEqual(
                    captured.returncode, 2, captured.stdout + captured.stderr
                )
                payload = json.loads(captured.stdout)
                self.assertEqual(payload["status"], "BLOCKED_REDIRECT_LIMIT")
                self.assertEqual(payload["origin_policy"], "scheme-host-port")
                self.assertFalse(out.exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_fixed_viewport_scroller_is_captured_to_its_full_dom_extent(self) -> None:
        html = """<!doctype html>
<style>
html, body { margin: 0; height: 100%; overflow: hidden; }
.scroller { height: 100vh; overflow: auto; }
.row { height: 500px; }
.row:nth-child(odd) { background: #184e77; }
.row:nth-child(even) { background: #f4a261; }
</style>
<main class="scroller">
  <div class="row">one</div><div class="row">two</div>
  <div class="row">three</div><div class="row">four</div>
  <div class="row">five</div><div class="row">six</div>
  <div class="row">seven</div><div class="row">eight</div>
</main>
"""
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "overflow.html"
            fixture.write_text(html, encoding="utf-8")
            out = Path(tmp) / "full.png"
            captured = subprocess.run(
                [
                    sys.executable,
                    str(RUNTIME),
                    "capture",
                    "--url",
                    fixture.as_uri(),
                    "--out",
                    str(out),
                    "--browser",
                    str(CHROMIUM),
                    "--allow-file-urls",
                    "--same-origin-only",
                    "--engine",
                    "cdp",
                    "--full-page",
                    "--viewport",
                    "640x400",
                    "--consent",
                    "off",
                    "--wait",
                    "0",
                    "--timeout",
                    "30000",
                ],
                capture_output=True,
                text=True,
                timeout=45,
            )
            self.assertEqual(captured.returncode, 0, captured.stdout + captured.stderr)
            payload = json.loads(captured.stdout)
            self.assertEqual(payload["status"], "CAPTURED")
            self.assertTrue(payload["navigation_complete"])
            self.assertEqual(payload["document_ready_state"], "complete")
            self.assertTrue(payload["full_page_complete"])
            self.assertGreaterEqual(payload["document_height"], 4000)
            self.assertGreaterEqual(payload["height"], 4000)
            self.assertGreater(payload["bytes"], 0)
            self.assertEqual(len(payload["sha256"]), 64)
            self.assertEqual(payload["origin_policy"], "scheme-host-port")
            self.assertTrue(out.is_file())


@unittest.skipUnless(CHROMIUM, "local Chromium is not installed")
class ChromiumPdfFixtureTests(unittest.TestCase):
    def test_trusted_tall_fixture_prints_and_verifies_as_multipage(self) -> None:
        fixture = U2S_ROOT / "u2s" / "htmlfixtures" / "tall.html"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tall.pdf"
            printed = subprocess.run(
                [
                    sys.executable,
                    str(RUNTIME),
                    "print-pdf",
                    "--url",
                    fixture.as_uri(),
                    "--out",
                    str(out),
                    "--browser",
                    str(CHROMIUM),
                    "--allow-file-urls",
                    "--same-origin-only",
                    "--no-sandbox",
                    "--consent",
                    "off",
                    "--wait",
                    "0",
                    "--timeout",
                    "30000",
                ],
                capture_output=True,
                text=True,
                timeout=45,
            )
            self.assertEqual(printed.returncode, 0, printed.stdout + printed.stderr)
            printed_payload = json.loads(printed.stdout)
            self.assertEqual(printed_payload["status"], "PDF_PRINTED")
            self.assertTrue(printed_payload["navigation_complete"])
            self.assertTrue(printed_payload["print_background"])
            self.assertTrue(printed_payload["prefer_css_page_size"])
            self.assertEqual(printed_payload["media"], "print")
            self.assertTrue(printed_payload["same_origin_only"])
            self.assertRegex(
                printed_payload["browser"]["version"],
                r"(?:Chromium|Chrome).*[0-9]",
            )
            self.assertRegex(printed_payload["runtime_version"], r"^\d+\.\d+\.\d+$")

            verified = subprocess.run(
                [sys.executable, str(RUNTIME), "verify-pdf", "--pdf", str(out)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
            verified_payload = json.loads(verified.stdout)
            self.assertEqual(verified_payload["final_verdict"], "UNVERIFIED")
            self.assertEqual(verified_payload["status"], "STRUCTURALLY_VALID")
            self.assertTrue(verified_payload["structurally_valid"])
            self.assertGreaterEqual(verified_payload["page_count"], 2)
            self.assertEqual(len(verified_payload["sha256"]), 64)

    def test_trusted_fixture_subresource_ssrf_aborts_before_pdf_write(self) -> None:
        fixture = U2S_ROOT / "u2s" / "htmlfixtures" / "ssrf.html"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "must-not-exist.pdf"
            printed = subprocess.run(
                [
                    sys.executable,
                    str(RUNTIME),
                    "print-pdf",
                    "--url",
                    fixture.as_uri(),
                    "--out",
                    str(out),
                    "--browser",
                    str(CHROMIUM),
                    "--allow-file-urls",
                    "--no-sandbox",
                    "--consent",
                    "off",
                    "--wait",
                    "800",
                    "--timeout",
                    "30000",
                ],
                capture_output=True,
                text=True,
                timeout=45,
            )
            self.assertEqual(printed.returncode, 2, printed.stdout + printed.stderr)
            payload = json.loads(printed.stdout)
            self.assertIn(
                payload["status"],
                {"BLOCKED_METADATA_ENDPOINT", "BLOCKED_PRIVATE_ADDRESS"},
            )
            self.assertFalse(out.exists(), "no PDF may be published after an intercepted SSRF hit")


if __name__ == "__main__":
    unittest.main()
