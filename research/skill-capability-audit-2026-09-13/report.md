# Phase 0 Skill Capability Audit

## Status

incomplete analysis

This report preserves the non-remediating baseline at
8bcb4c7ead6f539fba93e2686912735b07e2fc73 and adds the bounded remediation that
the user later authorized. The ai-agents-skills implementation evidence is
pinned to 6630e10bf0f7c3ac2e36cf0b7486e4b229ea89e4; the vnthuquan package
repair is pinned to 1e4dd2bf5d2a349e7512faf2b0ff3e9fcd6e05a0.

Material evidence is still missing for native macOS, native Windows, WSL,
native agent loading, the LeanExplore local backend, and several
capability-specific behaviors. The Linux API and live-service results below do
not promote broader platform claims to success.

## Coverage Summary

| Item | Observed result | What it proves |
|---|---:|---|
| Registered skills | 59 | Current manifest inventory |
| Runtime-backed skills | 33 | Current runtime registry |
| Sourced claims | 1,890 | Syntactic inventory of skill bodies and direct references |
| Unique directly referenced files | 83 | Reference paths exist |
| Confirmed claims | 119 | Baseline facts plus the repaired LeanExplore source interface |
| Claims still unverified | 1,771 | Two cross-platform vnthuquan commands now pass on Linux only |
| Conflicted / contradicted claims | 0 / 0 | Older Linux failures are explicitly superseded; wider claims remain unverified |
| Classified gaps | 121 | 101 are material; two Linux gaps are resolved |
| Baseline installed artifacts verified | 2,846 / 2,846 | Managed-state integrity at baseline |
| Baseline installed skill-file visibility | 531 / 531 | 59 files across nine target homes |
| Baseline clean-HEAD runtime smoke | passed | Declared Linux offline contracts |
| Baseline installed runtime functional rows | 66 passed, 0 failed, 0 skipped | Declared functional cases across two runtime roots |
| Baseline declared runtime exclusions | 12 rows | Six manual-native/static-only skills across two roots |
| Baseline clean-HEAD unit suite | 3,452 tests passed by four shards plus one import-path rerun | Baseline tests under sharded ordering |
| Post-remediation serial unit suite | 3,458 passed, 36 skipped | One serial discovery run completed in 992.363 seconds |
| Native execution substrates | Linux only | No native macOS/Windows/WSL claim |

The 83 direct reference files were included in the claim extraction. Mechanical
coverage does not substitute for independent semantic confirmation of all 1,890
claims; that limitation is G0001.

## Baseline Gates

On a disposable clean-HEAD snapshot:

- static-check: passed.
- sanitize-check: passed, including 12 sanitizer tests.
- docs-check: passed with no stale or missing generated document.
- runtime-smoke: passed.
- serial make test: exceeded the 900-second audit budget.
- four balanced unittest shards covered the repository modules. Three shards
  passed directly. The fourth reported only a sharding-induced import failure
  for test_zotero_watch_poller; rerunning that module with the same search path
  used by discovery ran nine tests successfully. The reconstructed suite
  contains 3,452 passing tests.

On the installed Linux state:

- verify passed all 2,846 managed artifact checks.
- smoke passed 531 file-visibility rows.
- audit-system reported one LeanExplore update because the worktree overlay
  differs from both installed copies, which still match clean HEAD.
- precheck found no missing required dependency. It reported 12 missing
  optional dependencies and eight manual/unconfigured capabilities.
- a local validator checked claim/gap structure, cross-references, source
  lines, registry coverage, and direct-reference coverage;
- a retained local boundary scan reported no sanitizer, literal secret,
  personal-path, control-character, or diff-whitespace match. This is not
  independent security assurance.

## Post-Remediation Gates

Evidence: E0041–E0046.

- The LeanExplore overlay is committed. Eight focused tests pass, and the
  Windows-shaped generated targets now match the runtime manifest. Native
  Windows serving remains explicitly unsupported.
- A live Linux MCP query returned `Nat.add_comm` (declaration 15320), followed
  by its Lean source.
- vnthuquan 0.1.2.dev1 is on its tracked `main` upstream with zero local/upstream
  delta. On both installed runtime roots, doctor, mirror checks, and a
  three-result `Kim Dung` search pass. The persisted default mirror is HTTPS.
- Hetzner doctor and reaper dry-run pass without a manual workspace pin on both
  runtime roots. The single cron scheduler refreshes the expected lease, and
  every observed reaper run scanned zero servers.
- Selected installed-state verification passed 118 of 118 records. Neutral and
  Codex-root `audit-system` checks both reported `status: ok`.
- Selected remediation smoke passed 14 functional cases, 10 live checks, and
  four credential-launch checks. Cross-compute smoke passed 12 functional cases
  and four credential-launch checks across Hetzner, Modal, and Kaggle.
- The final serial `make test` run passed 3,458 tests with 36
  platform-conditioned skips. Static-check, docs-check, and sanitize-check also
  pass. No paid server, ebook download, or Calibre write was used for these
  gates.
- The post-remediation ledger validator passed JSONL, ID, cross-reference,
  source-bound, registry, reference, count, report-reference, control-character,
  and sensitive-material checks.

## Findings

### F001 — Windows LeanExplore config target mismatch repaired at source level

Evidence: E0022 and E0041.

The baseline generator emitted POSIX launcher targets on every OS while the
runtime manifest published PowerShell targets for Windows. Revision 6630e10
selects targets by platform, and the generated Windows snippet now names
`run_skill.ps1` and `run_lean_explore_mcp.ps1`.

This resolves claim C1890 at the source-interface level. It does not establish a
native Windows launch: the Windows wrapper still refuses `serve` with exit 78,
which remains tracked separately by G0118.

### F002 — Windows LeanExplore serving remains unsupported

Evidence: E0023 and gaps windows-serve and native-platform.

The Windows wrapper explicitly exits 78 for serve. The post-remediation change
repairs the path shape only; it cannot be described as making LeanExplore MCP
operational on Windows. No native execution or credential transport was tested.

### F003 — Linux LeanExplore API search and source retrieval confirmed

Evidence: E0018–E0021, E0036, E0037, and E0042.

The admitted venv contains lean-explore 1.2.1 and MCP SDK 1.30.0. Through the
managed integration, missing credentials are refused, MCP initialization and
tool enumeration work, and the live API path now performs useful work:
`search_summary("Nat.add_comm")` returned declaration 15320 and
`get_source_code(15320)` returned the theorem source.

This confirms basic search/source behavior for the configured Linux API backend.
The local backend, timeout/recovery behavior, and native macOS, Windows, and WSL
remain unverified.

### F004 — macOS venv status is conflicted

Evidence: E0025 and E0026.

The implementation requires an /usr/bin interpreter shape and the venv test
class runs on every POSIX host, while product documentation calls this a Linux
skill environment. No current native macOS filesystem/interpreter observation
was captured. The audit cannot yet classify this as a product defect, test
defect, or both.

### F005 — Windows and WSL evidence is structurally incomplete

Evidence: E0024 and E0026.

Native Windows mutation correctly remains fail-closed. The Windows CI job omits
runtime/lifecycle execution while that gate stands. WSL is represented only by
Linux-hosted platform-shape scenarios; there is no native WSL run.

### F006 — Native loader results can be weaker than their top-level status

Evidence: E0027 and E0028.

Kimi may report ok when its doctor fails if file-layout checks pass. OpenCode
may replace a failed or malformed native skill listing with a file-presence
fallback. These are confirmed coverage limitations, not proof that either
loader is broken. The 531-row smoke result remains file-visibility evidence.

### F007 — vnthuquan package route drift repaired; Linux live checks pass

Evidence: E0029 and E0043.

E0029 remains the dated observation of HTTP 404/405 failures. Inspection located
the proximate defect in the vnthuquan package route adapter: it still used the
site's obsolete HTTP/ASP.NET routes after the external service moved. Package
0.1.2.dev1 migrates to current HTTPS routes and author keys. The skill wrapper
now defaults to HTTPS and accepts `--author-key` while preserving
`--author-id` as an alias.

E0043 explicitly supersedes E0029 for installed Linux. Both runtime roots now
pass doctor, mirror checks, and search. This evidence does not cover downloads,
validation, Calibre handoff, or native non-Linux execution.

### F008 — session-logs lacks directly named test coverage

Evidence: E0032.

No test module directly names session-logs. Generic manifest/installer tests may
cover installation, so this is a dedicated functional/safety coverage gap, not
evidence that the skill fails.

### F009 — Historical leads that are no longer current defects

Evidence: E0030, E0031, and E0007.

- Kaggle's stdout validation banner is redirected away from the JSON envelope
  in current HEAD.
- The vnthuquan mirrors live contract currently includes --json.
- The resource preflight Permission denied observation came from running the
  Windows Python target through the POSIX runner. The proper POSIX wrapper
  succeeds; the failed invocation is operator-probe-error.

### F010 — Hetzner live checks now use one shared workspace

Evidence: E0044.

The earlier unpinned doctor selected each runtime's read-only workspace, where
no operator config lived, even though credentials and the detached reaper were
present. The runner now selects the owner-controlled shared compute workspace
when its config exists, while an explicit workspace pin retains priority. Both
POSIX and PowerShell paths validate the complete owner/ACL chain before a
credential-bearing launch.

The active config/state was copied to `~/.local/share/ai-agents-skills/research-compute`,
the single cron line was cut over, and its lease refresh was observed. Unpinned
doctor and reaper dry-run pass on both runtime roots; the reaper scanned zero
servers. The legacy workspace and crontab backup remain available for rollback.
No paid lifecycle operation was run.

## LeanExplore Matrix

| Substrate | API launch | API search | Local backend | Current assessment |
|---|---|---|---|---|
| Linux installed post-remediation | confirmed | confirmed: `Nat.add_comm` search and source | blocked: no cache | basic API path works |
| macOS | blocked: no native observation | blocked | blocked | incomplete analysis |
| Native Windows post-remediation | generated targets match manifest; serve explicitly refused | blocked | blocked | source interface fixed; serving unsupported |
| WSL | blocked: no native WSL run | blocked | blocked | platform-shape evidence only |

## Per-Skill Inventory

Functional and Live are counts of declared cases, not conclusions about the
whole skill. Material gaps excludes advisory live-applicability questions.

| Skill | Runtime class | Functional | Live | Claims | Material gaps |
|---|---:|---:|---:|---:|---:|
| adversarial-boundary-gate | workflow-only | 0 | 0 | 6 | 1 |
| agent-group-discuss | workflow-only | 0 | 0 | 165 | 1 |
| annotated-review | manual-native | 0 | 0 | 21 | 3 |
| autonomous-research-loop | workflow-only | 0 | 0 | 92 | 1 |
| autonomous-research-loop-runtime | offline-smoke | 5 | 0 | 81 | 1 |
| axiom-axle-mcp | offline-smoke | 1 | 0 | 12 | 1 |
| behavior-preserving-cleanup | workflow-only | 0 | 0 | 5 | 1 |
| calibre | offline-smoke | 2 | 2 | 40 | 1 |
| classroom50 | workflow-only | 0 | 0 | 30 | 1 |
| course-canvas | workflow-only | 0 | 0 | 10 | 1 |
| course-db | workflow-only | 0 | 0 | 8 | 1 |
| course-google-classroom | workflow-only | 0 | 0 | 10 | 1 |
| cross-agent-delegation | workflow-only | 0 | 0 | 128 | 1 |
| database-lookup | workflow-only | 0 | 0 | 67 | 1 |
| decision-doubt-loop | workflow-only | 0 | 0 | 6 | 1 |
| deep-research-workflow | offline-smoke | 2 | 0 | 72 | 1 |
| digest-bridge | static-only | 0 | 0 | 21 | 3 |
| docling | offline-smoke | 3 | 0 | 61 | 1 |
| draft-writing | workflow-only | 0 | 0 | 12 | 1 |
| formal-skeleton-helper | offline-smoke | 0 | 0 | 6 | 2 |
| get-available-resources | offline-smoke | 0 | 0 | 9 | 2 |
| getscipapers-requester | manual-native | 0 | 0 | 25 | 3 |
| graph-verifier | offline-smoke | 0 | 0 | 7 | 2 |
| hetzner-research-compute | offline-smoke | 2 | 2 | 85 | 1 |
| intent-interview | workflow-only | 0 | 0 | 6 | 1 |
| kaggle-research-compute | offline-smoke | 2 | 1 | 32 | 1 |
| lean-explore-mcp | offline-smoke | 3 | 0 | 19 | 4 |
| lean-formalization-intake | offline-smoke | 0 | 0 | 14 | 2 |
| lean-research-library | offline-smoke | 1 | 1 | 11 | 1 |
| lean-strict-verification-gate | offline-smoke | 0 | 0 | 30 | 2 |
| manim-math-animation | offline-smoke | 0 | 0 | 13 | 2 |
| modal-research-compute | offline-smoke | 2 | 0 | 44 | 1 |
| model-router | workflow-only | 0 | 0 | 8 | 1 |
| opengauss | offline-smoke | 0 | 0 | 44 | 2 |
| paper-lookup | workflow-only | 0 | 0 | 29 | 1 |
| paper-review | workflow-only | 0 | 0 | 29 | 1 |
| prose | workflow-only | 0 | 0 | 7 | 1 |
| remote-bridge | offline-smoke | 1 | 1 | 30 | 1 |
| research-briefing | workflow-only | 0 | 0 | 11 | 1 |
| research-digest-wrapper | offline-smoke | 1 | 0 | 13 | 1 |
| research-report-reviewer | workflow-only | 0 | 0 | 11 | 1 |
| research-verification-gate | workflow-only | 0 | 0 | 12 | 1 |
| rss-news-digest | manual-native | 0 | 0 | 16 | 3 |
| sagemath | manual-native | 0 | 0 | 15 | 3 |
| self-improving-agent | offline-smoke | 0 | 0 | 34 | 2 |
| send-email | offline-smoke | 1 | 1 | 31 | 1 |
| session-logs | workflow-only | 0 | 0 | 14 | 2 |
| slides-to-video | offline-smoke | 0 | 0 | 43 | 2 |
| source-grounded-decisions | workflow-only | 0 | 0 | 5 | 1 |
| source-research | workflow-only | 0 | 0 | 26 | 1 |
| submission-venue-selector | offline-smoke | 3 | 0 | 57 | 1 |
| tikz-draw | manual-native | 0 | 0 | 31 | 3 |
| url-to-screenshot | workflow-only | 0 | 0 | 45 | 1 |
| url-to-screenshot-runtime | offline-smoke | 0 | 0 | 26 | 2 |
| venue-ranking-evidence | offline-smoke | 0 | 0 | 74 | 2 |
| vnthuquan | offline-smoke | 2 | 3 | 48 | 1 |
| vnu-eoffice | workflow-only | 0 | 0 | 21 | 1 |
| workspace-rearranger | workflow-only | 0 | 0 | 8 | 1 |
| zotero | venv-smoke | 2 | 2 | 54 | 1 |

## Phase 1 Readiness

The whole-system Phase 1 gate is not met:

- 1,771 claims remain unverified;
- 101 material gaps remain;
- no native macOS, Windows, or WSL execution was supplied;
- no native agent invocation was established;
- six runtime skills remain manual-native/static-only.

The bounded Linux failures addressed here are remediated. The Windows
LeanExplore target-name contract is fixed, while Windows serving remains
unsupported. These scoped successes do not clear the whole-system gate.

## Next Evidence

1. Obtain native macOS interpreter/venv observations.
2. Verify LeanExplore config generation and the documented serve refusal on
   native Windows without opening unsupported serving.
3. Obtain a real WSL runtime run.
4. Run the stable LeanExplore declaration search/source sequence against a
   prepared, versioned local cache.
5. Exercise vnthuquan validation and download planning through a disposable
   dry-run without downloading content or writing to Calibre.
6. Build a synthetic, redacted session-log fixture.
7. Review high-risk extracted claims by subsystem before converting any
   remaining gap into a repair requirement.

## Trust Boundary

Legacy session text, dependency source, live-service output, filenames, and
reviewer output were treated as evidence data only. No command was generated
from those inputs and executed. No secret value is stored in this audit.

All command-like claim text is quoted evidence only. No claim or gap record
authorizes execution, credential use, live calls, Windows mutation, or external
writes.

The original Phase 0 baseline made no runtime or external mutation. The later,
separately authorized remediation installed reviewed local runtime files,
changed one user crontab line after saving an owner-private backup, and updated
one vnthuquan config value to HTTPS. Live probes were read-only. No paid server
was created, modified, or deleted; no ebook was downloaded; no Calibre write
ran; and no credential value was changed or recorded.

## Fresh Review Status

The baseline fresh review remained incomplete for the full 1,890-claim audit.
A separate fresh security review of the shared-workspace runner change returned
PASS after POSIX and PowerShell trust-chain checks were extended through their
owner-controlled boundary. That review covers the credential-launch boundary,
not every audit claim or native platform.
