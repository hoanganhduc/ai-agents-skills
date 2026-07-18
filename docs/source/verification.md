# Verification

Verification is selective. Only installed and enabled managed artifacts from the
installer state are checked.
If no managed artifacts match the requested scope, `verify` returns
`no-managed-artifacts` instead of `ok`.

`verify` checks installer ownership and file integrity. It does not prove that
an agent runtime has loaded a skill. Use `smoke` for the separate
agent-discovery compatibility check; smoke results can be `ok`, `degraded`,
`unsupported`, or `skipped` with reasons.

Use verification after any applied install, uninstall, migration, adoption, or
rollback. It is intentionally narrower than `precheck`: `precheck` checks
software availability, while `verify` checks whether this installer still owns
the files and managed instruction blocks it recorded. For adopted user-owned
files, verification checks that the file still matches the hash recorded at
adoption time.

Use `lifecycle-test` as the default installer acceptance gate. It creates fake
roots, runs dry-run install, confirms the dry-run did not write files, applies
the install, compares normalized dry-run and applied actions, runs `verify` and
`smoke`, dry-runs uninstall, applies uninstall, and confirms the fake root
returns to its baseline outside installer state, including directories. Fake
roots are deleted after successful cases unless `--keep-fake-roots` is passed.
`fake-root-lifecycle` runs the same checks for a caller-selected install scope.
The matrix treats forced symlink mode as an expected-degraded smoke scenario
when Codex or DeepSeek is included, because those adapters may not load
file-symlinked `SKILL.md` files without native evidence.
Use `--matrix stress` for broader local coverage: all skills, all portable
workflow artifacts with backing skills, individual-agent installs, paths with
spaces, changed managed files, missing managed files, outside-root state
tampering, and corrupt state reporting.

Common commands:

```bash
make lifecycle-test ARGS="--matrix default --platform-shape all"
make lifecycle-test ARGS="--matrix full --platform-shape linux"
make lifecycle-test ARGS="--matrix stress --platform-shape linux"
make fake-root-lifecycle ARGS="--skill zotero --platform-shape linux"
make fake-root-lifecycle ARGS="--skill self-improving-agent --platform-shape all"
make runtime-smoke
make runtime-smoke ARGS="--skills self-improving-agent"
make install ARGS="--profile research-core --apply --root <fake-root> --post-install-smoke strict"
make verify ARGS="--root <fake-or-real-root>"
make verify ARGS="--skill zotero --root <fake-or-real-root>"
make verify ARGS="--skills zotero,docling --root <fake-or-real-root>"
make smoke ARGS="--skill zotero --root <fake-or-real-root>"
python3 -m installer.ai_agents_skills --json runtime-inventory --source-root <runtime-root>
```

Fast local maintainer checks:

```bash
make static-check
make sanitize-check
make test
make docs-check
make runtime-smoke
make lifecycle-test ARGS="--matrix default --platform-shape all"
```

Closest single-host CI parity pass:

```bash
make static-check
make sanitize-check
make test
make docs-check
python3 -m pip install networkx psutil
make runtime-smoke
python3 -m pip install -r docs/requirements.txt
make docs-site
make lifecycle-test ARGS="--matrix stress --platform-shape all"
```

Linux CI runs the stress lifecycle matrix across all platform shapes. Python
3.10 compatibility, macOS, and Windows jobs run narrower subsets that still
include static checks, sanitizer checks, tests, and generated-doc checks.

CI checks generated docs with `make docs-check`, which renders expected docs
without mutating `README.md` or `docs/`. Run `make docs` only when you intend
to refresh generated files. Run `make docs-site` after installing
`docs/requirements.txt` when Sphinx rendering matters.

Post-install smoke:

- `install --apply` runs post-install smoke by default in `auto` mode.
- `auto` runs installer verification, agent-visible skill smoke, and offline
  runtime smoke for installed runtime-backed skills with safe manifest
  contracts. Smoke failures are reported, but a successful apply still exits
  `0`.
- `--post-install-smoke verify` runs only installer integrity verification.
- `--post-install-smoke strict` returns nonzero if any post-install check
  fails, degrades, or is unsupported; the install is still recorded as applied.
- `--post-install-smoke off` skips these checks.

The post-install runtime layer is offline-only. It uses the installed runtime
runner, copies managed runtime files into a temporary scratch workspace, strips
secret-like environment variables, and forbids live APIs, package installation,
MCP/client config writes, and background server starts.

Result meanings:

- `ok`: all selected managed artifacts passed their checks.
- `no-managed-artifacts`: the selected scope has no installer-managed files to check.
- `missing` or failed checks: a managed file, marker, block, or format-specific condition no longer matches recorded state.

CLI exit codes: `verify`, `smoke`, `runtime-smoke`, `lifecycle-test`, and
`docs-check` exit `0` only for `ok`. Status values such as
`no-managed-artifacts`, `degraded`, `stale`, or `failed` are nonzero unless a
higher-level lifecycle scenario intentionally records them as expected.

Current skill checks:

- `L1 file-exists`
- `L2 installed-signature-match`
- `L3 metadata-valid`
- `L4 managed-marker` for copy and reference installs
- `L5 symlink`, `source-exists`, and `source-match` for symlink installs
- `L6 no-secret-leak`
- `L7 agent-visible`
- `L8 adopted-hash-match` for adopted user-owned files

Current instruction-block checks:

- `S1 file-exists`
- `S2 managed-block-present`
- `S3 no-secret-leak` for the managed block text only; surrounding user
  instructions are outside installer ownership

Current support-file checks:

- `A1 file-exists`
- `A2 installed-signature-match`
- `A3 managed-marker` for copied support files
- `A4 symlink`, `source-exists`, and `source-match` for symlinked support files
- `A5 no-secret-leak`

Current runtime-file checks:

- `R1 file-exists`
- `R2 installed-signature-match`
- `R3 source-hash-match` after declared newline normalization
- `R4 runtime-mode`
- `R5 runtime-newline-policy`
- `R6 no-secret-leak`

Current optional artifact checks:

- `O1 file-exists`
- `O2 installed-signature-match`
- `O3 managed-marker`
- `O4 no-secret-leak`
- `O5 format-specific checks for Codex TOML personas and Claude frontmatter`

The verifier intentionally skips skills and artifacts that were not installed.
Lifecycle tests include smoke checks for installed runtime-backed skills when a
smoke command is declared. Runner-specific `doctor` commands and direct
`agent-loads-config` checks are not automatic yet; use `precheck`, skill
doctors, and the agent's own diagnostics for those layers.

Use `runtime-smoke` to install the portable runtime files into a temporary
Codex root and execute the installed native runtime runner for the current host.
On Windows it exercises both `run_skill.ps1` and `run_skill.bat`; on Linux and
macOS it exercises `run_skill.sh`. The default runtime smoke currently covers
`autonomous-research-loop-runtime`, `axiom-axle-mcp`, `deep-research-workflow`, `formal-skeleton-helper`, `get-available-resources`, `graph-verifier`, `lean-explore-mcp`, `lean-formalization-intake`, `lean-strict-verification-gate`, `manim-math-animation`, `self-improving-agent`, `send-email`, `slides-to-video`, `submission-venue-selector`, `url-to-screenshot-runtime`, forcing copy-mode runtime installation in a temporary
root. It requires Python plus any dependencies needed by the selected smoke
contracts, including `psutil` and `networkx` for the default CI path. Passing
`--skills` may only select skills that are supported by this runtime-smoke
harness.

Runtime smoke coverage classes are explicit for every runtime-backed skill:

| Skill | Coverage | Smoke Contract | Reason |
|---|---|---|---|
| `annotated-review` | `manual-native` | no | Annotation workflows require user-provided documents and optional local tooling; no safe generic offline smoke is declared. |
| `autonomous-research-loop-runtime` | `offline-smoke` | yes | Smoke validates local loop ledger initialization, append, validation, and status without network, package installs, provider CLIs, config writes, or subagent spawning. |
| `axiom-axle-mcp` | `offline-smoke` | yes | Smoke validates inert AXLE setup guidance without installing packages or starting services. |
| `calibre` | `manual-native` | no | Calibre workflows depend on the user's local ebook library and profile selection. |
| `deep-research-workflow` | `offline-smoke` | yes | Selftest smoke is offline and validates the workflow guard contracts. |
| `digest-bridge` | `static-only` | no | Digest bridge helpers are covered by static/runtime inventory checks; no generic input digest is shipped for smoke. |
| `docling` | `doctor-only` | no | Docling conversion and OCR need local parser dependencies and documents; use the doctor path for environment checks. |
| `formal-skeleton-helper` | `offline-smoke` | yes | Smoke writes a minimal local skeleton and validates JSON output without network or secrets. |
| `get-available-resources` | `offline-smoke` | yes | Smoke records local resource metadata to a temporary file without network or secrets. |
| `getscipapers-requester` | `manual-native` | no | External paper retrieval is intentionally manual/network-gated and has no generic offline smoke. |
| `graph-verifier` | `offline-smoke` | yes | Smoke validates a small local graph fixture and JSON result without network. |
| `hetzner-research-compute` | `manual-native` | no | Hetzner lifecycle verbs require an HCLOUD_TOKEN and provision paid servers; they are not safe for generic offline smoke. The offline dry-run and guard paths are covered by tests/test_hetzner_research_compute.py. |
| `kaggle-research-compute` | `manual-native` | no | Kaggle lifecycle verbs require the new Kaggle API token (KAGGLE_API_TOKEN or ~/.kaggle/access_token) and push real kernels; per Kaggle ToS no live call is made in the build. The offline dry-run, resume-loop, fan-out, and guard paths are covered by tests/test_kaggle_research_compute.py (all kaggle CLI calls and the kagglehub-validate hook mocked). |
| `lean-explore-mcp` | `offline-smoke` | yes | Smoke validates inert LeanExplore MCP setup guidance without installing packages, starting services, or calling live APIs. |
| `lean-formalization-intake` | `offline-smoke` | yes | Doctor smoke records local Lean availability without installing dependencies. |
| `lean-strict-verification-gate` | `offline-smoke` | yes | Doctor smoke records local Lean availability and scanner status without installing dependencies. |
| `manim-math-animation` | `offline-smoke` | yes | Selftest validates scene-spec round-trips, the generated Manim source (Write/MathTex/TransformMatchingTex/emphasis), and the manim/ffmpeg argv builders with no network, package install, Manim, LaTeX, or ffmpeg. |
| `modal-research-compute` | `manual-native` | no | Modal workflows require explicit external compute credentials and are not safe for generic offline smoke. |
| `research-digest-wrapper` | `manual-native` | no | Digest runs depend on configured topics and external feeds; no generic offline smoke is declared. |
| `rss-news-digest` | `manual-native` | no | RSS digesting depends on configured feeds and network access. |
| `sagemath` | `manual-native` | no | SageMath availability is host-dependent and too heavy for default offline CI smoke. |
| `self-improving-agent` | `offline-smoke` | yes | Smoke validates local learning-plan generation without network, package installs, or config writes. |
| `send-email` | `offline-smoke` | yes | Selftest builds, serializes, and re-parses plain-text, HTML, and attachment messages in memory and checks cc/bcc envelope expansion, port/security inference, header-injection rejection, and password redaction with no network, SMTP connection, package install, or real secrets. |
| `slides-to-video` | `offline-smoke` | yes | Selftest validates the deterministic core (1:1 pairing, duration re-basing, language-aware engine ladder, math verbalization, effect filtergraph building, caption formatting, clip args, and the SHA-pinned approval gate) with no network, package install, ffmpeg, or TTS. |
| `submission-venue-selector` | `offline-smoke` | yes | Smoke validates schemas, privacy gates, and offline not-ready behavior without retrieval or secrets. |
| `tikz-draw` | `manual-native` | no | TikZ workflows depend on TeX toolchains and user-provided figure specs. |
| `url-to-screenshot-runtime` | `offline-smoke` | yes | Selftest validates the deterministic core (browser-detection candidate order for linux/macos/windows synthetic layouts, SSRF URL-admission gate, CDP command JSON with no --remote-allow-origins flag and a --host-resolver-rules MAP pin, consent-selector list, viewport/full-page arg builders, in-memory blank-output detector, the verify gate on synth golden+blank, and per-OS process-kill strategy selection) with no network, browser launch, or package install. |
| `vnthuquan` | `manual-native` | no | Vietnam Thu Quan discovery/download flows are network and library-profile gated. |
| `zotero` | `manual-native` | no | Zotero workflows depend on the user's local library, profile, and optional cloud credentials. |

```bash
make runtime-smoke
make runtime-smoke ARGS="--skills graph-verifier,formal-skeleton-helper"
make runtime-smoke ARGS="--skills self-improving-agent"
```

`self-improving-agent` has a portable offline smoke contract for its
cross-target learning review, command-safety, error-detection, and canonical
integration-plan helper surface. Native Windows PowerShell/CMD behavior still
requires running the Windows `make.bat` and runtime runner checks on Windows;
Linux-hosted Windows platform-shape tests verify install layout, not native
Windows execution.

Docling has a skill-specific runtime doctor because it may rely on a dedicated
Docling environment and heavier OCR/model packages that are not part of the
default runtime-smoke harness:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/docling/run_docling.sh doctor
```

`smoke` can also return `no-managed-artifacts` when no managed skill-file
artifacts match the selected scope.

Related pages: [Installation](installation.md), [Audit And Migration](audit-and-migration.md),
[OpenClaw Integration Plan](openclaw-integration-plan.md),
[OpenClaw Install Target Plan](openclaw-install-target-plan.md),
[Uninstall And Rollback](uninstall-rollback.md), [Troubleshooting](troubleshooting.md).
