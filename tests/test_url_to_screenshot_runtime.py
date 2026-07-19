from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
U2S_ROOT = REPO_ROOT / "canonical" / "runtime" / "skills" / "url-to-screenshot-runtime"
if str(U2S_ROOT) not in sys.path:
    sys.path.insert(0, str(U2S_ROOT))


class BlankDetectorTests(unittest.TestCase):
    def test_uniform_png_is_blank_by_color(self) -> None:
        from u2s import blank, pngtools

        result = blank.is_blank(pngtools.make_uniform_png(64, 64, (255, 255, 255)))
        self.assertTrue(result.is_blank)
        self.assertEqual(result.reason, "near-uniform-color")
        self.assertEqual(result.dominant_color_fraction, 1.0)

    def test_golden_png_is_not_blank(self) -> None:
        from u2s import blank, pngtools

        result = blank.is_blank(pngtools.make_two_color_png(64, 64))
        self.assertFalse(result.is_blank)
        self.assertEqual((result.width, result.height), (64, 64))

    def test_tiny_png_is_blank_by_byte_floor(self) -> None:
        from u2s import blank, pngtools

        result = blank.is_blank(pngtools.make_tiny_png())
        self.assertTrue(result.is_blank)
        self.assertEqual(result.reason, "below-byte-floor")

    def test_undecodable_bytes_are_blank_not_pass(self) -> None:
        from u2s import blank

        result = blank.is_blank(b"\x89PNG\r\n\x1a\n" + b"x" * 300)
        self.assertTrue(result.is_blank)
        self.assertEqual(result.reason, "decode-failed")


class DetectionTests(unittest.TestCase):
    def _make_tree(self, tmp: Path, rel: str) -> Path:
        target = tmp / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("bin\n", encoding="utf-8")
        return target

    def test_linux_install_location_detected(self) -> None:
        import tempfile

        from u2s import detect

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_tree(root, "usr/bin/chromium")
            info = detect.detect_browser(os_name="posix", candidate_root=str(root), env={}, which=lambda _n: None)
            self.assertIsNotNone(info.path)
            self.assertEqual(info.family, "chromium")

    def test_macos_app_bundle_detected(self) -> None:
        import tempfile

        from u2s import detect

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_tree(root, "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
            info = detect.detect_browser(os_name="darwin", candidate_root=str(root), env={}, which=lambda _n: None)
            self.assertIsNotNone(info.path)
            self.assertEqual(info.family, "chrome")

    def test_windows_programfiles_x86_detected(self) -> None:
        import tempfile

        from u2s import detect

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_tree(root, "Program Files (x86)/Google/Chrome/Application/chrome.exe")
            env = {"PROGRAMFILES(X86)": r"C:\Program Files (x86)", "PROGRAMFILES": r"C:\Program Files",
                   "LOCALAPPDATA": r"C:\AppData\Local"}
            info = detect.detect_browser(os_name="nt", candidate_root=str(root), env=env, which=lambda _n: None)
            self.assertIsNotNone(info.path)
            self.assertEqual(info.family, "chrome")

    def test_env_override_wins(self) -> None:
        import tempfile

        from u2s import detect

        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / "my-browser"
            override.write_text("bin\n", encoding="utf-8")
            info = detect.detect_browser(os_name="posix",
                                         env={"URL_TO_SCREENSHOT_BROWSER": str(override)}, which=lambda _n: None)
            self.assertEqual(info.source, "env-override")
            self.assertEqual(info.path, str(override))

    def test_missing_browser_fail_soft(self) -> None:
        from u2s import detect

        info = detect.detect_browser(os_name="posix", candidate_root="/nonexistent-root", env={},
                                     which=lambda _n: None, exists=lambda _p: False, glob_fn=lambda _p: [])
        self.assertIsNone(info.path)
        self.assertEqual(info.status, "missing")


class CdpArgvTests(unittest.TestCase):
    def _argv(self):
        from u2s import cdp

        return cdp.build_cdp_launch_argv(
            cdp.CdpLaunchSpec(browser_path="/usr/bin/chromium", user_data_dir="/tmp/url2png_x",
                              resolver_pin=("example.com", "93.184.216.34"))
        )

    def test_no_remote_allow_origins_flag_at_all(self) -> None:
        argv = self._argv()
        self.assertNotIn("--remote-allow-origins=*", argv)
        self.assertFalse(any(a.startswith("--remote-allow-origins") for a in argv))

    def test_binds_loopback_ephemeral_port(self) -> None:
        argv = self._argv()
        self.assertIn("--remote-debugging-address=127.0.0.1", argv)
        self.assertIn("--remote-debugging-port=0", argv)

    def test_host_resolver_rules_pin_present(self) -> None:
        argv = self._argv()
        self.assertIn("--host-resolver-rules=MAP example.com 93.184.216.34", argv)

    def test_client_sends_no_origin_header(self) -> None:
        from u2s import cdp

        headers = cdp.client_request_headers()
        self.assertNotIn("Origin", headers)
        self.assertNotIn("origin", {k.lower() for k in headers})


class NavigationLifecycleTests(unittest.TestCase):
    def test_stale_initial_page_load_cannot_complete_target_navigation(self) -> None:
        import json
        import time

        from u2s import cdp

        class QueuedWebSocket:
            def __init__(self) -> None:
                self.sent: list[dict] = []
                self.messages = [
                    {"method": "Page.loadEventFired", "params": {}},
                    {
                        "method": "Page.lifecycleEvent",
                        "params": {
                            "name": "load",
                            "frameId": "old-frame",
                            "loaderId": "new-tab-loader",
                        },
                    },
                    {
                        "id": 1,
                        "result": {
                            "frameId": "target-frame",
                            "loaderId": "target-loader",
                        },
                    },
                    {
                        "method": "Page.lifecycleEvent",
                        "params": {
                            "name": "load",
                            "frameId": "target-frame",
                            "loaderId": "target-loader",
                        },
                    },
                ]

            def send_text(self, body: str) -> None:
                self.sent.append(json.loads(body))

            def recv_text(self, *, deadline: float | None = None) -> str:
                self.assert_deadline = deadline
                if not self.messages:
                    raise AssertionError("navigation returned before its loader completed")
                return json.dumps(self.messages.pop(0))

        websocket = QueuedWebSocket()
        session = cdp._CdpSession(websocket)
        loader_id = session.navigate_with_fetch(
            "https://example.test/", deadline=time.monotonic() + 1
        )
        self.assertEqual(loader_id, "target-loader")
        self.assertEqual(websocket.sent[0]["method"], "Page.navigate")
        self.assertEqual(websocket.messages, [])

    def test_document_extent_refuses_incomplete_parser_state(self) -> None:
        from u2s import cdp

        class IncompleteSession:
            def call(self, method: str, params: dict | None = None, *, deadline: float):
                if method == "Page.getLayoutMetrics":
                    return {"cssContentSize": {"width": 1280, "height": 800}}
                if method == "Runtime.evaluate":
                    return {
                        "result": {
                            "value": {
                                "width": 1280,
                                "height": 800,
                                "readyState": "loading",
                                "elementsScanned": 40,
                                "elementsTotal": 40,
                                "complete": True,
                            }
                        }
                    }
                raise AssertionError(method)

        with self.assertRaisesRegex(cdp._OutputBlocked, "readiness"):
            cdp._measure_document_extent(IncompleteSession(), 99.0)


class FullPageClipTests(unittest.TestCase):
    def test_clip_uses_css_content_size_and_device_scale(self) -> None:
        from u2s import cdp

        metrics = {"cssContentSize": {"width": 1280, "height": 1600},
                   "contentSize": {"width": 2560, "height": 3200}}
        clip = cdp.build_full_page_clip(metrics, device_scale=2.0)
        self.assertEqual((clip.width, clip.height), (1280, 1600))
        self.assertEqual(clip.scale, 2.0)

    def test_clip_area_bomb_cap_fires_before_capture(self) -> None:
        from u2s import cdp

        with self.assertRaises(ValueError):
            cdp.build_full_page_clip({"cssContentSize": {"width": 20000, "height": 20000}}, device_scale=4.0)

    def test_full_page_request_never_degrades_to_viewport(self) -> None:
        from u2s import capture, consent

        # C2: full-page + consent-blank retries full-page in CDP, then UNVERIFIED;
        # it must never fall back to a viewport one-shot.
        first = capture.consent_blank_next(full_page=True, retried=False)
        second = capture.consent_blank_next(full_page=True, retried=True)
        self.assertEqual(first, consent.FALLBACK_CDP_NO_CONSENT)
        self.assertEqual(second, consent.FALLBACK_UNVERIFIED)
        self.assertNotIn(consent.FALLBACK_ONESHOT, (first, second))
        # A viewport request may drop to one-shot.
        self.assertEqual(capture.consent_blank_next(full_page=False), consent.FALLBACK_ONESHOT)

    def test_full_page_operation_expands_viewport_and_attests_extent(self) -> None:
        import base64

        from u2s import cdp, pngtools

        class FakeSession:
            def __init__(self) -> None:
                self.viewport_height = 64
                self.calls: list[tuple[str, dict]] = []

            def call(self, method: str, params: dict | None = None, *, deadline: float):
                values = params or {}
                self.calls.append((method, values))
                if method == "Page.getLayoutMetrics":
                    return {
                        "cssContentSize": {
                            "width": 64,
                            "height": self.viewport_height,
                        }
                    }
                if method == "Runtime.evaluate":
                    return {
                        "result": {
                            "value": {
                                "width": 64,
                                "height": 400,
                                "readyState": "complete",
                                "elementsScanned": 8,
                                "elementsTotal": 8,
                                "complete": True,
                            }
                        }
                    }
                if method == "Emulation.setDeviceMetricsOverride":
                    self.viewport_height = values["height"]
                    return {}
                if method == "Page.captureScreenshot":
                    encoded = base64.b64encode(
                        pngtools.make_two_color_png(64, 400)
                    ).decode("ascii")
                    return {"data": encoded}
                raise AssertionError(method)

        session = FakeSession()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "full.png"
            result = cdp._capture_png_operation(
                session,
                cdp.CdpCaptureRequest(
                    browser_path="browser",
                    url="file:///trusted.html",
                    out_path=str(out),
                    width=64,
                    height=64,
                    full_page=True,
                ),
                99.0,
                False,
            )
            self.assertTrue(out.is_file())
        viewport_calls = [
            params
            for method, params in session.calls
            if method == "Emulation.setDeviceMetricsOverride"
        ]
        self.assertEqual(viewport_calls[0]["height"], 400)
        self.assertEqual((result["width"], result["height"]), (64, 400))
        self.assertEqual(result["document_height"], 400)
        self.assertEqual(result["document_ready_state"], "complete")
        self.assertTrue(result["full_page_complete"])
        self.assertGreater(result["bytes"], 0)
        self.assertEqual(len(result["sha256"]), 64)

    def test_atomic_png_publication_replaces_symlink_without_touching_victim(self) -> None:
        from u2s import cdp, pngtools

        data = pngtools.make_two_color_png(64, 64)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            victim = root / "victim.png"
            victim.write_bytes(b"untouched")
            output = root / "capture.png"
            output.symlink_to(victim)
            width, height, digest = cdp._atomic_write_png(str(output), data)
            self.assertEqual(victim.read_bytes(), b"untouched")
            self.assertFalse(output.is_symlink())
            self.assertEqual(output.read_bytes(), data)
            self.assertEqual((width, height), (64, 64))
            self.assertEqual(len(digest), 64)
            self.assertEqual(list(root.glob(f".{output.name}.*.tmp")), [])

    def test_full_page_attestation_fails_if_document_grows_during_capture(self) -> None:
        import base64

        from u2s import cdp, pngtools

        class GrowingSession:
            def __init__(self) -> None:
                self.viewport_height = 64
                self.capture_finished = False

            def call(self, method: str, params: dict | None = None, *, deadline: float):
                values = params or {}
                if method == "Page.getLayoutMetrics":
                    return {
                        "cssContentSize": {
                            "width": 64,
                            "height": self.viewport_height,
                        }
                    }
                if method == "Runtime.evaluate":
                    height = 450 if self.capture_finished else 400
                    return {
                        "result": {
                            "value": {
                                "width": 64,
                                "height": height,
                                "readyState": "complete",
                                "elementsScanned": 8,
                                "elementsTotal": 8,
                                "complete": True,
                            }
                        }
                    }
                if method == "Emulation.setDeviceMetricsOverride":
                    self.viewport_height = values["height"]
                    return {}
                if method == "Page.captureScreenshot":
                    self.capture_finished = True
                    return {
                        "data": base64.b64encode(
                            pngtools.make_two_color_png(64, 400)
                        ).decode("ascii")
                    }
                raise AssertionError(method)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "must-not-exist.png"
            with self.assertRaisesRegex(cdp._OutputBlocked, "document grew"):
                cdp._capture_png_operation(
                    GrowingSession(),
                    cdp.CdpCaptureRequest(
                        browser_path="browser",
                        url="file:///trusted.html",
                        out_path=str(out),
                        width=64,
                        height=64,
                        full_page=True,
                    ),
                    99.0,
                    False,
                )
            self.assertFalse(out.exists())


class ConsentTests(unittest.TestCase):
    def test_selector_set_is_consent_scoped(self) -> None:
        from u2s import consent

        self.assertGreaterEqual(len(consent.CONSENT_SELECTORS), 5)
        lowered = " ".join(consent.CONSENT_SELECTORS).lower()
        self.assertNotIn("age", lowered)
        self.assertNotIn("paywall", lowered)
        self.assertNotIn("login", lowered)

    def test_removal_expression_targets_selectors(self) -> None:
        from u2s import consent

        expr = consent.build_removal_expression()
        self.assertIn("querySelectorAll", expr)
        self.assertIn("remove()", expr)


class VerifyGateTests(unittest.TestCase):
    def test_golden_is_verified(self) -> None:
        from u2s import pngtools
        from u2s import verify as verify_mod

        result = verify_mod.verify_png(pngtools.make_two_color_png(64, 64), expected_width=64, expected_height=64)
        self.assertEqual(result.final_verdict, verify_mod.VERIFIED)
        self.assertTrue(result.ok)

    def test_blank_is_unverified(self) -> None:
        from u2s import pngtools
        from u2s import verify as verify_mod

        result = verify_mod.verify_png(pngtools.make_uniform_png(64, 64), expected_width=64, expected_height=64)
        self.assertEqual(result.final_verdict, verify_mod.UNVERIFIED)
        self.assertEqual(result.checks["not_blank"], verify_mod.FAIL)

    def test_wrong_dimensions_is_unverified(self) -> None:
        from u2s import pngtools
        from u2s import verify as verify_mod

        result = verify_mod.verify_png(pngtools.make_two_color_png(64, 64), expected_width=128, expected_height=64)
        self.assertEqual(result.final_verdict, verify_mod.UNVERIFIED)
        self.assertEqual(result.checks["dimensions"], verify_mod.FAIL)


class BoundedPngVerificationTests(unittest.TestCase):
    @staticmethod
    def _crafted_png(
        width: int,
        height: int,
        decompressed: bytes,
        *,
        color_type: int = 2,
    ) -> bytes:
        import struct
        import zlib

        from u2s import pngtools

        ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
        # Keep the hostile fixture above the verifier's byte floor without making
        # either its compressed or decompressed payload materially large.
        padding = pngtools._chunk(b"tEXt", b"fixture-padding=" + b"x" * 64)
        return (
            pngtools.PNG_SIGNATURE
            + pngtools._chunk(b"IHDR", ihdr)
            + padding
            + pngtools._chunk(b"IDAT", zlib.compress(decompressed, 9))
            + pngtools._chunk(b"IEND", b"")
        )

    @staticmethod
    def _run_verify_cli(path: Path) -> tuple[int, dict]:
        import contextlib
        import io
        import json

        import url_to_screenshot_runtime as dispatcher

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = dispatcher.main(["verify", "--png", str(path)])
        return exit_code, json.loads(output.getvalue())

    def test_dimension_bomb_is_blocked_before_scanline_allocation(self) -> None:
        from u2s import pngtools
        from u2s import verify as verify_mod

        bomb = self._crafted_png(0xFFFFFFFF, 1, b"")
        result = verify_mod.verify_png(bomb)
        self.assertEqual(result.final_verdict, verify_mod.UNVERIFIED)
        self.assertEqual(result.status, verify_mod.BLOCKED_INPUT)
        self.assertIn("dimension", str(result.detail.get("decode_error", "")).lower())

        with self.assertRaises(pngtools.PngInputBlocked):
            pngtools.read_png(bomb)

    def test_decompression_bomb_is_blocked_at_expected_scanline_size(self) -> None:
        from u2s import verify as verify_mod

        # A 1x1 RGB image must decode to exactly four bytes: filter + RGB.
        bomb = self._crafted_png(1, 1, b"\x00" + b"\x00" * 64)
        result = verify_mod.verify_png(bomb)
        self.assertEqual(result.final_verdict, verify_mod.UNVERIFIED)
        self.assertEqual(result.status, verify_mod.BLOCKED_INPUT)
        self.assertIn("decompressed", str(result.detail.get("decode_error", "")).lower())

    def test_decoder_keeps_only_bounded_color_bins(self) -> None:
        from u2s import pngtools

        info = pngtools.read_png(pngtools.make_two_color_png(8, 4))
        self.assertEqual(info.bytes_per_pixel, 3)
        self.assertEqual(info.analyzed_pixels, 32)
        self.assertEqual(sum(info.color_counts), info.analyzed_pixels)
        self.assertFalse(hasattr(info, "rows"))

    def test_decoder_analyzes_every_pixel_with_fixed_bins(self) -> None:
        from u2s import pngtools

        info = pngtools.read_png(pngtools.make_two_color_png(256, 128))
        self.assertEqual(info.analyzed_pixels, 256 * 128)
        self.assertEqual(len(info.color_counts), 1 << 15)
        self.assertEqual(sum(info.color_counts), info.analyzed_pixels)

    def test_fully_transparent_rgba_is_unverified_as_blank(self) -> None:
        from u2s import verify as verify_mod

        raw = bytearray()
        for row in range(64):
            raw.append(0)
            for column in range(64):
                raw.extend(((row * 17) & 255, (column * 29) & 255, 127, 0))
        png = self._crafted_png(64, 64, bytes(raw), color_type=6)
        result = verify_mod.verify_png(png)
        self.assertEqual(result.final_verdict, verify_mod.UNVERIFIED)
        self.assertEqual(result.checks["not_blank"], verify_mod.FAIL)

    def test_sparse_colored_pixels_cannot_evade_full_blank_analysis(self) -> None:
        from u2s import verify as verify_mod

        width = height = 1000
        raw = bytearray()
        colored_remaining = 301
        for _row in range(height):
            raw.append(0)
            for _column in range(width):
                if colored_remaining:
                    raw.extend((200, 10, 10))
                    colored_remaining -= 1
                else:
                    raw.extend((255, 255, 255))
        png = self._crafted_png(width, height, bytes(raw))
        result = verify_mod.verify_png(png)
        self.assertEqual(result.final_verdict, verify_mod.UNVERIFIED)
        self.assertEqual(result.checks["not_blank"], verify_mod.FAIL)
        self.assertGreater(
            result.detail["blank"]["dominant_color_fraction"],
            0.999,
        )

    def test_tall_thin_dimension_bomb_is_blocked(self) -> None:
        from u2s import pngtools
        from u2s import verify as verify_mod

        bomb = self._crafted_png(1, pngtools.MAX_PNG_DIMENSION + 1, b"")
        result = verify_mod.verify_png(bomb)
        self.assertEqual(result.final_verdict, verify_mod.UNVERIFIED)
        self.assertEqual(result.status, verify_mod.BLOCKED_INPUT)
        self.assertIn("dimension", str(result.detail.get("decode_error", "")).lower())

    def test_missing_iend_is_rejected(self) -> None:
        from u2s import pngtools
        from u2s import verify as verify_mod

        png = pngtools.make_two_color_png(64, 64)
        result = verify_mod.verify_png(png[:-12])
        self.assertEqual(result.final_verdict, verify_mod.UNVERIFIED)
        self.assertIn("iend", str(result.detail.get("decode_error", "")).lower())

    def test_bad_chunk_crc_is_rejected(self) -> None:
        from u2s import pngtools
        from u2s import verify as verify_mod

        png = bytearray(pngtools.make_two_color_png(64, 64))
        png[29] ^= 1  # IHDR CRC begins after signature + length + tag + payload.
        result = verify_mod.verify_png(bytes(png))
        self.assertEqual(result.final_verdict, verify_mod.UNVERIFIED)
        self.assertIn("crc", str(result.detail.get("decode_error", "")).lower())

    def test_verifier_decodes_png_only_once(self) -> None:
        from unittest import mock

        from u2s import pngtools
        from u2s import verify as verify_mod

        png = pngtools.make_two_color_png(64, 64)
        original = pngtools.read_png
        with mock.patch.object(pngtools, "read_png", wraps=original) as read:
            result = verify_mod.verify_png(png)
        self.assertEqual(result.final_verdict, verify_mod.VERIFIED)
        self.assertEqual(read.call_count, 1)

    def test_capture_publication_rejects_malformed_png(self) -> None:
        from u2s import cdp
        from u2s import pngtools

        malformed = pngtools.make_two_color_png(64, 64)[:-12]
        with self.assertRaisesRegex(cdp._OutputBlocked, "invalid PNG"):
            cdp._png_metadata(malformed)

    def test_capture_publication_uses_verifier_dimension_limit(self) -> None:
        from u2s import cdp
        from u2s import pngtools

        malformed = self._crafted_png(1, pngtools.MAX_PNG_DIMENSION + 1, b"")
        with self.assertRaisesRegex(cdp._OutputBlocked, "dimension"):
            cdp._png_metadata(malformed)

    def test_oversized_viewport_is_blocked_before_browser_launch(self) -> None:
        from unittest import mock

        from u2s import cdp

        request = cdp.CdpCaptureRequest(
            browser_path="/fixture/chromium",
            url="https://example.test/",
            out_path="/tmp/never-created.png",
            width=100_001,
            height=1,
        )
        with mock.patch("subprocess.Popen") as launch:
            result = cdp.run_cdp_capture(request)
        self.assertEqual(result["status"], cdp.BLOCKED_INPUT)
        launch.assert_not_called()

    def test_verify_cli_rejects_oversized_regular_file_without_reading_it(self) -> None:
        from u2s import verify as verify_mod

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "oversized.png"
            with path.open("wb") as handle:
                handle.truncate(verify_mod.MAX_PNG_FILE_BYTES + 1)
            exit_code, result = self._run_verify_cli(path)

        self.assertEqual(exit_code, 2)
        self.assertEqual(result["final_verdict"], verify_mod.UNVERIFIED)
        self.assertEqual(result["status"], verify_mod.BLOCKED_INPUT)

    def test_verify_cli_rejects_symlink_instead_of_following_it(self) -> None:
        from u2s import pngtools
        from u2s import verify as verify_mod

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.png"
            target.write_bytes(pngtools.make_two_color_png(64, 64))
            link = root / "capture.png"
            try:
                link.symlink_to(target)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            exit_code, result = self._run_verify_cli(link)

        self.assertEqual(exit_code, 2)
        self.assertEqual(result["final_verdict"], verify_mod.UNVERIFIED)
        self.assertEqual(result["status"], verify_mod.BLOCKED_INPUT)

    def test_verify_cli_accepts_bounded_regular_png(self) -> None:
        from u2s import pngtools
        from u2s import verify as verify_mod

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.png"
            path.write_bytes(pngtools.make_two_color_png(64, 64))
            exit_code, result = self._run_verify_cli(path)

        self.assertEqual(exit_code, 0)
        self.assertEqual(result["final_verdict"], verify_mod.VERIFIED)


class ProcctlTests(unittest.TestCase):
    def test_import_procctl_succeeds_on_running_os(self) -> None:
        import importlib

        module = importlib.import_module("u2s.procctl")
        self.assertTrue(hasattr(module, "select_kill_strategy"))

    def test_select_kill_strategy_per_os_name(self) -> None:
        from u2s import procctl

        self.assertEqual(procctl.select_kill_strategy("posix").name, "posix-killpg")
        self.assertEqual(procctl.select_kill_strategy("nt").name, "windows-job-object")

    def test_popen_kwargs_per_os_name(self) -> None:
        from u2s import procctl

        self.assertTrue(procctl.popen_kwargs("posix").get("start_new_session"))
        self.assertIn("creationflags", procctl.popen_kwargs("nt"))


class EnvironmentTests(unittest.TestCase):
    def test_capture_without_browser_is_blocked_environment(self) -> None:
        from unittest import mock

        from u2s import capture, detect

        request = capture.CaptureRequest(url="https://93.184.216.34/")
        missing = detect.BrowserInfo(path=None, family="", status="missing")
        with mock.patch.object(capture, "resolve_browser", return_value=missing):
            result = capture.run_capture(request)
        self.assertEqual(result["status"], capture.BLOCKED_ENVIRONMENT)

    def test_same_origin_only_forces_cdp_and_refuses_oneshot(self) -> None:
        from u2s import capture

        auto = capture.CaptureRequest(
            url="https://93.184.216.34/",
            consent=False,
            same_origin_only=True,
        )
        self.assertEqual(capture.choose_tier(auto), capture.ENGINE_CDP)

        explicit = capture.CaptureRequest(
            url="https://93.184.216.34/",
            consent=False,
            engine=capture.ENGINE_ONESHOT,
            same_origin_only=True,
        )
        result = capture.run_capture(explicit)
        self.assertEqual(result["status"], capture.BLOCKED_INPUT)
        self.assertTrue(result["same_origin_only"])


class SelftestIsolationTests(unittest.TestCase):
    """M2/M3: the selftest import graph must not read the HTML fixtures or open sockets."""

    @staticmethod
    def _code_without_docstring(text: str) -> str:
        # Drop the module docstring so prose mentions do not count as code refs.
        import ast

        tree = ast.parse(text)
        doc = ast.get_docstring(tree, clean=False)
        if doc:
            text = text.replace(doc, "", 1)
        return text

    def test_selftest_does_not_read_html_fixtures(self) -> None:
        text = self._code_without_docstring((U2S_ROOT / "u2s" / "selftest.py").read_text(encoding="utf-8"))
        # No path construction into the committed capture-fixtures dir.
        self.assertNotIn("htmlfixtures", text)
        # No __file__-relative fixture reads in the blocking path.
        self.assertNotIn("__file__", text)
        self.assertNotIn("read_bytes", text)

    def test_selftest_byte_inputs_come_from_pngtools(self) -> None:
        text = (U2S_ROOT / "u2s" / "selftest.py").read_text(encoding="utf-8")
        self.assertIn("pngtools", text)

    def test_blank_module_does_not_read_html_fixtures(self) -> None:
        text = (U2S_ROOT / "u2s" / "blank.py").read_text(encoding="utf-8")
        self.assertNotIn("htmlfixtures", text)
        self.assertNotIn("__file__", text)


class HtmlFixtureNewlineTests(unittest.TestCase):
    """Mandatory no-CR assertion for the committed HTML capture-fixtures.

    static-check does not currently flag a CRLF .html before this change adds it,
    so this make-test assertion is the enforcement (paired with the
    `*.html text eol=lf` .gitattributes rule).
    """

    def test_committed_html_fixtures_have_no_carriage_return(self) -> None:
        fixtures_dir = U2S_ROOT / "u2s" / "htmlfixtures"
        html_files = sorted(fixtures_dir.glob("*.html"))
        self.assertTrue(html_files, "expected committed HTML capture-fixtures")
        for path in html_files:
            self.assertNotIn(b"\r", path.read_bytes(), f"{path} must be LF-only (no CR)")


if __name__ == "__main__":
    unittest.main()
