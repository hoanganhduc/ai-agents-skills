"""Offline self-test (the CI runtime smoke).

Exercises the deterministic core with the standard library only -- no network,
no browser launch, no socket, no package install. Validates the load-bearing
invariants:
  * browser-detection candidate order for synthetic linux/macos/windows layouts;
  * the fail-closed SSRF URL-admission gate (scheme, private/loopback/link-local,
    unconditional metadata denylist, opt-in private relaxation that still blocks
    metadata, env-var-alone never relaxes);
  * the CDP launch argv contains NEITHER ``--remote-allow-origins=*`` NOR any
    ``--remote-allow-origins=...`` value, the client sends no ``Origin`` header,
    and ``--host-resolver-rules`` MAP pin is present;
  * the consent selector list and removal expression;
  * viewport/full-page arg builders, full-page clip from ``cssContentSize`` with
    ``scale=device-scale-factor``, and the decompression-bomb area cap;
  * a ``--full-page`` request never silently degrades to a viewport capture;
  * the in-memory blank-output detector on synthesized PNGs;
  * the verify gate on a synth golden + blank;
  * ``import u2s.procctl`` succeeds and ``select_kill_strategy`` per ``os.name``.

Binding invariants:
  * (M3) byte inputs come only from ``u2s.pngtools``; the committed HTML capture
    fixtures are never read here, and there is no ``__file__``-relative fixture
    read in this blocking path.
  * (M2) no browser launch and no socket are reached from this import graph.

Prints a JSON body carrying BOTH the slides-to-video keys (``ok``, ``passed``,
``total``, ``failures``) AND the precedent offline-safety keys (``status``,
``smoke_mode``, ``network_required``, ``live_api_attempted``,
``package_install_attempted``, ``server_started``, ``browser_launched``). Exits
nonzero on any failure.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from . import blank as blank_mod
from . import capture, cdp, consent, detect, oneshot, pngtools, procctl, security
from . import verify as verify_mod


class _Checks:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def ok(self, name: str, cond: bool, detail: str = "") -> None:
        self.results.append((name, bool(cond), detail))

    def raises(self, name: str, fn, exc: type) -> None:
        try:
            fn()
            self.results.append((name, False, "expected exception, none raised"))
        except exc:
            self.results.append((name, True, ""))
        except Exception as other:  # wrong exception type
            self.results.append((name, False, f"raised {type(other).__name__}"))

    @property
    def passed(self) -> bool:
        return all(r[1] for r in self.results)


def _check_detection(c: _Checks, work: Path) -> None:
    # Synthetic candidate trees for all three OS layouts, probed on this host.
    linux_root = work / "linux_root"
    (linux_root / "usr" / "bin").mkdir(parents=True, exist_ok=True)
    (linux_root / "usr" / "bin" / "chromium").write_text("#!/bin/sh\n", encoding="utf-8")
    linux = detect.detect_browser(os_name="posix", candidate_root=str(linux_root), env={}, which=lambda _n: None)
    c.ok("detect_linux_install_location", linux.path is not None and linux.family == "chromium", str(linux.to_dict()))

    mac_root = work / "mac_root"
    app = mac_root / "Applications" / "Google Chrome.app" / "Contents" / "MacOS"
    app.mkdir(parents=True, exist_ok=True)
    (app / "Google Chrome").write_text("bin\n", encoding="utf-8")
    mac = detect.detect_browser(os_name="darwin", candidate_root=str(mac_root), env={}, which=lambda _n: None)
    c.ok("detect_macos_app_bundle", mac.path is not None and mac.family == "chrome", str(mac.to_dict()))

    win_root = work / "win_root"
    win_dir = win_root / "Program Files (x86)" / "Google" / "Chrome" / "Application"
    win_dir.mkdir(parents=True, exist_ok=True)
    (win_dir / "chrome.exe").write_text("exe\n", encoding="utf-8")
    win_env = {"PROGRAMFILES(X86)": r"C:\Program Files (x86)", "PROGRAMFILES": r"C:\Program Files",
               "LOCALAPPDATA": r"C:\AppData\Local"}
    win = detect.detect_browser(os_name="nt", candidate_root=str(win_root), env=win_env, which=lambda _n: None)
    c.ok("detect_windows_programfiles_x86", win.path is not None and win.family == "chrome", str(win.to_dict()))

    env_override = work / "custom-browser"
    env_override.write_text("bin\n", encoding="utf-8")
    ov = detect.detect_browser(os_name="posix", env={"URL_TO_SCREENSHOT_BROWSER": str(env_override)},
                               which=lambda _n: None)
    c.ok("detect_env_override", ov.source == "env-override" and ov.path == str(env_override))

    missing = detect.detect_browser(os_name="posix", candidate_root=str(work / "empty"), env={},
                                    which=lambda _n: None, exists=lambda _p: False, glob_fn=lambda _p: [])
    c.ok("detect_missing_fail_soft", missing.path is None and missing.status == "missing")


def _check_security(c: _Checks) -> None:
    # Public host admitted (resolve to a public literal via injected getaddrinfo-free path).
    ok_result = security.validate_target_url("https://93.184.216.34/path?token=secret#frag")
    c.ok("admit_public_ip", ok_result.host == "93.184.216.34")

    c.raises("block_scheme_file", lambda: security.validate_target_url("file:///etc/passwd"), security.TargetBlocked)
    c.raises("block_scheme_javascript", lambda: security.validate_target_url("javascript:alert(1)"),
             security.TargetBlocked)
    c.raises("block_loopback", lambda: security.validate_target_url("http://127.0.0.1/"), security.TargetBlocked)
    c.raises("block_private_10", lambda: security.validate_target_url("http://10.0.0.5/"), security.TargetBlocked)
    c.raises("block_link_local", lambda: security.validate_target_url("http://169.254.0.1/"), security.TargetBlocked)
    c.raises("block_metadata_ip", lambda: security.validate_target_url("http://169.254.169.254/latest/meta-data"),
             security.TargetBlocked)
    c.raises("block_metadata_host", lambda: security.validate_target_url("http://metadata.google.internal/"),
             security.TargetBlocked)

    # Opt-in private override relaxes layer 2 ...
    relaxed = security.validate_target_url("http://10.0.0.5/", allow_private=True)
    c.ok("override_relaxes_private", relaxed.private_targets_allowed is True and relaxed.host == "10.0.0.5")
    # ... but NEVER the metadata denylist ...
    c.raises("override_still_blocks_metadata_ip",
             lambda: security.validate_target_url("http://169.254.169.254/", allow_private=True),
             security.TargetBlocked)
    c.raises("override_still_blocks_metadata_host",
             lambda: security.validate_target_url("http://metadata.google.internal/", allow_private=True),
             security.TargetBlocked)
    # ... including the IPv4-mapped IPv6 form of a globally-classified metadata IP
    # (the Alibaba endpoint is not independently private, so the mapped form would
    # otherwise slip past under allow_private). See finding #5.
    c.raises("override_still_blocks_mapped_alibaba_metadata",
             lambda: security.validate_target_url("http://[::ffff:100.100.100.200]/", allow_private=True),
             security.TargetBlocked)
    c.raises("override_still_blocks_mapped_imds_metadata",
             lambda: security.validate_target_url("http://[::ffff:169.254.169.254]/", allow_private=True),
             security.TargetBlocked)
    # ... and NEVER the scheme allow-list.
    c.raises("override_still_blocks_scheme",
             lambda: security.validate_target_url("file:///x", allow_private=True), security.TargetBlocked)

    # file: stays BLOCKED_SCHEME without the explicit --allow-file-urls opt-in ...
    c.raises("file_blocked_without_optin",
             lambda: security.validate_target_url("file:///etc/passwd"), security.TargetBlocked)
    # ... and is admitted ONLY with the narrow trusted-fixture opt-in (no host, no SSRF check).
    file_ok = security.validate_target_url("file:///tmp/plain.html", allow_file_urls=True)
    c.ok("file_admitted_with_optin", file_ok.file_url is True and file_ok.scheme == "file")
    # The opt-in does not re-open other non-http schemes.
    c.raises("file_optin_does_not_open_javascript",
             lambda: security.validate_target_url("javascript:alert(1)", allow_file_urls=True),
             security.TargetBlocked)

    # Redaction drops query/fragment/userinfo.
    red = security.redact_url("https://user:secret@93.184.216.34:8443/p?token=abc#frag")
    c.ok("redact_drops_query", "token" not in red and "secret" not in red and red == "https://93.184.216.34:8443/p")

    # Per-request re-validation hook blocks a redirect/sub-resource to a private/metadata host.
    c.raises("revalidate_blocks_private",
             lambda: security.revalidate_resolved_address("10.0.0.5"), security.TargetBlocked)
    c.raises("revalidate_blocks_metadata",
             lambda: security.revalidate_resolved_address("169.254.169.254", allow_private=True),
             security.TargetBlocked)


def _check_cdp_argv(c: _Checks) -> None:
    spec = cdp.CdpLaunchSpec(
        browser_path="/usr/bin/chromium",
        user_data_dir="/tmp/url2png_x",
        resolver_pin=("example.com", "93.184.216.34"),
    )
    argv = cdp.build_cdp_launch_argv(spec)
    joined = " ".join(argv)
    c.ok("cdp_no_remote_allow_origins_wildcard", "--remote-allow-origins=*" not in joined)
    c.ok("cdp_no_remote_allow_origins_any", not any(a.startswith("--remote-allow-origins") for a in argv))
    c.ok("cdp_binds_loopback", "--remote-debugging-address=127.0.0.1" in argv)
    c.ok("cdp_ephemeral_port", "--remote-debugging-port=0" in argv)
    c.ok("cdp_host_resolver_pin", "--host-resolver-rules=MAP example.com 93.184.216.34" in argv)
    headers = cdp.client_request_headers()
    c.ok("cdp_client_no_origin", "Origin" not in headers and "origin" not in {k.lower() for k in headers})

    # Tier-1 oneshot argv also carries the resolver pin and no CDP/origin flag.
    o_argv = oneshot.build_oneshot_argv(
        oneshot.OneShotSpec(browser_path="/usr/bin/chromium", url="https://example.com/", out_path="/tmp/o.png",
                            resolver_pin=("example.com", "93.184.216.34"))
    )
    c.ok("oneshot_has_screenshot", any(a.startswith("--screenshot=") for a in o_argv))
    c.ok("oneshot_no_remote_allow_origins", not any(a.startswith("--remote-allow-origins") for a in o_argv))
    c.ok("oneshot_host_resolver_pin", "--host-resolver-rules=MAP example.com 93.184.216.34" in o_argv)

    # Fetch-interception decision (PRIMARY SSRF control): a paused request to a
    # metadata/private IP yields "fail" (failed before send), a public IP yields
    # "continue", a disallowed scheme yields "fail", and file: only continues with
    # the trusted-fixture opt-in. These are pure decisions (no socket).
    c.ok("fetch_blocks_metadata_ip",
         cdp.fetch_decision("http://meta/", ["169.254.169.254"], allow_private=True) == "fail")
    c.ok("fetch_blocks_private_ip",
         cdp.fetch_decision("http://priv/", ["10.0.0.5"]) == "fail")
    c.ok("fetch_blocks_mapped_metadata_ip",
         cdp.fetch_decision("http://m/", ["::ffff:169.254.169.254"], allow_private=True) == "fail")
    c.ok("fetch_continues_public_ip",
         cdp.fetch_decision("http://ok/", ["93.184.216.34"]) == "continue")
    c.ok("fetch_blocks_disallowed_scheme",
         cdp.fetch_decision("chrome://settings", []) == "fail")
    c.ok("fetch_blocks_file_without_optin",
         cdp.fetch_decision("file:///etc/passwd", []) == "fail")
    c.ok("fetch_continues_file_with_optin",
         cdp.fetch_decision("file:///tmp/x.html", [], allow_file_urls=True) == "continue")


def _check_full_page(c: _Checks) -> None:
    metrics = {"cssContentSize": {"width": 1280, "height": 4000}, "contentSize": {"width": 2560, "height": 8000}}
    clip = cdp.build_full_page_clip(metrics, device_scale=2.0)
    c.ok("clip_uses_csscontentsize", clip.width == 1280 and clip.height == 4000)
    c.ok("clip_scale_is_device_scale", clip.scale == 2.0)
    # area = (1280*2)*(4000*2) = 20.48M px, under the cap.
    c.ok("clip_area_under_cap", clip.pixel_area() <= cdp.MAX_CAPTURE_PIXELS)
    # Bomb cap fires before capture.
    huge = {"cssContentSize": {"width": 20000, "height": 20000}}
    c.raises("clip_area_bomb_cap", lambda: cdp.build_full_page_clip(huge, device_scale=4.0), ValueError)

    # A --full-page request never silently degrades to a viewport one-shot (C2).
    first = capture.consent_blank_next(full_page=True, retried=False)
    c.ok("fullpage_blank_retries_cdp", first == consent.FALLBACK_CDP_NO_CONSENT)
    second = capture.consent_blank_next(full_page=True, retried=True)
    c.ok("fullpage_blank_then_unverified", second == consent.FALLBACK_UNVERIFIED)
    viewport = capture.consent_blank_next(full_page=False, retried=False)
    c.ok("viewport_blank_drops_to_oneshot", viewport == consent.FALLBACK_ONESHOT)
    c.ok("fullpage_never_oneshot", consent.FALLBACK_ONESHOT not in (first, second))


def _check_consent(c: _Checks) -> None:
    c.ok("consent_has_selectors", len(consent.CONSENT_SELECTORS) >= 5)
    expr = consent.build_removal_expression()
    c.ok("consent_expr_queries", "querySelectorAll" in expr and "remove()" in expr)
    # Scoped to consent overlays; never age/paywall affordances.
    lowered = " ".join(consent.CONSENT_SELECTORS).lower()
    c.ok("consent_scope_only", "age" not in lowered and "paywall" not in lowered and "login" not in lowered)


def _check_blank_and_verify(c: _Checks) -> None:
    uniform = pngtools.make_uniform_png(64, 64, (255, 255, 255))
    golden = pngtools.make_two_color_png(64, 64)
    tiny = pngtools.make_tiny_png()

    c.ok("blank_uniform_true", blank_mod.is_blank(uniform).is_blank is True)
    c.ok("blank_golden_false", blank_mod.is_blank(golden).is_blank is False)
    c.ok("blank_tiny_true", blank_mod.is_blank(tiny).is_blank is True)
    metrics = blank_mod.is_blank(golden)
    c.ok("blank_metrics_dims", metrics.width == 64 and metrics.height == 64)

    v_ok = verify_mod.verify_png(golden, expected_width=64, expected_height=64)
    c.ok("verify_golden_verified", v_ok.final_verdict == verify_mod.VERIFIED, str(v_ok.to_dict()))
    v_blank = verify_mod.verify_png(uniform, expected_width=64, expected_height=64)
    c.ok("verify_blank_unverified", v_blank.final_verdict == verify_mod.UNVERIFIED)
    v_dims = verify_mod.verify_png(golden, expected_width=128, expected_height=64)
    c.ok("verify_wrong_dims_unverified", v_dims.final_verdict == verify_mod.UNVERIFIED)
    v_consent = verify_mod.verify_png(golden, expected_width=64, expected_height=64, consent_removed=True)
    c.ok("verify_consent_pass", v_consent.checks.get("consent") == verify_mod.PASS)


def _check_procctl(c: _Checks) -> None:
    import importlib

    importlib.import_module("u2s.procctl")  # T1: import succeeds on this OS
    posix = procctl.select_kill_strategy("posix")
    nt = procctl.select_kill_strategy("nt")
    c.ok("kill_posix_strategy", posix.name == "posix-killpg")
    c.ok("kill_windows_strategy", nt.name == "windows-job-object")
    c.ok("popen_kwargs_posix", procctl.popen_kwargs("posix").get("start_new_session") is True)
    c.ok("popen_kwargs_windows", "creationflags" in procctl.popen_kwargs("nt"))


def run_checks(work: Path) -> _Checks:
    c = _Checks()
    _check_detection(c, work)
    _check_security(c)
    _check_cdp_argv(c)
    _check_full_page(c)
    _check_consent(c)
    _check_blank_and_verify(c)
    _check_procctl(c)
    return c


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="url-to-screenshot selftest")
    parser.add_argument("--work-dir", default=None, help="scratch dir (default: a temp dir)")
    parser.add_argument("--output", default=None, help="write the JSON report here")
    args = parser.parse_args(argv)

    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="u2s_selftest_"))
    work.mkdir(parents=True, exist_ok=True)

    c = run_checks(work)
    report = {
        # slides-to-video keys
        "ok": c.passed,
        "passed": sum(1 for _, ok, _ in c.results if ok),
        "total": len(c.results),
        "failures": [{"check": n, "detail": d} for n, ok, d in c.results if not ok],
        # precedent offline-safety keys (machine-checked by validate_smoke_output)
        "status": "ok" if c.passed else "failed",
        "smoke_mode": "offline",
        "network_required": False,
        "live_api_attempted": False,
        "package_install_attempted": False,
        "server_started": False,
        "browser_launched": False,
        "config_written": False,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    return 0 if c.passed else 1
