"""url-to-screenshot runtime package.

Captures an arbitrary ``http(s)`` URL to a clean PNG (viewport or full page) or
browser-print PDF across Linux, macOS, and Windows, with cookie-consent
dismissal and deterministic artifact verification.

Design invariants (see ``canonical/skills/url-to-screenshot/references/``):
  * SSRF-safe admission: ``u2s.security.validate_target_url`` is a fail-closed
    pre-navigation chokepoint (scheme allow-list, resolve-then-check every A/AAAA,
    unconditional cloud-metadata denylist). It is an admission decision only and
    never binds Chromium's own resolver.
  * Browser-side defense-in-depth: the CDP ``Fetch`` domain intercepts and
    re-validates every requested URL (redirects and sub-resources) before send.
  * No third-party imports are required to run the engine or the offline
    ``selftest``: ``websocket-client``/``Pillow``/ImageMagick are OPTIONAL with
    standard-library fallbacks, so all browser-launch and socket code is LAZY
    (inside functions) and unreachable from the ``selftest`` import graph.
  * No committed binary fixtures: PNG and PDF smoke inputs are synthesized in
    memory.
"""

__version__ = "0.2.0"
