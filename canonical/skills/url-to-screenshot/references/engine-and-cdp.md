# Engine and CDP

The engine has two capture tiers. Both are preceded by the fail-closed SSRF
admission gate (`u2s.security.validate_target_url`).

## Tier-1: headless one-shot

`chromium --headless=new --screenshot=out.png URL`. Fast, no websocket, no CDP
loop. It is the `--consent off` fallback path, not the stock default. Its only
SSRF protections are the Python pre-resolve admission gate plus a single
`--host-resolver-rules="MAP host ip"` pin of the validated top-level host.
Redirect and sub-resource SSRF are unguarded in Tier-1 (the resolver pin covers
only the named initial host).

## Tier-2: CDP

With the default `--consent on`, ordinary captures enter Tier-2 because consent
dismissal is a CDP DOM op. The launch:

- `--remote-debugging-port=0` (ephemeral) and `--remote-debugging-address=127.0.0.1`
  (loopback bind);
- NO `--remote-allow-origins` flag at all. The stdlib websocket client sends NO
  `Origin` header, so Chromium's default-deny of Origin-bearing CDP applies. A
  scoped `--remote-allow-origins` at a guessed port would only OPEN a hole for a
  forged Origin, so it is never added.
- a single `--host-resolver-rules="MAP host ip"` pin of the validated initial
  host (defeats same-host rebind only; kept as defense-in-depth alongside the
  Fetch interception below, which is now the real control).

Flow: discover the page target via `/json` (stdlib `http.client`), capture the
per-target `webSocketDebuggerUrl` GUID, open a minimal stdlib websocket
(socket + SHA-1 handshake), then `Fetch.enable` (catch-all, request stage) ->
`Page.navigate` under interception -> `Page.loadEventFired` -> settle pump ->
consent removal -> `Page.captureScreenshot`.

The per-target GUID is a loopback handle, not a true secret: loopback `/json`
publishes it cleartext to any local process during the capture window.

### CDP Fetch interception (primary SSRF control)

The CDP `Fetch` domain is enabled with `{"patterns":[{"urlPattern":"*",
"requestStage":"Request"}]}` BEFORE `Page.navigate`, so every request — the main
navigation, every redirect hop, and every sub-resource / JS-initiated fetch — is
PAUSED before it is sent. For each `Fetch.requestPaused` the runner re-validates
the request: the scheme allow-list (`http`/`https`, plus `file:` only with
`--allow-file-urls`) and a fresh resolve-and-check of every resolved IP (metadata
denied unconditionally; private/loopback/link-local denied unless
`--allow-private-targets`). A violating request is failed with
`Fetch.failRequest({errorReason:"AccessDenied"})` so the body is never fetched,
and an admissible one is released with `Fetch.continueRequest`. Redirects are
capped (a 3xx reaching the request stage counts toward `MAX_REDIRECTS` ≈ 5).

When `--same-origin-only` is enabled, the same interception loop also rejects
every paused redirect and sub-resource whose origin tuple differs from the
initial request: scheme, canonical hostname, or effective port (explicit port,
otherwise 80 for HTTP and 443 for HTTPS). This flag forces Tier-2, disables
automatic Tier-1 fallback, and is attested as `same_origin_only=true` in the
result.

This is true interception — the request is blocked BEFORE send, not observed
after the fact. The same re-validation runs in every read loop (navigation, the
settle pump, consent eval, screenshot), so a post-load JS fetch to a private/
metadata host is paused and blocked too.

Navigation completion is loader-scoped. The engine waits for the
`Page.navigate` response, then accepts only a `Page.lifecycleEvent` load whose
frame and loader IDs match that response. It does not accept the unscoped
`Page.loadEventFired` emitted by Chromium's initial tab. Guarded results attest
this as `navigation_complete=true`.

v1 abort policy: ANY private/metadata (or disallowed-scheme) hit aborts the whole
capture with the matching `BLOCKED_*` status (no partial screenshot is produced
after a blocked private/metadata fetch). This is the simplest fail-closed choice;
the metadata fetch returns `BLOCKED_METADATA_ENDPOINT`, a private one
`BLOCKED_PRIVATE_ADDRESS`. Without this interception the Python admission gate is
advisory only for any host other than the main navigation host.

## Full-page

Full-page uses `Page.getLayoutMetrics` -> `cssContentSize` (CSS px) for the clip
width/height and passes `scale=device-scale-factor`. The requested pixel area
(`w*h*scale^2`) is checked against the decompression-bomb cap BEFORE capture. A
`--full-page` request never silently degrades to a viewport capture. The result
records the measured layout and output dimensions plus a completeness
attestation; consumers must check that attestation rather than treating
`full_page=true` alone as proof. It requires `document.readyState=complete`,
remeasures the document after screenshot generation, and refuses publication if
the page grew beyond the admitted clip. Completeness is relative to the bounded
measured DOM/layout extent and does not promise expansion of arbitrary nested
scroll containers, shadow trees, or out-of-process frames.

## Fallbacks

`auto` falls back Tier-2 -> Tier-1 on a CDP failure or on the
consent-removal-blanks-page guard. For a `--full-page` request that blanks after
consent removal, the engine re-attempts full-page in CDP WITHOUT consent removal
rather than dropping to a viewport one-shot; if still blank it emits
`BLANK_OUTPUT` / `UNVERIFIED`.

No Tier-1 fallback occurs when `--same-origin-only` is enabled, because Tier-1
cannot enforce the paused-request origin boundary.

For generic Tier-2 capture, cross-host requests retain a DNS TOCTOU residual:
the Python interceptor checks its own fresh resolution before release, while
Chromium performs the eventual connection. Strict same-origin capture admits no
different host and pins the initial host, so this cross-host residual does not
apply to the venue-proof path.
