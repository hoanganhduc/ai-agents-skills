# Goal Focus v2 Task Plan

## Context

Goal Focus v2 addresses a confirmed control-plane failure mode: legacy static
campaign configuration can disagree with the current path, recovery state, and
latest results, while advisory warnings still allow a stale direction to run.
The approved implementation makes goal and strategy state revisioned,
introduces structured pre-dispatch review, and prevents provider success from
becoming banked research without independent result review.

Development is isolated in a dedicated Goal Focus v2 worktree so the
canonical installation and active research loops are unchanged until the full
verification gate passes.

## Constraints

- Preserve existing v1 loops until explicit migration.
- Use only standard-library runtime dependencies.
- Keep host-owned panels outside the primary-agent sandbox.
- Require different provider families for result-review independence.
- Preserve user-specified compute-provider allowlists.
- Quiesce live loops before migration; do not race active ledger writes.
- Treat installation, migration, and restart as separate reversible steps.

## Implementation Phases

### Phase 0 — Evidence, baseline, and isolation (`completed`)

1. Inspect ARL/runtime policy, current helper code, tests, and lifecycle docs.
2. Capture an initial inventory of active loops and their driver/runtime shapes;
   refresh it immediately before cutover.
3. Create an isolated worktree from the clean canonical revision.
4. Run the relevant legacy baseline tests before behavioral edits.

Exit criterion: isolated branch exists and the selected baseline suite passes.

### Phase 1 — Revisioned Goal Focus core (`unit-complete`)

1. Add `goal_focus.py` contracts for goal, registry, plan, decisions, pending
   candidates, validation, progress classification, migration, and projections.
2. Add `state_transaction.py` with a loop-scoped lock, compare-and-swap
   revisions, complete post-image journals, atomic replace, idempotent JSONL,
   and interrupted-transaction recovery.
3. Add deterministic interval scoring, eligibility filtering, dominance, and
   bounded diverse-experiment selection.
4. Add unit tests for revision conflicts, crash points, direction selection,
   progress, ambiguity-preserving migration, accepted results, and rejected
   results.

Exit criterion: core/state-machine unit tests pass. This does not establish
driver integration or rollout readiness.

### Phase 2 — Structured panel contracts (`unit-complete`)

1. Add `strategy_advice.v1` and `result_review.v1` validation.
2. Normalize provider families; collapse CodeWhale/DeepSeek aliases.
3. Make strategy synthesis expose disagreement and result synthesis use a
   conservative verdict.
4. Build a complete-authority strategy brief that shows the current plan under
   explicit anti-anchoring instructions, plus a candidate-specific result brief.
5. Test unstructured-response rejection, same-family rejection, dissent,
   evidence requirements, and banking invariants.

Exit criterion: panel unit tests pass. Driver use of these contracts remains a
separate gate.

### Phase 3 — Runtime and driver contracts (`completed with injected runners`)

1. Add init mode selection and Goal Focus status/validate/migrate/replan/
   reconcile command handling.
2. Run transaction recovery, projection reconciliation, validation, and replan
   checks before every primary dispatch.
3. In enforce mode, suppress the legacy hard-path rewrite and use only the
   reviewed `current_plan.json` action.
4. Run host-owned `strategy_review` when no valid active plan exists; require
   valid structured advice and independent review before direction commit.
5. Pin the primary action to `plan_revision` and expose the authoritative goal
   contract in the prompt.
6. Stage successful primary output instead of appending it directly.
7. Run a different-family structured result review, then call one atomic
   finalize path for accept or reject.
8. Leave a candidate pending and emit a waiting event when review is unavailable;
   do not dispatch a second primary iteration.
9. Preserve off/monitor behavior and existing ledger stop invariants.

Exit criterion: runtime integration tests demonstrate the full accepted,
rejected, stale-plan, pending-review, and legacy paths with controlled runners.
Real trusted-local provider execution is verified in Phase 6B.

### Phase 4 — Notify v2 and transport integration (`completed`)

1. Define `aas.autoloop.notify.v2` and renderers for Markdown, plain text,
   Telegram HTML, and compact output. (`completed at unit level`)
2. Require Goal, Completed, Current, Plan, status, progress, executor, compute,
   and finished information. (`completed at unit level`)
3. Preserve explicit no-compute versus legacy/unreported compute and normalize
   supported services/statuses. (`completed at unit level`)
4. Build events from finalized runtime state for every drive/watch/supervisor
   outcome. (`completed and integration-tested`)
5. Add remote-bridge `send --event-json` rendering/routing and remember a
   delivery fingerprint only after successful transport. (`completed and integration-tested`)
6. Keep flat aliases for legacy consumers. (`completed and integration-tested`)

Exit criterion: notify and remote-bridge tests pass, including failure paths,
deduplication, topic stability, escaping, and mandatory-field retention.

### Phase 5 — Packaging and operator documentation (`completed`)

1. Register new runtime files and the Goal Focus workflow template in manifests.
2. Correct legacy v1 default descriptions.
3. Document authoritative state, modes, dispatch/review/finalize behavior,
   rejection, migration, notifications, and compatibility in both ARL skills.
4. Add this feature specification, plan, and task ledger.
5. Regenerate managed documentation only after manifests stabilize.

Exit criterion: manifest validation and docs checks pass with no stale generated
artifacts.

### Phase 6 — Verification and fresh-context review (`completed for safe-denial scope`)

1. Run focused Goal Focus, state-machine, panel, notify, remote-bridge, and ARL
   integration tests.
2. Run the full unit suite with notifications disabled and bytecode writes off.
3. Run runtime selftest/smoke and fake-root lifecycle tests across supported
   targets/platform shapes.
4. Rehearse migration dry-run/apply, ambiguous refusal, backup creation,
   interrupted finalize recovery, and rollback on disposable loop copies.
5. Obtain fresh-context code, test, and security reviews; resolve every blocking
   finding.

Exit criterion: all required checks pass and the fresh review reports no
blocking defect.

### Phase 6A — Separate governance from provider execution (`completed`)

1. Keep `off|monitor|enforce` as Goal-Focus governance modes.
2. Add an explicit `trusted-local` provider transport opt-in; keep omission and
   `strict-isolated` fail-closed.
3. Permit attested Claude/Codex primaries in trusted-local mode, preserving
   stdin prompts, minimized environments, provider/model pins, PID lifetime,
   timeouts, output bounds, and host-mediated staging.
4. Permit real trusted-local panels after the existing external-egress and
   secret/PII admission gates. Use private stdin where supported and record
   CodeWhale's one-shot argv limitation.
5. Require a validated systemd/cgroup scope plus inherited POSIX limits for
   wall time, CPU time/throughput, memory/swap/address space, open files, file
   size, core dumps, and captured output. Make cgroup `TasksMax` authoritative
   for the provider tree's process/thread count; intentionally do not use
   `RLIMIT_NPROC`, because it applies to the provider's entire real-user-id
   process population rather than the bounded provider tree. Deny before spawn
   if the backend or configuration is unavailable.
   Verify the process's actual cgroup leaf and inherited RLIMITs in a trusted
   pre-exec gate, then mask the cgroup API and known out-of-scope launch planes
   (user-manager/container sockets and live tmux/Screen controls) inside the
   provider namespace. Treat the file-size limit as per-file protection and do
   not claim an aggregate disk quota.
6. Keep Goal-Focus state, evidence, different-family review, and atomic banking
   identical across execution profiles.

Exit criterion: default strict denial still passes, while trusted-local tests
reach the formerly denied primary and panel branches.

### Phase 6B — Trusted-local functional verification (`completed`)

1. Run focused and full regression suites.
2. Verify each resource limit contract with subprocess fixtures, including
   hard-limit termination and descendant cleanup.
3. Run real Claude, Codex, and CodeWhale panel canaries with bounded prompts.
4. Run a disposable enforce/trusted-local cycle through strategy review,
   primary staging, result review, finalize/reject, and Notify v2 rendering.
5. Run runtime smoke, manifest/docs checks, and a fresh-context code/test review.

Exit criterion: the functional path passes with real provider evidence and no
Goal-Focus invariant regression.

Observed result: real CodeWhale strategy review selected the bounded action;
real Claude Fable 5/max produced the exact host-mediated candidate under the
mandatory primary resource profile; real CodeWhale reviewed the exact candidate
and conservatively returned `partial`/`safe_to_bank=false`; the host atomically
finalized a rejected ledger row with no claim banked. All provider scopes were
dead/clean, Goal-Focus/runtime validation had zero findings, and Notify v2
rendered the complete failure outcome. The real conservative-rejection path is
therefore proven; accepted finalization remains covered by controlled-runner
integration tests rather than a real-provider acceptance.

### Phase 7 — Controlled trusted-local rollout (`in progress`)

1. Wait for an iteration boundary and pause each active loop.
2. Record process ids, ledger tip, active provider, plan/campaign signals, and
   current runtime path.
3. Back up all control files and supervisor configuration.
4. Install the verified canonical revision.
5. Run migration dry-run per loop; resolve ambiguity without guessing.
6. Apply migration, validate authoritative state and compatibility projections,
   and run a no-dispatch status/preflight check.
7. Restart with the approved trusted-local provider/failover, compute,
   notification, external-egress, and mandatory resource-limit settings.
8. Observe the first strategy review, staged candidate, independent result
   review, finalization, and notification.
9. Roll back from the recorded backup if any invariant fails.

Exit criterion: every selected active loop is running from the verified runtime
with a valid v2 plan, no duplicate or lost ledger row, and a confirmed Notify v2
event.

### Phase 8 — Strict-isolated hostile-provider hardening (`deferred; safe-denial baseline complete`)

1. Treat primary output, candidate evidence, panel output, and notification
   input as untrusted data at the host boundary.
2. Replace in-primary authority mutation with one reserved, bounded submission
   in the candidate evidence directory; the host validates and stages it.
3. Run the primary in a Linux read-only mount/PID namespace that hides the host
   runtime, loop authority/WAL, user home, unrelated credentials, and provider
   stores. Re-expose only the exact reviewed project view, sealed selected
   provider closure/credential, and candidate evidence directory.
4. Pin and revalidate provider entrypoint, dependency closure, upstream family,
   and exact model immediately before spawn.
5. Require explicit external panel/notify egress consent and recursively reject
   or redact secret-bearing material before any provider/network call.
6. Add hostile-primary, sibling-dependency, direct-import/write, descendant,
   link-race, secret-exfiltration, and exact-model argv probes.
7. Treat a read-only whole-host view, shared networking, direct credential
   mounts, and an unbounded host-backed evidence directory as insufficient.
   Require an allowlisted filesystem view, credential-blind and prompt-private
   provider transport, endpoint-constrained egress, and resource bounds.
8. Until all four controls exist together, deny enforce primary dispatch before
   process creation and verify that denial has no worker, submission, reviewer,
   evidence, authority, or external-send side effect.

Security exit criterion: the complete constrained transport passes all hostile
probes and a fresh security reviewer confirms that the primary cannot read,
mutate, exfiltrate, exhaust, or impersonate the host control plane. This future
criterion is not a prerequisite for an explicitly accepted trusted-local run.

#### Rollout target profile — pilot loop

- Loop: the pilot project's `research/` loop directory.
- Notify title: the loop's stable research title.
- Stable topic/job slug: derived from the research title.
- Driver: Claude, model `claude-fable-5`, highest available thinking level.
- Provider execution: explicit `trusted-local`; no hostile-process containment
  claim is made.
- Panel: Claude, Codex, and CodeWhale; independence still requires a usable
  reviewer from a different provider family than the executor.
- Excluded providers: Grok and Kimi until the operator explicitly changes the
  credit/availability decision.
- Allowed external compute: Hetzner and Kaggle only; no other compute service
  may be inferred or substituted.
- Notify: enabled only with the explicit enforce-mode external-egress consent
  and the configured secure remote-bridge transport.

This profile may be applied only after Phase 6B passes and the live loop is
quiesced and backed up.

## Decisions

| Decision | Rationale | Status |
|---|---|---|
| Four authoritative Goal Focus files | Separates stable goal truth, mutable portfolio, current route, and audit history. | Accepted |
| Compatibility fields are projections | Prevents `next_preferred_path` or recovery prose from overruling a reviewed plan. | Accepted |
| Default new goal-focused loops to `enforce` | Advisory-only discipline did not prevent stale-route dispatch. | Accepted |
| Use interval dominance, not point-score certainty | Open-problem estimates are uncertain; overlapping bounds should trigger information-gathering work. | Accepted |
| Show the exact incumbent without giving it preference | Reviewers need the current plan to assess switching cost and stale state; explicit anti-anchoring instructions avoid treating sunk effort or active status as evidence. | Accepted |
| Stage before banking | Provider exit status is operational evidence, not claim verification. | Accepted |
| Require different-family direction and result review | Same-family aliases do not provide meaningful independence for either plan selection or claim banking. | Accepted |
| Charge rejected work but clear claims/progress | Preserves truthful resource accounting without contaminating the research record. | Accepted |
| Recoverable CAS transaction | Prevents partial cross-file state and stale concurrent writers. | Accepted |
| Explicit dry-run/apply migration | Existing loops must not change merely because the runtime was installed. | Accepted |
| Structured Notify v2 with flat aliases | Improves operator clarity without abruptly breaking legacy consumers. | Accepted |
| Roll out only after quiescing each loop | Avoids migration racing a live append/finalize. | Accepted |
| Separate Goal-Focus governance from provider containment | State discipline must remain usable with trusted operator-selected CLIs. | Accepted |
| Trusted-local is the current functional rollout profile | Restores the requested loop while retaining explicit strict-isolated denial for future hostile-provider hardening. | Accepted |

## Verification Plan

| Check | Command or method | Expected result |
|---|---|---|
| Core/state unit contracts | `python3 -B -m unittest tests.test_goal_focus tests.test_goal_focus_state_machine -v` | All tests pass. |
| Structured panel and Notify v2 | `python3 -B -m unittest tests.test_panel_parent tests.test_arl_notify -v` | All tests pass. |
| Runtime/bridge integration | `python3 -B -m unittest tests.test_autonomous_research_loop tests.test_remote_bridge -v` | Full v2 and legacy paths pass. |
| Full regression | `python3 -B -m unittest -v` | Pass, except documented platform-only skips. |
| Runtime smoke | Installer runtime-smoke for `autonomous-research-loop-runtime` | `status: ok`. |
| Manifest/docs | Manifest validation plus docs-check/generation | No manifest or generated-doc drift. |
| Install shapes | Fake-root lifecycle on supported agents and Linux/macOS/Windows/WSL shapes | Install/upgrade/uninstall lifecycle passes. |
| Migration rehearsal | Disposable copies of representative v1 loops | Dry-run is non-mutating; ambiguity selects no route and applies as `needs_replan`; apply backs up and validates. |
| Transaction recovery | Inject failure at each finalize checkpoint | One consistent post-state and no duplicate JSONL event. |
| Fresh review | Independent code, test, and security reviewers | No blocking findings. |
| Rollout observation | First live v2 iteration per migrated loop | Strategy, stage, review, finalize, and notify evidence all present. |

## Verification Results

| Date | Coverage | Result |
|---|---|---|
| 2026-07-29 | `tests.test_goal_focus`, `tests.test_goal_focus_state_machine`, `tests.test_panel_parent`, `tests.test_arl_notify` | Passed 59 tests in the isolated worktree after the core implementation completed. This confirms unit-level contracts only; runtime integration, packaging, and rollout remain pending. |
| 2026-07-29 | Goal Focus, transaction, Notify v2, panel, and remote-bridge focused suites | Passed 130 tests after trust-boundary remediation, including nested-parent symlink rejection, source-bound strategy review, concrete evidence snapshots, transport-secret redaction, and descriptor-bound log classification. |
| 2026-07-29 | `tests.test_autonomous_research_loop` | Passed 111 tests with one Windows-only skip, including enforce/monitor/off integration, live-driver migration refusal, source-CAS migration, notification iteration dedupe, pending-review recovery, and exact stage/review/finalize flow. |
| 2026-07-29 | Consolidated Goal Focus, transaction, panel, Notify v2, ARL, and remote-bridge focused suites | Passed 330 tests with one expected platform skip. A later bridge/adapter/Notify regression subset passed 75 tests after the final compatibility-boundary changes. |
| 2026-07-29 | Complete repository discovery suite, `/usr/bin/python3 -B -m unittest` | Initial pre-review run passed 1,420 tests in 491.087 seconds. After review remediation, the final run passed 1,431 tests in 475.334 seconds with nine documented skips and zero failures. |
| 2026-07-29 | Runtime smoke for ARL and remote-bridge | Passed both offline runtime smoke checks without network access or live provider calls. |
| 2026-07-29 | Fake-root lifecycle across Linux, macOS, Windows, and WSL shapes | Passed all four install/upgrade/uninstall lifecycle runs. |
| 2026-07-29 | Disposable v1 migration, backup, validation, and rollback rehearsal | Dry-run was byte-for-byte non-mutating; apply produced a valid migration and digest-valid backup; rollback restored exact original bytes while retaining v2 authority for audit. |
| 2026-07-29 | Manifest, generated docs, static, sanitization, and diff gates | Manifest validation, docs generation/check, static check, sanitization check, and diff check passed in the isolated worktree. |
| 2026-07-29 | Fresh code and test reviews plus blocking-finding remediation | Reviews found legacy topic-placeholder retention, invented legacy compute provenance, permissive staged compute validation, revision coercion, missing enforce-notify consent coverage, and missing supervisor behavior coverage. The resolver, Notify normalizer, compute validator, revision checks, estimate validation, and regressions were updated; a consolidated post-fix suite passed 342 tests with one expected skip. |
| 2026-07-29 | Post-fix code/test verdicts and strict provenance follow-up | Independent code and test reviewers marked every blocking finding resolved and returned PASS for the safe-denial scope. A final strict-type follow-up rejected non-string compute services/job references and numeric-string or non-finite durations; the 153 affected Notify, Goal Focus, and remote-bridge tests passed. Functional rollout remained BLOCK. |
| 2026-07-29 | Frozen-tree adversarial security review | Reviewer independently matched all 45 files and aggregate hash, passed 210 isolated tests from a private execution root, and returned PASS for the enforce-mode safe-denial scope. Functional rollout, real panels/primaries, and any secure-containment claim for monitor/legacy mode remained BLOCK. |
| 2026-07-30 | Terminal-state, provider-history, and compute-lane remediation | Added a host-only reviewed-goal terminal validator that reconstructs and fingerprint-checks the accepted candidate, exact archive/ledger review, satisfied obligation evidence, terminal authority, and unique finalize decision without weakening legacy proof-based success. Historical executable attestations are now checked statically while every new spawn/finalization still performs live re-attestation. Trusted-local primaries receive only credentials for Hetzner/Kaggle lanes selected by the host-pinned effective compute policy; unlisted, forbidden, model-proposed-only, unattested, notification, GitHub, and Modal credentials remain absent. Focused and class-level regressions passed. |
| 2026-07-30 | Resource enforcement independent of Goal-Focus governance | Explicit trusted-local execution now requires resource preflight and bounded spawn in `off`, `monitor`, and `enforce`. An unverified descendant cleanup is terminal after one spawn for every mode and cannot enter retry/failover. Output overrides have an exact 1--16 MiB contract with no silent clamp. Five focused release-blocker regressions and the consolidated Goal Focus/resource/panel/Notify/bridge/runtime/supervisor suite passed 308 tests. The exact Claude, Codex, and CodeWhale installed dependency closures also passed live executable/model/upstream attestation after removing group-write permission from the Codex package closure. |
| 2026-07-30 | Final clean-tree repository regression | After removing generated bytecode inventory artifacts, the pinned Python 3.11 `make test` run passed all 1,497 tests in 492.170 seconds with nine documented platform skips and zero failures. A fresh frozen-tree reviewer and the independent resource reviewer both returned PASS with no behavior-affecting rollout blocker. |

## Rollback Strategy

- Before installation, retain the previous canonical revision and generated
  runtime tree.
- Before each loop migration, retain the runtime-created timestamped backup and
  a separate operator record of supervisor/process configuration.
- If validation or the first finalized iteration fails, stop the new driver,
  restore the prior runtime revision and backed-up loop controls, validate the
  legacy ledger tip, then restart the previous supervisor.
- Never delete transaction journals or candidate archives during incident
  recovery; they are evidence for determining the only consistent post-state.

## Remaining Gaps

- Fresh code, test, and adversarial security reviews completed. Their blocking
  findings were remediated, and post-fix verdicts passed for the reviewed
  scopes.
- Disposable trusted-local canaries completed with real Claude, Codex, and
  CodeWhale providers, mandatory resource limits, host-owned staging, an
  independent rejection, atomic finalization, and Notify v2 rendering. This is
  functional canary evidence, not evidence from the installed live loop.
- The first installed live stage/review/finalize/notify cycle remains the
  current-release gate.
- Credential-blind, allowlisted-filesystem, resource-bounded,
  endpoint-constrained provider transport remains future strict-isolated
  hardening and is not a blocker for an explicit trusted-local rollout.
- A dormant legacy panel sandbox construction remains outside the admitted real
  execution path; it must be removed or hardened before any future transport
  enables it.
- Monitor/legacy primary execution retains a writable whole-host view and is
  compatibility behavior for trusted workloads, not secure containment.
- Legacy Mailbox stop/pause/resume path authority is not an operating-system
  security boundary. It requires separate hardening before security-sensitive
  remote control is treated as trusted.
- The blocked OpenClaw publisher cannot replace or clean up an already-published
  workspace adapter. Any such legacy copy needs a separately reviewed operator
  quarantine or recovery procedure.
- The opt-in raw shell notification hook inherits the ambient environment; it
  needs argv execution plus a narrow allowlisted environment before use with
  credentials. The OpenClaw adapter principal likewise needs to move from argv
  into a bounded structured stdin envelope before publishing.
- Notification PII detection is heuristic, and delivery is intentionally
  at-least-once across a crash between remote acceptance and dedupe persistence.
- No canonical installation or active research loop has been paused, migrated,
  or restarted from this worktree.
