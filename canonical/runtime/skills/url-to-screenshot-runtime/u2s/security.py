"""Fail-closed SSRF admission chokepoint for url-to-screenshot.

``validate_target_url`` is the single pre-navigation admission decision. It is an
*admission* gate only: it cannot bind Chromium's own resolver, redirects,
sub-resource fetches, or JS-initiated requests. Browser-side re-validation
(``u2s.cdp``) is the primary control for those; this module is the first wall.

Layers, in order, each fail-closed:
  1. Scheme allow-list -- only ``http``/``https``. Never overridable.
  2. Resolve-then-check every resolved A/AAAA via ``socket.getaddrinfo`` +
     stdlib ``ipaddress``: reject loopback, private, link-local, ``0.0.0.0/8``,
     multicast, reserved, and IPv4-mapped IPv6.
  3. Cloud-metadata host/IP denylist -- UNCONDITIONAL. Never disabled by
     ``--allow-private-targets``.

The opt-in relaxation requires the CLI flag (``allow_private=True``); the env var
``URL_TO_SCREENSHOT_ALLOW_PRIVATE=1`` alone never enables it, so an inherited or
poisoned environment cannot silently disable SSRF blocking. The relaxation
loosens layer 2 (private/loopback/link-local) ONLY; it never re-enables scheme
blocking (layer 1) and never the metadata denylist (layer 3).

``redact_url`` is a purpose-built URL redactor (drops query, fragment, and
userinfo) -- it is not the SMTP-password redactor from ``send-email``.

Pure standard library; no network access at import time.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Cloud-metadata endpoints that must never be reachable, by host or literal IP.
# Kept lowercase; matching is exact on host and on every resolved IP.
METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata",
        "metadata.goog",
    }
)
METADATA_IPS = frozenset(
    {
        "169.254.169.254",  # AWS / Azure / GCP IMDS
        "100.100.100.200",  # Alibaba Cloud
        "fd00:ec2::254",  # AWS IMDS over IPv6
    }
)

# Block reason vocabulary (kept in sync with the engine's blocked-state set).
BLOCKED_SCHEME = "BLOCKED_SCHEME"
BLOCKED_PRIVATE_ADDRESS = "BLOCKED_PRIVATE_ADDRESS"
BLOCKED_METADATA_ENDPOINT = "BLOCKED_METADATA_ENDPOINT"
BLOCKED_INPUT = "BLOCKED_INPUT"


class TargetBlocked(ValueError):
    """Raised when a URL is rejected by the admission gate."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass
class AdmissionResult:
    """Outcome of a successful admission decision."""

    url: str
    scheme: str
    host: str
    port: int
    resolved_ips: list[str] = field(default_factory=list)
    private_targets_allowed: bool = False
    file_url: bool = False


def _normalize_host(host: str) -> str:
    return host.strip().strip(".").lower()


def _ip_is_blocked(ip: ipaddress._BaseAddress) -> bool:
    """True if an address must be refused regardless of the override flag's layer-2 relaxation."""
    if ip.is_loopback or ip.is_private or ip.is_link_local:
        return True
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        if ip in ipaddress.ip_network("0.0.0.0/8"):
            return True
    if isinstance(ip, ipaddress.IPv6Address):
        # Unwrap IPv4-mapped IPv6 (::ffff:a.b.c.d) and re-check the embedded v4.
        if ip.ipv4_mapped is not None:
            return _ip_is_blocked(ip.ipv4_mapped)
    return False


def _is_metadata_ip(text: str) -> bool:
    if text in METADATA_IPS:
        return True
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    # Compare canonical forms so e.g. an expanded IPv6 literal still matches.
    for known in METADATA_IPS:
        try:
            if ip == ipaddress.ip_address(known):
                return True
        except ValueError:
            continue
    # Unwrap IPv4-mapped IPv6 (e.g. ``::ffff:100.100.100.200`` or its compressed
    # alias ``::ffff:6464:64c8``) and re-check the embedded v4. Without this, a
    # globally-classified metadata IP like Alibaba's 100.100.100.200 -- which is
    # NOT caught by the private/link-local block -- would slip past the metadata
    # denylist in its mapped form under ``--allow-private-targets``.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _is_metadata_ip(str(mapped))
    return False


def resolve_host(host: str) -> list[str]:
    """Resolve a host to its A/AAAA literals via ``getaddrinfo``.

    A host that is already an IP literal resolves to itself. Returns a
    de-duplicated, order-preserving list. Raises ``TargetBlocked`` on a
    resolution failure (fail-closed: an unresolvable host is not admitted).
    """
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise TargetBlocked(BLOCKED_INPUT, f"cannot resolve host {host!r}: {exc}") from exc
    seen: list[str] = []
    for info in infos:
        addr = info[4][0]
        # Strip any IPv6 scope id (e.g. fe80::1%eth0).
        addr = addr.split("%", 1)[0]
        if addr not in seen:
            seen.append(addr)
    if not seen:
        raise TargetBlocked(BLOCKED_INPUT, f"no addresses for host {host!r}")
    return seen


def validate_target_url(
    url: str, *, allow_private: bool = False, allow_file_urls: bool = False
) -> AdmissionResult:
    """Fail-closed admission decision for a target URL.

    ``allow_private`` (the CLI ``--allow-private-targets`` flag) relaxes ONLY the
    private/loopback/link-local block. It never relaxes the scheme allow-list and
    never the cloud-metadata denylist.

    ``allow_file_urls`` (the CLI ``--allow-file-urls`` flag) is a narrow opt-in
    for TRUSTED LOCAL FIXTURES/TESTING ONLY: it adds ``file:`` to the scheme
    allow-list. A ``file:`` URL has no remote host, so the SSRF IP checks are not
    applicable; it enables local file reads (e.g. ``file:///etc/passwd``) and must
    never be set on attacker-influenceable input. Like ``allow_private``, this
    requires the CLI flag; the environment alone never enables it.
    """
    if not isinstance(url, str) or not url.strip():
        raise TargetBlocked(BLOCKED_INPUT, "empty URL")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    # Narrow trusted-fixture path: file: URLs skip the SSRF IP checks (no host).
    if scheme == "file" and allow_file_urls:
        return AdmissionResult(
            url=url.strip(),
            scheme=scheme,
            host="",
            port=0,
            resolved_ips=[],
            private_targets_allowed=bool(allow_private),
            file_url=True,
        )
    if scheme not in ALLOWED_SCHEMES:
        raise TargetBlocked(BLOCKED_SCHEME, f"scheme {scheme or '(none)'!r} is not http/https")
    host = _normalize_host(parts.hostname or "")
    if not host:
        raise TargetBlocked(BLOCKED_INPUT, "URL has no host")
    port = parts.port or (443 if scheme == "https" else 80)

    # Layer 3 (host-name form): unconditional metadata denylist.
    if host in METADATA_HOSTS or _is_metadata_ip(host):
        raise TargetBlocked(BLOCKED_METADATA_ENDPOINT, f"metadata host {host!r}")

    resolved = resolve_host(host)
    for literal in resolved:
        # Layer 3 (resolved-IP form): unconditional, even under allow_private.
        if _is_metadata_ip(literal):
            raise TargetBlocked(BLOCKED_METADATA_ENDPOINT, f"resolves to metadata ip {literal}")
        ip = ipaddress.ip_address(literal)
        if _ip_is_blocked(ip):
            if allow_private:
                continue  # Layer 2 relaxed by the explicit CLI flag.
            raise TargetBlocked(BLOCKED_PRIVATE_ADDRESS, f"{host} -> {literal}")

    return AdmissionResult(
        url=url.strip(),
        scheme=scheme,
        host=host,
        port=port,
        resolved_ips=resolved,
        private_targets_allowed=bool(allow_private),
    )


def revalidate_resolved_address(literal: str, *, allow_private: bool = False) -> None:
    """Re-run the IP-level checks on a freshly resolved address (CDP per-request hook).

    Used by the browser-side ``Network``-domain re-validation in ``u2s.cdp`` on
    redirects and sub-resources. The metadata denylist is unconditional here too.
    """
    if _is_metadata_ip(literal):
        raise TargetBlocked(BLOCKED_METADATA_ENDPOINT, f"redirect/subresource to metadata ip {literal}")
    ip = ipaddress.ip_address(literal.split("%", 1)[0])
    if _ip_is_blocked(ip) and not allow_private:
        raise TargetBlocked(BLOCKED_PRIVATE_ADDRESS, f"redirect/subresource to {literal}")


def redact_url(url: str) -> str:
    """Return ``url`` with query, fragment, and userinfo stripped.

    Keeps scheme, host, port, and path so logs and ``result.json`` retain enough
    to identify the page without leaking tokens carried in the query string,
    fragment, or ``user:pass@`` userinfo.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "(unparseable-url)"
    host = parts.hostname or ""
    netloc = host
    if parts.port:
        netloc = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
