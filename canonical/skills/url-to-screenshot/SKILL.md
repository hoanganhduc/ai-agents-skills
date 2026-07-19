---
name: url-to-screenshot
description: Use when the user wants to capture a web page (an http or https URL) to a clean PNG screenshot, in viewport or full-page mode, with cookie-consent dismissal, timeouts, SSRF-safe URL admission, and blank-output verification, across Linux, macOS, and Windows. The executable engine ships as the url-to-screenshot-runtime skill.
metadata:
  short-description: Capture an http(s) URL to a verified PNG with consent dismissal and SSRF-safe admission
---

# URL to Screenshot

Capture an arbitrary `http(s)` URL to a clean, verified PNG (viewport or full
page) across Linux, macOS, and Windows. The flow detects an installed browser,
admits the URL through a fail-closed SSRF gate, captures over headless Chromium,
dismisses cookie-consent overlays, detects blank output, and ends with an
explicit `verify` gate that is the only thing allowed to declare success.

This is the repo's first skill that drives a real browser against an
attacker-influenceable URL, so the SSRF, sandbox, and timeout safeguards are
first-class and the security posture is documented honestly below.

## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command
target. Set `$runtime` to the installed runtime root. Multi-agent installs usually
use `%LOCALAPPDATA%\ai-agents-skills\runtime`.

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/url-to-screenshot-runtime/run_url_to_screenshot.bat" doctor
& "$runtime\run_skill.bat" "skills/url-to-screenshot-runtime/run_url_to_screenshot.ps1" doctor
```

POSIX examples below use `run_skill.sh` and the `.sh` command target.

## When to use

Use this skill when the user wants to:

- screenshot a public web page for documentation, research, or QA
- capture a full-page (beyond-viewport) image, not just the visible viewport
- capture at a specific viewport size or device-scale factor
- dismiss a cookie-consent overlay that occludes the content

Do NOT use this to bypass paywalls, age gates, or login walls, or to capture
content the agent's own policy would refuse. Consent dismissal is scoped to
cookie-consent overlays only.

## Runtime helper (verbs)

The executable engine ships as the `url-to-screenshot-runtime` skill. Run it via
the managed runner:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/url-to-screenshot-runtime/run_url_to_screenshot.sh doctor
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/url-to-screenshot-runtime/run_url_to_screenshot.sh capture --url https://example.com/ --out shot.png
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/url-to-screenshot-runtime/run_url_to_screenshot.sh verify --png shot.png --expected-width 1280 --expected-height 800
```

Verbs: `doctor` (readiness), `capture` (URL -> PNG, SSRF-gated), `verify` (the
artifact-truth gate), `selftest` (offline smoke).

Key options for `capture`: `--url` (required, http/https only), `--out`,
`--viewport WxH` (or `--width`/`--height`), `--full-page`, `--device-scale`,
`--wait`, `--timeout`, `--consent on|off`, `--engine auto|oneshot|cdp`,
`--browser`, `--allow-private-targets` (relaxes the private-IP block ONLY), and
`--allow-file-urls` (trusted local fixtures/testing ONLY), and
`--same-origin-only` (CDP-only strict initial-origin boundary) — see Security
notes.

## Required workflow

1. Run `doctor` first to confirm a browser is present. `file-exists` and
   `offline-smoke` verification passing does NOT imply a browser is installed --
   only `doctor` reports real capture readiness.
2. Run `capture` with the target URL. The URL is admitted through the SSRF gate
   before any browser launch; a blocked URL yields a `BLOCKED_*` verdict.
3. Run `verify` on the produced PNG. Treat the capture as done only when
   `verify` returns `final_verdict=VERIFIED`.

## Strict approval / verification surface

`capture` produces a PNG but never declares success. The `verify` verb is the
only thing that declares a real screenshot done: `final_verdict=VERIFIED` only
when file/decode/dimensions/not-blank/consent all PASS. "The file exists",
source inspection, or "Chromium exited 0" never constitute final success. Use
`BLOCKED_*` / `UNVERIFIED` wording for any non-VERIFIED state; do not use
approval-style wording for a capture that did not verify.

## Security notes

This skill fetches an attacker-influenceable host and drives a sandbox-sensitive
browser, so the security posture is stated plainly:

- **SSRF admission gate (fail-closed, pre-navigation).** `validate_target_url`
  enforces a scheme allow-list (`http`/`https` only), resolves every A/AAAA and
  rejects loopback/private/link-local/reserved/multicast addresses, and applies
  an UNCONDITIONAL cloud-metadata denylist (`169.254.169.254`,
  `metadata.google.internal`, and peers). It is an admission decision only; it
  cannot bind Chromium's own resolver, redirects, or sub-resource fetches.
- **Tier scope.** With the default `--consent on`, ordinary captures enter
  Tier-2 (CDP) because consent dismissal is a CDP DOM operation; Tier-1
  one-shot is the `--consent off` fallback. In default Tier-1 the only
  protections are the Python pre-resolve admission gate plus a single
  `--host-resolver-rules` MAP pin of the validated initial host, so
  redirect/sub-resource SSRF is unguarded there (in-scope-and-unmitigated). The
  PRIMARY browser-side control is Tier-2 CDP **`Fetch`-domain request
  interception**: `Fetch` is enabled (catch-all, request stage) BEFORE
  navigation, so every request — main frame, redirects, and sub-resources /
  JS-initiated fetches — is PAUSED and re-validated (scheme allow-list plus a
  fresh resolve-and-check of every resolved IP) and FAILED BEFORE SEND on a
  violation (`Fetch.failRequest`), never merely observed after the fact. v1
  policy: any private/metadata hit aborts the whole capture with the matching
  `BLOCKED_*` status; redirects are capped. The `--host-resolver-rules` pin
  remains as same-host-rebind defense-in-depth.
- **CDP origin posture.** The CDP endpoint launches bound to `127.0.0.1` on an
  ephemeral port with NO `--remote-allow-origins` flag at all, and the stdlib
  client sends NO `Origin` header, so Chromium's default-deny of Origin-bearing
  CDP applies. The real protections are this default-deny, the per-target
  `webSocketDebuggerUrl` GUID plus the loopback bind, and `finally` teardown. On
  a shared host, any local process can read the CDP endpoint and per-target GUID
  from loopback `/json` during the capture window; the GUID is a loopback handle,
  not a true secret.
- **Override scope.** `--allow-private-targets` relaxes the private/loopback/
  link-local block ONLY. It NEVER relaxes the scheme allow-list and NEVER the
  cloud-metadata denylist. It requires the CLI flag; the env var
  `URL_TO_SCREENSHOT_ALLOW_PRIVATE=1` alone does not enable it, so an inherited
  or poisoned environment cannot silently disable SSRF blocking.
- **`--allow-file-urls` (trusted fixtures only).** Off by default; without it,
  `file:`/`data:`/etc. stay `BLOCKED_SCHEME`. When set it adds `file:` to the
  scheme allow-list so the engine can capture trusted local HTML fixtures (the CI
  capture job uses it against `u2s/htmlfixtures/*.html`). It enables LOCAL FILE
  READS (e.g. `file:///etc/passwd`), so it is for trusted local fixtures/testing
  ONLY and must NEVER be used on attacker-influenceable input. Like
  `--allow-private-targets` it requires the CLI flag; the environment alone never
  enables it. A `file:` URL has no remote host, so the SSRF IP checks do not
  apply to it.
- **Strict origin boundary.** `--same-origin-only` forces CDP and aborts every
  paused redirect or sub-resource whose origin tuple differs from the initial
  request: scheme, canonical hostname, or effective port (explicit port,
  otherwise 80 for HTTP and 443 for HTTPS). Auto fallback to Tier-1 is disabled
  while the flag is active, and explicit Tier-1 is rejected.
- **Residual limitation for generic cross-host capture.** In ordinary Tier-2
  capture, an allowed request to another host is re-resolved and checked by the
  Python CDP interceptor before release, but Chromium resolves that cross-host
  request independently. The checked address can therefore differ from the
  address Chromium ultimately connects to: a cross-host DNS TOCTOU window is
  narrowed, not eliminated. Tier-1 one-shot has no per-request hook. The strict
  same-origin venue-proof path does not admit a different host and pins the
  initial host, so this particular cross-host residual is not a venue-proof
  limitation. This skill does not imply full SSRF protection for generic capture.

## References

- `references/engine-and-cdp.md` — CDP websocket (no `--remote-allow-origins`
  flag, no client `Origin` header), per-target GUID loopback handle, consent DOM
  removal, one-shot fallback, CDP `Fetch`-domain request interception (blocks
  before send, all hosts).
- `references/browsers-and-platforms.md` — browser detection order and per-OS
  notes.
- `references/verification-gates.md` — blank-output detection, render-wait /
  timeout semantics, the strict `verify` gate, and SSRF admission-vs-navigation
  scope.

## Boundaries

- The executable engine ships as the `url-to-screenshot-runtime` skill; this
  skill-file is the user-facing workflow and references.
- On openclaw, NEITHER skill installs natively: this skill-file ships
  `references/` files (a non-`SKILL.md` payload) so the openclaw skill-file
  install is blocked, and openclaw runtime support is manual/fake-root. Neither
  runs on openclaw real-system until an approved runtime manifest and broker
  exist.
- `file-exists` / `offline-smoke` verification passing does NOT imply a browser
  is present. Real capture readiness is reported only by `doctor`.
- The real native browser capture / CDP / timeout-reap tier on Windows and macOS
  is not exercised by automated CI; it requires a manual `doctor` + capture run.
  Windows job-object/`taskkill` reaping and locked-file profile cleanup are
  verified only by manual Windows runs.
