"""Tier-2 CDP launch + capture using the standard library.

Security posture (see references/engine-and-cdp.md and SKILL.md Security notes):
  * Launch headless bound to loopback with ``--remote-debugging-port=0`` and
    ``--remote-debugging-address=127.0.0.1``, and with NO ``--remote-allow-origins``
    flag at all. The stdlib websocket client sends NO ``Origin`` header, so
    Chromium's default-deny of Origin-bearing CDP applies; a scoped
    ``--remote-allow-origins`` at a guessed port would only OPEN a hole for a
    forged Origin, so it is never added.
  * Pin the validated initial host with ``--host-resolver-rules="MAP host ip"``
    (defeats same-host rebind only, kept as defense-in-depth).
  * The CDP ``Fetch`` domain is the PRIMARY browser-side SSRF control. ``Fetch``
    is enabled with a catch-all request-stage pattern BEFORE ``Page.navigate``,
    so every request (the main navigation, every redirect hop, and every
    sub-resource / JS-initiated fetch) is PAUSED before it is sent. Each paused
    request is re-validated -- scheme allow-list plus a fresh resolve-and-check
    of every resolved IP (metadata denied unconditionally; private/loopback/
    link-local denied unless ``--allow-private-targets``). A violating request is
    failed with ``Fetch.failRequest({errorReason:"AccessDenied"})`` so the body
    is never fetched, and the capture is aborted with the matching ``BLOCKED_*``
    status. This is true interception: the request is blocked BEFORE send, not
    observed after the fact. Redirects are capped at ``MAX_REDIRECTS``.

    v1 abort policy: ANY private/metadata hit (main frame OR sub-resource) aborts
    the whole capture. This is the simplest fail-closed choice -- a sub-resource
    SSRF attempt is treated as hard a failure as a main-frame one, so no partial
    screenshot is produced after a blocked private/metadata fetch.

The argv builder, the full-page clip builder, and the pure ``fetch_decision``
helper are pure functions so the offline selftest validates them without a
browser. The actual launch, ``/json`` discovery, websocket handshake,
``Page.navigate``, the ``Fetch`` interception loop, and ``Page.captureScreenshot``
live in ``run_cdp_capture`` and its helpers; every standard-library
``socket``/``http`` import there is local to those functions, so the launch path
is unreachable from the selftest import graph (the selftest imports only the pure
helpers above).
"""

from __future__ import annotations

from dataclasses import dataclass

# Decompression-bomb pixel cap: a requested capture area above this is refused
# before any capture. 100 megapixels is generous for full-page captures.
MAX_CAPTURE_PIXELS = 100_000_000
MAX_REDIRECTS = 5

BLOCKED_INPUT = "BLOCKED_INPUT"


@dataclass
class CdpLaunchSpec:
    browser_path: str
    user_data_dir: str
    width: int = 1280
    height: int = 800
    device_scale: float = 1.0
    no_sandbox: bool = False
    resolver_pin: tuple[str, str] | None = None  # (validated host, validated ip)


def build_cdp_launch_argv(spec: CdpLaunchSpec) -> list[str]:
    """Build the headless CDP launch argv.

    Invariant asserted by tests: the result contains NEITHER
    ``--remote-allow-origins=*`` NOR any ``--remote-allow-origins=...`` value, and
    binds the debugging endpoint to ``127.0.0.1`` on an ephemeral port (0).
    """
    argv = [
        spec.browser_path,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--disable-extensions",
        "--remote-debugging-port=0",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={spec.user_data_dir}",
        f"--window-size={spec.width},{spec.height}",
        f"--force-device-scale-factor={spec.device_scale:g}",
    ]
    if spec.no_sandbox:
        argv.append("--no-sandbox")
    if spec.resolver_pin is not None:
        host, ip = spec.resolver_pin
        argv.append(f'--host-resolver-rules=MAP {host} {ip}')
    # Deliberately NO --remote-allow-origins flag (see module docstring).
    return argv


def client_request_headers() -> dict[str, str]:
    """Headers the stdlib CDP websocket client sends.

    No ``Origin`` header -- relying on Chromium's default-deny of Origin-bearing
    CDP. Asserted by the security tests.
    """
    return {
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Version": "13",
    }


@dataclass
class FullPageClip:
    x: float
    y: float
    width: float
    height: float
    scale: float

    def pixel_area(self) -> float:
        return (self.width * self.scale) * (self.height * self.scale)

    def to_cdp(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "scale": self.scale,
        }


def build_full_page_clip(layout_metrics: dict, device_scale: float) -> FullPageClip:
    """Build the full-page ``Page.captureScreenshot`` clip from ``getLayoutMetrics``.

    Uses ``cssContentSize`` (CSS px) for width/height and passes
    ``scale=device-scale-factor``. Raises ``ValueError`` (BLOCKED_INPUT) when the
    requested pixel area (``w*h*scale^2``) exceeds the decompression-bomb cap,
    BEFORE any capture.
    """
    css = layout_metrics.get("cssContentSize") or layout_metrics.get("contentSize")
    if not css:
        raise ValueError(f"{BLOCKED_INPUT}: getLayoutMetrics has no cssContentSize")
    width = float(css.get("width", 0))
    height = float(css.get("height", 0))
    if width <= 0 or height <= 0:
        raise ValueError(f"{BLOCKED_INPUT}: non-positive content size")
    clip = FullPageClip(x=0.0, y=0.0, width=width, height=height, scale=float(device_scale))
    if clip.pixel_area() > MAX_CAPTURE_PIXELS:
        raise ValueError(
            f"{BLOCKED_INPUT}: requested area {clip.pixel_area():.0f}px exceeds cap {MAX_CAPTURE_PIXELS}"
        )
    return clip


def revalidate_request_address(literal: str, *, allow_private: bool = False) -> None:
    """Per-request CDP re-validation hook for a single freshly-resolved address.

    Delegates to the security module so the same IP rules apply on every hop.
    Raises ``security.TargetBlocked`` on a metadata/private violation.
    """
    from . import security

    security.revalidate_resolved_address(literal, allow_private=allow_private)


def fetch_decision(
    url: str,
    resolved_ips: list[str],
    *,
    allow_private: bool = False,
    allow_file_urls: bool = False,
) -> str:
    """Decide whether a paused ``Fetch.requestPaused`` request may proceed.

    Pure (no socket, no resolve): the caller passes the already-resolved IP
    literals for the request host. Returns ``"continue"`` when the request is
    admissible and ``"fail"`` when it must be blocked before send. The rules
    mirror the admission gate:

      * scheme allow-list -- only ``http``/``https`` (and ``file:`` only when
        ``allow_file_urls`` is set, for the trusted-fixture context);
      * every resolved IP is re-checked -- the metadata denylist is
        unconditional, and private/loopback/link-local is denied unless
        ``allow_private`` is set.

    A ``file:`` request has no remote host, so when it is admitted by the scheme
    check ``resolved_ips`` is irrelevant and need not be supplied. This helper is
    the offline-testable twin of the in-loop re-validation in ``run_cdp_capture``
    (which uses ``security.revalidate_resolved_address`` to also learn the precise
    ``BLOCKED_*`` reason).
    """
    from urllib.parse import urlsplit

    from . import security

    scheme = urlsplit(url).scheme.lower()
    if scheme == "file":
        return "continue" if allow_file_urls else "fail"
    if scheme not in security.ALLOWED_SCHEMES:
        return "fail"
    for literal in resolved_ips:
        try:
            security.revalidate_resolved_address(literal, allow_private=allow_private)
        except security.TargetBlocked:
            return "fail"
    return "continue"


# ---------------------------------------------------------------------------
# Real CDP runner (LAZY: every socket/http import below is function-local, so the
# selftest import graph -- which only touches the pure builders above -- never
# reaches a socket or a subprocess).
# ---------------------------------------------------------------------------

BLOCKED_TIMEOUT = "BLOCKED_TIMEOUT"
BLOCKED_PRIVATE_ADDRESS = "BLOCKED_PRIVATE_ADDRESS"
BLOCKED_METADATA_ENDPOINT = "BLOCKED_METADATA_ENDPOINT"
BLOCKED_SCHEME = "BLOCKED_SCHEME"
BLOCKED_ENVIRONMENT = "BLOCKED_ENVIRONMENT"


class CdpError(RuntimeError):
    """A recoverable CDP-runner failure (the engine may fall back to Tier-1)."""


class _WebSocket:
    """A minimal RFC 6455 client over a raw stdlib socket (text frames only).

    Implements just enough for the DevTools JSON protocol: the SHA-1 ``Sec-
    WebSocket-Key`` handshake, masked client text frames, and unmasked-server
    frame decode with continuation + control-frame handling. No ``Origin`` header
    is sent (Chromium default-deny of Origin-bearing CDP applies).
    """

    _GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, host: str, port: int, path: str, *, timeout: float):
        import socket as _socket

        self._socket_mod = _socket
        self._sock = _socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buf = b""
        self._handshake(host, port, path)

    def _handshake(self, host: str, port: int, path: str) -> None:
        import base64
        import hashlib
        import os as _os

        key = base64.b64encode(_os.urandom(16)).decode("ascii")
        # Deliberately NO Origin header (see module docstring / client_request_headers).
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode("ascii"))
        expected = base64.b64encode(
            hashlib.sha1((key + self._GUID).encode("ascii")).digest()
        ).decode("ascii")
        header = self._read_until(b"\r\n\r\n")
        text = header.decode("latin-1")
        if " 101 " not in text.split("\r\n", 1)[0]:
            raise CdpError(f"websocket handshake rejected: {text.splitlines()[0]!r}")
        if expected not in text:
            raise CdpError("websocket handshake accept-key mismatch")

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise CdpError("connection closed during handshake")
            self._buf += chunk
        head, _, rest = self._buf.partition(marker)
        self._buf = rest
        return head + marker

    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise CdpError("connection closed mid-frame")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send_text(self, text: str) -> None:
        import os as _os
        import struct as _struct

        payload = text.encode("utf-8")
        header = bytearray([0x81])  # FIN + text opcode
        length = len(payload)
        mask = _os.urandom(4)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += _struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += _struct.pack(">Q", length)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def recv_text(self) -> str:
        import struct as _struct

        while True:
            first2 = self._recv_exact(2)
            opcode = first2[0] & 0x0F
            masked = first2[1] & 0x80
            length = first2[1] & 0x7F
            if length == 126:
                (length,) = _struct.unpack(">H", self._recv_exact(2))
            elif length == 127:
                (length,) = _struct.unpack(">Q", self._recv_exact(8))
            mask_key = self._recv_exact(4) if masked else b""
            data = self._recv_exact(length)
            if masked:
                data = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:  # close
                raise CdpError("websocket closed by peer")
            if opcode in (0x9, 0xA):  # ping/pong control frames
                continue
            if opcode in (0x1, 0x0):  # text / continuation
                return data.decode("utf-8", "replace")
            # Binary or unexpected opcode: ignore and keep reading.

    def set_timeout(self, timeout: float) -> None:
        """Adjust the underlying socket read timeout (used to bound the settle pump)."""
        self._sock.settimeout(timeout)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class _CdpSession:
    """Sequential request/response over a single DevTools page websocket.

    Buffers protocol events (e.g. ``Page.loadEventFired``) so the runner can
    inspect them between commands. When ``Fetch`` interception is enabled, EVERY
    read loop (``call``, ``wait_event``, ``navigate_with_fetch``, ``pump_until``)
    routes each received message through ``_dispatch``, so a ``Fetch.requestPaused``
    is re-validated and continued/failed-before-send no matter which command is
    in flight -- including the consent eval, the settle window, and the
    screenshot. This closes the post-load window: a JS-initiated fetch to a
    private/metadata host after ``loadEventFired`` is still paused and blocked.
    """

    def __init__(self, ws: "_WebSocket"):
        self._ws = ws
        self._next_id = 0
        self.events: list[dict] = []
        # Fetch-interception state (off until enable_fetch_interception):
        self._intercept = False
        self._allow_private = False
        self._allow_file_urls = False
        self._redirects = 0

    def enable_fetch_interception(self, *, allow_private: bool, allow_file_urls: bool) -> None:
        """Turn on in-loop ``Fetch.requestPaused`` handling for every read loop.

        ``Fetch.enable`` itself is issued by the caller via ``call`` BEFORE
        ``Page.navigate``; this only records the policy used to resolve each
        paused request.
        """
        self._intercept = True
        self._allow_private = allow_private
        self._allow_file_urls = allow_file_urls
        self._redirects = 0

    def _dispatch(self, message: dict) -> None:
        """Handle one received CDP message: resolve a paused Fetch or buffer an event.

        Raises ``_FetchBlocked`` (SSRF violation, request failed before send) or
        ``CdpError`` (redirect cap exceeded) to abort the capture.
        """
        method = message.get("method")
        if self._intercept and method == "Fetch.requestPaused":
            params = message.get("params", {})
            request_id = params.get("requestId")
            status_code = params.get("responseStatusCode")
            if status_code is not None and 300 <= int(status_code) < 400:
                self._redirects += 1
                if self._redirects > MAX_REDIRECTS:
                    self.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "AccessDenied"})
                    raise CdpError(f"redirect chain exceeded {MAX_REDIRECTS} hops")
            try:
                _resolve_paused_request(
                    params, allow_private=self._allow_private, allow_file_urls=self._allow_file_urls
                )
            except _FetchBlocked:
                # Fail the request BEFORE it is sent, then abort the capture.
                self.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "AccessDenied"})
                raise
            self.send("Fetch.continueRequest", {"requestId": request_id})
            return
        if method is not None:
            self.events.append(message)

    def call(self, method: str, params: dict | None = None, *, deadline: float) -> dict:
        import json as _json
        import time as _time

        self._next_id += 1
        msg_id = self._next_id
        self._ws.send_text(_json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            if _time.monotonic() > deadline:
                raise CdpError(f"{BLOCKED_TIMEOUT}: timed out awaiting {method}")
            message = _json.loads(self._ws.recv_text())
            if message.get("id") == msg_id:
                if "error" in message:
                    raise CdpError(f"CDP {method} error: {message['error']}")
                return message.get("result", {})
            self._dispatch(message)

    def wait_event(self, method: str, *, deadline: float) -> dict | None:
        import json as _json
        import time as _time

        for event in self.events:
            if event.get("method") == method:
                return event
        while _time.monotonic() <= deadline:
            try:
                message = _json.loads(self._ws.recv_text())
            except CdpError:
                return None
            self._dispatch(message)
            if message.get("method") == method:
                return message
        return None

    def drain_events(self) -> list[dict]:
        out = self.events
        self.events = []
        return out

    def send(self, method: str, params: dict | None = None) -> None:
        """Send a fire-and-forget CDP command (no response wait).

        Used inside the ``Fetch`` interception path, where the ack to a
        ``Fetch.continueRequest``/``failRequest`` interleaves with further
        ``Fetch.requestPaused`` events and the navigation's own messages.
        """
        import json as _json

        self._next_id += 1
        self._ws.send_text(_json.dumps({"id": self._next_id, "method": method, "params": params or {}}))

    def navigate_with_fetch(self, url: str, *, deadline: float) -> None:
        """Send ``Page.navigate`` and pump messages until ``Page.loadEventFired``.

        ``enable_fetch_interception`` MUST already be active so ``_dispatch``
        re-validates each paused request. Raises ``_FetchBlocked`` (an SSRF
        violation -> the runner returns the carried ``BLOCKED_*``) or ``CdpError``
        (redirect cap / timeout).
        """
        import json as _json
        import time as _time

        self.send("Page.navigate", {"url": url})
        while True:
            if _time.monotonic() > deadline:
                raise CdpError(f"{BLOCKED_TIMEOUT}: timed out awaiting load with Fetch interception")
            message = _json.loads(self._ws.recv_text())
            self._dispatch(message)
            if message.get("method") == "Page.loadEventFired":
                return

    def pump_until(self, until: float, *, deadline: float) -> None:
        """Keep resolving paused requests during the settle window up to ``until``.

        The settle wait used to be a bare ``sleep``; under interception a JS fetch
        fired after load would otherwise sit paused (and so unvalidated) until the
        deadline. This pumps the socket so each such request is re-validated and
        continued/failed-before-send. Returns when ``until`` is reached; re-raises
        ``_FetchBlocked``/``CdpError`` on a violation. A short per-read timeout
        keeps it responsive and tolerates an idle socket (no events during
        settle); the original timeout is restored before returning.
        """
        import json as _json
        import time as _time

        cap = min(until, deadline)
        previous = max(0.05, cap - _time.monotonic())
        try:
            while True:
                remaining = cap - _time.monotonic()
                if remaining <= 0:
                    return
                self._ws.set_timeout(min(0.2, remaining))
                try:
                    text = self._ws.recv_text()
                except CdpError:
                    return
                except OSError:
                    continue  # idle read timeout: no message this slice, keep pumping
                self._dispatch(_json.loads(text))
        finally:
            self._ws.set_timeout(previous)


def _discover_page_target(port: int, *, deadline: float) -> tuple[str, str]:
    """Return ``(ws_path, target_id)`` for the page target via ``/json``.

    Uses the stdlib ``http.client`` against loopback only.
    """
    import http.client as _http
    import json as _json
    import time as _time
    from urllib.parse import urlsplit as _urlsplit

    last_err = ""
    while _time.monotonic() <= deadline:
        try:
            conn = _http.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/json")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", "replace")
            conn.close()
            targets = _json.loads(body)
            for target in targets:
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                    ws_url = target["webSocketDebuggerUrl"]
                    return _urlsplit(ws_url).path, target.get("id", "")
        except (OSError, ValueError) as exc:
            last_err = str(exc)
        _time.sleep(0.1)
    raise CdpError(f"{BLOCKED_TIMEOUT}: no page target on /json ({last_err})")


def _read_devtools_port(profile_dir, *, deadline: float) -> int:
    """Read the ephemeral DevTools port Chromium writes to ``DevToolsActivePort``."""
    import time as _time
    from pathlib import Path as _Path

    port_file = _Path(profile_dir) / "DevToolsActivePort"
    while _time.monotonic() <= deadline:
        if port_file.exists():
            try:
                first = port_file.read_text(encoding="utf-8").splitlines()[0].strip()
                return int(first)
            except (OSError, ValueError, IndexError):
                pass
        _time.sleep(0.05)
    raise CdpError(f"{BLOCKED_TIMEOUT}: DevToolsActivePort never appeared")


@dataclass
class CdpCaptureRequest:
    browser_path: str
    url: str
    out_path: str
    width: int = 1280
    height: int = 800
    device_scale: float = 1.0
    full_page: bool = False
    consent: bool = True
    wait_ms: int = 800
    timeout_ms: int = 30000
    no_sandbox: bool = False
    allow_private: bool = False
    allow_file_urls: bool = False
    resolver_pin: tuple[str, str] | None = None


class _FetchBlocked(Exception):
    """An intercepted request was failed before send; abort the capture.

    Carries the ``BLOCKED_*`` status the runner returns. Distinct from
    ``security.TargetBlocked`` so the runner can also abort on a redirect-cap
    breach (a ``CdpError``) without conflating the two.
    """

    def __init__(self, status: str, detail: str = ""):
        super().__init__(f"{status}: {detail}" if detail else status)
        self.status = status
        self.detail = detail


def _resolve_paused_request(params: dict, *, allow_private: bool, allow_file_urls: bool) -> None:
    """Re-validate one ``Fetch.requestPaused`` event; raise to abort, return to continue.

    Pure-ish: it resolves the request host (function-local ``security.resolve_host``,
    no socket import here -- ``security`` owns it) and re-checks every resolved IP.
    Raises ``_FetchBlocked`` with the precise ``BLOCKED_*`` status on a scheme or
    IP violation so the request is failed before send and the capture aborts.
    """
    from urllib.parse import urlsplit

    from . import security

    url = params.get("request", {}).get("url", "")
    scheme = urlsplit(url).scheme.lower()
    if scheme == "file":
        if not allow_file_urls:
            raise _FetchBlocked(BLOCKED_SCHEME, f"file: request blocked: {url!r}")
        return  # trusted local fixture: no remote host, no IP check
    if scheme not in security.ALLOWED_SCHEMES:
        raise _FetchBlocked(BLOCKED_SCHEME, f"scheme {scheme or '(none)'!r} blocked: {url!r}")
    host = urlsplit(url).hostname or ""
    if not host:
        raise _FetchBlocked(BLOCKED_INPUT, f"request has no host: {url!r}")
    try:
        resolved = security.resolve_host(host)
    except security.TargetBlocked as exc:
        raise _FetchBlocked(exc.reason, exc.detail) from exc
    for literal in resolved:
        try:
            security.revalidate_resolved_address(literal.strip("[]"), allow_private=allow_private)
        except security.TargetBlocked as exc:
            raise _FetchBlocked(exc.reason, exc.detail) from exc


def run_cdp_capture(request: CdpCaptureRequest) -> dict:
    """Launch Chromium over CDP and capture ``request.url`` to a PNG.

    Returns a result dict ``{"out_path", "width", "height", "consent_removed",
    "full_page"}`` on success, or a ``{"status": BLOCKED_*}`` dict on a blocked
    state. Raises ``CdpError`` on a recoverable CDP failure (the engine may then
    fall back to Tier-1). The browser tree and the temp profile dir are reaped in
    ``finally``.
    """
    import base64
    import os
    import subprocess
    import tempfile
    import time
    from pathlib import Path

    from . import consent as consent_mod
    from . import procctl, security

    deadline = time.monotonic() + max(1.0, request.timeout_ms / 1000.0)
    profile_dir = Path(tempfile.mkdtemp(prefix="url2png_"))
    spec = CdpLaunchSpec(
        browser_path=request.browser_path,
        user_data_dir=str(profile_dir),
        width=request.width,
        height=request.height,
        device_scale=request.device_scale,
        no_sandbox=request.no_sandbox,
        resolver_pin=request.resolver_pin,
    )
    argv = build_cdp_launch_argv(spec)
    os_name = os.name
    strategy = procctl.select_kill_strategy(os_name)
    proc = subprocess.Popen(  # noqa: S603 - argv built from validated inputs
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **procctl.popen_kwargs(os_name),
    )
    ws: _WebSocket | None = None
    try:
        if time.monotonic() > deadline:
            return {"status": BLOCKED_TIMEOUT, "reason": "timeout before launch settled"}
        port = _read_devtools_port(profile_dir, deadline=deadline)
        ws_path, _target = _discover_page_target(port, deadline=deadline)
        ws = _WebSocket("127.0.0.1", port, ws_path, timeout=max(0.2, deadline - time.monotonic()))
        session = _CdpSession(ws)

        session.call("Page.enable", deadline=deadline)
        session.call("Network.enable", deadline=deadline)
        # PRIMARY SSRF control: enable Fetch interception (catch-all, request
        # stage) BEFORE Page.navigate so every request -- main frame, redirects,
        # and sub-resources -- is paused and re-validated before it is sent.
        session.enable_fetch_interception(
            allow_private=request.allow_private, allow_file_urls=request.allow_file_urls
        )
        session.call(
            "Fetch.enable",
            {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]},
            deadline=deadline,
        )
        # Navigate under interception: each paused request is re-validated and
        # either continued or failed-before-send (aborting on a private/metadata
        # hit); redirects are capped. Returns on Page.loadEventFired.
        session.navigate_with_fetch(request.url, deadline=deadline)
        # Settle window: keep intercepting so a post-load JS fetch to a private/
        # metadata host is still paused and blocked, not merely observed.
        settle = min(max(0.0, request.wait_ms / 1000.0), max(0.0, deadline - time.monotonic()))
        if settle:
            session.pump_until(time.monotonic() + settle, deadline=deadline)

        consent_removed = False
        if request.consent:
            expr = consent_mod.build_removal_expression()
            try:
                result = session.call(
                    "Runtime.evaluate",
                    {"expression": expr, "returnByValue": True},
                    deadline=deadline,
                )
                removed = result.get("result", {}).get("value", 0)
                consent_removed = bool(removed)
            except CdpError:
                consent_removed = False

        params: dict = {"format": "png"}
        out_width = request.width
        out_height = request.height
        if request.full_page:
            metrics = session.call("Page.getLayoutMetrics", deadline=deadline)
            clip = build_full_page_clip(metrics, request.device_scale)  # bomb-cap before capture
            params["clip"] = clip.to_cdp()
            params["captureBeyondViewport"] = True
            out_width = int(round(clip.width * clip.scale))
            out_height = int(round(clip.height * clip.scale))

        shot = session.call("Page.captureScreenshot", params, deadline=deadline)
        data = shot.get("data")
        if not data:
            raise CdpError("Page.captureScreenshot returned no data")
        png_bytes = base64.b64decode(data)
        Path(request.out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(request.out_path).write_bytes(png_bytes)
        return {
            "out_path": request.out_path,
            "width": out_width,
            "height": out_height,
            "consent_removed": consent_removed,
            "full_page": request.full_page,
            "bytes": len(png_bytes),
        }
    except _FetchBlocked as exc:
        # An SSRF violation intercepted by the Fetch loop: the request was failed
        # BEFORE send, and the capture is aborted. A hard block, never a
        # silently-degraded fallback.
        return {"status": exc.status, "reason": exc.detail}
    except security.TargetBlocked as exc:
        # Defensive: any direct admission re-check that escapes is a hard block too.
        return {"status": exc.reason, "reason": exc.detail}
    except (CdpError, OSError) as exc:
        # A recoverable CDP/socket failure. If the wall-clock budget is spent it
        # is a timeout (the engine must not retry Tier-1); otherwise it is a CDP
        # failure the engine may fall back from.
        if time.monotonic() > deadline:
            return {"status": BLOCKED_TIMEOUT, "reason": f"cdp capture exceeded --timeout ({exc})"}
        return {"status": "CDP_FAILED", "reason": str(exc)}
    finally:
        if ws is not None:
            ws.close()
        strategy.kill(proc)
        procctl.cleanup_profile_dir(profile_dir)
