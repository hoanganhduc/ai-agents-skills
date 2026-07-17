# Implementation Plan — `url-to-screenshot` skill (ai-agents-skills)

> Repo: `/home/ubuntu/ai-agents-skills`. Every fact below was re-verified against the live repo and installer code:
> `manifest/*.yaml` are strict JSON-in-`.yaml`; the two 7-agent skills (`autonomous-research-loop`, `vnu-eoffice`)
> are **SKILL.md-only and NOT in `runtime.yaml`** (verification has **no** `offline-smoke`); every runtime offline-smoke
> skill is **5 agents** except `autonomous-research-loop-runtime` which is **6 agents (no openclaw)**; `runtime_newline_ok`
> reads file **bytes** and fails any PNG under `lf`/`crlf`, returning `True` only when `newline ∉ {lf,crlf}`;
> `validate_runtime_file` requires the `newline` key but does **not** restrict its value; there are **zero**
> `type: binary` runtime files and **zero** committed binary fixtures anywhere under `canonical/runtime`;
> `validate_smoke_output` is a per-skill if/elif ladder that **falls through to an exit-zero-only check** for any
> unlisted skill; `target_skill_block_reason` does **not** gate on `required_dependencies`; `.gitattributes` has rules
> only for `*.sh/*.py/*.ps1/*.bat/*.yaml/*.yml/*.json` (no `*.png`, no `*.html`); `pptx-render-system-tool` has a valid
> `macos` candidates key (`ffmpeg-system-tool` does **not** — only linux/windows); `slides-to-video` uses dispatcher
> `slides_to_video_runtime.py` + package `s2v/`, all runtime files `type: text`, `.sh`=0755/lf, `.bat/.ps1`=0644/crlf,
> `.py/.txt`=0644/lf, `smoke.timeout_seconds: 30`, and synthesizes all test data in-memory (no committed images).

---

## 0. Decisions binding for the whole plan (these resolve the panel disagreements and the high/medium critiques)

These four decisions are load-bearing and supersede any earlier draft wording.

1. **Two manifest skills, not one** (resolves the no-precedent 7-agent-runtime blocker). The repo has **no precedent**
   for a single skill that is both a multi-agent skill-file owner and a runtime offline-smoke engine. We follow the only
   established split exactly — `autonomous-research-loop` (skill-file, 7 agents, no runtime) +
   `autonomous-research-loop-runtime` (6 agents, no openclaw, offline-smoke):
   - **`url-to-screenshot`** — skill-file skill, **6 agents** (`codex, claude, deepseek, copilot, opencode, antigravity`;
     **no openclaw** — a reference-shipping skill cannot install its skill-file on openclaw, see B4 below), **no** runtime
     entry, verification `["file-exists","metadata-valid","agent-visible"]` (**no** offline-smoke). Owns `SKILL.md`,
     references, entrypoint.
   - **`url-to-screenshot-runtime`** — runtime-backed engine, **6 agents** (`codex, claude, deepseek, copilot, opencode,
     antigravity`; **no openclaw** — openclaw runtime is manual/fake-root per `docs/surfaces.md`), verification
     `["file-exists","metadata-valid","agent-visible","offline-smoke"]`. Owns the `u2s/` engine, runners, dispatcher, selftest.
   - The skill-file `SKILL.md` documents that the executable engine ships as `url-to-screenshot-runtime`. On openclaw
     **neither** skill installs natively: the skill-file ships `references/` files, and `planner.py:394-403` blocks the
     openclaw skill-file install whenever the canonical dir carries any non-`SKILL.md` file (engine remains
     manual/fake-root until an approved runtime manifest exists).

2. **No committed binary fixtures** (resolves the newline-verify blocker, the no-binary-precedent risk, the
   `.gitattributes` byte-stability risk, and fixture duplication — all at once). All PNG test inputs are **generated
   in-memory** by a single shared stdlib helper (`u2s/pngtools.py`, using `zlib`+`struct`), exactly matching the
   `s2v` "synthesize, don't ship" precedent. HTML fixtures used only by the optional Chromium capture job are committed,
   but the **blocking** selftest and unit tests touch **no committed binary file**. Consequence: no `.png` runtime
   files, no `type: binary` runtime entry, no new `.gitattributes` binary rule strictly required for blocking CI. We
   still add the `.gitattributes` HTML rule (see §2/§3g) because committed HTML capture-fixtures must stay LF.

3. **Selftest is exit-code-load-bearing AND machine-checked** (resolves the "JSON contract not enforced" blocker). The
   default `validate_smoke_output` only checks exit code for an unlisted skill. We do **both**:
   (a) `selftest` exits **nonzero** on any internal failure, so exit-zero is sufficient and the JSON body is advisory; and
   (b) we add a dedicated `elif skill == "url-to-screenshot-runtime"` branch to `validate_smoke_output` that parses
   stdout and asserts `ok is True`, `failures == []`, `passed == total`, and the offline-safety fields, mirroring the
   `axiom-axle-mcp`/`self-improving-agent`/`autonomous-research-loop-runtime` branches. **This is an installer code change**
   — Phase 6 explicitly includes it (the earlier "no installer code changes" claim is corrected).

4. **SSRF and CDP safety are defense-in-depth, not prose caveats** (resolves the two security blockers). The Python
   `validate_target_url()` gate is an **admission decision only** and cannot bind Chromium's resolver. For SSRF, the
   **primary** browser-side control is CDP `Network`-domain **per-request re-validation** (re-run the full S1 IP check on
   every requested URL's freshly-resolved address, abort on violation), backed by a single `--host-resolver-rules` MAP pin
   of the validated initial host (defeats same-host rebind only). For the CDP endpoint we launch with **no**
   `--remote-allow-origins` flag and have the stdlib client send **no `Origin` header**; the real protections are
   Chromium's default-deny of Origin-bearing CDP, the per-target `webSocketDebuggerUrl` GUID + loopback bind on an
   ephemeral port, and `finally` teardown. Scoped origins are kept only as optional hygiene, never load-bearing. SKILL.md
   states the residual browser-side rebind limitation honestly, that default Tier-1 leaves redirect/sub-resource SSRF
   unguarded, and that on a shared host any local process can read the CDP endpoint + GUID from loopback `/json` during
   the capture window; it never implies full SSRF protection.

### Naming (standardized, binding)
- Dispatcher: **`url_to_screenshot_runtime.py`** (mirrors `slides_to_video_runtime.py`).
- Engine package directory: **`u2s/`** (mirrors `s2v/`).
- Runner scripts: `run_url_to_screenshot.{sh,bat,ps1}`.
- Security chokepoint: **`u2s/security.py`** (inside the package).

---

## 1. Overview + chosen capture engine

`url-to-screenshot` captures an arbitrary `http(s)` URL to a clean, verified PNG (viewport or full-page), with
cookie-consent dismissal, blank-output detection, and a deterministic capture-then-verify workflow, across Linux,
macOS, and Windows. It is the repo's **first skill that drives a real browser against an attacker-influenceable URL**,
so SSRF/sandbox/timeout safeguards are first-class.

The executable engine is modeled on the `slides-to-video` / `send-email` / `manim-math-animation` exemplars (the only
**5-agent** runtime skills shipping `.sh` + `.bat` + `.ps1` with an `offline-smoke` contract). The skill-file/runtime
**split** is modeled on `autonomous-research-loop` + `autonomous-research-loop-runtime` (the only repo precedent for a
skill-file paired with a runtime engine). Because `url-to-screenshot` ships `references/` files, its skill-file is a
**6-agent** skill (no openclaw), and the runtime engine is likewise a **6-agent** skill.

### Chosen engine: (A) detect-installed-browser + headless Chromium over CDP — RECOMMENDED

| Axis | (A) Installed browser + CDP **[chosen]** | (B) Playwright | (C) Puppeteer |
|---|---|---|---|
| New managed deps | Zero; reuses a host browser (treated like `ffmpeg`/`calibre-cli`: **optional** detected tool) | `playwright` + `playwright install` (~400MB bundle per OS) | `node-runtime` + npm pkg + `node_modules` |
| Offline-smoke compat | Compatible — detect-only, no download | **Violates** smoke contract (`network: forbidden`, `package_install: forbidden`) | Same violation + `denied_patterns` blocks `package.json`/`node_modules` |
| Consent handling | Manual CDP DOM removal (prototype-proven) | Built-in selectors | Built-in selectors |
| Full-page | CDP `Page.captureScreenshot{captureBeyondViewport,clip}` + `getLayoutMetrics` | first-class | first-class |
| Verdict | **Wins**: only option the installer can provision detect-only and offline-smoke-safe | blocked by bundle download | blocked by Node + denied_patterns |

**Two-tier capture (auto mode):**
- **Tier-1 (one-shot):** headless one-shot `--screenshot` (no CDP loop). Fast, robust, no websocket. Its SSRF posture is
  weaker than Tier-2: it enforces ONLY the Python pre-resolve admission gate plus a single `--host-resolver-rules` MAP pin
  of the validated top-level host, so redirect/sub-resource SSRF is **unguarded** in Tier-1 (see §7 S2). Tier-1 is the
  `--consent off` fallback path, not the stock default.
- **Tier-2 (CDP):** used when consent removal or full-page-beyond-viewport is needed. With the default `--consent on`,
  ordinary captures enter Tier-2 because consent dismissal is a CDP DOM op. Launch headless bound to loopback with
  `--remote-debugging-port=0`, `--remote-debugging-address=127.0.0.1`, and **no `--remote-allow-origins` flag**; the
  stdlib client connects sending **no `Origin` header**, so Chromium's default-deny of Origin-bearing CDP applies.
  Discover the page target via `/json` (stdlib `http.client`), capture the per-target `webSocketDebuggerUrl` GUID (a
  loopback handle, not a true secret — `/json` publishes it cleartext to any local process), open a minimal stdlib
  websocket, `Page.navigate` → `Page.loadEventFired` → settle wait → consent removal → `Page.captureScreenshot`. The CDP
  `Network` domain is the PRIMARY SSRF control here (per-request re-validation of redirects/sub-resources).
- `auto` falls back Tier-2 → Tier-1 on CDP failure or on the consent-removal-blanks-page guard.

Dependency surface: **`python-runtime` (hard) + a detected browser (OPTIONAL, surfaced by `doctor`, never an install
gate)**; `websocket-client`/`Pillow`/`imagemagick` are all **optional** with stdlib fallbacks, so the engine and the
offline selftest run with zero third-party packages.

---

## 2. Exact file list (all paths absolute)

### Canonical skill body + references (owned by skill `url-to-screenshot`)
- `/home/ubuntu/ai-agents-skills/canonical/skills/url-to-screenshot/SKILL.md` — front-matter (`name`, `description`,
  `metadata.short-description`) + body: Windows Runtime Commands, When to use, Runtime helper (verbs), Required workflow,
  Strict approval/verification surface, **Security notes** (SSRF scope honesty + CDP origin posture, mirroring
  `send-email`), References, Boundaries. Security notes state explicitly: default Tier-1 enforces only the pre-resolve
  admission gate + single-host resolver pin (redirect/sub-resource SSRF unguarded there); the CDP endpoint launches with
  no `--remote-allow-origins` flag and the client sends no `Origin`; and on a shared host any local process can read the
  CDP endpoint + per-target GUID from loopback `/json` during the capture window. Boundaries state explicitly: (i) the
  executable engine ships as `url-to-screenshot-runtime`; (ii) on openclaw **neither** skill installs natively (the
  reference-shipping skill-file is blocked by `planner.py:394-403`); (iii) `file-exists`/`offline-smoke` verification does
  **not** imply a browser is present — real capture readiness is reported only by `doctor`.
- `/home/ubuntu/ai-agents-skills/canonical/skills/url-to-screenshot/references/engine-and-cdp.md` — CDP websocket (no
  `--remote-allow-origins` flag, no client `Origin` header), per-target GUID loopback handle, consent DOM removal,
  one-shot fallback, CDP `Network`-domain per-request re-validation.
- `/home/ubuntu/ai-agents-skills/canonical/skills/url-to-screenshot/references/browsers-and-platforms.md` — detection order
  + per-OS notes.
- `/home/ubuntu/ai-agents-skills/canonical/skills/url-to-screenshot/references/verification-gates.md` — blank-output
  detection, render-wait/timeout semantics, strict `verify` gate, SSRF admission-vs-navigation scope.

### Runtime engine (owned by skill `url-to-screenshot-runtime`; runtime_dir = `url-to-screenshot-runtime`)
- `/home/ubuntu/ai-agents-skills/canonical/skills/url-to-screenshot-runtime/SKILL.md` — runtime skill body (frontmatter
  `name: url-to-screenshot-runtime`, a description, `metadata.short-description`) + a **"Windows Runtime Commands"** block
  naming `run_skill.bat` and the per-skill `.bat`/`.ps1` targets (`run_url_to_screenshot.bat`, `run_url_to_screenshot.ps1`),
  mirroring `canonical/skills/autonomous-research-loop-runtime/SKILL.md`. Required because
  `test_runtime_integration.py:462-481` iterates `runtime_profiles.full.skills` and reads each
  `canonical/skills/<skill>/SKILL.md`, asserting `run_skill.bat` plus every Windows `.bat`/`.ps1` target string is present.
- `.../canonical/runtime/skills/url-to-screenshot-runtime/run_url_to_screenshot.sh` — POSIX/macOS/wsl runner (**0755, lf**),
  `select_python` + `URL_TO_SCREENSHOT_PYTHON` override, modeled on `run_slides_to_video.sh`.
- `.../canonical/runtime/skills/url-to-screenshot-runtime/run_url_to_screenshot.bat` — Windows CMD runner (**0644, crlf**).
- `.../canonical/runtime/skills/url-to-screenshot-runtime/run_url_to_screenshot.ps1` — Windows PowerShell runner (**0644, crlf**).
- `.../canonical/runtime/skills/url-to-screenshot-runtime/url_to_screenshot_runtime.py` — CLI dispatcher (argparse
  subcommands), verbs `doctor` / `capture` / `verify` / `selftest`; argv builders are pure functions so selftest validates
  them offline. **0644, lf.**
- `.../canonical/runtime/skills/url-to-screenshot-runtime/requirements.txt` — optional deps only (`websocket-client`,
  `Pillow`); stdlib fallback so install never blocks. **0644, lf.**
- `.../u2s/__init__.py` — package marker (mirrors `s2v/`).
- `.../u2s/security.py` — **fail-closed `validate_target_url()`** chokepoint (scheme allow-list,
  `getaddrinfo`+`ipaddress` private/loopback/link-local blocking, **unconditional** metadata-host denylist, per-hop
  re-validation hooks, narrow opt-in private-IP override, **dedicated URL-redaction helper** `redact_url()` that drops
  query/fragment/userinfo — a purpose-built helper, not `send-email`'s password redactor). The `--allow-private-targets`
  override relaxes ONLY the private/loopback/link-local block; the cloud-metadata denylist is **never** disabled by it.
  The relaxation requires the CLI flag (the env var `URL_TO_SCREENSHOT_ALLOW_PRIVATE=1` alone does not enable it, so an
  inherited/poisoned env cannot silently disable SSRF blocking). Pure stdlib, no network at import.
- `.../u2s/pngtools.py` — **shared in-memory PNG synth + decode** (stdlib `zlib`+`struct`): `make_png(width,height,rgb)`,
  `make_uniform_png`, `make_two_color_png`, `make_tiny_png`, and a minimal IHDR/IDAT reader. The single source of all
  test PNG bytes for both selftest and unit tests (no files on disk).
- `.../u2s/detect.py` — cross-OS Chromium/Chrome/Edge detector (`URL_TO_SCREENSHOT_BROWSER` override + PATH + per-OS
  install-location globs); returns `{path, family, version, channel}`; fail-soft to `missing`. Path-resolution logic is
  parameterized by an injectable `os_name`+`candidate_root` so it can be exercised for **all three** OS layouts on a
  Linux host.
- `.../u2s/oneshot.py` — Tier-1 headless one-shot `--screenshot` argv builder + runner (the `--consent off` fallback path;
  not the stock default — see C1 in §1).
- `.../u2s/cdp.py` — stdlib CDP client: launch with `--remote-debugging-port=0`, `--remote-debugging-address=127.0.0.1`,
  **no `--remote-allow-origins` flag** (client sends no `Origin` header; relies on Chromium default-deny of Origin-bearing
  CDP), `--host-resolver-rules` MAP pin for the validated initial host, `/json` discovery, websocket using the per-target
  GUID, `Page.navigate`/`captureScreenshot`, full-page `clip` from `getLayoutMetrics.cssContentSize` (CSS px) with
  `scale=device-scale-factor`, **`Network` domain enabled with per-request URL re-validation** (redirects/sub-resources) —
  the PRIMARY SSRF control.
- `.../u2s/consent.py` — consent selector list + `Runtime.evaluate` removal with `innerText`-collapse guard (consent
  overlays only — never age/paywall affordances). On a consent-removal-blanks-page detection the fallback follows the
  capture-mode matrix in §7 S6: a viewport request reverts to one-shot, but a `--full-page` request re-attempts full-page
  in CDP **without** consent removal rather than dropping to a viewport one-shot (one-shot cannot do full-page); if still
  blank it emits `BLANK_OUTPUT`/`UNVERIFIED` rather than silently returning a viewport capture mislabeled full-page.
- `.../u2s/capture.py` — capture orchestration: viewport vs full-page, width/height, device-scale, render wait, timeout;
  chooses Tier-1/Tier-2; owns the **platform-split process-control** strategy (see `procctl.py`). Full-page builds the CDP
  `clip` from `getLayoutMetrics.cssContentSize` (CSS px) for width/height and passes `scale=device-scale-factor`; the
  requested area (`w*h*scale^2`) is checked against the decompression-bomb pixel cap **before** capture. A `--full-page`
  request never silently degrades to a viewport capture (see §7 S6 fallback matrix and the C2 selftest assertion).
- `.../u2s/procctl.py` — **platform-split process launch + kill** (resolves the Windows-fragility critique): POSIX uses
  `start_new_session=True` + `os.killpg(SIGTERM→SIGKILL)`; Windows uses `CREATE_NEW_PROCESS_GROUP` + a Win32 **job object**
  (fallback `taskkill /T /F` on the PID tree) to reap the whole Chromium process tree; `rmtree` of the temp profile retries
  and ignores locked-file failures with a final best-effort sweep. **Import-time/platform safety (binding):** all
  platform-specific APIs (`os.killpg`/`signal.SIGKILL`/`os.setsid`, Linux-only; `subprocess.CREATE_NEW_PROCESS_GROUP`,
  Windows-only) are referenced ONLY inside `os.name`-guarded branches that do not execute on the other OS, never at module
  top level, so `import u2s.procctl` succeeds on every OS. `select_kill_strategy(os_name)` is a pure function returning an
  inert descriptor (string / lazily-bound callable) without touching the absent API; it is unit-tested per `os.name`.
- `.../u2s/blank.py` — dependency-light `is_blank(png_bytes) -> (bool, reason, metrics)`: file-too-small floor +
  decimated near-uniform-color ratio over raw decompressed IDAT scanlines (stdlib `zlib` via `pngtools`); returns
  `width/height/bytes/dominant_color_fraction`.
- `.../u2s/verify.py` — **artifact-truth VERIFICATION GATE** (analog of `tikz-draw approve`): `final_verdict=VERIFIED`
  only when file/decode/dimensions/not-blank/consent sub-checks all PASS; else nonzero with structured
  `BLOCKED_*`/`UNVERIFIED`.
- `.../u2s/naming.py` — output-path allocation under `AAS_RUNS_ROOT` (`url-to-screenshot/<run_id>/<host>_<ts>.png`) +
  `result.json` sidecar writer.
- `.../u2s/doctor.py` — env probe (browser, ImageMagick, Pillow) emitting JSON like `s2v/doctor.py`; installs nothing;
  fail-soft to `missing`/`BLOCKED_ENVIRONMENT`.
- `.../u2s/selftest.py` — offline smoke entrypoint (stdlib-only, no network/browser/install): detector resolution on
  **synthetic candidate dirs for all three OS layouts**, argv builders (incl. host-resolver-rules and the assertion that
  the CDP launch argv contains NEITHER `--remote-allow-origins=*` NOR any `--remote-allow-origins=...` value and that the
  client sends no `Origin`), consent list, full-page-never-degrades-to-viewport assertion (C2), blank-detector on
  **in-memory** PNGs from `pngtools`, verify-gate exercise on synth golden+blank, security asserts,
  `select_kill_strategy` per `os.name`. **Binding invariant (M3):** `selftest.py` and both unit-test files import only
  `u2s.pngtools` for byte inputs and never read `u2s/htmlfixtures/*` (no `__file__`-relative fixture reads in the blocking
  path). **Binding invariant (M2):** the selftest never imports/launches the browser or opens sockets — `u2s/cdp.py` and
  `u2s/oneshot.py` browser-launch/socket paths are unreachable from the selftest import graph (a static-check/test proves
  this). Prints a JSON body carrying BOTH the slides-to-video keys (`ok`, `passed`, `total`, `failures`) AND the precedent
  offline-safety keys (`status`, `smoke_mode`, `network_required:false`, `live_api_attempted:false`,
  `package_install_attempted:false`, `server_started:false`, `browser_launched:false`); **exits nonzero on any failure**.

> No `u2s/fixtures/` directory and no committed `*.png`. All PNG bytes come from `u2s/pngtools.py` at runtime.

### HTML capture-fixtures (committed; used ONLY by the optional/blocking Chromium capture job, never by blocking selftest/unit logic)
- `.../canonical/runtime/skills/url-to-screenshot-runtime/u2s/htmlfixtures/plain.html` — golden page (fixed-size colored
  box + headline at a known coordinate).
- `.../htmlfixtures/consent.html` — high-z-index `#cookie-banner` overlay covering the headline.
- `.../htmlfixtures/blank.html` — near-uniform white page.
- `.../htmlfixtures/slow.html` — deterministically delayed paint (render-wait/timeout test).
- `.../htmlfixtures/tall.html` — content taller than viewport (full-page test).

  These are `type: text`, `newline: lf`, `mode: 0644`. They are byte-stable LF text (covered by the new
  `*.html text eol=lf` `.gitattributes` rule, §3g), so they pass `runtime_newline_ok` and source-hash (R3) on all OSes.
  Note: `tools/static_check.py` `check_newline_policy` (`:136`) does **not** currently cover `.html` (it inspects
  `{.sh,.py,.md,.yaml,.yml,.json,.toml,.ps1,.bat}` and only errors CRLF for `.sh/.py`), so a CRLF-committed `.html` would
  pass static-check silently; §3g therefore both extends `check_newline_policy` to `.html` and adds a mandatory no-CR
  `make test` assertion as the enforcement.

### Entrypoint (slash command source; owned by skill `url-to-screenshot`)
- `/home/ubuntu/ai-agents-skills/canonical/entrypoints/url-to-screenshot.md` — entrypoint-alias source body routing
  URL→PNG requests; modeled on `slides-to-video.md`. States that verification passing does not imply a browser is present.

### Tests (run by `make test` on all CI OSes)
- `/home/ubuntu/ai-agents-skills/tests/test_url_to_screenshot_runtime.py` — pure-logic unit tests (imports the runtime
  package directly, `test_slides_to_video_runtime.py` precedent): blank-detector math on **in-memory** PNGs from
  `pngtools`, detection ranking for **all three OS layouts** on synthetic candidate dirs, CDP/arg builders that emit
  `--host-resolver-rules` and **no `--remote-allow-origins` flag** (asserts neither `=*` nor any scoped value, and no
  client `Origin`), full-page clip consumes `cssContentSize` with `scale=device-scale-factor` and the area is bomb-cap
  checked (C3), a `--full-page` request never silently degrades to viewport dimensions (C2), `import u2s.procctl` succeeds
  on the running OS (T1), consent set, verify-gate verdict logic, browser-absent → `BLOCKED_ENVIRONMENT` under mock,
  `select_kill_strategy(os_name)` returns the POSIX path for `posix` and the Windows path for `nt`.
  Capture/full-page/landmark assertions are gated to a SKIPPED-when-no-browser tier.
- `/home/ubuntu/ai-agents-skills/tests/test_url_to_screenshot_security.py` — offline security asserts (no Chromium, no
  network): blocked-internal-IP, cloud-metadata block, scheme allow-list, DNS-resolves-to-private (monkeypatched
  `getaddrinfo`) + redirect-to-private re-validation, **a Tier-1 redirect-to-private is NOT silently captured (S2)**,
  opt-in override that still BLOCKS 169.254.169.254 and metadata.google.internal (S3), `redact_url()` query-string
  redaction, blank/uniform detection on synth PNGs, no-DOM/text-persist + temp-dir cleanup, sandbox-disabled reporting,
  **CDP launch argv contains NEITHER `--remote-allow-origins=*` NOR any `--remote-allow-origins=...` value, the client
  sends no `Origin` header, and it binds 127.0.0.1**, and an asserted documented limitation that browser-side DNS-rebind
  is out of scope for the Python gate.
- No `tests/fixtures/url-to-screenshot/` PNG files — both test files import `u2s.pngtools` for byte-identical inputs,
  eliminating the duplication/divergence risk entirely.

### CI workflow edit (required for the capture tier)
- `/home/ubuntu/ai-agents-skills/.github/workflows/tests.yml` — add a new **blocking Linux Chromium capture job** that
  installs real Chromium via `browser-actions/setup-chrome`, runs the capture tier + `verify` against `file://` fixtures
  only (network forbidden), and asserts a VERIFIED golden, consent-not-blank, full-page > viewport, and that `--timeout 1`
  on `slow.html` yields `BLOCKED_TIMEOUT` with the process tree reaped and no leftover `url2png_*` temp dir / child
  Chromium PID. No browser-install precedent exists in current workflows, so this is a genuine `tests.yml` edit (not
  covered by the "zero workflow edits" offline-path statement).

---

## 3. Manifest changes (which YAML, what entries)

All `manifest/*.yaml` are **strict JSON-in-`.yaml`** — edit as JSON (no comments, no trailing commas), validate each with
`python3 -c 'import json; json.load(open(f))'` after editing. Docs are generated, never hand-edited.

### (a) `manifest/skills.yaml` → `skills` map — add **two** keys

**Skill-file skill (6 agents, no openclaw, no runtime, no offline-smoke):**
```json
"url-to-screenshot": {
  "description": "Capture a URL to a clean PNG screenshot with browser detection, cookie-consent dismissal, viewport or full-page modes, timeouts, SSRF-safe URL admission, and blank-output verification across Linux, macOS, and Windows.",
  "profiles": ["media", "full-research"],
  "supported_agents": ["codex", "claude", "deepseek", "copilot", "opencode", "antigravity"],
  "required_dependencies": ["python-runtime"],
  "optional_dependencies": ["chromium-browser-system-tool", "imagemagick-system-tool", "pillow-python-package", "websocket-client-python-package"],
  "optional_capabilities": ["cdp-capture", "consent-dismissal", "full-page-capture", "blank-output-detection", "ssrf-admission-gate"],
  "verification": ["file-exists", "metadata-valid", "agent-visible"]
}
```
- **No openclaw** for the skill-file: `planner.py:394-403` blocks the openclaw skill-file install when the canonical dir
  carries any non-`SKILL.md` file, and this skill ships `references/`. This matches every reference-shipping skill in the
  repo, so `url-to-screenshot` is a **6-agent** skill.

**Runtime engine skill (6 agents, no openclaw, offline-smoke):**
```json
"url-to-screenshot-runtime": {
  "description": "Runtime engine for url-to-screenshot: headless-browser CDP capture, SSRF-safe URL admission, consent dismissal, blank-output detection, and an offline self-test of the deterministic core.",
  "profiles": ["media", "full-research"],
  "supported_agents": ["codex", "claude", "deepseek", "copilot", "opencode", "antigravity"],
  "required_dependencies": ["python-runtime"],
  "optional_dependencies": ["chromium-browser-system-tool", "imagemagick-system-tool", "pillow-python-package", "websocket-client-python-package"],
  "optional_capabilities": ["cdp-capture", "consent-dismissal", "full-page-capture", "blank-output-detection", "ssrf-admission-gate", "offline-smoke"],
  "verification": ["file-exists", "metadata-valid", "agent-visible", "offline-smoke"]
}
```
- The split mirrors `autonomous-research-loop` (7 agents, SKILL.md-only) + `autonomous-research-loop-runtime` (6 agents,
  offline-smoke) — the only repo precedent. **Neither** 7-agent skill carries offline-smoke, and **neither** is in
  `runtime.yaml`; this plan respects that exactly.
- **`chromium-browser-system-tool` is now OPTIONAL** (resolves the "required-but-detect-only contradiction"). Verified:
  `target_skill_block_reason` does not gate on `required_dependencies`, but a `required` system tool is reported MISSING
  by precheck/doctor on every browser-less CI host, producing a degraded required-dependency signal that contradicts the
  offline-smoke posture. Moving it to `optional_dependencies` matches `ffmpeg`/`calibre` detect-only treatment; the hard
  need is surfaced only by the `doctor` verb. `python-runtime` is the only required dependency.

### (b) `manifest/runtime.yaml` → `skills` map — add key `"url-to-screenshot-runtime"`
Shape mirrors the `slides-to-video` entry. Adding it auto-enrolls the skill in `make runtime-smoke` because
`runtime_smoke.py` derives the allowlist from `smoke_coverage.status == "offline-smoke"`.

**Also append `"url-to-screenshot-runtime"` to `runtime_profiles.full.skills` (runtime.yaml:9-39).** This is REQUIRED:
`make runtime-smoke` calls `build_plan(... runtime_profile="full")` (`runtime_smoke.py:48-57`) and
`resolve_runtime_skills` (`runtime.py:121-130`) for the `full` profile **ignores `selected_skills`** and installs only
`runtime_profiles.full.skills`. Enrollment (via `smoke_coverage.status == "offline-smoke"`) already selects the skill for
smoke, but without this list entry its `run_url_to_screenshot.{sh,bat,ps1}` are never installed under the `full` profile,
so the smoke invocation fails to find the runner.

- `runtime_dir`: `"url-to-screenshot-runtime"`.
- `smoke_coverage`: `{ "status": "offline-smoke", "reason": "Selftest validates the deterministic core (browser-detection candidate order for linux/macos/windows synthetic layouts, SSRF URL-admission gate, CDP command JSON with no --remote-allow-origins flag and a --host-resolver-rules MAP pin, consent-selector list, viewport/full-page arg builders, in-memory blank-output detector, the verify gate on synth golden+blank, and per-OS process-kill strategy selection) with no network, browser launch, or package install." }`.
- `files[]`: one entry per shipped file. **Mode/newline matrix (verified against slides-to-video):**
  - `run_url_to_screenshot.sh` → `type: text`, `newline: lf`, `mode: 0755`, platforms `["linux","macos","wsl"]`.
  - `run_url_to_screenshot.bat`, `run_url_to_screenshot.ps1` → `type: text`, `newline: crlf`, `mode: 0644`, platforms `["windows"]`.
  - `url_to_screenshot_runtime.py`, `requirements.txt`, every `u2s/*.py`, every `u2s/htmlfixtures/*.html` → `type: text`,
    `newline: lf`, `mode: 0644`, platforms `["linux","macos","windows","wsl"]`.
  - **No binary entries.** There are no `.png` runtime files. (If a future change ever ships a committed binary runtime
    file, it MUST use `type: binary` and `newline: "none"` so `runtime_newline_ok` short-circuits to `True`, and it MUST
    be preceded by a dedicated installer round-trip test — but v1 ships none.)
  - Targets: `workspace/skills/url-to-screenshot-runtime/...` for every file, including `u2s/*.py` and `u2s/htmlfixtures/...`.
- `smoke`: `{ "schema": "runtime-smoke.v1", "mode": "offline", "command": { full five-key map mirroring runtime.yaml:285-291 — "linux"/"macos"/"wsl" → "workspace/skills/url-to-screenshot-runtime/run_url_to_screenshot.sh", "windows" → "workspace/skills/url-to-screenshot-runtime/run_url_to_screenshot.bat", "windows_ps1" → "workspace/skills/url-to-screenshot-runtime/run_url_to_screenshot.ps1" }, "args": ["selftest", "--work-dir", "{smoke_dir}/u2s"], "timeout_seconds": 60, "writes": { "workspace_scratch": true }, "safety": { "network": "forbidden", "live_api": "forbidden", "package_install": "forbidden", "server_start": "forbidden", "config_write": "forbidden", "provider_cli": "forbidden", "subagent_spawn": "forbidden", "real_secrets": "forbidden" } }`.
  - The `command` map carries all five keys (`linux`/`macos`/`wsl`/`windows`/`windows_ps1`) with full
    `workspace/skills/url-to-screenshot-runtime/...` paths, mirroring `slides-to-video` (runtime.yaml:285-291). The per-skill
    `run_url_to_screenshot.*` scripts are invoked indirectly through the shared `run_skill.*` runner.
  - All six mandatory forbidden keys present (`network/live_api/package_install/server_start/config_write/real_secrets`);
    schema verified (`validate_runtime_smoke_contract`). `timeout_seconds: 60` is a **deliberate** choice (selftest does
    three-OS detector synthesis + verify-gate exercises); it is legal (the guard rejects `timeout<=0` or `>120`, so the
    valid range is `1 <= t <= 120` inclusive) but does **not** "mirror exactly" the `slides-to-video` value of `30` — noted,
    not claimed as a copy.

### (c) `manifest/dependencies.yaml` — add `tools` + `packages` entries
`tools` (browser detector; multi-candidate; the **valid macos-key precedent is `pptx-render-system-tool`** — verified;
`ffmpeg-system-tool` has only linux/windows, so it is NOT cited here):
```json
"chromium-browser-system-tool": {
  "description": "Headless Chromium/Chrome/Edge for CDP-driven page screenshots.",
  "version_constraint": "any",
  "candidates": {
    "linux": ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome", "microsoft-edge", "microsoft-edge-stable"],
    "macos": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "chromium", "google-chrome"],
    "windows": ["%PROGRAMFILES%\\Google\\Chrome\\Application\\chrome.exe", "%PROGRAMFILES(X86)%\\Google\\Chrome\\Application\\chrome.exe", "%PROGRAMFILES%\\Microsoft\\Edge\\Application\\msedge.exe", "%PROGRAMFILES(X86)%\\Microsoft\\Edge\\Application\\msedge.exe", "%LOCALAPPDATA%\\Chromium\\Application\\chrome.exe", "chrome.exe", "msedge.exe", "chromium.exe"]
  },
  "capabilities": ["headless-screenshot", "cdp"]
}
```
Optional crop tool:
```json
"imagemagick-system-tool": {
  "description": "ImageMagick convert/magick for optional post-capture cropping.",
  "version_constraint": "any",
  "candidates": { "linux": ["magick", "convert"], "macos": ["magick", "convert"], "windows": ["magick.exe", "magick"] },
  "capabilities": ["image-crop"]
}
```
`packages` (reuse existing `pillow-python-package = {type: python, module: PIL}`; add):
```json
"websocket-client-python-package": { "type": "python", "module": "websocket" }
```

### (d) `manifest/system-dependencies.yaml` → `software` map — prose inventory
`system-dependencies.yaml` `scope.platforms` is `["linux","windows"]`; no existing entry carries a `macos` key and the
docs generator never reads one, so a `macos` key here would be silently dropped. **Omit the `macos` key** and rely on
`dependencies.yaml` `chromium-browser-system-tool.candidates.macos` (§3c) for macOS detection.
```json
"chromium-browser": {
  "display_name": "Chromium / Chrome / Edge (headless)",
  "requirement": "optional for url-to-screenshot page capture (headless CDP screenshot); reported by the doctor verb, not an install gate",
  "linux": "chromium or google-chrome on PATH, e.g. apt-get install chromium.",
  "windows": "chrome.exe or msedge.exe at the default Program Files path or on PATH.",
  "used_by": ["url-to-screenshot", "url-to-screenshot-runtime"]
}
```
(Optional analogous `imagemagick` entry if the crop path ships.)

### (e) `manifest/profiles.yaml` → append BOTH keys to BOTH profile lists
Both skills declare `profiles: ["media","full-research"]`, and `manifest.py:124-129` requires the skill→profile and
profile→skill links in BOTH directions; `full-research` is an explicit skill list, so a one-sided append raises
`ManifestError` in `load_manifests()` before any `make` target runs. Therefore:
- Append `"url-to-screenshot"` and `"url-to-screenshot-runtime"` to `profiles.media.skills` (current `media` =
  `[slides-to-video, manim-math-animation]`).
- Append `"url-to-screenshot"` and `"url-to-screenshot-runtime"` to `profiles.full-research.skills` as well (mirrors
  `slides-to-video`/`manim-math-animation`, which appear in both lists).

Do **not** invent a new `web` profile (over-engineering).

### (f) `manifest/artifacts.yaml` (slash command) — add under `artifacts["entrypoint-alias"]`
**5-agent set** (copilot + openclaw are `unsupported` for entrypoint-alias per `docs/surfaces.md`):
```json
"url-to-screenshot": {
  "description": "Route URL-to-PNG screenshot requests to the url-to-screenshot skill.",
  "source": "url-to-screenshot.md",
  "depends_on_skills": ["url-to-screenshot"],
  "supported_agents": ["codex", "claude", "deepseek", "opencode", "antigravity"]
}
```
- `depends_on_skills` is a **single** backing skill (`["url-to-screenshot"]`): all 15 existing aliases use exactly ONE
  backing skill, and two deps would render the alias only when BOTH install and would change owner-skill attribution.
- `antigravity` stays in `supported_agents` for selection eligibility (mirrors slides-to-video/manim), but on antigravity
  the like-named alias is **intentionally blocked** — `planner.py:514-524` blocks an antigravity `entrypoint-alias` whose
  `name` equals a managed skill name, and the alias shares the flat skills dir with the `url-to-screenshot` skill `.md`.
  Only the skill-file installs there; do not claim a rendered native alias on antigravity.

Append `"entrypoint-alias:url-to-screenshot"` to `artifact_profiles["research-entrypoints"].artifacts`.

### (g) `.gitattributes` + static-check — **REQUIRED edits in the same change** (defense-in-depth, not a hash prerequisite)
Add an explicit LF rule so committed HTML capture-fixtures stay LF on every checkout regardless of a contributor's
`core.autocrlf`:
```
*.html text eol=lf
```
This rule is **defense-in-depth** (git byte stability + the no-CR assertion), **not** a hard prerequisite for hash/verify:
`runtime_expected_sha256` normalizes newlines for `type: text` before hashing, so the R3 source-hash is already
CRLF-insensitive. Two enforcement edits are still REQUIRED because static-check does not currently cover `.html`
(`tools/static_check.py` `check_newline_policy` at `:136` inspects only `{.sh,.py,.md,.yaml,.yml,.json,.toml,.ps1,.bat}`
and only errors CRLF for `.sh/.py`):
1. Extend `tools/static_check.py` `check_newline_policy` to include `.html` (CRLF errors for `.html`).
2. Add a MANDATORY `make test` assertion that re-reads each committed `u2s/htmlfixtures/*.html` and asserts it contains no
   `\r` byte, so CRLF mangling fails CI early.

(No `*.png binary` rule is needed because v1 ships **no** committed binary fixture. If any binary fixture is ever added,
add `*.png binary` in the same commit and a source-hash drift test.)

### Generated docs (NOT hand-edited)
After manifest edits, run `make docs` to regenerate `docs/skills.md`, `docs/surfaces.md`, `docs/verification.md`
(runtime-smoke coverage table), dependency/profile pages, and `README.md`. `make docs-check` (CI) fails on drift.

---

## 4. Implementation phases (Spec → Design → Implement → Test → Verify → Register/Install → Cross-OS CI)

**Phase 1 — Spec.** Lock the verb contract (`doctor`/`capture`/`verify`/`selftest`), the option set (§ in 4.Implement),
the blocked-state vocabulary (`BLOCKED_SCHEME`, `BLOCKED_PRIVATE_ADDRESS`, `BLOCKED_METADATA_ENDPOINT`, `BLOCKED_INPUT`,
`BLOCKED_ENVIRONMENT`, `BLOCKED_TIMEOUT`, `BLANK_OUTPUT`, `UNVERIFIED`), and the single `final_verdict=VERIFIED` success
state. Decide the two-skill split (skill-file `url-to-screenshot` + engine `url-to-screenshot-runtime`). Write both
`SKILL.md` skeletons.

**Phase 2 — Design.** Two-tier engine (Tier-2 CDP is the stock default because consent dismissal is on by default; Tier-1
one-shot is the `--consent off` fallback), detect-only **optional** browser, fail-closed `validate_target_url()` admission
chokepoint before any launch, **CDP Network per-request re-validation as the PRIMARY browser-side SSRF control + a
host-resolver-rules MAP pin of the validated initial host** for defense-in-depth, CDP endpoint launched with **no
`--remote-allow-origins` flag** (client sends no `Origin`; relies on Chromium default-deny of Origin-bearing CDP),
**platform-split process control** (`procctl.py`), per-run `tempfile.mkdtemp` profile dir wiped in `finally` with
locked-file tolerance, structured `result.json` sidecar, **in-memory PNG synthesis** (`pngtools.py`). Finalize the `u2s/`
module split (security/pngtools/detect/oneshot/cdp/consent/capture/procctl/blank/verify/naming/doctor/selftest).

**Phase 3 — Implement.** Build the runtime. Verb surface and key options:

| Flag | Meaning | Default |
|---|---|---|
| `--url` | target, **scheme-validated http/https only**, SSRF-gated | required |
| `--out` | output PNG | auto-named under `AAS_RUNS_ROOT` |
| `--viewport WxH` / `--width/--height` | viewport size | `1280x800` |
| `--full-page` / `--viewport-only` | capture mode | viewport-only |
| `--device-scale` | DPR | `1` |
| `--wait` / `--timeout` | settle wait / hard nav cap (kill at expiry) | `800ms` / `30000ms` (max ~120000) |
| `--consent on\|off` | CDP consent removal (a Tier-2 DOM op); with the default `on`, `auto` enters Tier-2/CDP for ordinary captures, so Tier-1 one-shot is the `--consent off` fallback, not the stock default | `on` |
| `--engine auto\|oneshot\|cdp` | force a tier | `auto` |
| `--crop x,y,w,h` | ImageMagick crop if present, else CDP `clip` | none |
| `--browser` (`== URL_TO_SCREENSHOT_BROWSER`) | override | autodetect |
| `--allow-private-targets` (CLI flag required; the env `URL_TO_SCREENSHOT_ALLOW_PRIVATE=1` alone does NOT enable it) | relax the private/loopback/link-local IP block **only** — never scheme, and **never** the cloud-metadata denylist | off |

Runner scripts mirror `run_slides_to_video.{sh,bat,ps1}` (select Python, `URL_TO_SCREENSHOT_PYTHON` override, exec dispatcher).

**Phase 4 — Test.** Implement `tests/test_url_to_screenshot_runtime.py`, `tests/test_url_to_screenshot_security.py`, the
`selftest` body, the shared `u2s/pngtools.py` synth, and the committed HTML capture-fixtures. Run `make test`. No committed
PNGs; all PNG bytes are synthesized in-memory.

**Phase 5 — Verify.** `make static-check sanitize-check`, then `make verify` (installer file-integrity R1–R6: file-exists,
signature, source-hash after newline normalization, mode, newline policy, no-secret-leak — this is why mode/newline in
`runtime.yaml files[]` must be exact and why **no file is declared binary**), then
`make runtime-smoke ARGS="--skills url-to-screenshot-runtime"`.

**Phase 6 — Register / Install + the validator code change.**
- **No installer changes for install layout** — `planner/render/apply.py` are manifest-driven and iterate manifest skills
  against detected agents. The skill-file installs to its 6 agents; the runtime engine installs to its 6 agents.
  - **skill-file (`url-to-screenshot`):** claude=symlink, codex/deepseek/copilot=reference-adapter, opencode/antigravity=copy.
    All 6 receive `SKILL.md` + `references/` (antigravity flat `url-to-screenshot.md`). **No openclaw:** because the canonical
    dir ships `references/` (non-`SKILL.md` files), `planner.py:394-403` blocks the openclaw skill-file install entirely.
  - **runtime-file (`url-to-screenshot-runtime`):** codex/claude/deepseek/copilot/opencode/antigravity = native runtime-copy
    under the shared runtime root; **no openclaw** runtime entry (matching `autonomous-research-loop-runtime`).
  - **managed instruction block:** at most one managed block per selected agent — **but Copilot and OpenClaw emit no managed
    instruction block** (`agents.py:106` and `agents.py:153` set `instruction_blocks_enabled=False`); for those agents the
    skill body reaches them via the skill-file only. (OpenClaw is not a target here anyway, per the install-block above.)
    Uninstall removes only managed files/blocks.
  - **entrypoint-alias:** rendered only for its 5 supported agents (claude/opencode native command, codex/deepseek reference
    doc). On **antigravity** the like-named alias is **intentionally blocked** (`planner.py:514-524`: the alias `name` equals
    the managed skill name and they share the flat skills dir), so antigravity gets only the skill-file `url-to-screenshot.md`,
    not a rendered native alias.
- **REQUIRED installer code change (`installer/ai_agents_skills/runtime_smoke.py::validate_smoke_output`):** add
  `elif skill == "url-to-screenshot-runtime":` that parses selftest stdout JSON and asserts, using
  `payload.get(...) is True/False` (so a **missing** key fails rather than silently passing): `ok is True`,
  `failures == []`, `passed == total`, and every offline-safety field the selftest emits — `network_required is False`,
  `live_api_attempted is False`, `package_install_attempted is False`, `server_started is False`, and
  `browser_launched is False`. The selftest is co-designed to emit BOTH the s2v keys (`ok`, `passed`, `total`, `failures`)
  AND these precedent offline-safety keys (`status`, `smoke_mode`, plus the `*_required`/`*_attempted` flags), so the two
  contracts agree. This mirrors the `axiom-axle-mcp`/`self-improving-agent`/`autonomous-research-loop-runtime` branches.
  Without this branch the harness only checks the exit code; with it, the JSON contract the plan leans on for CI is
  machine-enforced. Add a unit test for the new branch (a synthetic `ok:false`/exit-0 stdout must FAIL the validator).
- **Do NOT add `url-to-screenshot-runtime` to the `RUNTIME_SMOKE_SKILLS` tuple (`runtime_smoke.py:21`).** That tuple is a
  fallback consulted only when the manifest-derived offline-smoke list is empty — which never happens once the skill is
  enrolled via `smoke_coverage.status == "offline-smoke"`. The existing offline-smoke skills (slides-to-video, send-email,
  manim) are NOT in the tuple either; the manifest enrollment fully covers selection, so the tuple edit would be a no-op.
- **Add a coverage test (M5):** assert that every `smoke_coverage.status == "offline-smoke"` skill whose selftest emits a
  JSON safety body has a corresponding `validate_smoke_output` branch (or that exit-code-only is explicitly accepted for
  it), so a future refactor cannot silently degrade the JSON contract back to exit-code-only.
- Run `make docs`, then `make docs-check`.

**Phase 7 — Cross-OS CI.** `runtime-smoke` auto-selects every `offline-smoke` skill, so the engine's offline selftest runs
on the existing matrix with **zero `.github/workflows` edits for the offline path**. Because `make runtime-smoke` →
`run_runtime_smoke` uses `host_platform = current_platform(None)` with **no host/target guard**, and CI runs it on
macos-latest (tests.yml:132) and windows-latest (tests.yml:188), the offline selftest (argv builders, three-OS detector
synthesis, blank detector, verify gate, security gate, `select_kill_strategy` per `os.name`) executes **NATIVELY on all
three OSes**, not only on Linux. `make.bat` already mirrors `make` on Windows.

**New deliverable — a `.github/workflows/tests.yml` edit (REQUIRED for the capture tier).** The blocking Linux Chromium
capture job (T3, the T4 capture half, the T5 reap-assert) needs a new job that installs real Chromium via
`browser-actions/setup-chrome`, runs the capture tier + `verify` against `file://` fixtures only, with network forbidden.
This is a `.github/workflows/tests.yml` edit (no browser-install precedent exists in current workflows), so it is listed
both in the §2 file list / Phase 7 deliverables and scoped here. The "zero workflow edits" claim applies **only** to the
offline path; the capture job is an explicit workflow edit. (If the capture job is instead demoted to non-blocking, state
plainly that T3 / T4-capture / T5-reap are NOT enforced on every PR.)

---

## 5. Testing matrix + the verification gate

Three independent gating layers (the repo's existing separation; no new ones invented):
- **`doctor`/precheck** = capability ("is a browser available?").
- **offline `selftest`** = engine-logic correctness (deterministic, network-free, browser-free; the always-on CI layer).
- **runtime `verify` verb** = artifact truth (the only thing allowed to declare a real screenshot done) — direct analog
  of `tikz-draw approve`.

| Phase | What it asserts | Browser? | Network? | Runs in CI? |
|---|---|---|---|---|
| T1 deterministic core | scheme normalizer, **three-OS** detection ranking, CDP/arg builders that emit **no `--remote-allow-origins` flag** (assert neither `=*` nor any scoped value; client sends no `Origin`) + `--host-resolver-rules`, `import u2s.procctl` succeeds, `select_kill_strategy` per `os.name` | no | no | **yes** (selftest + `make test`, run NATIVELY on Linux/macOS/Windows) |
| T2 blank detection | `is_blank` on **in-memory** synth PNGs (uniform→True, tiny→True, golden→False) + structured metrics | no | no | **yes** |
| T3 dimension/pixel | requested-size honored, full-page height > viewport, flat-fill landmark pixel within tolerance | **yes** | no (file://) | **yes (blocking Linux Chromium job)** |
| T4 consent | paired capture of `consent.html`: occluded vs dismissed; **not-blank after removal**; pure-tier asserts selector set + fallback rule, and that a `--full-page` request never silently degrades to viewport (C2) | yes (paired w/ pure counterpart) | no | pure part **yes (always)**; capture part **yes (blocking Linux job)** |
| T5 timeout/failure | `slow.html` + `--timeout 1` → `BLOCKED_TIMEOUT` **with the process tree actually reaped**; missing file → `BLOCKED_INPUT`; mocked no-browser → `BLOCKED_ENVIRONMENT` | mocked (pure) / yes (capture) | no | pure **yes (always)**; reap-assert **yes (blocking Linux job)** |
| T6 **verify gate** | `final_verdict=VERIFIED` only when file/decode/dimensions/not-blank/consent all PASS; blank→`UNVERIFIED` | no (runs on a PNG) | no | **yes** |
| T7 selftest contract | runs T1/T2/T4-pure/T5-pure + verify-gate; prints `{ok,passed,total,failures,...}`; **exits nonzero on any failure** | no | no | **yes** (and machine-checked by the new validate_smoke_output branch) |
| Security (S-tests) | SSRF/IP-block/scheme/redaction/no-persist/sandbox-report + Tier-1 redirect-to-private not silently captured (S2) + `--allow-private-targets` still blocks metadata (S3) + CDP argv has no `--remote-allow-origins` and client sends no `Origin` + per-OS kill (see §7) | no | no | **yes** |

**The verification gate (T6, `verify` verb).** `capture` produces a PNG but **never** declares success. `verify` (given
PNG path + expected size) exits `0` **only** when ALL hold: `final_verdict=VERIFIED`; `file_exists=PASS` and `bytes >= floor`;
`decode=PASS`; `dimensions=PASS`; `not_blank=PASS`; `consent=PASS|SKIPPED`. Any other state exits nonzero with a structured
`BLOCKED_*`/`UNVERIFIED` verdict and the failing sub-check. `capture`, source inspection, "the file exists", or "Chromium
exited 0" NEVER constitute final success — SKILL.md states this and forbids approval-style wording for any non-VERIFIED state
(mirrors `tikz-draw` SKILL.md doctrine).

**What blocking CI actually exercises (honest scope).** The always-on offline selftest + `make test` cover the deterministic
core, three-OS detector globs, blank detection, the verify gate, the security gate, and per-OS kill-strategy selection — and
because `make[.bat] runtime-smoke` runs with **no host/target guard**, the offline selftest executes **NATIVELY on
macos-latest and windows-latest**, not only on Linux (the `status=skipped` logic in `runtime_smoke.py:97-106` belongs to
`run_installed_runtime_smoke` / lifecycle `--platform-shape`, NOT to `make runtime-smoke`). The
**new blocking Linux Chromium job** (real `browser-actions/setup-chrome`, `file://` fixtures only, no network) additionally
covers T3, the T4 capture half, and the T5 reap-assert — so "requested size honored", "consent removal does not blank",
"full-page height > viewport", and "`--timeout 1` reaps the process tree and yields `BLOCKED_TIMEOUT`" are run on every PR,
not merely asserted against a committed golden. The **only** true residual CI gap is the real **browser capture / CDP /
timeout-reap tier on Windows and macOS** — that path remains unverified by automated CI (see §6) and requires the
documented manual `doctor`+capture run; this is stated in SKILL.md Boundaries.

**Wiring into `make verify`/`smoke`/`lifecycle-test`** (installer-level):
- `make verify` — picks up runtime files automatically; verifies ownership/hash/mode/newline/no-secret-leak. (Installer
  `verify` ≠ the skill's `verify` gate — SKILL.md keeps the two meanings distinct.)
- `make smoke` — picks up both `SKILL.md` files; agent-discovery only.
- `make runtime-smoke` — runs the engine's `selftest` across `run_url_to_screenshot.{sh,bat,ps1}` (auto-enrolled via
  `offline-smoke`); the new `validate_smoke_output` branch enforces the JSON body.
- `make lifecycle-test ARGS="--matrix stress --platform-shape all"` — install→dry-run-vs-applied→verify+smoke→uninstall→
  baseline-restore on fake roots across both skills; the acceptance gate (matrix enumerates installed runtime-backed skills
  via the manifests).
- Pre-commit command: `make static-check sanitize-check test docs-check runtime-smoke`.

---

## 6. Cross-OS notes (Linux / macOS / Windows)

- **Browser detection (detect-only, never installs; OPTIONAL dependency):** `URL_TO_SCREENSHOT_BROWSER` first, then per-OS:
  - Linux: PATH `chromium`/`chromium-browser`/`google-chrome`/`google-chrome-stable`/`chrome`/`microsoft-edge`; then
    `/usr/bin/`, `/snap/bin/chromium`, `/opt/google/chrome/chrome`.
  - macOS: PATH names, then `/Applications/Google Chrome.app/.../Google Chrome`, `/Applications/Chromium.app/.../Chromium`,
    `/Applications/Microsoft Edge.app/.../Microsoft Edge`.
  - Windows: `%PROGRAMFILES%`/`%PROGRAMFILES(X86)%`/`%LOCALAPPDATA%` globs for `Google\Chrome\Application\chrome.exe`,
    `Chromium\Application\chrome.exe`, `Microsoft\Edge\Application\msedge.exe`, plus PATH `chrome.exe`/`msedge.exe`/`chromium.exe`.
    Use `os.path.expandvars` + glob. **`detect.py` is parameterized by `os_name`+`candidate_root`, so the selftest/unit
    tests deterministically exercise the Windows `%PROGRAMFILES(X86)%`/expandvars globs and macOS app-bundle paths on a
    Linux host** (resolving "Windows/macOS detector globs are untested").
- **Process control is platform-split (`procctl.py`):** POSIX = `start_new_session=True` + `os.killpg(SIGTERM→SIGKILL)`;
  Windows = `CREATE_NEW_PROCESS_GROUP` + Win32 **job object** (fallback `taskkill /T /F` on the PID tree) to reap the full
  Chromium tree (renderer/gpu/zygote), then `rmtree` with retry/ignore-locked + a final best-effort sweep. `os.killpg`/
  `start_new_session` are POSIX-only and are **never** referenced on Windows. `select_kill_strategy(os_name)` is unit-tested
  per `os.name`.
- **Runtime files:** `.sh` (0755/lf, linux/macos/wsl), `.bat`+`.ps1` (0644/crlf, windows), `.py`/`.txt`/`.html` (0644/lf).
  **No binary runtime files.** `run_url_to_screenshot.sh` resolves the runtime root; `make.bat` mirrors `make` on Windows.
- **Native CI scope (offline vs real-capture).** The offline `selftest` runs **natively on Linux, macOS, and Windows** via
  `make[.bat] runtime-smoke` (argv builders, three-OS detector synthesis, blank detector, verify gate, security gate,
  `select_kill_strategy` per `os.name`) — there is no host/target guard on that path. What **cannot** be exercised by CI is
  the **real native browser capture / CDP / timeout-reap tier on Windows and macOS**: the lifecycle
  `run_installed_runtime_smoke` returns `status=skipped` when `target_platform != host_platform` (so the Windows/macOS
  "shape" jobs verify install **layout** only), and no Win/mac browser-capture job is run. SKILL.md documents a manual
  Windows `doctor` + real capture via `make.bat` and `run_url_to_screenshot.bat/.ps1`, the same posture `slides-to-video`
  uses.
- **openclaw:** runtime-file support is `manual`/fake-root per `docs/surfaces.md`; therefore the **runtime engine skill is
  6 agents (no openclaw)**, mirroring `autonomous-research-loop-runtime`. The skill-file `url-to-screenshot` **also does not
  install on openclaw**, because it ships `references/` and `planner.py:394-403` blocks the openclaw skill-file install
  whenever the canonical dir carries a non-`SKILL.md` file. SKILL.md Boundaries documents that neither skill runs on
  openclaw real-system until an approved runtime manifest + broker exists.
- **Blocking Linux Chromium capture job** (new `.github/workflows/tests.yml` edit — see §2 file list / Phase 7): install
  real Chromium (`browser-actions/setup-chrome`), run the capture tier + `verify` against `file://` fixtures, assert a
  VERIFIED golden, consent-not-blank, full-page>viewport, and that `--timeout 1` on `slow.html` yields `BLOCKED_TIMEOUT`
  with the process tree reaped — **including a Linux-job assertion that after `--timeout 1` no `url2png_*` temp dir and no
  child Chromium PID remain**. **Never** add a network capture to CI (forbidden by the smoke safety contract and flaky). A
  separate **non-blocking** macOS capture job may be added (browser availability is environmental there), but Linux capture
  is blocking. **Windows job-object/`taskkill` reaping and locked-file `rmtree` are verified ONLY by manual Windows runs**
  (documented in SKILL.md Boundaries, the `slides-to-video` posture) — the offline selftest only unit-tests
  `select_kill_strategy`.

---

## 7. Security safeguards (with tests)

This is the highest-blast-radius skill in the repo (outbound fetch to an attacker-influenceable host + sandbox-sensitive
Chromium). Safeguards are baked into `u2s/security.py`, `u2s/cdp.py`, `u2s/procctl.py`, the engine, `SKILL.md` (Security
notes), the manifest, and dedicated offline tests.

- **S1 SSRF — admission gate + browser-side defense-in-depth (honest scope).** `validate_target_url()` is the
  **pre-navigation admission decision only**; it cannot bind Chromium's own resolver, redirects, sub-resource fetches, or
  JS-initiated requests. SKILL.md states this plainly and never implies full SSRF protection. **Per-tier scope:** in default
  **Tier-1 one-shot**, the ONLY protections are this Python pre-resolve admission gate plus a single `--host-resolver-rules`
  MAP pin of the validated top-level host — a 3xx redirect or sub-resource to a private host (e.g. `169.254.169.254`,
  `10.0.0.5`) is **followed with no abort**, so redirect/sub-resource SSRF is **unguarded in Tier-1** (documented as
  in-scope-and-unmitigated in SKILL.md; the alternative is to reject 3xx / disable redirects in Tier-1). The full
  per-request re-validation runs only in **Tier-2 (CDP `Network`)**.
  1. **Scheme allow-list:** only `http`/`https`. Reject `file:`/`ftp:`/`gopher:`/`data:`/`chrome:`/`view-source:`/`about:`/
     `blob:`/`javascript:`/unknown → `BLOCKED_SCHEME`. **Not overridable.**
  2. **Resolve-then-check every resolved A/AAAA** via `socket.getaddrinfo` + stdlib `ipaddress`: reject loopback (`127/8`,
     `::1`), private (`10/8`, `172.16/12`, `192.168/16`, `fc00::/7`), link-local (`169.254/16`, `fe80::/10`), `0.0.0.0/8`,
     multicast, reserved, IPv4-mapped IPv6 → `BLOCKED_PRIVATE_ADDRESS`.
  3. **Metadata-host denylist (UNCONDITIONAL):** `169.254.169.254`, `metadata.google.internal`, `100.100.100.200`,
     `fd00:ec2::254` → `BLOCKED_METADATA_ENDPOINT`. **Never disabled** by `--allow-private-targets`; if metadata access
     were ever truly needed it would require a separate, narrower flag (not provided in v1).
  4. **CDP `Network` per-request re-validation is the PRIMARY browser-side control (Tier-2):** re-run the full S1 IP check
     on **every** requested URL's freshly-resolved address (redirects + sub-resources), aborting on violation; cap redirects
     (~5). Backed by `--host-resolver-rules="MAP <validated-host> <validated-ip>"`, which pins **only the named initial
     host** (it defeats same-host rebind but does NOT cover a different redirect/sub-resource host, which gets fresh DNS).
     Document that without the Network re-validation the Python gate is advisory only, and that **browser-side rebind is
     out of scope of the Python pre-resolve** (mitigated by per-request Network re-validation, with host-resolver-rules as
     a same-host-only backstop).
  5. **Narrow opt-in override:** `--allow-private-targets` (the **CLI flag is required**; the env var
     `URL_TO_SCREENSHOT_ALLOW_PRIVATE=1` alone does NOT enable it, so an inherited/poisoned `smoke_env`/`os.environ` cannot
     silently disable SSRF blocking) relaxes layer 2 (private/loopback/link-local) **only**, off by default, logged as
     `"private_targets_allowed": true`; **never** re-enables scheme blocking (layer 1) and **never** the metadata denylist
     (layer 3).
- **S2 Resource limits / process & temp hygiene (platform-split).** Hard wall-clock `--timeout` (default ~30s, max ~120s)
  reaped via `u2s/procctl.py` (POSIX `os.killpg`; Windows job-object/`taskkill /T /F`); fresh `tempfile.mkdtemp(prefix=
  "url2png_")` `--user-data-dir` per run, removed in `finally` even on crash/timeout, with locked-file-tolerant `rmtree`
  retry + final sweep so a slow Windows reap does not leak the profile; output pixel/byte caps (decompression-bomb guard);
  single-flight, no background server.
- **S3 `--no-sandbox` + CDP origin posture.** Sandbox ON by default; auto-add `--no-sandbox` only when root/container is
  detected, recorded as `"sandbox": "disabled"` + reason (never silent). CDP debugging endpoint bound to **`127.0.0.1`**
  on an ephemeral port. The engine launches with **no `--remote-allow-origins` flag at all**, and the stdlib client sends
  **no `Origin` header** — so Chromium's default-deny of Origin-bearing CDP applies and a forged-Origin client is rejected;
  a scoped `--remote-allow-origins` at a guessed port would only OPEN a hole for that forged Origin, so it is dropped (kept
  only as optional hygiene, never load-bearing). The real protections are: (a) Chromium default-deny of Origin-bearing CDP,
  (b) the per-target `webSocketDebuggerUrl` GUID from `/json` + loopback bind on an ephemeral port, (c) `finally` teardown.
  The GUID is **not a true secret** — loopback `/json` publishes it cleartext to any local process, so on a shared host any
  local process can read the CDP endpoint + GUID during the capture window; SKILL.md states this. A security test asserts
  the launch argv contains **NEITHER** `--remote-allow-origins=*` **NOR** any `--remote-allow-origins=...` value, that the
  client sends no `Origin`, and that the port binds loopback.
- **S4 Content-safety / appropriate-use.** SKILL.md Appropriate-use note — captures public, unauthenticated pages for
  legitimate documentation/research/QA; not a policy-bypass surface; the agent refuses per its own policy before invoking;
  no credential/cookie injection by default; consent removal scoped to consent overlays only, never age/paywall affordances.
- **S5 Privacy.** PNG-only output + minimal `result.json` (final URL host, dimensions, blank verdict, flags) by default;
  no `page.html`/text/console/cookie dumps; per-run temp profile wiped; any DOM/text debug dump behind an explicit flag to
  a temp path; **redact URL query strings/fragments/userinfo via the dedicated `u2s.security.redact_url()` helper** (a
  purpose-built URL redactor — `send-email`'s `_redact` only strips the SMTP password and is cited as analogous intent only,
  not reused). Covered by the no-secret-leak verifier checks and the sanitizer test.
- **S6 Blank/paywall/timeout as first-class failures (fallback matrix).** Mandatory post-capture `is_blank` (tiny file +
  near-uniform color). On a CDP+consent removal that blanks the page, the fallback depends on capture mode: for a
  **viewport** request, fall back to one-shot `--screenshot`; for a **`--full-page`** request, re-attempt full-page in CDP
  **without** consent removal (one-shot cannot do full-page, so dropping to viewport one-shot would silently downgrade a
  full-page request) — and if still blank, emit `BLANK_OUTPUT`/`UNVERIFIED` rather than returning a viewport capture
  mislabeled full-page. A selftest asserts a full-page request never silently degrades to viewport dimensions (C2). Blocked
  states use `BLOCKED_*`, never approval-style wording.

**Security tests (offline, no Chromium, no network)** in `tests/test_url_to_screenshot_security.py` + the in-module
`selftest`: blocked-internal-IP; cloud-metadata block; scheme allow-list; DNS-resolves-to-private (monkeypatched
`getaddrinfo`) + redirect-to-private re-validation; **a Tier-1 redirect-to-private is NOT silently captured (S2)**;
override flips a private host AND records `private_targets_allowed: true` but does NOT re-enable `file:`/`javascript:` **and
still BLOCKS `169.254.169.254` + `metadata.google.internal` (S3)**; the env var
`URL_TO_SCREENSHOT_ALLOW_PRIVATE=1` alone (without the CLI flag) does NOT relax the private-IP block; `redact_url()`
query-string redaction (no token substring); blank/uniform PNG detection on synth fixtures; no-persist (no `*.html`/text/
cookie artifacts; no leftover `url2png_*` dir); sandbox-disabled reporting carries reason; **CDP launch argv asserts
loopback bind, contains NEITHER `--remote-allow-origins=*` NOR any `--remote-allow-origins=...` value, the client sends no
`Origin` header, and `--host-resolver-rules` is present**; `select_kill_strategy` returns the correct per-`os.name` path;
`import u2s.procctl` succeeds (T1); and an explicit asserted-limitation test that browser-side DNS-rebind is out of scope of
the Python pre-resolve (mitigated by per-request Network re-validation, with host-resolver-rules as a same-host backstop).

---

## 8. Resolved questions (formerly open)

1. **Lifecycle/runtime-smoke enumeration** — confirmed `runtime-smoke` derives its allowlist from
   `smoke_coverage.status == "offline-smoke"`; the engine skill auto-enrolls. The `RUNTIME_SMOKE_SKILLS` tuple is **not**
   edited — it is a fallback consulted only when the manifest-derived offline-smoke list is empty (never, once enrolled),
   and the existing offline-smoke skills (slides-to-video, send-email, manim) are not in it either, so adding the skill
   would be a no-op. Validate with `make lifecycle-test ARGS="--matrix stress --platform-shape linux"` and confirm
   `url-to-screenshot-runtime` appears.
2. **`validate_smoke_output` contract — RESOLVED: a custom validator branch IS required.** Verified: the function falls
   through to an **exit-zero-only** check for any unlisted skill, so the JSON body is otherwise unenforced. Phase 6 adds a
   dedicated `elif skill == "url-to-screenshot-runtime"` branch (asserting `ok is True`, `failures == []`,
   `passed == total`, and the offline-safety fields `network_required`/`live_api_attempted`/`package_install_attempted`/
   `server_started`/`browser_launched` all `is False`, via `payload.get(...)` so a missing key fails) **and** the selftest
   exits nonzero on any failure. The selftest is co-designed to emit BOTH the s2v `{ok,passed,total,failures}` keys AND the
   precedent offline-safety keys, so the two contracts agree (M1). A coverage test (M5) asserts every offline-smoke skill
   with a JSON safety body has such a branch. This is an acknowledged installer code change.
3. **Pillow vs pure-stdlib PNG read — RESOLVED stdlib-only.** The blocking blank-detector and selftest pixel checks use the
   stdlib `zlib`+`struct` path in `u2s/pngtools.py` (synth + decode) and never require Pillow; `Pillow` stays optional for
   richer capture-tier assertions only.
4. **`websocket-client` vs stdlib CDP websocket — RESOLVED stdlib-only.** A stdlib websocket (socket + SHA-1 handshake)
   keeps the core engine and selftest third-party-free; `websocket-client` stays optional. The blocking Linux Chromium job
   exercises the stdlib handshake against a real DevTools endpoint, confirming it is reliable enough to keep the package
   optional.
5. **ImageMagick crop path — RESOLVED keep optional** with a CDP `clip` fallback (no hard dependency added).
6. **Fixture duplication — RESOLVED eliminated.** All PNG bytes are synthesized in-memory by `u2s/pngtools.py` and imported
   by both the selftest and the unit tests; there are no committed PNGs to drift. Committed HTML capture-fixtures are
   single-copy under `u2s/htmlfixtures/` and protected by the new `*.html text eol=lf` `.gitattributes` rule, a `.html`
   extension added to `tools/static_check.py check_newline_policy`, and a mandatory no-CR assertion in `make test`. The
   `.gitattributes` rule is defense-in-depth (git byte stability), **not** a hash prerequisite — `runtime_expected_sha256`
   already normalizes newlines for `type: text` before hashing, so source-hash R3 is CRLF-insensitive.