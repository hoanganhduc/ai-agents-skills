# Goal Focus v2 Specification

## Goal

Make an autonomous research loop follow the best-supported registered direction
toward its main research goal, while preventing stale campaign text, unchecked
results, and partial state writes from silently becoming the loop's truth.
Goal Focus v2 replaces advisory-only path discipline with a revisioned goal
contract, an explicit approach portfolio, a reviewed current plan, and an
auditable stage-review-finalize boundary.

The selection guarantee is deliberately bounded: the runtime chooses the
best-supported eligible direction under the recorded evidence, estimates,
budget, and policy. It does not claim to know the objectively best route through
an open problem.

## Problem Statement

Legacy `goal_priority.v1` can inject useful campaign advice, but its static
`primary_campaign`, dynamic `next_preferred_path`, recovery prose, and latest
ledger row can disagree. Because v1 warnings do not block dispatch, a worker can
continue a locally productive but globally stale direction. A successful
provider exit can also be mistaken for a reviewed research result.

Goal Focus v2 must therefore separate stable goal truth from mutable strategy,
derive compatibility views from one current plan, and require independent
review before a candidate changes the banked research ledger.

## Scope

- In scope:
  - Revisioned Goal Focus state and cross-file validation.
  - Deterministic approach filtering, interval scoring, and bounded selection.
  - Pre-dispatch recovery, reconciliation, strategy review, and replan gates.
  - One staged iteration candidate at a time.
  - Different-family structured result review before banking.
  - Recoverable compare-and-swap transactions for multi-file finalization.
  - Explicit rejected-work accounting and semantics.
  - `goal_priority.v1` dry-run/apply migration with provenance and backups.
  - Notify v2 structured events and legacy notification compatibility.
  - An explicit provider-execution profile independent of Goal-Focus
    governance mode.
  - Installation metadata, tests, documentation, and staged migration of active
    loops after verification.
- Out of scope:
  - Proving that a selected approach is objectively optimal.
  - Treating panel votes as mathematical evidence.
  - Changing the existing hard loop budgets or terminal stop rules.
  - Silently reopening a blocked route without a new mechanism and review.
  - Inferring compute usage from prose, filenames, or artifact paths.
  - Widening user-specified compute-provider allowlists.

## Assumptions

- Exactly one host driver owns dispatch for a loop directory at a time.
- The host runtime, authority files, transaction journal, trusted operator
  provider pins, and host mediation path are trusted. Primary output, evidence,
  panel output, and notification input are always treated as untrusted data.
- Goal-Focus governance and provider-process containment are independent axes.
  `enforce` governs plan selection, staging, review, and banking; it does not by
  itself claim operating-system isolation from an operator-selected CLI.
- The `trusted-local` provider profile trusts the selected CLI with the
  dedicated project tree, its own credentials, and shared network access. It
  retains prompt admission, minimized child environments, exact provider/model
  pins, hard wall/CPU/memory/task/open-file/file-size limits, descendant cleanup,
  bounded persisted output, staged submissions, and independent review, but
  makes no credential-blindness, host-filesystem-containment, or
  network-isolation claim. The systemd cgroup `TasksMax` limit is authoritative
  for process/thread creation by the provider tree. `RLIMIT_NPROC` is
  intentionally not used because Linux applies it across every process sharing
  the provider's real user id, which could disrupt or be consumed by unrelated
  services on the same account.
- The `strict-isolated` profile requires an allowlisted filesystem view, a
  credential-blind provider transport, private prompt delivery, bounded
  CPU/memory/process/file resources, and endpoint-constrained egress. Until the
  complete boundary exists, that profile fails closed before process creation
  and never falls back to `trusted-local`.
- `loop_state.json`, `budget.json`, `iterations.jsonl`, and `recovery.md` remain
  supported for existing ARL consumers.
- Provider independence is determined by normalized model family, so aliases
  such as CodeWhale and DeepSeek do not count as independent reviewers.
  Configurable routes count only when the host launch pins their upstream;
  Kimi and generic multi-provider gateways remain unverified.
- Executable/upstream/model/closure attestation is a trusted operator assertion
  checked immediately before spawn. It is not cryptographic proof that a remote
  service honors the asserted identity.
- Existing v1 loops remain on legacy behavior until migration applies
  successfully. Installing the new runtime alone does not mutate them.

## Authoritative State

| File | Schema | Authority |
|---|---|---|
| `goal_contract.json` | `goal_contract.v2` | Main goal, success criteria, scope, insufficient-result rules, and the evidence-backed obligation DAG. |
| `approach_registry.json` | `approach_registry.v2` | Campaigns, approaches, eligibility, dependencies, estimates, blockers, evidence, and reopen conditions. |
| `current_plan.json` | `current_plan.v2` | The single selected campaign/approach, bounded next action, target obligations, scope lock, falsifier, compute policy, horizon, and revision pins. |
| `direction_decisions.jsonl` | `direction_decision.v2` | Append-only initialization, migration, selection, revision, and outcome provenance. |

`loop_state.goal`, `loop_state.success_criteria`,
`loop_state.next_preferred_path`, `loop_state.goal_focus_projection`, and the
Goal Focus managed block in `recovery.md` are compatibility projections. They
must be regenerated from the authoritative files and must never override them.

Transient state is explicit:

- `iteration_candidate.json` contains the one pending primary result.
- `iteration_dispatch.json` contains one host-pinned in-flight dispatch intent
  (provider family, exact plan fingerprint/revisions, campaign, approach, and
  candidate id) and is atomically consumed by staging.
- `candidate_quarantine.json` is a fixed non-reviewable tombstone for the exact
  candidate or dispatch observed after a failed host completion gate. Creation
  is compare-and-swap serialized with staging; dispatch, staging, finalization,
  retry, and failover remain blocked until exact-fingerprint operator release.
- `.goal_focus/candidates/<candidate-id>.json` archives finalized candidates.
- `.goal_focus/quarantined_candidates/<quarantine-id>.json` archives explicitly
  released failed-completion tombstones without changing ledger or budget.
- `.goal_focus_transactions/` contains recoverable write-ahead transaction
  journals.
- `.goal_focus.lock` serializes control-state writers.

## Goal-Focus Governance Modes

| Mode | Behavior |
|---|---|
| `off` | Goal Focus v2 does not govern dispatch. Legacy loop behavior remains available. |
| `monitor` | Validate and report drift and replan signals without enforcing an active-plan or review-before-bank gate. It preserves legacy dispatch/banking behavior and is observational only. |
| `enforce` | Require recovered, coherent state and a reviewed, active, unexpired plan before primary dispatch. A pending candidate or replan trigger blocks new research dispatch. |

New v2 loops default to `enforce`. Unmigrated v1 loops retain their prior
`goal_priority` defaults and semantics.

Provider execution is selected separately with
`AAS_AUTOLOOP_PROVIDER_TRANSPORT`:

| Profile | Behavior |
|---|---|
| unset / `strict-isolated` | Deny real enforce-mode primaries and real external panels until the complete hostile-provider transport exists. |
| `trusted-local` | Permit explicitly pinned local provider CLIs in a dedicated trusted project. Goal-Focus state, staging, review, notification gates, and mandatory host resource limits remain enforced. |

Profile selection is operator launch configuration. Invalid or omitted values
never enable `trusted-local`, and strict isolation never downgrades implicitly.

## Direction Selection Contract

1. Filter out closed, blocked, parked, stale, compute-forbidden, or
   dependency-ineligible approaches.
2. Obtain structured `strategy_advice.v1` from the host-owned strategy-review
   panel. Advice must expose inspected and uninspected evidence, interval
   estimates, falsifiers, objections, and one bounded next action. The host
   snapshots the complete goal contract, approach registry, and current plan
   shown to reviewers, including both semantic fingerprints and exact source
   hashes; a commit is valid only against that same authority snapshot.
3. Score each eligible approach conservatively using the following default
   weights:

   ```text
   +5 goal resolution contribution
   +3 information gain
   +2 option value
   +1 diversity
   -2 execution cost
   -2 verification cost
   -4 bridge debt
   -3 dependency risk
   -1 correlated redundancy
   ```

4. Select a dominant exploitation route only when its conservative lower bound
   exceeds every competitor's optimistic upper bound.
5. If no route robustly dominates, select a bounded discriminating experiment
   and retain a portfolio of at most three mechanism-diverse live approaches.
6. Commit one direction only after structured review passes with a genuinely
   different-family reviewer; this applies to initial selection, retention,
   and campaign/approach switches.
7. Record the decision, estimates, dissent, evidence gaps, revision pins, and
   trigger in `direction_decisions.jsonl`.

The strategy brief must show the complete current plan together with the goal
contract and approach registry. Hiding the incumbent would prevent reviewers
from evaluating switching cost, detecting stale plan text, and proving that
they inspected the exact authority they bind. Anti-anchoring is instead an
explicit instruction: prior effort, active status, or list order is not
positive evidence and gives the incumbent no presumption or tie-break benefit.

## Replan Triggers

The pre-dispatch gate must evaluate at least:

- missing, non-active, stale, or revision-inconsistent plans;
- selected approaches that are missing or no longer eligible;
- plan-horizon expiry or stale approach estimates;
- explicit trip wires or new unreviewed counterevidence;
- substantive structured-panel dissent;
- a pending result review;
- three finalized iterations without global obligation reduction; and
- three consecutive scope-only or `encoding_only` iterations.

In `enforce` mode, any unresolved trigger prevents a new primary research
dispatch. Operational reviewer unavailability leaves the loop waiting; it does
not justify silently reverting to the stale route.

## Iteration State Machine

```text
recover transaction journals
  -> reconcile compatibility projections
  -> validate revisions and plan eligibility
  -> structured strategy review/replan when required
  -> persist exact host dispatch intent
  -> dispatch exactly one bounded primary action
  -> host validates bounded completion and stages iteration_candidate.json
     OR atomically tombstones any candidate/dispatch left by a failed gate
  -> structured result_review.v1 by a different provider family
  -> atomically finalize accepted or rejected work
  -> notify from finalized state
```

### Pre-dispatch

- Finish any interrupted write-ahead transaction idempotently.
- Regenerate stale managed projections from `current_plan.json`.
- Refuse dispatch when authoritative state is invalid, a candidate is pending,
  or a mandatory replan has not produced a reviewed active plan.
- Pin the dispatched action to the reviewed primary-provider family, candidate
  id, and canonical full-object fingerprints of the current plan, goal
  contract, and approach registry before the worker launches. Provider failover
  or same-revision authority mutation forces a new strategy review/failure.
- An interrupted in-flight dispatch is never silently rerun. Status exposes its
  exact id; after confirming the original worker is gone, an operator may use
  `goal-focus recover-dispatch --cancel --dispatch-id <exact-id>`.

### Stage

- A provider exit does not bank a result.
- Enforce mode accepts only a known provider family, not `--cmd` or an
  unverified multi-family gateway. Provider-specific command, argument, binary,
  and short-alias overrides are also rejected because a logical provider name
  cannot attest the process they substitute. The exact operator-pinned model is
  injected into the launched argv and must match the reviewed attestation.
- Under `trusted-local`, the primary is expected to follow the host-mediated
  submission contract from the dedicated project root. The CLI is trusted with
  host-visible project and credential access; its proposed result is not
  trusted and cannot be banked directly. Under `strict-isolated`, the primary
  additionally cannot import the authority core or write the loop directory
  and may write only the candidate evidence area and one reserved submission.
  After exit, the host descriptor-opens that submission without following
  links, validates its schema/run/dispatch/candidate ids, exact bytes, compute
  provenance, and secret boundary, then stages `iteration_candidate.json` and
  securely removes/quarantines the submission. Direct staging, a missing
  intent, or a stale id fails without mutation.
- A second candidate cannot be staged while one is pending.
- Every material proposed result has a unique claim id, compute use is explicit
  (including explicit no-compute), and reported services must satisfy the
  reviewed allow/deny policy. Each material claim also names at least one safe,
  opaque single-component evidence id under
  `.goal_focus/evidence/<candidate-id>/`; hidden/path-like/sensitive names are
  rejected. An identifier alone is not evidence: the host opens each declared
  private single-link regular UTF-8 artifact without following symlinks,
  enforces individual/aggregate size bounds, and embeds its complete content,
  size, source path, and digest into the immutable candidate shown to reviewers.
- Claude and Codex trusted-local primaries receive prompts through stdin rather
  than argv/environment/temp files. Trusted-local panels use stdin where their
  CLI supports it; a provider such as CodeWhale whose one-shot interface
  requires argv is explicitly recorded as an argv transport. Every trusted-local
  provider is placed in a dedicated systemd scope with hard memory, swap,
  task-count, CPU-throughput, and runtime limits and inherits POSIX limits for
  address space, cumulative CPU time, open files, file size, and core dumps.
  A root-owned Python gate verifies the actual scope cgroup leaf and inherited
  limits before it execs the provider. The provider then enters a PID/cgroup
  namespace with the cgroup API and known host process-launch control sockets
  (including the live user-manager, container, tmux, and Screen paths) masked.
  The scope's cgroup `TasksMax` is the process/thread-count boundary;
  `RLIMIT_NPROC` is deliberately omitted because it is per-real-UID rather than
  per-provider-tree. Invalid limits or an unavailable limit backend deny
  execution before provider spawn. Bounded combined output and per-file
  `RLIMIT_FSIZE` are enforced, but no aggregate disk-quota claim is made.
  Credential-blind delivery, an allowlisted research
  snapshot, hard scratch isolation, and exact-endpoint network brokering remain
  requirements only for `strict-isolated`.

### Independent result review

- Review output must validate as `result_review.v1`.
- Every review binds the canonical fingerprint of the complete pending
  candidate, not only its candidate id.
- The reviewer family must differ from the primary executor family.
- `safe_to_bank` is true exactly for a passing verdict.
- A passing review must identify inspected paths, review every proposed claim
  as supported with exact claim-id coverage, and report no failed machine check.
  Every supported claim and accepted obligation review must cite at least one
  evidence id present in the staged candidate and in that reviewer's own
  `inspected_paths`; obligation reviews must also name the exact requested
  target status.
- Each passing reviewer must cover the entire exact requested claim and
  obligation set; the host cannot combine disjoint partial coverage into
  apparent unanimity.
- The final banking boundary revalidates every embedded `result_review.v1`,
  including `safe_to_bank`, machine checks, exact candidate fingerprint,
  exact claim/obligation coverage, and that every cited staged evidence id is
  present in that reviewer's own `inspected_paths`; an ad hoc or outer summary
  pass cannot bypass the raw review contract.
- Conflicting candidate ids/fingerprints, any fail verdict, or material dissent
  prevents acceptance.

### Atomic finalize

- Finalization rechecks candidate content and full authority fingerprints, then
  uses hash plus revision compare-and-swap for every authoritative input.
- Panel provider processes use verified prompt-only launch shapes with model
  tools and custom instructions disabled. On Linux, a sealed wrapper hides the
  project and user home and exposes only a revalidated provider closure and a
  descriptor-copied minimum credential. Exact upstream/model pins are launch
  arguments, not labels. Missing verified prompt-only isolation fails closed.
  Host prompt inputs use bounded
  no-follow reads; semantic decisions bind the in-memory response digest rather
  than mutable raw transcript files. Host artifact writes reject symlinked
  directories and atomically replace leaf symlinks without following them.
- One recoverable transaction writes the ledger record and updated control
  post-images, archives the candidate, and removes the pending file.
- The transaction journal makes interrupted application idempotently
  recoverable. JSONL event ids prevent duplicate appends. Transaction targets,
  their parent directories, locks, preimages, and postimages are opened with
  no-follow traversal so a symlinked nested target cannot escape the loop.
- Compatibility projections are reconciled after the authoritative commit.

If result review is unavailable or returns an operational error, the candidate
remains pending, no finalized-attempt budget is charged, and no new primary
iteration launches. Only a substantive failed/rejected review finalizes the
candidate as rejected.

## Rejected-Work Semantics

A substantively rejected candidate is an auditable attempted iteration, not
research progress:

- It is finalized with `bank_status: rejected` and the review findings.
- Its real iteration, token, and USD deltas are charged to the budget; duration,
  executor, and compute provenance remain on the rejected ledger record.
- Claim ids, claims, obligation transitions, and claim-support projections are
  cleared.
- `goal_contribution`, `campaign_delta`, and `global_delta` are `none`.
- The plan moves to `needs_replan` unless an explicit reviewed post-image says
  otherwise.
- Notifications report failure/rejection and must not say the work was banked.

## Progress Semantics

Every accepted finalized iteration records two distinct progress axes:

- `campaign_delta`: `none | incremental | substantial | closed`
- `global_delta`: `none | reduced | satisfied`

`global_delta` requires an evidence-backed transition of a named bridge or
terminal obligation. Work under `scope_lock: encoding_only` is campaign-local
unless it discharges such an obligation. Finite samples, special cases,
uncertified counterexamples, and elegant reductions are useful artifacts, but
they are not automatically goal resolution.

Obligation dependencies are authoritative: a transition cannot skip an open
predecessor. Success criteria are conjunctive by default, so `global_delta:
satisfied` means every criterion obligation is satisfied/closed after the
reviewed transition set. The host derives termination from that post-state: a
worker `stop` without goal satisfaction is normalized to continuing/replan,
while reviewed goal satisfaction stops even if the worker requested continue.

## Notify v2 Contract

Each externally emitted event uses schema `aas.autoloop.notify.v2` and renders
the following mandatory human-readable blocks:

1. `Goal` — the main problem being solved.
2. `Completed` — what was actually finalized; explicitly say when nothing was
   banked.
3. `Current` — where the research now stands and the plain-language outcome of
   the current event.
4. `Plan` — the next bounded action or reason the loop is waiting.

Every rendering also includes:

- a research-specific title and stable topic slug;
- iteration, review, and loop status;
- iteration-budget and goal-progress summary;
- executor provider plus structured agent provenance: the driver actually used,
  panel agents that returned usable work, and any other participating agents;
- structured compute provenance;
- finish time and duration for terminal iteration events.

Compute provenance distinguishes explicit no-compute (`reported: true`, empty
run list) from legacy/unreported compute (`reported: false`). Each run records a
service (`local`, `hetzner`, `kaggle`, `modal`, `github-actions`, or a safe
`other:<slug>`), status, and optional job reference/timing. It must never be
inferred from prose.

Agent provenance follows the same evidence discipline. Each driver, panel, and
other group records `reported` plus normalized agent records. An explicit empty
agent list means that role was not used; `reported: false` means legacy or
unreported. Panel notifications name actual usable responders, not merely the
configured invite list.

Markdown, plain text, bounded Telegram HTML, and compact renderers must retain
all mandatory fields. Exact delivery identity is based on the structured event
and rendered body. A second semantic retry fingerprint excludes only event ids
and timing drift, so timestamp-rebuilt retries serialize one cross-process
check-send-remember critical section while materially changed status, content,
agent, or compute outcomes remain deliverable. A delivery is remembered only
after the transport reports success. Configured transport secrets and common
credential forms are recursively redacted from every nested string in the event
before rendering, transport results, fingerprinting, or dedupe persistence.
This includes unrendered extension fields and event ids; a secret-bearing event
id is replaced by a deterministic opaque digest-derived id. Transport endpoints
require HTTPS, reject URL credentials and redirects, and permit localhost HTTP
only behind an explicit development opt-in.
Common explicit PII forms are also redacted from notification values, including
email, phone, government ids, addresses/DOB, and labeled participant/patient/
subject data. The heuristic is a conservative last-mile safeguard, not a claim
of complete personal-data detection.

In enforce mode, configured credentials and `--notify auto` do not by themselves
authorize external disclosure. Network delivery additionally requires
`AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS=allow`; absent consent, the host records
only local audit. External strategy/result review similarly requires
`AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS=allow`, which explicitly authorizes the
complete bounded authority brief and candidate evidence snapshot to leave the
host for selected reviewers.
Because silent redaction could alter research evidence, a panel prompt with a
PII finding is refused unless
`AAS_AUTOLOOP_EXTERNAL_PII_APPROVAL_SHA256` matches the SHA-256 of the exact
outbound prompt bytes. Findings expose categories only, never matched values.

## Migration and Compatibility

Migration from `goal_priority.v1` is explicit and non-destructive:

1. Run `goal-focus migrate --dry-run` to inspect the proposed contract,
   registry, plan, provenance, dynamic campaign signals, and whether apply is
   allowed.
2. If current path, recovery, latest finalized ledger, and hard-replan audit
   disagree, report ambiguity and refuse to guess. Apply may preserve that as
   an unselected `needs_replan` plan, but enforce mode cannot dispatch until a
   structured strategy review or an explicit reviewed active-campaign
   selection resolves it.
3. Run `goal-focus migrate --apply` only after the proposal is accepted. Apply
   atomically claims the loop before checking the registry and holds that claim
   through validation/commit. Current-runtime drivers check before and after
   registration, remove a raced registration, and refuse to start while the
   claim exists. Apply also refuses a pre-existing live driver; a safely parsed
   claim owned by a dead local PID may be reclaimed.
4. Snapshot exact bytes and presence/absence for `goal_priority.json`,
   `loop_state.json`, `budget.json`, `iterations.jsonl`, `recovery.md`, and the
   hard-replan audit. The proposal and UUID-suffixed `.goal_focus_backups/`
   postimages are derived from that same snapshot.
5. Create the v2 files, exact byte backups, a backup manifest, and a migration
   decision transactionally. The decision durably records the manifest path,
   manifest digest, source inventory/digests, transaction id, and restore
   instructions. Reconcile the legacy projections, then validate before
   restart. The transaction compares
   every legacy source hash or absence and every absent v2 target before any
   write; concurrent mutation returns `source_changed` with no partial v2 state.

Legacy compatibility rules:

- Unmigrated loops keep v1 behavior (`enabled: false`, `discipline_mode: soft`
  unless explicitly configured otherwise).
- Existing flat notification consumers continue to receive compatibility
  fields while the structured v2 envelope is authoritative.
- Legacy events can be upgraded without inventing compute provenance or a
  banked result.
- `loop_state.json` and `recovery.md` remain readable by older supervisors, but
  v2 never treats edits to their managed projections as authoritative.

## Interfaces

- `canonical/runtime/skills/autonomous-research-loop-runtime/goal_focus.py`
- `canonical/runtime/skills/autonomous-research-loop-runtime/state_transaction.py`
- `canonical/runtime/skills/autonomous-research-loop-runtime/notify_v2.py`
- `canonical/runtime/skills/autonomous-research-loop-runtime/panel_parent.py`
- `canonical/runtime/skills/autonomous-research-loop-runtime/autonomous_research_loop_runtime.py`
- `canonical/runtime/skills/remote-bridge/remote_bridge.py`
- `canonical/runtime/skills/autonomous-research-loop-runtime/arl_drive_supervisor.sh`
- `canonical/skills/autonomous-research-loop/SKILL.md`
- `canonical/skills/autonomous-research-loop-runtime/SKILL.md`
- `canonical/templates/goal-focus.md`
- `manifest/{skills,artifacts,runtime}.yaml`
- `tests/test_goal_focus.py`
- `tests/test_goal_focus_state_machine.py`
- `tests/test_panel_parent.py`
- `tests/test_arl_notify.py`
- `tests/test_remote_bridge.py`
- `tests/test_autonomous_research_loop.py`

## Acceptance Criteria

- New v2 loops can initialize in `off`, `monitor`, or `enforce` mode; new
  goal-focused runs default to `enforce`.
- Enforce mode cannot dispatch with missing, stale, inconsistent, unreviewed,
  expired, or pending-candidate state.
- Strategy advice and result review reject unstructured or schema-invalid
  responses.
- Same-family aliases cannot satisfy the independent result-review gate.
- A primary result is staged before review and reaches `iterations.jsonl` only
  through atomic finalization.
- Rejected work consumes its actual budget and banks no claim or goal progress.
- Interrupted multi-file transactions recover idempotently and stale writers
  fail compare-and-swap.
- Migration defaults to dry-run, never selects from ambiguous direction
  signals, preserves ambiguity as `needs_replan` when applied, retains backups,
  rejects a live driver or changed source snapshot, and validates after apply.
- Notify v2 renders Goal, Completed, Current, Plan, status, progress, executor,
  driver/panel/other-agent provenance, compute, and finished information across
  all transports.
- Legacy v1 loops and flat notification consumers remain functional until
  explicitly migrated.
- Focused and full repository tests, runtime smoke, docs checks, installer
  lifecycle checks, and fresh-context review pass before active-loop rollout.
- `enforce + trusted-local` reaches a real strategy review, primary execution,
  staged candidate, different-family result review, atomic finalize/reject, and
  Notify v2 event without bypassing any Goal-Focus authority gate.
- Every trusted-local primary and panel subprocess is denied unless validated
  wall/CPU/memory/swap/task/address-space/process/open-file/file-size/core/output
  restrictions are installed, recorded, and inherited by descendants.
- `enforce + strict-isolated` denies before provider/reviewer spawn while its
  transport is unavailable, and never falls back to trusted-local.

## Verification

- Unit contracts:
  `python3 -B -m unittest tests.test_goal_focus tests.test_goal_focus_state_machine tests.test_panel_parent tests.test_arl_notify -v`
- Runtime and compatibility:
  `python3 -B -m unittest tests.test_autonomous_research_loop tests.test_remote_bridge -v`
- Full suite: `python3 -B -m unittest -v`
- Installer metadata: manifest validation, docs check/generation, runtime smoke,
  and fake-root lifecycle checks for supported targets and platform shapes.
- Rollout rehearsal: migrate copies of representative v1 loops, validate,
  inject a transaction interruption, recover, and confirm no duplicate ledger
  row.
- Fresh-context code, test, and security review before cutover.

## Risks

- A stale compatibility view may mislead old tools unless every authoritative
  commit reconciles it.
- Overly permissive panel fallback could turn operational reviewer failure into
  unreviewed banking; enforce mode must fail closed at that boundary.
- Incorrect provider-family mapping could create false independence. Enforce
  mode therefore pins configurable upstreams, rejects process-shape overrides,
  and treats unresolved families as unverified.
- Migration inference can select a stale legacy primary; conflicting dynamic
  signals must remain unresolved rather than guessed.
- Transaction recovery protects state atomicity, not the semantic truth of a
  reviewed claim.
- Different-family LLM review reduces correlated error but does not guarantee
  semantic correctness or prompt-injection resistance. The host guarantees
  immutable candidate bytes, prompt-only/no-tools isolation, exact raw-review
  digests/fingerprints, complete claim/evidence coverage checks, and fail-closed
  operational errors; material claims still require deterministic machine
  checks or human approval when the application needs a hard semantic guarantee.
- A trusted-local provider can read or modify host-visible files, use available
  provider credentials, and access shared networking. Mitigations are a
  dedicated trusted project, scoped provider accounts, backups, a minimized
  child environment, prompt/evidence/notification secret gates, exact identity
  pins, timeouts, descendant cleanup, and reversible rollout. These mitigations
  are not a hostile-process containment claim.
- Active-loop cutover can race a live append; migration requires quiescing each
  loop at an iteration boundary and retaining a rollback backup.
