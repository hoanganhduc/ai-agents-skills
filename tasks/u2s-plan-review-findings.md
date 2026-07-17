# url-to-screenshot plan — consolidated verified review findings + fix spec

Three independent reviews, reconciled. All findings below are **verified against the actual repo code** (file:line cited).
Sources: Codex (8 issues), Claude 17-agent panel (34 issues, self-verified), fresh-context Codex verifier (confirmed all 8 + 8 refuted-claims).

Apply each fix as a **surgical markdown edit** to `tasks/u2s-plan-under-review.md`. Preserve the doc's structure and style. Do not renumber whole sections.

---

## BLOCKING — manifest / install (the plan fails CI as written)

**B1. `full-research` profile bidirectional membership** *(high; Codex#1, Claude#1, verifier-confirmed)*
Both new skills declare `profiles: ["media","full-research"]` (§3a, ~lines 217/230) but §3e (~310-313) appends only to `profiles.media.skills`. `manifest.py:124-129` requires the skill→profile and profile→skill links in BOTH directions; `full-research` is an explicit list, so `load_manifests()` raises `ManifestError` before any `make` target runs.
FIX: In §3e, also append `url-to-screenshot` and `url-to-screenshot-runtime` to `profiles.full-research.skills` (mirrors `slides-to-video`/`manim-math-animation`, which appear in both lists).

**B2. Runtime skill missing from `runtime_profiles.full.skills`** *(high; Codex#2, verifier-confirmed)*
`make runtime-smoke` calls `build_plan(... runtime_profile="full")` (`runtime_smoke.py:48-57`); `resolve_runtime_skills` (`runtime.py:121-130`) for the `full` profile **ignores `selected_skills`** and installs only `runtime_profiles.full.skills`. The skill IS smoke-selected (via `smoke_coverage.status=="offline-smoke"`, `runtime_smoke.py:207-216`) but its `run_url_to_screenshot.{sh,bat,ps1}` are never installed → smoke invocation fails.
FIX: In the §3b runtime.yaml edit, add `url-to-screenshot-runtime` to `runtime_profiles.full.skills` (runtime.yaml:9-39). (Mechanism note: enrollment already works; the gap is the file install under the `full` profile.)

**B3. Missing `canonical/skills/url-to-screenshot-runtime/SKILL.md`** *(high once B2 applied; Codex#7, Claude#8, verifier-confirmed)*
`test_runtime_integration.py:462-481` iterates `runtime_profiles.full.skills` and reads `canonical/skills/<skill>/SKILL.md`, requiring `run_skill.bat` plus each Windows `.bat`/`.ps1` target string. §2 file list ships only `canonical/skills/url-to-screenshot/SKILL.md`.
FIX: Add `canonical/skills/url-to-screenshot-runtime/SKILL.md` to the §2 file list with `name: url-to-screenshot-runtime` frontmatter, a description, and a "Windows Runtime Commands" block naming `run_skill.bat` and the `.bat`/`.ps1` targets (mirror `canonical/skills/autonomous-research-loop-runtime/SKILL.md`).

**B4. OpenClaw skill-file + `references/` is a blocked combo** *(high; Codex#3, Claude#4, verifier-confirmed)*
`planner.py:394-403` blocks OpenClaw skill-file install when the canonical dir has any non-`SKILL.md` file; `test_installer.py:1273-1298` asserts the block (no SKILL.md installed). §2 (~112-117) adds three `references/` files, so the §6 "all 7 receive SKILL.md" claim (~389) is false.
FIX: Drop `openclaw` from `url-to-screenshot` `supported_agents` → **6-agent skill** (matches every reference-shipping skill in the repo; the runtime split the plan already adopts). Correct the "all 7 / installs to openclaw" claims to 6.

**B5. Antigravity entrypoint-alias name collides with skill name** *(med; Codex#4, Claude#15, verifier-confirmed)*
`planner.py:514-524` blocks an antigravity `entrypoint-alias` whose `name` equals a managed skill name; alias and skill share the flat skills dir (`agents.py:140/145`). The alias is named `url-to-screenshot` = the skill, so on antigravity it is blocked, not rendered. Phase 6 (~394) overstates antigravity coverage.
FIX: Keep `antigravity` in the alias `supported_agents` (selection eligibility, mirrors slides-to-video/manim) but correct the Phase 6 wording: on antigravity the like-named alias is **intentionally blocked** (name collides with the flat skill `.md`); only the skill-file installs there. Do not claim a rendered native alias on antigravity.

**B6. Entrypoint-alias `depends_on_skills` cardinality** *(low; Claude)*
§3f (~321) gives the alias `depends_on_skills: ['url-to-screenshot','url-to-screenshot-runtime']`. All 15 existing aliases use exactly ONE backing skill; two deps means the alias only renders if BOTH install and changes owner-skill attribution.
FIX: Use single backing skill `['url-to-screenshot']`.

**B7. "Exactly one managed instruction block per selected agent" is false** *(Codex refuted#8, verifier-confirmed)*
`agents.py:106` (copilot) and `agents.py:153` (openclaw) set `instruction_blocks_enabled=False`. Plan asserts one block per agent (~392).
FIX: Correct the claim — Copilot and OpenClaw emit no managed instruction block; the skill body reaches them via the skill-file only.

---

## SECURITY — design correctness (must fix before shipping an attacker-facing capture path)

**S1. Drop the scoped `--remote-allow-origins=<discovered-port>` requirement** *(high; Codex#6, Claude#2, verifier-confirmed)*
Mandated across Decisions#4, §1, §2 cdp.py, §3a, §4 Phase2, §5 T1, §7 S3. Two defects: (1) chicken-and-egg — `--remote-debugging-port=0` emits the port only AFTER launch, so it cannot be a launch-flag value; (2) wrong-purpose — `--remote-allow-origins` gates the websocket client's `Origin` header (client-controlled), so a no-Origin stdlib client already gets full CDP access, while a scoped flag at a guessed port only OPENS a hole for that forged Origin. The per-target GUID is not a real "secret" (loopback `/json` publishes it cleartext to any local process).
FIX: Drop scoped `--remote-allow-origins` entirely; launch with NO such flag and have the stdlib client send no `Origin` header. Document the real protections: (a) Chromium default-deny of Origin-bearing CDP, (b) per-target `webSocketDebuggerUrl` GUID + loopback bind on an ephemeral port, (c) `finally` teardown. Keep scoped origins only as optional hygiene, not load-bearing. State in SKILL.md that on a shared host any local process can read the CDP endpoint+GUID from loopback `/json` during the capture window. Rewrite the S3 test to assert the launch argv contains NEITHER `--remote-allow-origins=*` NOR any `--remote-allow-origins=...` value, and that the client sends no `Origin`.

**S2. Default Tier-1 one-shot path has no redirect/sub-resource SSRF re-validation** *(high; Codex#5, Claude#3, verifier-confirmed)*
§1 makes Tier-1 one-shot `--screenshot` the default (no CDP loop); but per-request CDP `Network` re-validation + redirect capping require the CDP connection, so they do NOT run in the default capture. In Tier-1 the only protections are the Python pre-resolve admission gate + a single `--host-resolver-rules` MAP pin of the top-level host; a 3xx redirect or sub-resource to a private host (169.254.169.254, 10.0.0.5) is followed with no abort. The "DNS cannot rebind mid-navigation / load-bearing mitigation" claim is overstated — a MAP rule pins only the named host; a different redirect/sub-resource host gets fresh DNS.
FIX: State plainly that default Tier-1 enforces ONLY the pre-resolve admission gate + single-host resolver pin, and that redirect/sub-resource SSRF is unguarded there. Soften the host-resolver-rules claim (pins ONLY the validated initial host → defeats same-host rebind). Make CDP `Network`-domain per-request re-validation (re-run the full S1 IP check on every requested URL's freshly-resolved address, abort on violation) the PRIMARY documented browser-side control. Then pick one: (a) make Tier-2 CDP+Network interception the default when SSRF posture matters, (b) reject 3xx in Tier-1 (disable redirects), or (c) document Tier-1 redirect/sub-resource SSRF as explicitly in-scope-and-unmitigated in SKILL.md. Add a security test asserting a redirect-to-private in Tier-1 is NOT silently captured. (This subsumes the standalone host-resolver-rules issue.)

**S3. `--allow-private-targets` also re-opens the cloud-metadata denylist** *(low; Claude)*
The opt-in override disables private/loopback/link-local block AND the metadata denylist (169.254.169.254, metadata.google.internal) together; and it is env-var driven (`URL_TO_SCREENSHOT_ALLOW_PRIVATE=1`), which `smoke_env`/inherited env can set silently (not a secret-named var).
FIX: Keep the cloud-metadata denylist UNCONDITIONAL (never disabled by `--allow-private-targets`); require a separate narrower flag if metadata access is ever truly needed. Prefer the CLI flag over the env var for the private-IP relaxation (or require both) so an inherited/poisoned env cannot silently disable SSRF blocking. Add a security test that `--allow-private-targets` still BLOCKS 169.254.169.254 and metadata.google.internal.

---

## CROSS-OS / TESTING HONESTY

**T1. `procctl.py` import-time/reference platform safety** *(high; Codex#8, Claude#6, verifier-confirmed)*
The offline selftest imports the whole `u2s` package and runs on real windows-latest/macos-latest CI. `os.killpg`/`signal.SIGKILL`/`os.setsid` (Linux-only) and `subprocess.CREATE_NEW_PROCESS_GROUP` (Windows-only) would `AttributeError` if referenced at import time or in `select_kill_strategy` on the wrong OS.
FIX: Add an explicit Spec/Design requirement that `procctl.py` references all platform-specific APIs ONLY inside `os.name`-guarded branches not executed on the other OS; that `select_kill_strategy(os_name)` returns an inert descriptor (string / lazily-bound callable) without touching the absent API; and add a unit assert that `import u2s.procctl` succeeds on every OS.

**T2. CI scope honesty — offline selftest runs NATIVELY on Windows/macOS** *(high; Claude)*
§5/§6 (~478-481) claim native Win/mac headless/CDP "cannot be exercised by Linux-hosted CI" — conflating two paths. `make runtime-smoke` → `run_runtime_smoke` uses `host_platform=current_platform(None)` with NO host/target guard, and CI runs it on macos-latest (tests.yml:132) and windows-latest (tests.yml:188). The skill auto-enrolls (offline-smoke), so its selftest (argv builders, three-OS detector synthesis, blank detector, verify gate, security gate, `select_kill_strategy` per `os.name`) executes NATIVELY on all three OSes. The `status=skipped` logic (`runtime_smoke.py:97-106`) belongs only to `run_installed_runtime_smoke` (lifecycle/post_install_smoke), not `make runtime-smoke`.
FIX: Correct §5/§6 — the offline selftest runs NATIVELY on macos-latest and windows-latest via `make[.bat] runtime-smoke`. Reserve "skipped / install-layout-only / cannot be exercised" wording for the real BROWSER capture path (Tier-2 CDP) and the lifecycle `--platform-shape` shapes. True residual gap: only the real browser capture/CDP/timeout-reap tier is unverified on Win/mac CI.

**T3. Blocking Linux-Chromium capture job needs a `tests.yml` edit not in the file list** *(high; Claude)*
The testing model leans on a new blocking Linux Chromium capture job (§7 ~408, §6 ~486-490, §5 ~424-426) for T3/T4-capture/T5-reap, but that requires editing `.github/workflows/tests.yml`, which is NOT in the §2 file list nor a phase deliverable; §7 (~407) says "zero `.github/workflows` edits" then describes the job — a contradiction. No browser-install precedent exists in current workflows.
FIX: Add the `.github/workflows/tests.yml` edit (new linux-capture job using `browser-actions/setup-chrome`, `file://` fixtures only, network forbidden) to the §2 file list AND Phase 7 as an explicit deliverable; scope the "zero workflow edits" wording to the offline path only. (Alternatively demote the capture job to non-blocking and state plainly that T3/T4-capture/T5-reap are NOT enforced on every PR.)

**T4. Reap reliability is asserted, not offline-CI-provable** *(low; Claude)*
`--timeout 1` reaping the whole Chromium tree / locked-file rmtree can only be exercised in the blocking Linux Chromium job; the offline selftest only unit-tests `select_kill_strategy`.
FIX: Mark Windows job-object/taskkill reaping + locked-file rmtree as verified ONLY by manual Windows runs (SKILL.md Boundaries, slides-to-video posture). Add a Linux-job assertion that after `--timeout 1` no `url2png_*` temp dir and no child Chromium PID remain.

---

## SMOKE / SELFTEST CONTRACT

**M1. `validate_smoke_output` schema vs selftest JSON (internal contradiction)** *(med; Claude)*
§0.3/Phase6 assert the new branch checks `ok`, `failures==[]`, `passed==total`, AND offline-safety fields (`network_required is False`...), but the slides-to-video model emits only `{ok,passed,total,failures}` while existing validator branches key on a different schema (`status=='ok'`, `smoke_mode=='offline'`, `*_attempted/*_required`...). Two incompatible contracts.
FIX: Co-design them. Have the selftest emit BOTH the s2v `{ok,passed,total,failures}` keys AND the precedent offline-safety keys (`status`, `smoke_mode`, `network_required`, `*_attempted/*_required`, `browser_launched`), and write the new `elif skill=='url-to-screenshot-runtime'` branch to assert all of them using `payload.get(...) is False/True` (so a missing key fails rather than silently passing). State exactly which keys the validator checks. Keep the negative unit test (synthetic `ok:false`/exit-0 must FAIL the validator).

**M2. Smoke `safety: forbidden` is declarative, not an enforced sandbox** *(med; Claude)*
`run_smoke_process` is plain `subprocess.run(..., capture_output=True, timeout=...)` with `smoke_env` inheriting `os.environ` (minus secret-NAMED vars); `validate_runtime_smoke_contract` only checks `safety.*` keys for PRESENCE. For a browser-launching skill, "forbidden" and self-reported `network_required:false` are honor-system.
FIX: State explicitly that smoke `safety: forbidden` is a contract assertion, not an enforced sandbox, and that offline correctness depends on the selftest never importing/launching the browser or opening sockets. Make the new branch assert the strongest self-reported invariants (`network_required`/`live_api_attempted`/`server_started`/`package_install_attempted`/`browser_launched` all False), AND add a static-check/test that greps the selftest import graph to prove `u2s/cdp.py` and `u2s/oneshot.py` browser-launch/socket paths are unreachable from the selftest.

**M3. Selftest must not depend on committed HTML fixtures** *(low; Claude)*
The runtime-smoke harness runs the selftest from a scratch copy of the runtime tree; a `__file__`-relative read of `u2s/htmlfixtures/*.html` would couple the always-on gate to committed fixtures + copy fidelity.
FIX: State as a binding invariant that `u2s/selftest.py` and both unit-test files import only `u2s.pngtools` for byte inputs and never read `u2s/htmlfixtures/*` (no `__file__`-relative fixture reads in the blocking path); add a guard/test that fails if the selftest references the htmlfixtures dir.

**M4. `RUNTIME_SMOKE_SKILLS` tuple edit is a no-op** *(low; Claude)*
Phase 6 (~401-402, §8 ~557) adds the skill to the `RUNTIME_SMOKE_SKILLS` tuple, consulted only as a fallback when the manifest-derived offline-smoke list is empty (never, once enrolled). Existing offline-smoke skills (slides-to-video, send-email, manim) are NOT in the tuple.
FIX: Drop the `RUNTIME_SMOKE_SKILLS` edit from Phase 6 (manifest `smoke_coverage.status:offline-smoke` enrollment already covers it). If kept, remove the "redundant safeguard" framing and note it fires only in the impossible empty-enrollment case.

**M5. No parity test forces a `validate_smoke_output` branch** *(low; Claude)*
The default validator falls through to exit-code-only for unlisted skills; nothing asserts that offline-smoke skills have a custom branch, so a future refactor could silently degrade the JSON contract.
FIX: Add a coverage test asserting every `smoke_coverage.status=="offline-smoke"` skill whose selftest emits a JSON safety body has a corresponding `validate_smoke_output` branch (or document exit-code-only as accepted for some skills).

---

## FIXTURES / NEWLINE

**F1. `.html` LF is not enforced by static-check** *(med; Claude)*
`tools/static_check.py` `check_newline_policy` inspects only `{.sh,.py,.md,.yaml,.yml,.json,.toml,.ps1,.bat}` (`:136`) and only errors CRLF for `.sh/.py`; `runtime_newline_ok` checks the installed (already LF-normalized) copy. A CRLF-committed `.html` passes static-check silently.
FIX: Keep the `*.html text eol=lf` `.gitattributes` rule AND make the no-CR make-test assertion MANDATORY; ADDITIONALLY add `.html` to `tools/static_check.py` `check_newline_policy` (or extend its CRLF check to `.html`). Alternatively eliminate committed HTML and synthesize fixtures in a temp file at test time (mirroring the plan's PNG "synthesize, don't ship" stance). State explicitly that static-check does not currently cover `.html`.

**F2. `.gitattributes` R3 rationale overstated** *(low; Claude)*
`runtime_expected_sha256` normalizes newlines for `type:text` before hashing, so source-hash R3 is already CRLF-insensitive; the rule is not a hard prerequisite for hash/verify.
FIX: Keep the rule + no-CR test as defense-in-depth (git byte stability, the no-CR assertion) but correct the rationale: R3 source-hash already normalizes newlines for `type:text`.

---

## CONSENT / FULL-PAGE LOGIC

**C1. "Default safe one-shot" contradicts default `--consent on`** *(med; Claude)*
Defaults are `--consent=on` (~368) and `--engine=auto` (~369), but consent removal is a CDP DOM op (Tier-2); so with stock defaults every ordinary capture must enter Tier-2/CDP, and the advertised Tier-1 "fast, no websocket" default is not actually the default.
FIX: Either change the default to `--consent off` (so Tier-1 is genuinely default, CDP opt-in) OR restate honestly that the default path is Tier-2/CDP because consent dismissal is on by default and one-shot Tier-1 is the `--consent off` fallback. Align §1, §5, SKILL.md.

**C2. Consent-blank fallback vs full-page (logic gap)** *(med; Claude)*
If Tier-2 consent removal blanks the page, the documented fallback is one-shot `--screenshot` — but one-shot cannot do full-page, so a `--full-page` request silently downgrades to viewport.
FIX: Define the fallback matrix: for `--full-page` + consent-blank, re-attempt full-page in CDP WITHOUT consent removal rather than dropping to viewport one-shot, OR emit `BLANK_OUTPUT`/`UNVERIFIED` rather than silently returning a viewport capture mislabeled full-page. Add a selftest asserting a full-page request never silently degrades to viewport dimensions.

**C3. Full-page clip size field / device-scale** *(low; Claude)*
Full-page uses `getLayoutMetrics` + `Page.captureScreenshot{captureBeyondViewport,clip}` but never says which size field feeds the clip; `getLayoutMetrics` returns both `contentSize` (device px) and `cssContentSize` (CSS px); clip dims are CSS px. With `--device-scale>1` these diverge.
FIX: Pin: use `cssContentSize` (CSS px) for clip width/height and pass `scale=device-scale-factor`; assert the full-page clip builder consumes `cssContentSize` and that requested area (`w*h*scale^2`) is checked against the decompression-bomb cap BEFORE capture.

---

## MANIFEST PRECISION

**P1. `system-dependencies.yaml` chromium-browser macos key out of scope** *(low; Claude)*
`system-dependencies.yaml` `scope.platforms: ["linux","windows"]`; no existing entry carries a `macos` key and the docs generator never reads one, so the §3d macos prose is silently dropped.
FIX: Drop the `macos` key from the chromium-browser entry (keep linux/windows prose); rely on `dependencies.yaml` `chromium-browser-system-tool.candidates.macos` for macOS detection.

**P2. `runtime.yaml` smoke.command shape + timeout legality** *(low; Claude)*
§3b describes the command loosely and says `timeout_seconds:60` is "legal (1 < t <= 120)". The precedent (slides-to-video, runtime.yaml:285-291) is an explicit 5-key map; the guard rejects `timeout<=0` or `>120`, so valid range is `1 <= t <= 120` inclusive.
FIX: Write the command map with all five keys (`linux`/`macos`/`wsl` → `run_url_to_screenshot.sh`, `windows` → `.bat`, `windows_ps1` → `.ps1`) using full `workspace/skills/url-to-screenshot-runtime/...` paths (mirror runtime.yaml:285-291). Note the per-skill scripts are invoked indirectly through the shared `run_skill.*` runner. Correct the legality note to "1 <= timeout <= 120 inclusive".
