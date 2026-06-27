# Verification gates

Three independent gating layers, matching the repo's existing separation:

- **`doctor` / precheck** — capability: is a browser available?
- **offline `selftest`** — engine-logic correctness (deterministic, network-free,
  browser-free; the always-on CI layer).
- **`verify` verb** — artifact truth: the only thing allowed to declare a real
  screenshot done.

## Blank-output detection

A capture is blank when its byte length is below a small floor (a 1x1 PNG is
~69 bytes; real captures are kilobytes) or when a decimated sample of its pixels
is overwhelmingly a single dominant color (>= 98.5%). Detection runs on raw
decompressed PNG scanlines via stdlib `zlib`, so it never needs Pillow. It
returns `width`, `height`, `bytes`, and `dominant_color_fraction`.

## Render-wait and timeout

`--wait` is the post-load settle delay (default 800 ms). `--timeout` is the hard
navigation cap (default 30000 ms, max ~120000 ms); at expiry the process tree is
reaped (`u2s.procctl`) and the verdict is `BLOCKED_TIMEOUT`. The temp profile is
always cleaned up, even on crash or timeout.

## The strict `verify` gate

`verify` exits 0 only when ALL of these hold:

- `file` PASS and `bytes >= floor`;
- `decode` PASS;
- `dimensions` PASS (matches the expected size when supplied);
- `not_blank` PASS;
- `consent` PASS or SKIPPED.

Any other state exits nonzero with a structured `BLOCKED_*` / `UNVERIFIED`
verdict naming the failing sub-check. `capture`, source inspection, "the file
exists", or "Chromium exited 0" never constitute final success.

## SSRF admission vs navigation scope

The Python `validate_target_url` gate is a pre-navigation admission decision
only. It cannot bind Chromium's resolver, redirects, sub-resource fetches, or
JS-initiated requests. In default Tier-1 the only protections are this gate plus
a single-host `--host-resolver-rules` pin, so redirect/sub-resource SSRF is
unguarded there. Tier-2 CDP `Fetch`-domain request interception is the primary
browser-side control: every request is paused and re-validated before send, and a
violation is failed (`Fetch.failRequest`) before the body is fetched. The
cloud-metadata denylist is unconditional in both the admission gate and the
per-request interception check.
