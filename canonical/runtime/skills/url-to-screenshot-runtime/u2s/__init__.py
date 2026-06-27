"""url-to-screenshot runtime package.

Captures an arbitrary ``http(s)`` URL to a clean, verified PNG (viewport or
full page) across Linux, macOS, and Windows, with cookie-consent dismissal,
blank-output detection, and a deterministic capture-then-verify workflow.

Design invariants (see ``canonical/skills/url-to-screenshot/references/``):
  * SSRF-safe admission: ``u2s.security.validate_target_url`` is a fail-closed
    pre-navigation chokepoint (scheme allow-list, resolve-then-check every A/AAAA,
    unconditional cloud-metadata denylist). It is an admission decision only and
    never binds Chromium's own resolver.
  * Browser-side defense-in-depth: the CDP ``Network`` domain re-validates every
    requested URL (redirects, sub-resources); the Python gate is advisory there.
  * No third-party imports are required to run the engine or the offline
    ``selftest``: ``websocket-client``/``Pillow``/ImageMagick are OPTIONAL with
    standard-library fallbacks, so all browser-launch and socket code is LAZY
    (inside functions) and unreachable from the ``selftest`` import graph.
  * No committed binary fixtures: all PNG test bytes are synthesized in memory by
    ``u2s.pngtools`` (``zlib`` + ``struct``).
"""

__version__ = "0.1.0"
