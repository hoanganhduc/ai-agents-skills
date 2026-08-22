from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
U2S_ROOT = REPO_ROOT / "canonical" / "runtime" / "skills" / "url-to-screenshot-runtime"
if str(U2S_ROOT) not in sys.path:
    sys.path.insert(0, str(U2S_ROOT))


def _addrinfo(*ips: str):
    import socket

    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0)) for ip in ips]


class SchemeAllowListTests(unittest.TestCase):
    def test_blocks_non_http_schemes(self) -> None:
        from u2s import security

        for url in ("file:///etc/passwd", "javascript:alert(1)", "data:text/html,x",
                    "ftp://example.com/x", "gopher://example.com/", "about:blank"):
            with self.assertRaises(security.TargetBlocked) as ctx:
                security.validate_target_url(url)
            self.assertEqual(ctx.exception.reason, security.BLOCKED_SCHEME)

    def test_allows_http_and_https_public_ip(self) -> None:
        from u2s import security

        result = security.validate_target_url("http://93.184.216.34/")
        self.assertEqual(result.host, "93.184.216.34")
        self.assertEqual(result.scheme, "http")


class PrivateAddressBlockTests(unittest.TestCase):
    def test_blocks_literal_private_loopback_linklocal(self) -> None:
        from u2s import security

        for url, _ in (("http://127.0.0.1/", "loopback"), ("http://10.0.0.5/", "private"),
                       ("http://192.168.1.1/", "private"), ("http://169.254.0.1/", "link-local")):
            with self.assertRaises(security.TargetBlocked) as ctx:
                security.validate_target_url(url)
            self.assertEqual(ctx.exception.reason, security.BLOCKED_PRIVATE_ADDRESS)

    def test_dns_resolving_to_private_is_blocked(self) -> None:
        from u2s import security

        with mock.patch("u2s.security.socket.getaddrinfo", return_value=_addrinfo("10.0.0.5")):
            with self.assertRaises(security.TargetBlocked) as ctx:
                security.validate_target_url("http://evil.example.com/")
        self.assertEqual(ctx.exception.reason, security.BLOCKED_PRIVATE_ADDRESS)

    def test_ipv4_mapped_ipv6_private_is_blocked(self) -> None:
        from u2s import security

        with self.assertRaises(security.TargetBlocked):
            security.validate_target_url("http://[::ffff:10.0.0.5]/")


class MetadataDenylistTests(unittest.TestCase):
    def test_metadata_ip_blocked(self) -> None:
        from u2s import security

        with self.assertRaises(security.TargetBlocked) as ctx:
            security.validate_target_url("http://169.254.169.254/latest/meta-data/")
        self.assertEqual(ctx.exception.reason, security.BLOCKED_METADATA_ENDPOINT)

    def test_metadata_host_blocked(self) -> None:
        from u2s import security

        with self.assertRaises(security.TargetBlocked) as ctx:
            security.validate_target_url("http://metadata.google.internal/")
        self.assertEqual(ctx.exception.reason, security.BLOCKED_METADATA_ENDPOINT)

    def test_dns_resolving_to_metadata_ip_blocked(self) -> None:
        from u2s import security

        with mock.patch("u2s.security.socket.getaddrinfo", return_value=_addrinfo("169.254.169.254")):
            with self.assertRaises(security.TargetBlocked) as ctx:
                security.validate_target_url("http://innocent.example.com/")
        self.assertEqual(ctx.exception.reason, security.BLOCKED_METADATA_ENDPOINT)

    def test_ipv4_mapped_metadata_blocked_even_with_override(self) -> None:
        # Finding #5: the IPv4-mapped IPv6 form of a globally-classified metadata
        # IP (Alibaba 100.100.100.200) and of the IMDS IP must stay BLOCKED even
        # under --allow-private-targets. The mapped Alibaba form is NOT
        # independently private, so only the metadata denylist catches it.
        from u2s import security

        for url in ("http://[::ffff:100.100.100.200]/", "http://[::ffff:6464:64c8]/",
                    "http://[::ffff:169.254.169.254]/"):
            with self.assertRaises(security.TargetBlocked) as ctx:
                security.validate_target_url(url, allow_private=True)
            self.assertEqual(ctx.exception.reason, security.BLOCKED_METADATA_ENDPOINT, url)

    def test_revalidate_blocks_mapped_metadata_even_with_override(self) -> None:
        # The same unwrap must protect the per-request redirect/sub-resource hook.
        from u2s import security

        with self.assertRaises(security.TargetBlocked) as ctx:
            security.revalidate_resolved_address("::ffff:100.100.100.200", allow_private=True)
        self.assertEqual(ctx.exception.reason, security.BLOCKED_METADATA_ENDPOINT)

    # IPv6 carries an IPv4 address five standard ways. Finding #5 unwrapped one of
    # them (``ipv4_mapped``); each literal below is the SAME metadata address in
    # one of the other four, and each was ADMITTED under --allow-private-targets
    # against a module docstring that calls this denylist "UNCONDITIONAL. Never
    # disabled by ``--allow-private-targets``". NAT64 is the live one: an
    # IPv6-only cloud subnet reaches IPv4 IMDS through exactly 64:ff9b::/96.
    TRANSITION_ENCODED_METADATA = (
        ("ipv4-compatible ::/96", "::169.254.169.254"),
        ("ipv4-compatible ::/96", "::100.100.100.200"),
        ("6to4 2002::/16", "2002:a9fe:a9fe::"),
        ("6to4 2002::/16", "2002:6464:64c8::"),
        ("nat64 well-known 64:ff9b::/96", "64:ff9b::169.254.169.254"),
        ("nat64 well-known 64:ff9b::/96", "64:ff9b::100.100.100.200"),
        ("nat64 local-use 64:ff9b:1::/48", "64:ff9b:1::169.254.169.254"),
        ("nat64 local-use 64:ff9b:1::/48", "64:ff9b:1::100.100.100.200"),
        ("teredo 2001:0::/32", "2001:0:4136:e378:8000:63bf:5601:5601"),
        ("teredo 2001:0::/32", "2001:0:4136:e378:8000:63bf:9b9b:9b37"),
    )

    def test_transition_encoded_metadata_blocked_even_with_override(self) -> None:
        from u2s import security

        for encoding, literal in self.TRANSITION_ENCODED_METADATA:
            with self.subTest(encoding=encoding, literal=literal):
                with self.assertRaises(security.TargetBlocked) as ctx:
                    security.validate_target_url(f"http://[{literal}]/", allow_private=True)
                self.assertEqual(
                    ctx.exception.reason, security.BLOCKED_METADATA_ENDPOINT, literal
                )

    def test_revalidate_blocks_transition_encoded_metadata_too(self) -> None:
        """The redirect/sub-resource hook shares the unwrap, so it must share the fix."""

        from u2s import security

        for encoding, literal in self.TRANSITION_ENCODED_METADATA:
            with self.subTest(encoding=encoding, literal=literal):
                with self.assertRaises(security.TargetBlocked) as ctx:
                    security.revalidate_resolved_address(literal, allow_private=True)
                self.assertEqual(
                    ctx.exception.reason, security.BLOCKED_METADATA_ENDPOINT, literal
                )

    def test_dns_resolving_to_transition_encoded_metadata_blocked(self) -> None:
        """A hostname whose AAAA record carries IMDS through NAT64 is the realistic
        delivery: the literal never appears in the URL the caller passes."""

        from u2s import security

        with mock.patch(
            "u2s.security.socket.getaddrinfo",
            return_value=_addrinfo("64:ff9b::169.254.169.254"),
        ):
            with self.assertRaises(security.TargetBlocked) as ctx:
                security.validate_target_url(
                    "http://innocent.example.com/", allow_private=True
                )
        self.assertEqual(ctx.exception.reason, security.BLOCKED_METADATA_ENDPOINT)

    def test_transition_encodings_of_a_public_ipv4_stay_admitted(self) -> None:
        """The control on over-blocking. Widening the unwrap denies the metadata
        addresses and nothing else: the same encodings of a public IPv4 pass."""

        from u2s import security

        for literal in ("::ffff:93.184.216.34", "::93.184.216.34", "2002:5db8:d822::",
                        "64:ff9b::93.184.216.34", "64:ff9b:1::93.184.216.34",
                        "2001:0:4136:e378:8000:63bf:a247:27dd"):
            with self.subTest(literal=literal):
                result = security.validate_target_url(
                    f"http://[{literal}]/", allow_private=True
                )
                self.assertEqual(result.host, literal)

    def test_rfc6052_embeds_ipv4_at_every_prefix_length(self) -> None:
        """Pins the bit layout: RFC 6052 skips the reserved u-byte at bits 64-71,
        so the extraction is not simply "the low 32 bits" for prefixes under /96."""

        import ipaddress

        from u2s import security

        cases = {
            32: "2001:db8:c000:221::",
            40: "2001:db8:1c0:2:21::",
            48: "2001:db8:122:c000:2:2100::",
            56: "2001:db8:122:3c0:0:221::",
            64: "2001:db8:122:344:c0:2:2100::",
            96: "2001:db8:122:344::192.0.2.33",
        }
        for prefix_len, literal in cases.items():
            with self.subTest(prefix_len=prefix_len):
                self.assertEqual(
                    security._rfc6052_ipv4(int(ipaddress.IPv6Address(literal)), prefix_len),
                    ipaddress.IPv4Address("192.0.2.33"),
                )


class OverrideTests(unittest.TestCase):
    def test_override_relaxes_private_only(self) -> None:
        from u2s import security

        result = security.validate_target_url("http://10.0.0.5/", allow_private=True)
        self.assertTrue(result.private_targets_allowed)
        self.assertEqual(result.host, "10.0.0.5")

    def test_override_still_blocks_metadata(self) -> None:
        from u2s import security

        # S3: --allow-private-targets never re-opens the metadata denylist.
        with self.assertRaises(security.TargetBlocked):
            security.validate_target_url("http://169.254.169.254/", allow_private=True)
        with self.assertRaises(security.TargetBlocked):
            security.validate_target_url("http://metadata.google.internal/", allow_private=True)

    def test_override_still_blocks_scheme(self) -> None:
        from u2s import security

        with self.assertRaises(security.TargetBlocked) as ctx:
            security.validate_target_url("file:///etc/passwd", allow_private=True)
        self.assertEqual(ctx.exception.reason, security.BLOCKED_SCHEME)

    def test_env_var_alone_does_not_relax(self) -> None:
        # The CLI flag is required; the env var alone never relaxes the block.
        # validate_target_url only relaxes when allow_private=True is passed, and
        # the dispatcher binds that solely from --allow-private-targets, never the env.
        import os

        from u2s import security

        # (a) The parser default is False without the flag.
        import url_to_screenshot_runtime as dispatcher

        parser = dispatcher.build_parser()
        ns = parser.parse_args(["capture", "--url", "http://10.0.0.5/"])
        self.assertFalse(ns.allow_private_targets)

        # (b) End-to-end: even with URL_TO_SCREENSHOT_ALLOW_PRIVATE=1 in the
        # environment, validate_target_url (which never reads the env) still
        # BLOCKS a private IP when allow_private is not explicitly passed.
        with mock.patch.dict(os.environ, {"URL_TO_SCREENSHOT_ALLOW_PRIVATE": "1"}):
            with self.assertRaises(security.TargetBlocked) as ctx:
                security.validate_target_url("http://10.0.0.5/")
            self.assertEqual(ctx.exception.reason, security.BLOCKED_PRIVATE_ADDRESS)


class RedirectRevalidationTests(unittest.TestCase):
    def test_revalidate_blocks_private_redirect(self) -> None:
        from u2s import security

        with self.assertRaises(security.TargetBlocked) as ctx:
            security.revalidate_resolved_address("10.0.0.5")
        self.assertEqual(ctx.exception.reason, security.BLOCKED_PRIVATE_ADDRESS)

    def test_revalidate_blocks_metadata_even_with_override(self) -> None:
        from u2s import security

        with self.assertRaises(security.TargetBlocked) as ctx:
            security.revalidate_resolved_address("169.254.169.254", allow_private=True)
        self.assertEqual(ctx.exception.reason, security.BLOCKED_METADATA_ENDPOINT)

    def test_cdp_revalidation_hook_delegates_to_security(self) -> None:
        from u2s import cdp, security

        with self.assertRaises(security.TargetBlocked):
            cdp.revalidate_request_address("169.254.169.254")

    def test_revalidation_helper_blocks_private_redirect_target(self) -> None:
        # The per-hop revalidation helper (the building block the CDP runner calls
        # before following a redirect/sub-resource) raises on a private/metadata
        # literal. Tier-1 one-shot has no per-request hook by design (its
        # redirect/sub-resource SSRF is documented as in-scope-and-unmitigated),
        # so this asserts the helper itself, not a Tier-1 capture.
        from u2s import security

        with self.assertRaises(security.TargetBlocked):
            security.revalidate_resolved_address("169.254.169.254")


class FetchInterceptionDecisionTests(unittest.TestCase):
    """The pure Fetch-interception decision (PRIMARY browser-side SSRF control).

    ``cdp.fetch_decision`` is the offline-testable twin of the in-loop
    re-validation in ``run_cdp_capture``: given a paused request URL and its
    already-resolved IPs, it returns ``"continue"`` (admissible) or ``"fail"``
    (blocked before send). The Fetch loop itself lives only in the lazy runner, so
    these tests stay browser/socket-free.
    """

    def test_metadata_ip_fails_even_with_allow_private(self) -> None:
        from u2s import cdp

        self.assertEqual(
            cdp.fetch_decision("http://host/x", ["169.254.169.254"], allow_private=True),
            "fail",
        )

    def test_private_ip_fails(self) -> None:
        from u2s import cdp

        self.assertEqual(cdp.fetch_decision("http://host/x", ["10.0.0.5"]), "fail")

    def test_mapped_ipv6_metadata_fails_even_with_allow_private(self) -> None:
        from u2s import cdp

        self.assertEqual(
            cdp.fetch_decision("http://host/x", ["::ffff:169.254.169.254"], allow_private=True),
            "fail",
        )

    def test_private_ip_continues_when_allow_private(self) -> None:
        from u2s import cdp

        # allow_private relaxes the private block but the public-IP path must still continue.
        self.assertEqual(cdp.fetch_decision("http://host/x", ["10.0.0.5"], allow_private=True), "continue")

    def test_public_ip_continues(self) -> None:
        from u2s import cdp

        self.assertEqual(cdp.fetch_decision("http://host/x", ["93.184.216.34"]), "continue")

    def test_any_resolved_ip_private_fails(self) -> None:
        from u2s import cdp

        # A host resolving to both a public and a private literal is blocked.
        self.assertEqual(
            cdp.fetch_decision("http://host/x", ["93.184.216.34", "10.0.0.5"]),
            "fail",
        )

    def test_disallowed_scheme_fails(self) -> None:
        from u2s import cdp

        for url in ("chrome://settings", "data:text/html,x", "view-source:http://x/", "blob:http://x/y"):
            self.assertEqual(cdp.fetch_decision(url, []), "fail", url)

    def test_file_scheme_requires_optin(self) -> None:
        from u2s import cdp

        self.assertEqual(cdp.fetch_decision("file:///etc/passwd", []), "fail")
        self.assertEqual(cdp.fetch_decision("file:///tmp/x.html", [], allow_file_urls=True), "continue")

    def test_same_origin_only_rejects_cross_host_redirect_and_subresource(self) -> None:
        from u2s import cdp

        for request_url in (
            "https://redirect.example.net/landing",
            "https://cdn.example.net/script.js",
        ):
            with self.subTest(request_url=request_url):
                self.assertEqual(
                    cdp.fetch_decision(
                        request_url,
                        ["93.184.216.34"],
                        same_origin_only=True,
                        initial_url="https://official.example/",
                    ),
                    "fail",
                )

    def test_same_origin_only_compares_scheme_host_and_effective_port(self) -> None:
        from u2s import cdp

        self.assertEqual(
            cdp.fetch_decision(
                "https://official.example:443/asset",
                ["93.184.216.34"],
                same_origin_only=True,
                initial_url="https://OFFICIAL.EXAMPLE./",
            ),
            "continue",
        )
        for request_url in (
            "http://official.example/asset",
            "https://official.example:444/asset",
        ):
            with self.subTest(request_url=request_url):
                self.assertEqual(
                    cdp.fetch_decision(
                        request_url,
                        ["93.184.216.34"],
                        same_origin_only=True,
                        initial_url="https://official.example/",
                    ),
                    "fail",
                )

    def test_same_origin_only_preserves_trusted_file_fixture_navigation(self) -> None:
        from u2s import cdp

        self.assertEqual(
            cdp.fetch_decision(
                "file:///tmp/asset.css",
                [],
                allow_file_urls=True,
                same_origin_only=True,
                initial_url="file:///tmp/page.html",
            ),
            "continue",
        )

    def test_fetch_dispatch_aborts_cross_host_redirect_and_subresource(self) -> None:
        import json

        from u2s import cdp

        class RecordingWebSocket:
            def __init__(self) -> None:
                self.messages: list[dict] = []

            def send_text(self, body: str) -> None:
                self.messages.append(json.loads(body))

        for params in (
            {
                "requestId": "redirect",
                "responseStatusCode": 302,
                "request": {"url": "https://redirect.example.net/"},
            },
            {
                "requestId": "subresource",
                "resourceType": "Script",
                "request": {"url": "https://cdn.example.net/script.js"},
            },
        ):
            with self.subTest(request_id=params["requestId"]):
                websocket = RecordingWebSocket()
                session = cdp._CdpSession(websocket)
                session.enable_fetch_interception(
                    allow_private=False,
                    allow_file_urls=False,
                    same_origin_only=True,
                    initial_url="https://official.example/",
                )
                with self.assertRaises(cdp._FetchBlocked) as caught:
                    session._dispatch(
                        {"method": "Fetch.requestPaused", "params": params}
                    )
                self.assertEqual(caught.exception.status, cdp.BLOCKED_CROSS_ORIGIN)
                self.assertEqual(
                    websocket.messages[-1]["method"], "Fetch.failRequest"
                )

    def test_network_redirect_chain_is_capped_per_request(self) -> None:
        from u2s import cdp

        class RecordingWebSocket:
            def send_text(self, _body: str) -> None:
                pass

        session = cdp._CdpSession(RecordingWebSocket())
        session.enable_fetch_interception(
            allow_private=True,
            allow_file_urls=False,
            initial_url="http://127.0.0.1/",
        )
        for hop in range(cdp.MAX_REDIRECTS):
            session._dispatch(
                {
                    "method": "Network.requestWillBeSent",
                    "params": {
                        "requestId": "chain-1",
                        "redirectResponse": {"status": 302},
                        "request": {"url": f"http://127.0.0.1/{hop + 1}"},
                    },
                }
            )
        with self.assertRaises(cdp._FetchBlocked) as caught:
            session._dispatch(
                {
                    "method": "Network.requestWillBeSent",
                    "params": {
                        "requestId": "chain-1",
                        "redirectResponse": {"status": 302},
                        "request": {"url": "http://127.0.0.1/overflow"},
                    },
                }
            )
        self.assertEqual(caught.exception.status, cdp.BLOCKED_REDIRECT_LIMIT)


class RedactionTests(unittest.TestCase):
    def test_redact_drops_query_fragment_userinfo(self) -> None:
        from u2s import security

        red = security.redact_url("https://user:hunter2@93.184.216.34:8443/path?token=tok#frag")
        self.assertEqual(red, "https://93.184.216.34:8443/path")
        self.assertNotIn("token", red)
        self.assertNotIn("tok", red)
        self.assertNotIn("hunter2", red)


class CdpLaunchSecurityTests(unittest.TestCase):
    def test_launch_argv_has_no_remote_allow_origins_and_binds_loopback(self) -> None:
        from u2s import cdp

        argv = cdp.build_cdp_launch_argv(
            cdp.CdpLaunchSpec(browser_path="/usr/bin/chromium", user_data_dir="/tmp/url2png_y",
                              resolver_pin=("example.com", "93.184.216.34"))
        )
        self.assertNotIn("--remote-allow-origins=*", argv)
        self.assertFalse(any(a.startswith("--remote-allow-origins") for a in argv))
        self.assertIn("--remote-debugging-address=127.0.0.1", argv)
        self.assertIn("--host-resolver-rules=MAP example.com 93.184.216.34", argv)

    def test_client_sends_no_origin(self) -> None:
        from u2s import cdp

        self.assertNotIn("Origin", cdp.client_request_headers())


class DocumentedLimitationTests(unittest.TestCase):
    def test_browser_side_dns_rebind_is_out_of_scope_of_python_gate(self) -> None:
        # The Python pre-resolve gate is an admission decision only; it cannot
        # bind Chromium's resolver. The host-resolver-rules pin covers only the
        # validated initial host (same-host rebind), and browser-side rebind on a
        # different redirect/sub-resource host is mitigated by per-request CDP
        # Network re-validation, not by the Python gate. This test documents and
        # asserts that the pin targets exactly the validated host.
        from u2s import capture, security

        with mock.patch("u2s.security.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
            admission = security.validate_target_url("http://example.com/")
        pin = capture.resolver_pin(admission)
        self.assertEqual(pin, ("example.com", "93.184.216.34"))



class SharedAddressSpaceTests(unittest.TestCase):
    """RFC 6598 shared address space must fail layer 2 like any RFC 1918 block.

    ``ipaddress`` classifies 100.64.0.0/10 as neither private nor globally
    reachable -- the one documented exception to that dichotomy -- so a check
    written on ``is_private`` alone admitted the whole range. That matters here
    specifically: Alibaba's metadata endpoint 100.100.100.200 lives inside it,
    and it was pinned as a single literal while every neighbour on its own /24
    was reachable.
    """

    IN_RANGE = ("100.64.0.0", "100.64.0.1", "100.100.100.1", "100.127.255.255")
    # The addresses immediately below and above the /10; both are public.
    OUT_OF_RANGE = ("100.63.255.255", "100.128.0.0")

    def test_the_stdlib_really_does_classify_the_range_as_neither(self) -> None:
        """The premise of the bug. If this ever fails, the guard's shape can change."""

        import ipaddress

        addr = ipaddress.ip_address("100.64.0.1")
        self.assertFalse(addr.is_private)
        self.assertFalse(addr.is_global)
        self.assertFalse(addr.is_reserved)
        self.assertFalse(addr.is_loopback)
        self.assertFalse(addr.is_link_local)
        self.assertFalse(addr.is_multicast)

    def test_blocks_every_literal_in_the_range(self) -> None:
        from u2s import security

        for literal in self.IN_RANGE:
            with self.subTest(literal=literal):
                with self.assertRaises(security.TargetBlocked) as ctx:
                    security.validate_target_url(f"http://{literal}/")
                self.assertEqual(ctx.exception.reason, security.BLOCKED_PRIVATE_ADDRESS)

    def test_blocks_a_hostname_that_resolves_into_the_range(self) -> None:
        from u2s import security

        with mock.patch.object(security.socket, "getaddrinfo",
                               return_value=_addrinfo("100.100.100.1")):
            with self.assertRaises(security.TargetBlocked) as ctx:
                security.validate_target_url("http://cgnat.example.com/")
        self.assertEqual(ctx.exception.reason, security.BLOCKED_PRIVATE_ADDRESS)

    def test_the_neighbours_of_the_pinned_metadata_ip_are_blocked_too(self) -> None:
        """The whole /24 around Alibaba's IMDS, not just the one pinned literal."""

        from u2s import security

        for host in ("100.100.100.1", "100.100.100.199", "100.100.100.201"):
            with self.subTest(host=host):
                with self.assertRaises(security.TargetBlocked):
                    security.validate_target_url(f"http://{host}/")
        # and the pinned literal still reports the stronger reason
        with self.assertRaises(security.TargetBlocked) as ctx:
            security.validate_target_url("http://100.100.100.200/")
        self.assertEqual(ctx.exception.reason, security.BLOCKED_METADATA_ENDPOINT)

    def test_does_not_over_block_the_boundaries(self) -> None:
        """A /10, not a /8: the addresses either side stay reachable."""

        from u2s import security

        for literal in self.OUT_OF_RANGE:
            with self.subTest(literal=literal):
                result = security.validate_target_url(f"http://{literal}/")
                self.assertEqual(result.host, literal)

    def test_redirects_and_subresources_are_revalidated_against_the_range(self) -> None:
        from u2s import security

        for literal in self.IN_RANGE:
            with self.subTest(literal=literal):
                with self.assertRaises(security.TargetBlocked):
                    security.revalidate_resolved_address(literal)

    def test_the_explicit_flag_relaxes_it_exactly_like_rfc1918(self) -> None:
        """Layer 2, not layer 3: --allow-private-targets reaches it."""

        from u2s import security

        for literal in ("100.64.0.1", "10.0.0.1"):
            with self.subTest(literal=literal):
                result = security.validate_target_url(
                    f"http://{literal}/", allow_private=True
                )
                self.assertEqual(result.host, literal)
        security.revalidate_resolved_address("100.64.0.1", allow_private=True)

    def test_the_flag_still_never_reaches_the_metadata_endpoint_inside_it(self) -> None:
        from u2s import security

        with self.assertRaises(security.TargetBlocked) as ctx:
            security.validate_target_url(
                "http://100.100.100.200/", allow_private=True
            )
        self.assertEqual(ctx.exception.reason, security.BLOCKED_METADATA_ENDPOINT)


if __name__ == "__main__":
    unittest.main()
