#!/usr/bin/env python3
"""url-to-screenshot runtime dispatcher.

Subcommands:
  doctor    report environment readiness (browser, ImageMagick, Pillow); installs nothing
  capture   capture a URL to a PNG (SSRF-gated; requires a browser)
  verify    artifact-truth gate on a captured PNG (the only thing that declares success)
  print-pdf print a URL to a bounded PDF through the guarded CDP path
  verify-pdf conservative structural gate on a printed PDF (never a visual pass)
  selftest  offline smoke (no network/browser/socket/install)

Invoke via the managed runner, e.g.:
  bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/url-to-screenshot-runtime/run_url_to_screenshot.sh doctor
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_doctor(_args: argparse.Namespace) -> int:
    from u2s import doctor

    return doctor.main([])


def cmd_capture(args: argparse.Namespace) -> int:
    from u2s import capture as capture_mod
    from u2s import security

    width, height = _parse_viewport(args)
    request = capture_mod.CaptureRequest(
        url=args.url,
        out_path=args.out or "",
        width=width,
        height=height,
        device_scale=args.device_scale,
        full_page=args.full_page,
        consent=(args.consent == "on"),
        engine=args.engine,
        wait_ms=args.wait,
        timeout_ms=args.timeout,
        allow_private=args.allow_private_targets,
        allow_file_urls=args.allow_file_urls,
        same_origin_only=args.same_origin_only,
        browser=args.browser,
        no_sandbox=args.no_sandbox,
    )
    try:
        result = capture_mod.run_capture(request)
    except security.TargetBlocked as exc:
        _emit({"status": exc.reason, "detail": exc.detail, "url": security.redact_url(args.url)})
        return 2
    _emit(result)
    return 0 if result.get("status") in {"CAPTURED", "VERIFIED"} else 2


def cmd_verify(args: argparse.Namespace) -> int:
    from u2s import verify as verify_mod

    try:
        png_bytes = verify_mod.read_png_file(args.png)
    except (OSError, ValueError) as exc:
        _emit({"final_verdict": "UNVERIFIED", "status": "BLOCKED_INPUT", "detail": str(exc)})
        return 2
    result = verify_mod.verify_png(
        png_bytes,
        expected_width=args.expected_width,
        expected_height=args.expected_height,
        consent_removed=(True if args.consent_removed else None),
    )
    _emit(result.to_dict())
    return 0 if result.ok else 2


def cmd_print_pdf(args: argparse.Namespace) -> int:
    from u2s import pdf as pdf_mod
    from u2s import security

    request = pdf_mod.PrintPdfRequest(
        url=args.url,
        out_path=args.out or "",
        media=args.media,
        print_background=args.print_background,
        prefer_css_page_size=args.prefer_css_page_size,
        consent=(args.consent == "on"),
        wait_ms=args.wait,
        timeout_ms=args.timeout,
        max_bytes=args.max_bytes,
        allow_private=args.allow_private_targets,
        allow_file_urls=args.allow_file_urls,
        same_origin_only=args.same_origin_only,
        browser=args.browser,
        no_sandbox=args.no_sandbox,
    )
    try:
        result = pdf_mod.run_print_pdf(request)
    except security.TargetBlocked as exc:
        _emit({"status": exc.reason, "detail": exc.detail, "url": security.redact_url(args.url)})
        return 2
    _emit(result)
    return 0 if result.get("status") == "PDF_PRINTED" else 2


def cmd_verify_pdf(args: argparse.Namespace) -> int:
    from u2s import pdf as pdf_mod

    result = pdf_mod.verify_pdf_file(args.pdf, max_bytes=args.max_bytes)
    _emit(result.to_dict())
    return 0 if result.structurally_valid else 2


def cmd_selftest(args: argparse.Namespace) -> int:
    from u2s import selftest

    forwarded: list[str] = []
    if args.work_dir:
        forwarded += ["--work-dir", args.work_dir]
    if args.output:
        forwarded += ["--output", args.output]
    return selftest.main(forwarded)


def _parse_viewport(args: argparse.Namespace) -> tuple[int, int]:
    if args.viewport:
        w, _, h = args.viewport.lower().partition("x")
        return int(w), int(h)
    return args.width, args.height


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="url-to-screenshot")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor").set_defaults(func=cmd_doctor)

    cap = sub.add_parser("capture")
    cap.add_argument("--url", required=True, help="target URL (http/https only, SSRF-gated)")
    cap.add_argument("--out", default=None, help="output PNG path (default: auto-named under AAS_RUNS_ROOT)")
    cap.add_argument("--viewport", default=None, help="viewport WxH (e.g. 1280x800)")
    cap.add_argument("--width", type=int, default=1280)
    cap.add_argument("--height", type=int, default=800)
    cap.add_argument("--full-page", action="store_true", dest="full_page")
    cap.add_argument("--device-scale", type=float, default=1.0, dest="device_scale")
    cap.add_argument("--wait", type=int, default=800, help="settle wait in ms")
    cap.add_argument("--timeout", type=int, default=30000, help="hard navigation cap in ms")
    cap.add_argument("--consent", choices=["on", "off"], default="on")
    cap.add_argument("--engine", choices=["auto", "oneshot", "cdp"], default="auto")
    cap.add_argument("--browser", default=None, help="browser path override (== URL_TO_SCREENSHOT_BROWSER)")
    cap.add_argument(
        "--allow-private-targets",
        action="store_true",
        dest="allow_private_targets",
        help="relax the private/loopback/link-local IP block ONLY (never scheme, never metadata)",
    )
    cap.add_argument(
        "--allow-file-urls",
        action="store_true",
        dest="allow_file_urls",
        help="allow file:// URLs for TRUSTED LOCAL FIXTURES/TESTING ONLY "
        "(enables local file reads; never use on attacker-influenceable input)",
    )
    cap.add_argument(
        "--same-origin-only",
        action="store_true",
        dest="same_origin_only",
        help="abort if any paused request scheme, canonical host, or effective port "
        "differs from the initial origin (CDP only)",
    )
    cap.add_argument(
        "--no-sandbox",
        action="store_true",
        dest="no_sandbox",
        help="force Chromium --no-sandbox (for root/container/CI hosts where the "
        "sandbox cannot initialize); recorded as sandbox=disabled",
    )
    cap.set_defaults(func=cmd_capture)

    ver = sub.add_parser("verify")
    ver.add_argument("--png", required=True, help="path to the captured PNG")
    ver.add_argument("--expected-width", type=int, default=None, dest="expected_width")
    ver.add_argument("--expected-height", type=int, default=None, dest="expected_height")
    ver.add_argument("--consent-removed", action="store_true", dest="consent_removed")
    ver.set_defaults(func=cmd_verify)

    pdf = sub.add_parser("print-pdf")
    pdf.add_argument("--url", required=True, help="target URL (http/https only, SSRF-gated)")
    pdf.add_argument("--out", default=None, help="output PDF path (default: auto-named under AAS_RUNS_ROOT)")
    pdf.add_argument("--media", choices=["print", "screen"], default="print")
    pdf.add_argument(
        "--print-background",
        action=argparse.BooleanOptionalAction,
        default=True,
        dest="print_background",
        help="include CSS backgrounds (default: true)",
    )
    pdf.add_argument(
        "--prefer-css-page-size",
        action=argparse.BooleanOptionalAction,
        default=True,
        dest="prefer_css_page_size",
        help="prefer CSS @page size (default: true)",
    )
    pdf.add_argument("--wait", type=int, default=800, help="settle wait in ms")
    pdf.add_argument("--timeout", type=int, default=30000, help="hard navigation/print cap in ms")
    pdf.add_argument(
        "--max-bytes",
        type=int,
        default=100 * 1024 * 1024,
        dest="max_bytes",
        help="decoded PDF byte cap (hard maximum: 104857600)",
    )
    pdf.add_argument("--consent", choices=["on", "off"], default="on")
    pdf.add_argument("--browser", default=None, help="browser path override (== URL_TO_SCREENSHOT_BROWSER)")
    pdf.add_argument(
        "--allow-private-targets",
        action="store_true",
        dest="allow_private_targets",
        help="relax the private/loopback/link-local IP block ONLY (never scheme, never metadata)",
    )
    pdf.add_argument(
        "--allow-file-urls",
        action="store_true",
        dest="allow_file_urls",
        help="allow file:// URLs for TRUSTED LOCAL FIXTURES/TESTING ONLY",
    )
    pdf.add_argument(
        "--same-origin-only",
        action="store_true",
        dest="same_origin_only",
        help="abort if any paused request scheme, canonical host, or effective port "
        "differs from the initial origin",
    )
    pdf.add_argument(
        "--no-sandbox",
        action="store_true",
        dest="no_sandbox",
        help="force Chromium --no-sandbox; recorded in the JSON result",
    )
    pdf.set_defaults(func=cmd_print_pdf)

    pdf_ver = sub.add_parser("verify-pdf")
    pdf_ver.add_argument("--pdf", required=True, help="path to the printed PDF")
    pdf_ver.add_argument(
        "--max-bytes",
        type=int,
        default=100 * 1024 * 1024,
        dest="max_bytes",
        help="PDF byte cap (hard maximum: 104857600)",
    )
    pdf_ver.set_defaults(func=cmd_verify_pdf)

    st = sub.add_parser("selftest")
    st.add_argument("--work-dir", default=None)
    st.add_argument("--output", default=None)
    st.set_defaults(func=cmd_selftest)
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
