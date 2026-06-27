---
name: url-to-screenshot-runtime
description: Runtime engine for url-to-screenshot. Use to detect a browser, capture a URL to a PNG over CDP, verify a captured PNG, or run the offline self-test of the deterministic core, without network, package installation, or live browser launch in the smoke path.
metadata:
  short-description: Headless-browser URL-to-PNG capture engine with an SSRF-safe admission gate and offline self-test
---

# URL to Screenshot Runtime

This companion skill provides the executable engine for the `url-to-screenshot`
skill: browser detection, the fail-closed SSRF URL-admission gate, headless CDP
capture, cookie-consent dismissal, blank-output detection, the artifact-truth
`verify` gate, and an offline `selftest`.

It is intentionally runtime-backed and is installed only for targets that support
runtime skill helpers. It is not an OpenClaw skill-file target.

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

## Commands

From a configured ai-agents-skills runtime, prefer:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/url-to-screenshot-runtime/run_url_to_screenshot.sh selftest
```

Common commands:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/url-to-screenshot-runtime/run_url_to_screenshot.sh doctor
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/url-to-screenshot-runtime/run_url_to_screenshot.sh capture --url https://example.com/ --out shot.png
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/url-to-screenshot-runtime/run_url_to_screenshot.sh verify --png shot.png --expected-width 1280 --expected-height 800
```

## Verbs

- `doctor` — report capture readiness (browser, ImageMagick, Pillow); installs
  nothing; fail-soft to `missing` / `BLOCKED_ENVIRONMENT`. This is the only
  surface that reports whether a real capture is possible.
- `capture` — capture a URL to a PNG. The target URL is admitted through the
  fail-closed SSRF gate before any browser launch.
- `verify` — the artifact-truth gate: `final_verdict=VERIFIED` only when
  file/decode/dimensions/not-blank/consent all PASS; otherwise a structured
  `BLOCKED_*` / `UNVERIFIED`. Nothing else declares success.
- `selftest` — offline smoke (no network, no browser launch, no socket, no
  package install). Prints a JSON contract and exits nonzero on any failure.

## Guarantees

The `selftest` path:

- uses only the Python standard library
- does not require network access
- does not install packages
- does not start servers
- does not launch a browser or open a socket
- synthesizes all PNG test bytes in memory (no committed binary fixtures, and it
  never reads the committed HTML capture fixtures)

`websocket-client`, `Pillow`, and ImageMagick are optional; the engine and the
offline self-test run with no third-party packages. A host browser
(Chromium/Chrome/Edge) is an optional system tool surfaced by `doctor`, never an
install gate.

Use the canonical `url-to-screenshot` skill for the user-facing workflow and
this helper only for the executable engine.
