# url-to-screenshot — fix the Tier-2 SSRF gap (observe → intercept)

## The defect (HIGH, verified by direct code read)
`u2s/cdp.py::run_cdp_capture` re-validates network requests AFTER `Page.loadEventFired` + settle, via the observe-only `Network.requestWillBeSent`, and only inspects redirect-hop `remoteIPAddress`. So:
- Redirect/sub-resource requests to private/metadata hosts are **actually sent** (the browser, inside the victim network, fetches them); only the final screenshot is suppressed.
- Non-redirect sub-resources are never re-validated.
- This contradicts the documented "PRIMARY browser-side SSRF control … aborts the navigation on a violation."

The admission gate (`validate_target_url`) + `--host-resolver-rules` pin still protect the MAIN navigation host. The gap is every OTHER host reached via redirect or sub-resource in Tier-2.

## The fix: real request interception via the CDP `Fetch` domain
Block each request BEFORE it is sent.

1. After the websocket opens and `Network.enable`, call `Fetch.enable` with `{"patterns":[{"urlPattern":"*","requestStage":"Request"}]}` **before** `Page.navigate`.
2. Restructure the runner into an interception event loop that, until `Page.loadEventFired` (or deadline), pumps CDP messages and for every `Fetch.requestPaused` event resolves it:
   - Parse `params.request.url`. **Scheme check:** allow only `http`/`https` (and `file:` only when `allow_file_urls` AND it is a local fixture context). Anything else (`chrome:`,`data:`,`view-source:`,`blob:`,`file:` without the flag) → `Fetch.failRequest({requestId, errorReason:"AccessDenied"})` and continue (do not abort the whole capture for a stray blocked sub-resource scheme; but a blocked MAIN-frame target aborts).
   - **IP check:** `security.resolve_host(host)` then `security.revalidate_resolved_address(ip, allow_private=...)` for every resolved IP (metadata unconditional; private unless allow_private). On `TargetBlocked` → `Fetch.failRequest({requestId, errorReason:"AccessDenied"})`; if it is the main document/navigation request, abort the capture with that `BLOCKED_*` status; for a sub-resource, fail just that request and record it (the screenshot still completes without the blocked resource) — OR, simpler and safer for v1, abort the whole capture on ANY private/metadata hit (document this choice). Pick one and be consistent; aborting-on-any-hit is acceptable and simplest.
   - **Redirect cap:** track redirect count (a `Fetch.requestPaused` whose `responseStatusCode` is 3xx, or per-requestId chain) → over `MAX_REDIRECTS` → fail + abort.
   - Otherwise `Fetch.continueRequest({requestId})`.
3. The host-resolver-rules pin stays (defense-in-depth) but Fetch interception is now the real control and covers ALL hosts.
4. Keep the existing reap/cleanup `finally` and the bomb-cap-before-capture.
5. Delete the now-superseded `_validate_network_events` post-hoc path (or keep only as a redundant backstop — but the Fetch loop is authoritative).

## Tests (must PROVE blocking, not just suppression)
Add to `tests/test_url_to_screenshot_security.py` and an integration check:
- Pure: a `Fetch.requestPaused`-style unit test that a paused request to `169.254.169.254` / `10.0.0.5` / `::ffff:169.254.169.254` yields a `Fetch.failRequest` decision, and a public IP yields `Fetch.continueRequest`; a `file:`/`chrome:` sub-resource without the flag yields failRequest.
- Integration (with /usr/bin/chromium): a committed fixture `htmlfixtures/ssrf.html` that does `fetch('http://169.254.169.254/x')` (and/or an `<img src>` to a private IP) and writes the outcome into the DOM. Capturing it with `--allow-file-urls` must (a) return `BLOCKED_METADATA_ENDPOINT` (capture aborted) OR, if you chose per-resource fail, produce a screenshot where the metadata fetch visibly FAILED (DOM shows the error, never the metadata body) — and prove the request was failed (the page never received metadata content). A positive control (`https://example.com`) still captures fine.
- Keep the offline selftest browser/socket-free (the Fetch loop lives in the lazy runner; selftest tests only pure decision helpers — add a pure `fetch_decision(url, resolved_ips, allow_private, allow_file_urls) -> "continue"|"fail"` helper and unit-test THAT offline).

## Docs
Update `cdp.py` docstring, `references/engine-and-cdp.md`, and SKILL.md Security notes to describe **Fetch-domain request interception (blocks before send, all hosts)** accurately — no overstatement.

## Verification (paste outputs)
1. Offline gates still green (static-check, test, runtime-smoke, docs-check); selftest still launches no browser/opens no socket; clean `__pycache__` after in-place runs.
2. With `/usr/bin/chromium`: the SSRF fixture is BLOCKED (metadata/private request failed before send — prove the page never got the body); `https://example.com` still captures a real PNG via CDP; `--timeout 1` still reaps.
3. Report files changed + each verification command/result.

Do not commit; leave in the working tree.
