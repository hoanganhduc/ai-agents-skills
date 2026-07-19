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
    status. With ``same_origin_only`` enabled, a paused redirect or sub-resource
    whose canonical scheme, hostname, or effective port differs from the initial
    request is also failed before send.
    This is true interception: the request is blocked BEFORE send, not
    observed after the fact. Redirects are capped at ``MAX_REDIRECTS``.

    v1 abort policy: ANY private/metadata hit (main frame OR sub-resource) aborts
    the whole capture. This is the simplest fail-closed choice -- a sub-resource
    SSRF attempt is treated as hard a failure as a main-frame one, so no partial
    screenshot is produced after a blocked private/metadata fetch.

The argv/parameter builders, full-page clip builder, and pure ``fetch_decision``
helper are pure functions so the offline selftest validates them without a
browser. The actual launch, ``/json`` discovery, websocket handshake,
``Page.navigate``, and ``Fetch`` interception loop live in one shared guarded
runner used by ``Page.captureScreenshot`` and ``Page.printToPDF``. Every
standard-library ``socket``/``http`` import there is function-local, so the
launch path is unreachable from the offline selftest.
"""

from __future__ import annotations

from dataclasses import dataclass

from .limits import MAX_CAPTURE_PIXELS, MAX_PNG_DIMENSION, MAX_PNG_FILE_BYTES

# Decompression-bomb pixel cap: a requested capture area above the shared
# verifier boundary is refused before any capture.
MAX_REDIRECTS = 5
MAX_DOM_ELEMENTS = 50_000
MAX_FULL_PAGE_VIEWPORT_PASSES = 3
MAX_VIEWPORT_CSS_DIMENSION = 100_000
MAX_PNG_BYTES = MAX_PNG_FILE_BYTES
# Hard cap for a decoded browser-print artifact. Callers may choose a lower
# limit, but never a higher one, so ``Page.printToPDF`` cannot persist an
# unbounded result.
MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_BUFFERED_EVENTS = 64

BLOCKED_INPUT = "BLOCKED_INPUT"
BLOCKED_CROSS_ORIGIN = "BLOCKED_CROSS_ORIGIN"
BLOCKED_REDIRECT_LIMIT = "BLOCKED_REDIRECT_LIMIT"
ORIGIN_POLICY_SCHEME_HOST_PORT = "scheme-host-port"


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
    import math

    width = float(css.get("width", 0))
    height = float(css.get("height", 0))
    scale = float(device_scale)
    if (
        not math.isfinite(width)
        or not math.isfinite(height)
        or not math.isfinite(scale)
        or width <= 0
        or height <= 0
        or scale <= 0
    ):
        raise ValueError(f"{BLOCKED_INPUT}: non-positive content size")
    if width > MAX_VIEWPORT_CSS_DIMENSION or height > MAX_VIEWPORT_CSS_DIMENSION:
        raise ValueError(
            f"{BLOCKED_INPUT}: document dimensions exceed the CSS dimension cap "
            f"{MAX_VIEWPORT_CSS_DIMENSION}"
        )
    clip = FullPageClip(x=0.0, y=0.0, width=width, height=height, scale=scale)
    if clip.pixel_area() > MAX_CAPTURE_PIXELS:
        raise ValueError(
            f"{BLOCKED_INPUT}: requested area {clip.pixel_area():.0f}px exceeds cap {MAX_CAPTURE_PIXELS}"
        )
    return clip


def build_print_to_pdf_params(
    *, print_background: bool = True, prefer_css_page_size: bool = True
) -> dict:
    """Return the deterministic ``Page.printToPDF`` parameter set.

    Media emulation is a separate ``Emulation.setEmulatedMedia`` call. Keeping
    this builder pure lets the offline selftest cover the browser-print contract
    without opening a socket or launching Chromium.
    """
    return {
        "printBackground": bool(print_background),
        "preferCSSPageSize": bool(prefer_css_page_size),
        "transferMode": "ReturnAsBase64",
    }


def _atomic_write_pdf(out_path: str, pdf_bytes: bytes, *, max_bytes: int) -> None:
    """Atomically persist one bounded PDF, leaving no partial destination.

    The browser response is validated for a PDF signature and size before the
    destination is touched. The temporary file lives beside the destination so
    ``os.replace`` is atomic on the target filesystem.
    """
    import os
    import tempfile
    from pathlib import Path

    if max_bytes <= 0 or max_bytes > MAX_PDF_BYTES:
        raise _OutputBlocked(BLOCKED_INPUT, f"PDF byte limit must be in 1..{MAX_PDF_BYTES}")
    if not pdf_bytes.startswith(b"%PDF-"):
        raise _OutputBlocked("UNVERIFIED", "Page.printToPDF returned bytes without a PDF signature")
    if not pdf_bytes or len(pdf_bytes) > max_bytes:
        raise _OutputBlocked(
            "BLOCKED_OUTPUT",
            f"PDF size {len(pdf_bytes)} exceeds byte limit {max_bytes}",
        )

    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(pdf_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, destination)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _png_metadata(png_bytes: bytes) -> tuple[int, int, str]:
    """Return bounded PNG dimensions and digest before publishing the artifact."""

    import hashlib
    from . import pngtools

    try:
        info = pngtools.read_png(png_bytes)
    except ValueError as exc:
        raise _OutputBlocked(
            "UNVERIFIED",
            f"Page.captureScreenshot returned invalid PNG bytes: {exc}",
        ) from exc
    return info.width, info.height, hashlib.sha256(png_bytes).hexdigest()


def _atomic_write_png(out_path: str, png_bytes: bytes) -> tuple[int, int, str]:
    """Atomically replace the PNG destination instead of following a symlink."""

    import os
    import tempfile
    from pathlib import Path

    width, height, digest = _png_metadata(png_bytes)
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(png_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, destination)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return width, height, digest


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
    same_origin_only: bool = False,
    initial_url: str = "",
    initial_hostname: str = "",
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
      * when ``same_origin_only`` is set, the request's canonical scheme,
        hostname, and effective port must equal ``initial_url``. The legacy
        ``initial_hostname`` argument remains only for the offline selftest and
        older callers; production passes the complete initial URL.

    A ``file:`` request has no remote host, so when it is admitted by the scheme
    check ``resolved_ips`` is irrelevant and need not be supplied. This helper is
    the offline-testable twin of the in-loop re-validation in ``run_cdp_capture``
    (which uses ``security.revalidate_resolved_address`` to also learn the precise
    ``BLOCKED_*`` reason).
    """
    from urllib.parse import urlsplit

    from . import security

    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    request_hostname = parsed.hostname or ""
    if same_origin_only:
        try:
            request_origin = _canonical_origin(url)
            if initial_url:
                initial_origin = _canonical_origin(initial_url)
            else:
                # Backward-compatible hostname-only input. Bind its missing
                # scheme/port to the current request; new security-sensitive
                # callers must pass ``initial_url``.
                initial_origin = (
                    request_origin[0],
                    _canonical_hostname(initial_hostname),
                    request_origin[2],
                )
        except ValueError:
            return "fail"
        if request_origin != initial_origin:
            return "fail"
    if scheme == "file":
        return "continue" if allow_file_urls else "fail"
    if scheme not in security.ALLOWED_SCHEMES:
        return "fail"
    if not request_hostname:
        return "fail"
    for literal in resolved_ips:
        try:
            security.revalidate_resolved_address(literal, allow_private=allow_private)
        except security.TargetBlocked:
            return "fail"
    return "continue"


def _canonical_hostname(hostname: str) -> str:
    """Normalize a URL hostname for the strict same-host interception policy."""

    import ipaddress

    candidate = hostname.rstrip(".").lower()
    if not candidate:
        return ""
    try:
        return ipaddress.ip_address(candidate).compressed.lower()
    except ValueError:
        try:
            return candidate.encode("idna").decode("ascii")
        except UnicodeError:
            return candidate


def _canonical_origin(url: str) -> tuple[str, str, int | None]:
    """Return ``(scheme, canonical-host, effective-port)`` for origin checks.

    Default HTTP(S) ports are made explicit, so ``https://example/`` and
    ``https://example:443/`` compare equal. ``file:`` URLs retain their
    canonical authority (normally empty) and have no port; this keeps trusted
    local-fixture navigations within the file origin while still rejecting a
    transition to a network scheme.
    """
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    host = _canonical_hostname(parsed.hostname or "")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid URL port: {exc}") from exc
    if port is None:
        if scheme == "http":
            port = 80
        elif scheme == "https":
            port = 443
    if scheme == "file":
        port = None
    return scheme, host, port


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
    _MAX_HANDSHAKE_BYTES = 64 * 1024

    def __init__(
        self, host: str, port: int, path: str, *, timeout: float, max_message_bytes: int
    ):
        import socket as _socket

        self._socket_mod = _socket
        self._sock = _socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buf = bytearray()
        if max_message_bytes <= 0:
            raise CdpError("websocket message limit must be positive")
        self._max_message_bytes = max_message_bytes
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
        header = self._read_until(b"\r\n\r\n", max_bytes=self._MAX_HANDSHAKE_BYTES)
        text = header.decode("latin-1")
        if " 101 " not in text.split("\r\n", 1)[0]:
            raise CdpError(f"websocket handshake rejected: {text.splitlines()[0]!r}")
        if expected not in text:
            raise CdpError("websocket handshake accept-key mismatch")

    def _read_until(self, marker: bytes, *, max_bytes: int) -> bytes:
        while marker not in self._buf:
            if len(self._buf) >= max_bytes:
                raise CdpError("websocket handshake exceeds the header limit")
            chunk = self._sock.recv(4096)
            if not chunk:
                raise CdpError("connection closed during handshake")
            self._buf.extend(chunk)
            if len(self._buf) > max_bytes and marker not in self._buf:
                raise CdpError("websocket handshake exceeds the header limit")
        marker_end = self._buf.index(marker) + len(marker)
        if marker_end > max_bytes:
            raise CdpError("websocket handshake exceeds the header limit")
        head, _, rest = self._buf.partition(marker)
        self._buf = rest
        return bytes(head + marker)

    def _recv_exact(self, n: int, *, deadline: float | None = None) -> bytes:
        import time as _time

        if n < 0:
            raise CdpError("websocket read length must be non-negative")
        if not isinstance(self._buf, bytearray):
            self._buf = bytearray(self._buf)
        out = bytearray()
        if self._buf:
            take = min(n, len(self._buf))
            out.extend(self._buf[:take])
            del self._buf[:take]
        while len(out) < n:
            if deadline is not None:
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    raise CdpError(f"{BLOCKED_TIMEOUT}: websocket receive deadline expired")
                self._sock.settimeout(max(0.001, remaining))
            chunk = self._sock.recv(min(65536, n - len(out)))
            if not chunk:
                raise CdpError("connection closed mid-frame")
            out.extend(chunk)
        return bytes(out)

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

    def recv_text(self, *, deadline: float | None = None) -> str:
        import struct as _struct

        fragments: list[bytes] = []
        total = 0
        continuing = False
        while True:
            first2 = self._recv_exact(2, deadline=deadline)
            fin = bool(first2[0] & 0x80)
            if first2[0] & 0x70:
                raise CdpError("websocket frame uses unsupported RSV bits")
            opcode = first2[0] & 0x0F
            masked = first2[1] & 0x80
            if masked:
                raise CdpError("websocket server frame must not be masked")
            length = first2[1] & 0x7F
            if length == 126:
                (length,) = _struct.unpack(">H", self._recv_exact(2, deadline=deadline))
            elif length == 127:
                (length,) = _struct.unpack(">Q", self._recv_exact(8, deadline=deadline))
                if length & (1 << 63):
                    raise CdpError("websocket frame has an invalid 64-bit length")
            if opcode >= 0x8 and (not fin or length > 125):
                raise CdpError("websocket control frame is fragmented or oversized")
            if length > self._max_message_bytes or total + length > self._max_message_bytes:
                raise CdpError("websocket message exceeds the configured byte limit")
            data = self._recv_exact(length, deadline=deadline)
            if opcode == 0x8:  # close
                raise CdpError("websocket closed by peer")
            if opcode in (0x9, 0xA):  # ping/pong control frames
                continue
            if opcode == 0x1:
                if continuing:
                    raise CdpError("websocket started a new text message mid-fragment")
                fragments = [data]
                total = length
                continuing = not fin
                if fin:
                    try:
                        return data.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise CdpError("websocket text frame is not UTF-8") from exc
                continue
            if opcode == 0x0:
                if not continuing:
                    raise CdpError("websocket continuation has no initial text frame")
                fragments.append(data)
                total += length
                if fin:
                    try:
                        return b"".join(fragments).decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise CdpError("websocket text message is not UTF-8") from exc
                continue
            raise CdpError(f"websocket received unsupported opcode {opcode}")

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

    Buffers protocol events (especially loader-scoped ``Page.lifecycleEvent``)
    so the runner can
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
        self._same_origin_only = False
        self._initial_url = ""
        self._initial_hostname = ""
        self._redirect_counts: dict[str, int] = {}

    def enable_fetch_interception(
        self,
        *,
        allow_private: bool,
        allow_file_urls: bool,
        same_origin_only: bool = False,
        initial_url: str = "",
        initial_hostname: str = "",
    ) -> None:
        """Turn on in-loop ``Fetch.requestPaused`` handling for every read loop.

        ``Fetch.enable`` itself is issued by the caller via ``call`` BEFORE
        ``Page.navigate``; this only records the policy used to resolve each
        paused request.
        """
        self._intercept = True
        self._allow_private = allow_private
        self._allow_file_urls = allow_file_urls
        self._same_origin_only = same_origin_only
        self._initial_url = initial_url
        self._initial_hostname = initial_hostname
        self._redirect_counts = {}

    def _dispatch(self, message: dict) -> None:
        """Handle one received CDP message: resolve a paused Fetch or buffer an event.

        Raises ``_FetchBlocked`` (SSRF violation or redirect cap) to abort the
        capture with a hard ``BLOCKED_*`` status.
        """
        method = message.get("method")
        if self._intercept and method == "Network.requestWillBeSent":
            params = message.get("params", {})
            request_id = params.get("requestId")
            if isinstance(request_id, str) and isinstance(
                params.get("redirectResponse"), dict
            ):
                redirects = self._redirect_counts.get(request_id, 0) + 1
                self._redirect_counts[request_id] = redirects
                if redirects > MAX_REDIRECTS:
                    raise _FetchBlocked(
                        BLOCKED_REDIRECT_LIMIT,
                        f"redirect chain exceeded {MAX_REDIRECTS} hops",
                    )
            return
        if self._intercept and method in {
            "Network.loadingFinished",
            "Network.loadingFailed",
        }:
            request_id = message.get("params", {}).get("requestId")
            if isinstance(request_id, str):
                self._redirect_counts.pop(request_id, None)
            return
        if self._intercept and method == "Fetch.requestPaused":
            params = message.get("params", {})
            request_id = params.get("requestId")
            try:
                _resolve_paused_request(
                    params,
                    allow_private=self._allow_private,
                    allow_file_urls=self._allow_file_urls,
                    same_origin_only=self._same_origin_only,
                    initial_url=self._initial_url,
                    initial_hostname=self._initial_hostname,
                )
            except _FetchBlocked:
                # Fail the request BEFORE it is sent, then abort the capture.
                self.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "AccessDenied"})
                raise
            self.send("Fetch.continueRequest", {"requestId": request_id})
            return
        if method in {"Page.loadEventFired", "Page.lifecycleEvent"}:
            if len(self.events) >= MAX_BUFFERED_EVENTS:
                self.events.pop(0)
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
            message = _json.loads(self._ws.recv_text(deadline=deadline))
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
                message = _json.loads(self._ws.recv_text(deadline=deadline))
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

    def navigate_with_fetch(self, url: str, *, deadline: float) -> str:
        """Navigate and wait for this navigation's loader-scoped load event.

        ``enable_fetch_interception`` MUST already be active so ``_dispatch``
        re-validates each paused request. Raises ``_FetchBlocked`` (an SSRF
        violation or redirect-cap breach -> the runner returns the carried
        ``BLOCKED_*``) or ``CdpError`` (timeout/protocol failure).

        ``Page.loadEventFired`` has no frame/loader identity. Chromium can emit
        one for the initial new-tab document after ``Page.navigate`` is sent;
        accepting that stale event lets capture race the real response parser.
        ``Page.navigate`` returns the new loader ID, so only its matching
        ``Page.lifecycleEvent(name=load)`` is accepted. Matching events that
        arrive before the command response remain available in ``events``.
        """
        import json as _json
        import time as _time

        self.drain_events()
        navigation = self.call("Page.navigate", {"url": url}, deadline=deadline)
        error_text = navigation.get("errorText")
        if error_text:
            raise CdpError(f"Page.navigate failed: {error_text}")
        loader_id = navigation.get("loaderId")
        frame_id = navigation.get("frameId")
        if not isinstance(loader_id, str) or not loader_id:
            raise CdpError("Page.navigate returned no loaderId for a document navigation")

        def is_matching_load(message: dict) -> bool:
            if message.get("method") != "Page.lifecycleEvent":
                return False
            params = message.get("params", {})
            return (
                params.get("name") == "load"
                and params.get("loaderId") == loader_id
                and (not frame_id or params.get("frameId") == frame_id)
            )

        if any(is_matching_load(event) for event in self.events):
            return loader_id
        while True:
            if _time.monotonic() > deadline:
                raise CdpError(
                    f"{BLOCKED_TIMEOUT}: timed out awaiting loader-scoped navigation load"
                )
            message = _json.loads(self._ws.recv_text(deadline=deadline))
            self._dispatch(message)
            if is_matching_load(message):
                return loader_id

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
                    text = self._ws.recv_text(deadline=cap)
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
    same_origin_only: bool = False
    resolver_pin: tuple[str, str] | None = None


@dataclass
class CdpPrintPdfRequest:
    browser_path: str
    url: str
    out_path: str
    media: str = "print"
    print_background: bool = True
    prefer_css_page_size: bool = True
    consent: bool = True
    wait_ms: int = 800
    timeout_ms: int = 30000
    max_bytes: int = MAX_PDF_BYTES
    no_sandbox: bool = False
    allow_private: bool = False
    allow_file_urls: bool = False
    same_origin_only: bool = False
    resolver_pin: tuple[str, str] | None = None
    # The launch still needs a finite initial viewport even though print layout
    # is page-based. Keep the existing capture defaults for parity.
    width: int = 1280
    height: int = 800
    device_scale: float = 1.0


class _FetchBlocked(Exception):
    """An intercepted request was failed before send; abort the capture.

    Carries the ``BLOCKED_*`` status the runner returns. Distinct from
    ``security.TargetBlocked`` so origin and redirect-chain policy failures can
    use the same hard-block path without conflating them with address admission.
    """

    def __init__(self, status: str, detail: str = ""):
        super().__init__(f"{status}: {detail}" if detail else status)
        self.status = status
        self.detail = detail


class _OutputBlocked(Exception):
    """An artifact failed a bounded-output invariant before publication."""

    def __init__(self, status: str, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


def _resolve_paused_request(
    params: dict,
    *,
    allow_private: bool,
    allow_file_urls: bool,
    same_origin_only: bool = False,
    initial_url: str = "",
    initial_hostname: str = "",
) -> None:
    """Re-validate one ``Fetch.requestPaused`` event; raise to abort, return to continue.

    Pure-ish: it enforces the optional canonical-origin boundary, resolves the
    request host (function-local ``security.resolve_host``, no socket import here
    -- ``security`` owns it), and re-checks every resolved IP. Raises
    ``_FetchBlocked`` with the precise ``BLOCKED_*`` status on an origin,
    scheme, or IP violation so the request is failed before send and the capture
    aborts.
    """
    from urllib.parse import urlsplit

    from . import security

    url = params.get("request", {}).get("url", "")
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    if same_origin_only:
        try:
            request_origin = _canonical_origin(url)
            if initial_url:
                initial_origin = _canonical_origin(initial_url)
            else:
                # Legacy selftest/caller compatibility; production supplies
                # the full initial URL so scheme and port remain authoritative.
                initial_origin = (
                    request_origin[0],
                    _canonical_hostname(initial_hostname),
                    request_origin[2],
                )
        except ValueError as exc:
            raise _FetchBlocked(BLOCKED_INPUT, str(exc)) from exc
        if request_origin != initial_origin:
            raise _FetchBlocked(
                BLOCKED_CROSS_ORIGIN,
                f"request origin {request_origin!r} differs from initial origin",
            )
    if scheme == "file":
        if not allow_file_urls:
            raise _FetchBlocked(BLOCKED_SCHEME, f"file: request blocked: {url!r}")
        return  # trusted local fixture: no remote host, no IP check
    if scheme not in security.ALLOWED_SCHEMES:
        raise _FetchBlocked(BLOCKED_SCHEME, f"scheme {scheme or '(none)'!r} blocked: {url!r}")
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


_DOM_EXTENT_EXPRESSION = f"""(() => {{
  const limit = {MAX_DOM_ELEMENTS};
  const all = document.getElementsByTagName('*');
  const total = all.length;
  const scanned = Math.min(total, limit);
  let width = 0;
  let height = 0;
  const include = (node) => {{
    if (!node) return;
    width = Math.max(width, Number(node.scrollWidth) || 0,
                     Number(node.offsetWidth) || 0,
                     Number(node.clientWidth) || 0);
    height = Math.max(height, Number(node.scrollHeight) || 0,
                      Number(node.offsetHeight) || 0,
                      Number(node.clientHeight) || 0);
    const rect = node.getBoundingClientRect();
    if (rect) {{
      width = Math.max(width, (Number(rect.right) || 0) + window.scrollX);
      height = Math.max(height, (Number(rect.bottom) || 0) + window.scrollY);
    }}
  }};
  include(document.documentElement);
  include(document.body);
  for (let index = 0; index < scanned; index += 1) include(all[index]);
  return {{
    width: Math.ceil(width),
    height: Math.ceil(height),
    readyState: document.readyState,
    elementsScanned: scanned,
    elementsTotal: total,
    complete: total <= limit
  }};
}})()"""


def _finite_positive_dimension(value, label: str) -> float:
    import math

    if isinstance(value, bool):
        raise _OutputBlocked("BLOCKED_OUTPUT", f"invalid {label} from browser")
    try:
        dimension = float(value)
    except (TypeError, ValueError) as exc:
        raise _OutputBlocked("BLOCKED_OUTPUT", f"invalid {label} from browser") from exc
    if not math.isfinite(dimension) or dimension <= 0:
        raise _OutputBlocked("BLOCKED_OUTPUT", f"invalid {label} from browser")
    return dimension


def _measure_document_extent(
    session: _CdpSession, deadline: float
) -> tuple[float, float, dict]:
    """Measure layout plus bounded overflow-aware DOM extent in CSS pixels."""

    metrics = session.call("Page.getLayoutMetrics", deadline=deadline)
    css = metrics.get("cssContentSize") or metrics.get("contentSize")
    if not isinstance(css, dict):
        raise _OutputBlocked("BLOCKED_OUTPUT", "getLayoutMetrics has no content size")
    layout_width = _finite_positive_dimension(css.get("width"), "layout width")
    layout_height = _finite_positive_dimension(css.get("height"), "layout height")

    evaluated = session.call(
        "Runtime.evaluate",
        {"expression": _DOM_EXTENT_EXPRESSION, "returnByValue": True},
        deadline=deadline,
    )
    if evaluated.get("exceptionDetails"):
        raise _OutputBlocked("BLOCKED_OUTPUT", "DOM extent evaluation failed")
    extent = evaluated.get("result", {}).get("value")
    if not isinstance(extent, dict):
        raise _OutputBlocked("BLOCKED_OUTPUT", "DOM extent evaluation returned no value")
    if extent.get("readyState") != "complete":
        raise _OutputBlocked(
            "BLOCKED_OUTPUT",
            f"document readiness is {extent.get('readyState')!r}, not 'complete'",
        )
    scanned = extent.get("elementsScanned")
    total = extent.get("elementsTotal")
    if (
        isinstance(scanned, bool)
        or isinstance(total, bool)
        or not isinstance(scanned, (int, float))
        or not isinstance(total, (int, float))
        or scanned < 0
        or total < 0
        or scanned > MAX_DOM_ELEMENTS
        or total > MAX_DOM_ELEMENTS
        or scanned != total
        or extent.get("complete") is not True
    ):
        raise _OutputBlocked(
            "BLOCKED_OUTPUT",
            f"DOM extent scan exceeds the {MAX_DOM_ELEMENTS}-element limit",
        )
    dom_width = _finite_positive_dimension(extent.get("width"), "document width")
    dom_height = _finite_positive_dimension(extent.get("height"), "document height")
    return max(layout_width, dom_width), max(layout_height, dom_height), extent


def _expanded_full_page_clip(
    session: _CdpSession, request: CdpCaptureRequest, deadline: float
) -> tuple[FullPageClip, float, float, str]:
    """Expand the viewport to a bounded overflow-aware document extent.

    Some sites make the root viewport fixed-height and put their content in an
    ``overflow:auto`` child. ``Page.getLayoutMetrics`` then reports only the
    viewport. A bounded DOM scan includes each element's scroll extent, and the
    viewport is enlarged iteratively so viewport-relative layouts can settle.
    """

    import math

    document_width, document_height, _extent = _measure_document_extent(
        session, deadline
    )
    target_width = max(float(request.width), document_width)
    target_height = max(float(request.height), document_height)

    for _pass in range(MAX_FULL_PAGE_VIEWPORT_PASSES):
        viewport_width = int(math.ceil(target_width))
        viewport_height = int(math.ceil(target_height))
        # Enforce both CSS-dimension and decoded-pixel caps before asking the
        # browser to allocate the expanded viewport.
        clip = build_full_page_clip(
            {
                "cssContentSize": {
                    "width": viewport_width,
                    "height": viewport_height,
                }
            },
            request.device_scale,
        )
        # Chromium applies both the emulated device scale and the screenshot
        # clip scale. Preserve that existing rendering behavior, but account
        # for both factors before capture so the decoded-pixel cap remains a
        # pre-allocation guard rather than only a post-decode check.
        browser_pixel_area = clip.pixel_area() * request.device_scale**2
        if browser_pixel_area > MAX_CAPTURE_PIXELS:
            raise _OutputBlocked(
                BLOCKED_INPUT,
                f"requested browser area {browser_pixel_area:.0f}px exceeds cap "
                f"{MAX_CAPTURE_PIXELS}",
            )
        session.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": viewport_width,
                "height": viewport_height,
                "deviceScaleFactor": request.device_scale,
                "mobile": False,
            },
            deadline=deadline,
        )
        document_width, document_height, _extent = _measure_document_extent(
            session, deadline
        )
        next_width = max(float(request.width), document_width)
        next_height = max(float(request.height), document_height)
        if next_width <= viewport_width and next_height <= viewport_height:
            return (
                clip,
                document_width,
                document_height,
                str(_extent["readyState"]),
            )
        target_width = next_width
        target_height = next_height

    raise _OutputBlocked(
        "BLOCKED_OUTPUT",
        "document extent did not stabilize within the bounded viewport expansion",
    )


def _capture_png_operation(
    session: _CdpSession,
    request: CdpCaptureRequest,
    deadline: float,
    consent_removed: bool,
) -> dict:
    """Capture, validate, and atomically publish PNG bytes."""
    import base64
    import binascii
    import math

    params: dict = {"format": "png"}
    document_width: float | None = None
    document_height: float | None = None
    full_page_complete: bool | None = None
    document_ready_state: str | None = None
    clip: FullPageClip | None = None
    if request.full_page:
        (
            clip,
            document_width,
            document_height,
            document_ready_state,
        ) = _expanded_full_page_clip(session, request, deadline)
        params["clip"] = clip.to_cdp()
        params["captureBeyondViewport"] = True

    shot = session.call("Page.captureScreenshot", params, deadline=deadline)
    data = shot.get("data")
    if not isinstance(data, str) or not data:
        raise CdpError("Page.captureScreenshot returned no data")
    try:
        png_bytes = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CdpError(f"Page.captureScreenshot returned invalid base64: {exc}") from exc
    if len(png_bytes) > MAX_PNG_BYTES:
        raise _OutputBlocked(
            "BLOCKED_OUTPUT",
            f"PNG size {len(png_bytes)} exceeds byte limit {MAX_PNG_BYTES}",
        )

    out_width, out_height, digest = _png_metadata(png_bytes)
    if clip is not None:
        # Re-measure after capture before publishing or attesting completeness.
        # A page can grow after its load event (or during screenshot encoding);
        # comparing only against the pre-capture clip would reproduce the same
        # false-complete class with a different timing trigger.
        final_document_width, final_document_height, final_extent = (
            _measure_document_extent(session, deadline)
        )
        document_width = final_document_width
        document_height = final_document_height
        document_ready_state = str(final_extent["readyState"])
        if document_width > clip.width or document_height > clip.height:
            raise _OutputBlocked(
                "BLOCKED_OUTPUT",
                f"document grew to {document_width:g}x{document_height:g} CSS px "
                f"after the admitted {clip.width:g}x{clip.height:g} capture",
            )
        expected_width = int(math.ceil(clip.width * clip.scale))
        expected_height = int(math.ceil(clip.height * clip.scale))
        if out_width < expected_width or out_height < expected_height:
            raise _OutputBlocked(
                "BLOCKED_OUTPUT",
                f"full-page PNG {out_width}x{out_height} does not cover "
                f"the admitted {expected_width}x{expected_height} extent",
            )
        full_page_complete = True
    out_width, out_height, digest = _atomic_write_png(request.out_path, png_bytes)
    return {
        "out_path": request.out_path,
        "width": out_width,
        "height": out_height,
        "consent_removed": consent_removed,
        "full_page": request.full_page,
        "bytes": len(png_bytes),
        "sha256": digest,
        "document_width": document_width,
        "document_height": document_height,
        "document_ready_state": document_ready_state,
        "full_page_complete": full_page_complete,
    }


def _print_pdf_operation(
    session: _CdpSession,
    request: CdpPrintPdfRequest,
    deadline: float,
    consent_removed: bool,
) -> dict:
    """Print bounded PDF bytes after the same guarded navigation as PNG."""
    import base64
    import binascii
    import hashlib

    session.call(
        "Emulation.setEmulatedMedia",
        {"media": request.media},
        deadline=deadline,
    )
    result = session.call(
        "Page.printToPDF",
        build_print_to_pdf_params(
            print_background=request.print_background,
            prefer_css_page_size=request.prefer_css_page_size,
        ),
        deadline=deadline,
    )
    data = result.get("data")
    if not isinstance(data, str) or not data:
        raise CdpError("Page.printToPDF returned no data")

    # Reject an encoded response that cannot possibly fit before allocating a
    # second decoded buffer. CDP base64 has no whitespace.
    encoded_limit = 4 * ((request.max_bytes + 2) // 3)
    if len(data) > encoded_limit:
        raise _OutputBlocked(
            "BLOCKED_OUTPUT",
            f"encoded PDF exceeds byte limit {request.max_bytes}",
        )
    try:
        pdf_bytes = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CdpError(f"Page.printToPDF returned invalid base64: {exc}") from exc
    _atomic_write_pdf(request.out_path, pdf_bytes, max_bytes=request.max_bytes)
    return {
        "out_path": request.out_path,
        "bytes": len(pdf_bytes),
        "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        "consent_removed": consent_removed,
        "media": request.media,
        "print_background": request.print_background,
        "prefer_css_page_size": request.prefer_css_page_size,
    }


def _run_guarded_cdp(request, operation, *, operation_name: str) -> dict:
    """Launch, navigate under Fetch interception, run one artifact operation.

    This is the sole browser path used by both PNG capture and PDF printing.
    ``Fetch.enable`` remains before ``Page.navigate`` and every subsequent CDP
    read is dispatched through the same per-request SSRF validator. Browser-tree
    reaping and profile cleanup remain in one ``finally`` block.
    """
    import os
    import math
    import subprocess
    import tempfile
    import time
    from pathlib import Path
    from . import consent as consent_mod
    from . import procctl, security

    width = request.width
    height = request.height
    scale = request.device_scale
    if (
        type(width) is not int
        or type(height) is not int
        or not isinstance(scale, (int, float))
        or isinstance(scale, bool)
        or not math.isfinite(float(scale))
        or width <= 0
        or height <= 0
        or scale <= 0
        or width > MAX_PNG_DIMENSION
        or height > MAX_PNG_DIMENSION
        or width * height * float(scale) ** 2 > MAX_CAPTURE_PIXELS
    ):
        return {
            "status": BLOCKED_INPUT,
            "reason": "initial viewport exceeds the shared dimension or pixel cap",
        }
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
        if hasattr(request, "max_bytes"):
            encoded_output_limit = 4 * ((int(request.max_bytes) + 2) // 3)
        else:
            # Bound PNG bytes near the lossless RGBA size plus conservative
            # container/row overhead; base64 adds at most 4/3 overhead.
            encoded_output_limit = 4 * ((MAX_PNG_BYTES + 2) // 3)
        ws = _WebSocket(
            "127.0.0.1",
            port,
            ws_path,
            timeout=max(0.2, deadline - time.monotonic()),
            max_message_bytes=encoded_output_limit + 1024 * 1024,
        )
        session = _CdpSession(ws)

        session.call("Page.enable", deadline=deadline)
        session.call(
            "Page.setLifecycleEventsEnabled",
            {"enabled": True},
            deadline=deadline,
        )
        session.call("Network.enable", deadline=deadline)
        # PRIMARY SSRF control: enable Fetch interception (catch-all, request
        # stage) BEFORE Page.navigate so every request -- main frame, redirects,
        # and sub-resources -- is paused and re-validated before it is sent.
        session.enable_fetch_interception(
            allow_private=request.allow_private,
            allow_file_urls=request.allow_file_urls,
            same_origin_only=request.same_origin_only,
            initial_url=request.url,
        )
        session.call(
            "Fetch.enable",
            {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]},
            deadline=deadline,
        )
        # Navigate under interception: each paused request is re-validated and
        # either continued or failed-before-send (aborting on a private/metadata
        # hit); redirects are capped. Returns only after the loader ID returned
        # by Page.navigate emits its own lifecycle ``load`` event.
        navigation_loader_id = session.navigate_with_fetch(
            request.url, deadline=deadline
        )
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

        def observed_navigation_url() -> str:
            try:
                history = session.call("Page.getNavigationHistory", deadline=deadline)
                entries = history.get("entries", [])
                current_index = history.get("currentIndex")
                if (
                    isinstance(entries, list)
                    and isinstance(current_index, int)
                    and 0 <= current_index < len(entries)
                ):
                    candidate = entries[current_index].get("url", "")
                    if isinstance(candidate, str):
                        return security.redact_url(candidate)
            except CdpError:
                pass
            try:
                evaluated = session.call(
                    "Runtime.evaluate",
                    {
                        "expression": "globalThis.location.href",
                        "returnByValue": True,
                    },
                    deadline=deadline,
                )
                candidate = evaluated.get("result", {}).get("value", "")
                if isinstance(candidate, str):
                    return security.redact_url(candidate)
            except CdpError:
                pass
            return ""

        initial_url = observed_navigation_url()
        outcome = operation(session, request, deadline, consent_removed)
        # Printing can run page scripts (including ``beforeprint`` handlers),
        # while a capture may race a late navigation.  Attest the URL only
        # after the artifact operation so callers can enforce their source
        # boundary against the page that actually produced the output.
        final_url = observed_navigation_url()
        if initial_url:
            outcome["initial_url"] = initial_url
        if final_url:
            outcome["final_url"] = final_url
        outcome["navigation_complete"] = bool(navigation_loader_id)
        outcome["same_origin_only"] = bool(request.same_origin_only)
        outcome["origin_policy"] = (
            ORIGIN_POLICY_SCHEME_HOST_PORT if request.same_origin_only else "none"
        )
        return outcome
    except _FetchBlocked as exc:
        # An SSRF violation intercepted by the Fetch loop: the request was failed
        # BEFORE send, and the capture is aborted. A hard block, never a
        # silently-degraded fallback.
        return {"status": exc.status, "reason": exc.detail}
    except security.TargetBlocked as exc:
        # Defensive: any direct admission re-check that escapes is a hard block too.
        return {"status": exc.reason, "reason": exc.detail}
    except _OutputBlocked as exc:
        return {"status": exc.status, "reason": exc.detail}
    except (CdpError, OSError) as exc:
        # A recoverable CDP/socket failure. If the wall-clock budget is spent it
        # is a timeout (the engine must not retry Tier-1); otherwise it is a CDP
        # failure the engine may fall back from.
        if time.monotonic() > deadline:
            return {
                "status": BLOCKED_TIMEOUT,
                "reason": f"cdp {operation_name} exceeded --timeout ({exc})",
            }
        return {"status": "CDP_FAILED", "reason": str(exc)}
    finally:
        if ws is not None:
            ws.close()
        strategy.kill(proc)
        procctl.cleanup_profile_dir(profile_dir)


def run_cdp_capture(request: CdpCaptureRequest) -> dict:
    """Capture a PNG through guarded CDP navigation and shared cleanup."""
    return _run_guarded_cdp(request, _capture_png_operation, operation_name="capture")


def run_cdp_print_pdf(request: CdpPrintPdfRequest) -> dict:
    """Print a PDF through guarded CDP navigation and shared cleanup."""
    if request.media not in {"print", "screen"}:
        return {"status": BLOCKED_INPUT, "reason": "media must be 'print' or 'screen'"}
    if request.max_bytes <= 0 or request.max_bytes > MAX_PDF_BYTES:
        return {
            "status": BLOCKED_INPUT,
            "reason": f"PDF byte limit must be in 1..{MAX_PDF_BYTES}",
        }
    return _run_guarded_cdp(request, _print_pdf_operation, operation_name="print-pdf")
