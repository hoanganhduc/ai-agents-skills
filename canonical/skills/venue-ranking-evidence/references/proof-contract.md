# Proof Contract

## Bundle

A proof bundle contains:

- `official-page.pdf`: unmodified browser Print-to-PDF output;
- `official-page.png`: full-page screen-layout reference with capture-metadata
  completeness attestation relative to the measured top-level layout;
- a manifest entry in `proofs.jsonl` linking both artifacts to one observation.

Record the requested observation URL and both actual redacted final record URLs,
venue/source/observation IDs, edition or metric year, category or collection,
UTC capture time, browser and runtime versions, actual media mode/page settings,
background setting, loader-scoped navigation completion, measured PNG document
readiness/layout/output dimensions and completeness,
byte counts, page count, SHA-256 hashes, expected visible markers, and warnings.
Copy capture settings from the PDF/PNG runtime sidecars; do not hard-code or
infer metadata that the browser runtime did not attest.

## Capture

Use the guarded browser runtime with strict same-origin interception so the
initial URL, redirects, and every paused subresource retain the admitted scheme,
canonical hostname, and effective port. The initial host is resolver-pinned;
therefore the generic URL runtime's residual cross-host DNS TOCTOU does not apply
to this same-origin ICORE proof path.
Require the load event for the exact frame and loader returned by
`Page.navigate`; an unscoped initial-tab load event is not evidence that the
requested record finished parsing. A PNG proof also requires
`document.readyState=complete` before and after capture, with a post-capture
extent remeasurement that fails closed if the page grew beyond the admitted
clip.
Wait for expected title, identifier, edition/year, and
claimed value markers. Expand relevant tabs or sections when provider policy
allows normal browser interaction. Do not bypass CAPTCHA, login, or access
controls.

Only a reviewed source-specific association adapter may bind visible text to an
observation. Generic title/value proximity on a multi-record page is not proof.
The current adapter is ICORE-only: it binds the actual final PDF and PNG record
URLs to the selected ICORE detail-page ID and checks the adjacent
`Source: <edition>` / `Rank: <value>` block under the venue title. Requested,
PDF-final, and PNG-final URLs must identify the same admitted record. Other
sources remain proof-ineligible until an equivalent reviewed built-in adapter,
freshness design, policy review, and fixtures exist. Authenticated/profile proof
is not implemented.

`media=print` represents browser Print. `media=screen` requests screen CSS before
printing but still paginates into PDF. Neither is a promise of pixel identity.

## Verification

Only `verify` may return `VERIFIED`. Require at least:

- PDF signature and bounded byte size;
- positive bounded structural page count (structural validity alone is not a
  final proof verdict);
- matching SHA-256 and manifest references;
- nonblank capture/DOM evidence;
- actual PDF/PNG sidecar metadata matches the manifest rather than assumed
  settings;
- requested, PDF-final, and PNG-final URLs bind to the same selected official
  record under the source allowlist;
- the PNG full-page completeness attestation and decoded dimensions agree with
  the measured capture metadata;
- PDF and PNG sidecars attest loader-scoped navigation completion, and the PNG
  sidecar attests complete document readiness;
- expected identity and claim markers;
- source-specific venue identity and record/row claim association;
- no access-denied, login, CAPTCHA, error, or skeleton-page markers.

Return `UNVERIFIED` or a specific `BLOCKED_*` status on any unmet requirement.
Cached proof must be labelled cached and may not silently satisfy a live-proof
request.

`proof` performs preliminary capture checks but records the bundle as
`UNVERIFIED`; only `verify` reruns the structural PDF/PNG checks, recomputes
markers from the source/venue/observation records, and may emit `VERIFIED`.
