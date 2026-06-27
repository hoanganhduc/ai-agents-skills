from __future__ import annotations

import sys
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


class FullPageClipTests(unittest.TestCase):
    def test_clip_uses_css_content_size_and_device_scale(self) -> None:
        from u2s import cdp

        metrics = {"cssContentSize": {"width": 1280, "height": 4000},
                   "contentSize": {"width": 2560, "height": 8000}}
        clip = cdp.build_full_page_clip(metrics, device_scale=2.0)
        self.assertEqual((clip.width, clip.height), (1280, 4000))
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
