# url-to-screenshot — Option A: wire the real capture + fix the review findings

Decision: **A — make the skill actually capture screenshots.** The current implementation is a verified offline scaffold but `run_capture` is a no-PNG stub and the CI capture job is dead-on-arrival. Fix every issue in `tasks/u2s-impl-review-findings.md`. Work in the real tree at `/home/ubuntu/ai-agents-skills`. Do NOT commit.

A real Chromium is available at `/usr/bin/chromium` (v149) — you MUST use it to prove capture works end-to-end. Do not declare done until `capture` produces a real PNG that passes `verify`.

## Hard invariant (do not break)
The **offline selftest must stay offline**: `u2s/selftest.py` and the runtime-smoke path must NEVER launch a browser or open a socket. The static-check/test that proves `u2s/cdp.py` and `u2s/oneshot.py` browser-launch/socket paths are unreachable from the selftest import graph must still pass. Only the `capture` verb and the CI job exercise real capture. Keep all third-party imports optional (stdlib-only blocking path).

## 1. Wire Tier-1 one-shot (u2s/oneshot.py)
Implement the runner that actually launches: `chromium --headless --screenshot=<out.png> --hide-scrollbars --window-size=W,H [--virtual-time-budget=<wait_ms>] <url>` via `u2s/procctl.py` (platform-split launch + timeout reap), into a fresh `tempfile.mkdtemp(prefix="url2png_")` `--user-data-dir`, cleaned up in `finally` via `procctl.cleanup_profile_dir`. Add `--no-sandbox` only when root/container is detected (record `"sandbox":"disabled"`+reason). Return the PNG path. Honor `--timeout` (hard wall-clock kill -> `BLOCKED_TIMEOUT`). Tier-1 cannot do full-page (document; a full-page request must not silently use Tier-1 — see capture.py routing).

## 2. Implement the real CDP runner (u2s/cdp.py)
The docstring already promises this — implement it. Stdlib WebSocket client (socket + `Sec-WebSocket-Key` SHA-1 handshake + frame encode/decode), no third-party requirement (`websocket-client` stays optional fallback). Flow:
- Launch Chromium headless via procctl, bound to loopback: `--remote-debugging-port=0 --remote-debugging-address=127.0.0.1`, **no `--remote-allow-origins` flag**, fresh `url2png_` user-data-dir, `--host-resolver-rules="MAP <validated-host> <validated-ip>"`.
- Discover the page target via `http://127.0.0.1:<port>/json` (stdlib `http.client`/`urllib`); take the per-target `webSocketDebuggerUrl`. Connect sending **no `Origin` header**.
- `Page.enable`, `Network.enable`; navigate (`Page.navigate`) -> wait `Page.loadEventFired` + settle wait; **CDP `Network` per-request re-validation**: on each `Network.requestWillBeSent`/redirect, re-run `security.revalidate_resolved_address` on the freshly-resolved IP and ABORT (close + `BLOCKED_PRIVATE_ADDRESS`/`BLOCKED_METADATA_ENDPOINT`) on violation; cap redirects (~5).
- Optional consent removal via `Runtime.evaluate` (the `u2s/consent.py` JS) with the blank-page guard + the §7-S6 full-page fallback matrix.
- `Page.captureScreenshot` (viewport, or full-page via `getLayoutMetrics.cssContentSize` clip + `scale=device-scale-factor`, area checked against the bomb cap BEFORE capture). Base64-decode -> write PNG.
- Tear down (close ws, reap browser tree via procctl) in `finally`; remove the temp profile dir.

## 3. Wire run_capture (u2s/capture.py)
Replace the `READY` stub with a real orchestration: `security.validate_target_url()` admission gate -> `detect` browser (if missing -> `BLOCKED_ENVIRONMENT`) -> choose tier (`--consent on` default -> Tier-2/CDP; `--consent off` or `--engine oneshot` -> Tier-1; `--engine cdp` forces Tier-2) -> launch + capture -> allocate output path via `u2s/naming.py` (or honor `--out`) -> write PNG + `result.json` sidecar -> return the real path/verdict. `auto` falls back Tier-2 -> Tier-1 on CDP failure or consent-blank (per the matrix). A `--full-page` request must never silently degrade to a viewport capture. Remove the dead `from dataclasses import ... field` import here and in cdp.py.

## 4. file:// for trusted local fixtures (security.py + dispatcher)
The CI capture job tests against `file://` fixtures, but the scheme allow-list rejects `file://`. Add a narrow, explicit opt-in: a new CLI flag `--allow-file-urls` (default OFF; env var alone must NOT enable it, same discipline as `--allow-private-targets`). When set, the scheme allow-list also accepts `file:` (SSRF IP checks are N/A for `file:`). Document clearly in SKILL.md Security notes that `--allow-file-urls` is for **trusted local fixtures/testing only** and enables local file reads (e.g. `file:///etc/passwd`), so it must never be used on attacker-influenceable input. Without the flag, `file:`/`data:`/etc. stay `BLOCKED_SCHEME`.

## 5. Fix the metadata-denylist bypass (security.py) — MEDIUM, do this
In `_is_metadata_ip`, after the literal/canonical comparison, unwrap IPv4-mapped IPv6 and re-check: if `getattr(ip,'ipv4_mapped',None) is not None`, return `_is_metadata_ip(str(ip.ipv4_mapped))`. Apply the same so `revalidate_resolved_address` is covered. Add selftest + security-test cases asserting `http://[::ffff:100.100.100.200]/` and `http://[::ffff:169.254.169.254]/` are BLOCKED_METADATA_ENDPOINT even with `allow_private=True`. Do not weaken any existing control.

## 6. Fix the CI capture job (.github/workflows/tests.yml)
Make the `linux-capture` job actually pass: install Chromium (`browser-actions/setup-chrome`), then run `capture --allow-file-urls --engine cdp --url file://<abs>/plain.html --out golden.png` and `verify golden.png` -> assert `VERIFIED`; consent fixture -> not-blank after removal; full-page (`tall.html`) height > viewport; `--timeout 1` on `slow.html` -> `BLOCKED_TIMEOUT` with no leftover `url2png_*` dir and no child Chromium PID. Network must stay forbidden (file:// only). Ensure no command aborts under `set -euo pipefail` because of a now-allowed scheme.

## 7. Honesty pass (docs match code)
Now that capture really captures: confirm `canonical/skills/url-to-screenshot/SKILL.md` Required-workflow/verbs honestly describe capture producing a PNG; `u2s/cdp.py` docstring matches the implemented runner; `references/engine-and-cdp.md` "CDP Network re-validation is active" is now true; tighten `references/browsers-and-platforms.md` temp-cleanup wording to match the wired `cleanup_profile_dir`.

## 8. Fix the low-severity test/validator nits
- `installer/ai_agents_skills/runtime_smoke.py` all-passed check: require both keys present (e.g. `payload.get("passed") is not None and payload.get("passed") == payload.get("total")`) so absent keys fail, not pass via `None == None`.
- `tests/test_url_to_screenshot_smoke.py` parity guard: require the explicit `skill == "<name>"` branch, drop the bare-name substring fallback that any mention satisfies.
- `tests/test_url_to_screenshot_security.py::test_env_var_alone_does_not_relax`: actually set `URL_TO_SCREENSHOT_ALLOW_PRIVATE=1` and call `validate_target_url`, asserting a private IP stays BLOCKED.
- `test_tier1_redirect_to_private_is_not_silently_captured`: exercise the documented Tier-1 path (or rename to match what it asserts) and add a real assertion that a Tier-1 capture of a redirect-to-private is not silently produced.

## Verification — do ALL, paste outputs
1. Offline gates unchanged & green: validate manifests parse; `selftest --work-dir /tmp/u2s-A` exits 0 (and STILL launches no browser / opens no socket — confirm the import-graph static check passes); `make static-check`, `make test`, `make runtime-smoke`, `make docs && make docs-check`. (After running anything in-place, `find canonical/runtime/skills/url-to-screenshot-runtime -name __pycache__ -prune -exec rm -rf {} +` so the inventory test stays green.)
2. **REAL capture (the point of Option A):** using `/usr/bin/chromium`:
   - `capture --allow-file-urls --engine cdp --url file://<abs path>/u2s/htmlfixtures/plain.html --out /tmp/g.png` then `verify /tmp/g.png` -> VERIFIED; confirm `/tmp/g.png` is a real non-blank PNG.
   - `capture --engine oneshot --url https://example.com --out /tmp/h.png` -> a real PNG (proves Tier-1 + real http(s)).
   - `capture --engine cdp --url https://example.com --out /tmp/c.png` -> a real PNG (proves the CDP runner + Network re-validation against a real site).
   - `capture --url http://169.254.169.254/ ...` -> BLOCKED_METADATA_ENDPOINT (SSRF still closed); `--allow-private-targets` still blocks it.
   - `--timeout 1` on a slow/large page -> BLOCKED_TIMEOUT with the browser tree reaped and no leftover `url2png_*` temp dir.
3. Report: files changed, each verification command + result, and confirm the offline selftest still launches no browser.

Do not commit. Leave everything in the working tree for review.
