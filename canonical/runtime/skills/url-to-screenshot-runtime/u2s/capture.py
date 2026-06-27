"""Capture orchestration: tier selection, SSRF admission, and capture-mode logic.

This module owns the decision logic (which tier, viewport vs full-page, the
decompression-bomb area check, the consent-blank fallback) as pure functions so
the offline selftest validates them without a browser. The actual capture
(``run_capture``) is LAZY: it imports the browser-launch modules and the
subprocess machinery only when invoked, so the selftest import graph never
reaches a browser launch or a socket.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import cdp, consent, detect, security

ENGINE_AUTO = "auto"
ENGINE_ONESHOT = "oneshot"
ENGINE_CDP = "cdp"

BLOCKED_ENVIRONMENT = "BLOCKED_ENVIRONMENT"
BLOCKED_INPUT = "BLOCKED_INPUT"
BLOCKED_TIMEOUT = "BLOCKED_TIMEOUT"


@dataclass
class CaptureRequest:
    url: str
    out_path: str = ""
    width: int = 1280
    height: int = 800
    device_scale: float = 1.0
    full_page: bool = False
    consent: bool = True
    engine: str = ENGINE_AUTO
    wait_ms: int = 800
    timeout_ms: int = 30000
    allow_private: bool = False
    allow_file_urls: bool = False
    browser: str | None = None


def choose_tier(request: CaptureRequest) -> str:
    """Select Tier-1 (oneshot) or Tier-2 (cdp) for an ``auto`` request.

    With the default ``consent=on``, ordinary captures enter Tier-2 because
    consent dismissal is a CDP DOM op. Full-page also requires Tier-2. Tier-1 is
    chosen only when consent is off and the capture is viewport-only.
    """
    if request.engine == ENGINE_ONESHOT:
        return ENGINE_ONESHOT
    if request.engine == ENGINE_CDP:
        return ENGINE_CDP
    if request.consent or request.full_page:
        return ENGINE_CDP
    return ENGINE_ONESHOT


def admit(request: CaptureRequest) -> security.AdmissionResult:
    """Run the fail-closed SSRF admission gate; raises ``TargetBlocked`` on refusal."""
    return security.validate_target_url(
        request.url,
        allow_private=request.allow_private,
        allow_file_urls=request.allow_file_urls,
    )


def resolver_pin(admission: security.AdmissionResult) -> tuple[str, str] | None:
    """The single ``--host-resolver-rules`` MAP pin of the validated initial host.

    Pins only the named initial host (defeats same-host rebind only). Returns
    ``None`` if there is no resolved IP to pin.
    """
    if not admission.resolved_ips:
        return None
    return (admission.host, admission.resolved_ips[0])


def check_full_page_area(layout_metrics: dict, device_scale: float) -> cdp.FullPageClip:
    """Build + bomb-cap-check the full-page clip BEFORE capture (delegates to cdp)."""
    return cdp.build_full_page_clip(layout_metrics, device_scale)


def consent_blank_next(full_page: bool, *, retried: bool = False) -> str:
    """Expose the consent-blank fallback decision (see consent.consent_blank_fallback)."""
    return consent.consent_blank_fallback(full_page, retried_without_consent=retried)


def resolve_browser(request: CaptureRequest) -> detect.BrowserInfo:
    """Resolve the browser; honor ``--browser`` / ``URL_TO_SCREENSHOT_BROWSER``."""
    env = dict(os.environ)
    if request.browser:
        env["URL_TO_SCREENSHOT_BROWSER"] = request.browser
    return detect.detect_browser(env=env)


def detect_sandbox_disable() -> tuple[bool, str]:
    """Decide whether ``--no-sandbox`` is needed (root or container detected).

    Returns ``(disable, reason)``. The sandbox stays ON unless we are root or
    inside a container, where the Chromium sandbox cannot initialize; the reason
    is recorded so the disable is never silent.
    """
    getuid = getattr(os, "geteuid", None)
    if getuid is not None and getuid() == 0:
        return True, "running as root (uid 0); sandbox cannot initialize"
    if os.path.exists("/.dockerenv"):
        return True, "container detected (/.dockerenv)"
    try:
        with open("/proc/1/cgroup", encoding="utf-8") as handle:
            cgroup = handle.read()
        if "docker" in cgroup or "kubepods" in cgroup or "containerd" in cgroup:
            return True, "container detected (cgroup)"
    except OSError:
        pass
    return False, ""


def _allocate_out_path(request: CaptureRequest, admission: security.AdmissionResult) -> str:
    if request.out_path:
        return request.out_path
    from . import naming

    host = admission.host or "local-file"
    return str(naming.allocate_output(host))


def run_capture(request: CaptureRequest) -> dict:
    """Execute a capture end-to-end (lazy browser launch).

    Orchestration: SSRF admission gate -> browser detection -> tier selection
    (``--consent on`` default routes to Tier-2/CDP; ``--consent off`` or
    ``--engine oneshot`` routes to Tier-1; ``--engine cdp`` forces Tier-2) ->
    launch + capture -> write the PNG and a ``result.json`` sidecar -> return the
    real path/verdict. ``auto`` falls back Tier-2 -> Tier-1 on a CDP failure (a
    ``--full-page`` request never degrades to a viewport Tier-1 capture).

    Returns a structured result dict. On a missing browser, returns
    ``BLOCKED_ENVIRONMENT`` without launching anything. The subprocess/socket
    modules are imported lazily, so this is never reached by the selftest.
    """
    admission = admit(request)  # may raise TargetBlocked
    browser = resolve_browser(request)
    if browser.status != "available" or not browser.path:
        return {
            "status": BLOCKED_ENVIRONMENT,
            "reason": "no browser available; run doctor",
            "url": security.redact_url(request.url),
        }

    no_sandbox, sandbox_reason = detect_sandbox_disable()
    out_path = _allocate_out_path(request, admission)
    pin = resolver_pin(admission)
    tier = choose_tier(request)
    timeout_s = max(1.0, request.timeout_ms / 1000.0)

    common = {
        "url": security.redact_url(request.url),
        "browser": browser.to_dict(),
        "tier": tier,
        "resolver_pin": pin,
        "private_targets_allowed": admission.private_targets_allowed,
        "sandbox": "disabled" if no_sandbox else "enabled",
    }
    if sandbox_reason:
        common["sandbox_reason"] = sandbox_reason

    result = _run_tier(
        tier, request, browser.path, out_path, pin, no_sandbox, timeout_s
    )

    # auto: fall back Tier-2 -> Tier-1 on a recoverable CDP failure. A full-page
    # request must NOT silently degrade to a viewport one-shot, so it is not
    # retried in Tier-1.
    if (
        request.engine == ENGINE_AUTO
        and tier == ENGINE_CDP
        and not request.full_page
        and result.get("status") not in (None, "VERIFIED")
        and result.get("status") not in (
            cdp.BLOCKED_PRIVATE_ADDRESS,
            cdp.BLOCKED_METADATA_ENDPOINT,
            cdp.BLOCKED_SCHEME,
            BLOCKED_INPUT,
            BLOCKED_TIMEOUT,
        )
    ):
        common["tier"] = ENGINE_ONESHOT
        common["fallback"] = f"cdp->oneshot ({result.get('status')})"
        result = _run_tier(
            ENGINE_ONESHOT, request, browser.path, out_path, pin, no_sandbox, timeout_s
        )

    payload = {**common, **result}
    _write_sidecar(out_path, payload)
    return payload


def _run_tier(
    tier: str,
    request: CaptureRequest,
    browser_path: str,
    out_path: str,
    pin: tuple[str, str] | None,
    no_sandbox: bool,
    timeout_s: float,
) -> dict:
    """Launch one tier and return a capture result dict (status + dimensions)."""
    from . import oneshot

    if tier == ENGINE_CDP:
        cdp_req = cdp.CdpCaptureRequest(
            browser_path=browser_path,
            url=request.url,
            out_path=out_path,
            width=request.width,
            height=request.height,
            device_scale=request.device_scale,
            full_page=request.full_page,
            consent=request.consent,
            wait_ms=request.wait_ms,
            timeout_ms=request.timeout_ms,
            no_sandbox=no_sandbox,
            allow_private=request.allow_private,
            allow_file_urls=request.allow_file_urls,
            resolver_pin=pin,
        )
        outcome = cdp.run_cdp_capture(cdp_req)
        if outcome.get("status"):
            return outcome  # a BLOCKED_* state
        return {
            "status": "CAPTURED",
            "out_path": outcome["out_path"],
            "width": outcome.get("width"),
            "height": outcome.get("height"),
            "consent_removed": outcome.get("consent_removed"),
            "full_page": outcome.get("full_page"),
        }

    # Tier-1 one-shot (viewport only; full-page is never routed here).
    spec = oneshot.OneShotSpec(
        browser_path=browser_path,
        url=request.url,
        out_path=out_path,
        width=request.width,
        height=request.height,
        device_scale=request.device_scale,
        no_sandbox=no_sandbox,
        wait_ms=request.wait_ms,
        resolver_pin=pin,
    )
    outcome = oneshot.run_oneshot(spec, timeout=timeout_s)
    if outcome.get("reaped"):
        return {"status": BLOCKED_TIMEOUT, "reason": "one-shot exceeded --timeout"}
    return {"status": "CAPTURED", "out_path": out_path, "consent_removed": False}


def _write_sidecar(out_path: str, payload: dict) -> None:
    from pathlib import Path

    from . import naming

    try:
        if Path(out_path).exists():
            naming.write_result_sidecar(Path(out_path), payload)
    except OSError:
        pass
