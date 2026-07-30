# Goal Focus v2 Tasks

## Scope and evidence

- [x] Confirm the approved implementation and rollout scope.
- [x] Record trusted-local as the accepted current execution profile and
  strict-isolated as future hardening.
- [x] Inspect ARL/runtime skill docs, lifecycle guidance, legacy defaults,
  Goal Focus core, transaction layer, panel schemas, Notify v2, and focused
  tests.
- [x] Create and work from an isolated worktree.
- [x] Record the behavioral specification, decisions, risks, verification gate,
  and rollback plan.

## Goal Focus core

- [x] Add revisioned `goal_contract.v2`, `approach_registry.v2`,
  `current_plan.v2`, and `direction_decision.v2` contracts.
- [x] Add validation across goal, registry, plan, ledger, pending candidate, and
  compatibility projections.
- [x] Add eligibility filtering, conservative/optimistic interval scoring,
  dominance selection, and bounded diverse exploration.
- [x] Add explicit campaign/global progress classification and replan triggers.
- [x] Add v1 migration planning, no-guess ambiguity handling, provenance,
  backups, and transactional apply primitives.
- [x] Add pending-candidate staging and accepted/rejected finalization primitives.

## Transaction safety

- [x] Add cross-process loop locking.
- [x] Add compare-and-swap revision checks.
- [x] Add complete post-image transaction journals and atomic replacement.
- [x] Add idempotent JSONL event appends.
- [x] Add interrupted-transaction recovery and crash-point tests.

## Structured review

- [x] Add strict `strategy_advice.v1` validation and synthesis.
- [x] Add strict `result_review.v1` banking invariants.
- [x] Normalize provider families and reject CodeWhale/DeepSeek as independent
  of each other.
- [x] Add complete-authority strategy briefs with explicit anti-anchoring and
  candidate-specific review briefs.
- [x] Add unit tests for invalid structure, dissent, same-family aliases, and
  fail-closed review outcomes.

## Runtime integration

- [x] Finish the `goal-focus` CLI family and stable JSON output contracts.
- [x] Enforce pre-dispatch recovery, reconciliation, validation, and replan.
- [x] Commit a reviewed strategy before primary dispatch.
- [x] Pin the primary prompt and candidate to exact revision/hash authority.
- [x] Replace direct banking with stage -> result review -> atomic finalize.
- [x] Keep a candidate pending and suppress new work while review is unavailable.
- [x] Verify rejected work charges budget but clears claims and goal progress.
- [x] Verify off/monitor and unmigrated v1 compatibility paths.
- [x] Bind migration proposal/backups/apply to exact legacy source bytes and
  refuse apply while a live driver owns the loop.
- [x] Enable real trusted-local primary execution without weakening Goal-Focus
  staging or review-before-bank invariants.
- [x] Enable real trusted-local Claude/Codex/CodeWhale panels behind the existing
  external-egress and prompt-admission gates.
- [x] Enforce validated systemd/cgroup and POSIX limits for trusted-local
  primary and panel descendants: wall/CPU, memory/swap/address space,
  cgroup `TasksMax` for process/thread count, open files, file size, core dumps,
  and captured output. Do not use per-real-UID `RLIMIT_NPROC`, which can affect
  unrelated processes and is not a provider-tree boundary.
- [x] Prove invalid limit settings or an unavailable limit backend deny before
  provider spawn, and prove timeout/limit cleanup removes descendants.
- [x] Apply mandatory resource preflight and bounded spawn to every explicit
  trusted-local execution independently of Goal-Focus governance mode; make an
  unverified descendant cleanup terminal before retry/failover.
- [x] Make the configurable capture limit an exact 1--16 MiB contract without
  silent clamping and keep the attested upper bound identical.
- [x] Pass a disposable real-provider strategy -> stage -> review ->
  reject-finalize/Notify-render cycle with exact real provider outputs and
  verified resource cleanup.
- [x] Quarantine any candidate or dispatch left by a failed host completion
  gate; serialize tombstone creation against staging/finalization, stop the
  supervisor without retry/failover, and require exact-fingerprint recovery.

## Notify v2

- [x] Add the `aas.autoloop.notify.v2` schema and validators.
- [x] Add required Goal, Completed, Current, and Plan sections.
- [x] Add status, progress, executor, driver/panel/other-agent provenance,
  compute, finish time, and duration fields.
- [x] Add Markdown, plain, bounded Telegram HTML, and compact renderers.
- [x] Add explicit no-compute versus legacy/unreported semantics.
- [x] Add legacy flat-field conversion helpers and unit tests.
- [x] Finish runtime event mapping for every drive/watch/supervisor outcome.
- [x] Verify remote-bridge `--event-json` delivery and per-transport rendering.
- [x] Verify delivery fingerprints are persisted only after successful send.
- [x] Add short-lived event/iteration deduplication for timestamp-only retries.
- [x] Serialize semantic retry check -> send -> remember across processes while
  allowing materially changed events through.
- [x] Redact configured/common credentials from transport exceptions and
  structured free-text provenance.
- [x] Recursively redact the full event envelope, including event ids and
  extension values, before result/dedupe persistence.
- [x] Require HTTPS/no-credential/no-redirect endpoints and harden delivery
  locks/registry against link and replacement attacks.
- [x] Record driver, usable panel agents, and any other agents explicitly in
  every Notify v2 rendering.

## Strict-isolated future hardening

- [x] Fail closed before spawning an enforce-mode primary when the complete
  credential-blind, prompt-private, allowlisted-filesystem, resource-bounded,
  constrained-egress transport is unavailable.
- [x] Scope that denial to strict-isolated/default execution and prove an
  explicit trusted-local launch reaches provider execution.
- [ ] Run enforce-mode primaries in a read-only mount/PID namespace with the
  host runtime, authority, WAL, home, and credential stores inaccessible.
- [ ] Allow only the per-candidate evidence directory to be writable and ingest
  one bounded host-validated submission after sandbox exit.
- [ ] Bind provider entrypoint, dependency closure, upstream family, and exact
  model to the reviewed and launched command.
- [x] Require explicit panel and notification egress consent and secret-gate
  every outbound prompt/event.
- [ ] Prove injected primaries cannot import the host core, write authority,
  change runtime/provider dependencies, leak unrelated credentials, or leave
  descendants.
- [ ] Bound scratch/evidence storage, cgroup task count, descriptors, memory,
  and file size so a hostile primary cannot exhaust the host; keep the task
  bound scoped to the provider tree rather than the shared real user id.
- [ ] Broker provider networking to the exact attested service while denying
  host loopback, private/link-local ranges, and cloud metadata.
- [ ] Deliver provider credentials without making them readable by the primary
  model's tools, children, environment, filesystem, or process metadata.

## Documentation and packaging

- [x] Add the Goal Focus v2 workflow reference template.
- [x] Correct false legacy v1 default descriptions.
- [x] Add feature `SPEC.md`, `PLAN.md`, and `TASKS.md`.
- [x] Document Goal Focus v2 and Notify v2 in both ARL skill guides.
- [x] Finish runtime/artifact/skill manifest wiring.
- [x] Regenerate managed docs after manifests stabilize.
- [x] Run manifest validation and docs checks.

## Verification

- [x] Run focused Goal Focus, transaction, structured-panel, Notify v2, and
  remote-bridge tests (130 passed on 2026-07-29).
- [x] Run focused ARL runtime integration tests (111 passed, one Windows-only
  skip, on 2026-07-29).
- [x] Run the complete repository unit suite after review remediation (1,431
  passed, nine expected skips, on 2026-07-29).
- [x] Run runtime selftest/smoke for ARL and remote-bridge offline runtimes.
- [x] Run fake-root lifecycle checks for Linux, macOS, Windows, and WSL shapes.
- [x] Rehearse dry-run/apply migration and exact-byte rollback on disposable
  loop copies.
- [x] Obtain fresh-context code review.
- [x] Obtain independent test review.
- [x] Obtain fresh-context adversarial security review for the frozen
  safe-denial scope.
- [x] Resolve every blocking code/test review finding and rerun affected checks.
- [x] Verify trusted-local primary/panel execution and preserve strict-isolated
  fail-closed behavior.
- [x] Verify resource-limit metadata and hard-limit behavior in unit,
  integration, and real-provider canary runs.
- [x] Rerun the consolidated Goal Focus/resource/panel/Notify/bridge/runtime/
  supervisor regression set after the terminal-history, compute-lane, and
  governance-independent resource fixes (308 passed on 2026-07-30).
- [x] Run the final clean-tree complete repository suite after all release
  fixes (1,497 passed, nine expected platform skips, on 2026-07-30).

## Active-loop rollout

- [x] Record the held clawfree rollout profile: title `Clawfree
  reconfiguration`, slug `clawfree-reconfiguration`, Claude
  `claude-fable-5` at highest available thinking as driver, Claude/Codex/
  CodeWhale panel, Grok/Kimi excluded, Hetzner/Kaggle-only compute, and Notify
  v2 enabled behind explicit egress consent, using the explicit trusted-local
  provider execution profile.
- [ ] Wait for iteration boundaries and pause all approved active loops.
- [ ] Capture pre-cutover processes, ledger tips, providers, and control state.
- [ ] Back up loop controls and supervisor configuration.
- [ ] Install the verified canonical revision.
- [ ] Dry-run migration per loop and resolve ambiguous direction signals without
  guessing.
- [ ] Apply migration and validate each loop before dispatch.
- [ ] Restart with the approved provider/failover, compute, goal-focus, and
  notification settings.
- [ ] Observe the first complete v2 stage/review/finalize/notify cycle per loop.
- [ ] Roll back any loop whose invariants fail.

## Unchecked or pending evidence

- The complete disposable real-provider conservative-rejection cycle and final
  clean-tree regression passed; the installed live rollout remains pending.
- Strict-isolated credential-blind, allowlisted-filesystem, resource-bounded,
  endpoint-constrained transport remains unimplemented future hardening, not a
  blocker for an explicit trusted-local rollout.
- Legacy Mailbox control authority and already-published OpenClaw workspace
  copies require separately reviewed hardening/recovery work.
- No canonical install or live loop has been changed from this worktree.
