---
name: autonomous-research-loop
description: Run bounded autonomous research iterations with evidence gates, recovery ledgers, and optional cross-agent handoffs; prefers host-owned multi-agent panel with single-path drive primary; scripted force-loop defaults (Goal Focus enforce, hard goal_priority, notify ON). Use when the user asks to continue research autonomously, run a research loop, integrate autonomous agent loops, or keep improving a research workflow without repeated prompts.
---

# Autonomous Research Loop

Use this skill to run research autonomously while preserving bounded scope,
evidence, recovery state, and explicit stop conditions. It is an orchestration
contract, not an instruction to run indefinitely.

## Core Rule

Every autonomous loop must have:

1. A concrete goal.
2. Success criteria.
3. Hard budgets.
4. A loop ledger.
5. Evidence gates.
6. Recovery notes.
7. Stop conditions.

If any of those are missing, create them before starting the first autonomous
iteration.

## When To Use

Use this skill for:

- Continuing nontrivial research across multiple iterations.
- Running source discovery, synthesis, verification, and revision without asking
  after every step.
- Coordinating bounded subagent research or panel review.
- Integrating autonomous behavior into an existing research workflow.
- Resuming a research loop after interruption or context compaction.

Do not use it for:

- Trivial one-shot lookups.
- User requests that explicitly ask only for a plan or analysis.
- User requests that ask only to investigate, diagnose, review, audit, verify,
  or report, unless the user also explicitly asks for autonomous follow-on work.
- Work that lacks a safe budget or stop condition.
- Blind command execution without inspectable evidence.

## Required Loop Files

For a research workspace, keep these files in the active research directory:

- `loop_state.json`: goal, success criteria, mode, stop flags, current status.
- `budget.json`: iteration, wall-clock, token, cost, depth, and child-agent limits.
- `iterations.jsonl`: append-only record of each loop iteration. The runtime
  shards it at 8 MB into `iterations.1.jsonl`, `iterations.2.jsonl`, … so a
  long campaign never reaches the 16 MB cap every reader enforces. The shards
  plus the live file are **one** ledger: read them in numeric order, then the
  live file, or iteration numbering restarts at the first rotation.
- `recovery.md`: latest resume point, blockers, next safe action, and evidence gaps.

These remain the base ARL ledger and compatibility surface. Goal Focus v2 adds
its four authoritative strategy files described below; it does not replace the
budget or append-only iteration ledger.

If a runtime helper is available, prefer the companion
`autonomous-research-loop-runtime` skill to initialize and validate these files.
If it is not available, create the files manually in the same structure.

## Loop Modes

Choose the narrowest mode that can satisfy the goal:

- `monitor`: check whether new evidence or tasks exist, then stop if nothing
  changed.
- `bounded-research`: search, analyze, verify, and write within declared
  budgets.
- `implementation-support`: inspect code or docs, propose research-backed
  changes, and verify integration assumptions.
- `panel-loop`: use a bounded multi-agent discussion, then synthesize and
  verify the result.
- `recovery`: resume from `recovery.md`, validate state, and continue only from
  the recorded next action.

## Preflight

Before starting an autonomous run:

1. State the scope and material exclusions.
2. Define success criteria in observable terms.
3. Set hard budgets:
   - maximum iterations
   - maximum wall time or user-visible turns
   - maximum child workers
   - maximum source hops or search depth
   - maximum spend or token budget when applicable
4. Define stop conditions. The enforcement policy in
   `canonical/instructions/autonomous-loop-enforcement.md` governs them: user
   requirements override everything, so capture them into
   `loop_state.stop_conditions` at init. When the user gave no overriding
   requirement, the loop stops only on:
   - the required number of loops reached
   - credit or quota exhausted, or a user-set spend cap hit
   - the stated goal fully resolved, confirmed by a machine-checkable success
     check
   - a user message asking to stop, pause, or switch tasks
   Plateau, evidence-gap, and repeated-blocker signals are not terminal under
   these defaults; record them, then downgrade the iteration decision to
   `revise` or `delegate` and continue. They end the loop only when the user set
   them as an explicit stop condition.
5. Initialize or validate the loop files.

The maximum iteration budget is a hard cap, not a target to exceed while
searching for success. A loop may run fewer iterations when success or a true
hard stop condition occurs, but it must never append more than
`max_iterations` records. A normal early `stop` before the final allowed
iteration is valid only when the success criteria are met and the iteration
cites a machine-checkable proof/success artifact. Otherwise continue, revise,
or delegate; do not mark the loop `blocked` early. A self-marked blocker is not
a stop under the enforcement policy: record it and continue. The decision
`blocked` is reserved for the final allowed iteration, when the budget is
exhausted without success.

## Iteration Protocol

Each iteration must record:

- iteration number
- timestamp
- mode
- objective
- evidence checked
- actions taken
- output produced
- remaining gaps
- budget consumed or estimated
- decision: `continue`, `revise`, `delegate`, `stop`, or `blocked`

Only use a continuing decision (`continue`, `revise`, or `delegate`) when the
next iteration has a concrete objective and remaining budget. The final allowed
iteration must be terminal (`stop` or `blocked`); if success criteria have not
been satisfied by then, stop as budget exhausted instead of leaving the loop
`running`. Before the final allowed iteration, `stop` must mean success/proof
found and must cite at least one evidence id that resolves to a proof artifact,
and `blocked` is not accepted: record the blocker and continue with `revise` or
`delegate`.


### Heavy computation inside iterations

Route heavy computation (exhaustive enumeration, certificate suites, censuses)
through the unified broker exposed by `modal-research-compute`. The recommended
order is `local > Kaggle > Modal > Hetzner > GitHub Actions`; a valid custom order
keeps local first and may reorder or omit unique remote lanes, while explicit
backend overrides are hard pins that still pass lane safety gates.

**User-named compute resources are strict.** If the user asks for specific lanes
(e.g. "use Hetzner and Kaggle", "no local dual residual"), encode that on the job as
`policy.backends: ["kaggle", "hetzner"]` (or a single `policy.backend`) and **only**
use listed lanes. Do not fall back to local or other unlisted backends while any
requested lane is still available; only after **all** requested lanes are out of
credits/resources/guards may you block, defer, or ask the user — never silently
expand the set. Do **not** bypass the broker with ad-hoc heavy local processes
while an allowlist is active (see `canonical/instructions/compute-offload-routing.md`
§ User-requested compute resources).

Let the broker's adequacy and self-preservation policy decide among **permitted**
lanes. Use `run plan` as the **routing** decision boundary. Execute a selected
Kaggle or Hetzner lane through its corresponding lane skill (`preflight`, then
Hetzner `oneshot --confirm` or Kaggle `run --confirm`); `run submit` dispatches
only Modal/GitHub Actions. Ensure lane credentials are in the process
environment before lifecycle verbs (Hetzner: `HCLOUD_TOKEN` in env only — never
print or argv). If a strict allowlist reports all lanes exhausted, re-run
**same-bundle lane preflight** with credentials loaded and record
`available`/`budget_verdict`/`reason` before banking a multi-iteration
infrastructure blocker; that recheck is diagnostic and does not widen the
allowlist. See `skills/hetzner-research-compute/references/agent-loop-integration.md`.
When the broker is unavailable **and** the user did not forbid local, use the
throttled local queue defined in
`skills/modal-research-compute/references/local-compute-throttle.md`
(cross-platform: lockfile singleton, idle priority, chunked resumable
checkpoints, load guards). Never launch ad-hoc unthrottled heavy processes. Record run
IDs and quote runner-log lines (not agent summaries) when banking offloaded
results, and treat a red run with zero artifacts as a wiring bug but a red
run with FAIL rows as mathematics to investigate.

## Evidence Gates

Apply the relevant gates before accepting an iteration output:

- Source claims require source IDs or file references.
- Current facts require dated source checks.
- User-facing writing requires `writing-style-settings.md` to be loaded before
  final prose. Mathematical, TCS, graph-theoretic, Lean, or LaTeX writing also
  requires `math-manuscript-style.md`.
- Early proof/success stops require an evidence id backed by a local
  machine-checkable proof artifact, such as
  `proof_artifacts/<evidence_id>.json`, whose checker metadata reports a
  passed check and whose proof file exists in the loop directory.
- Code or workflow changes require local inspection of relevant files.
- Multi-agent conclusions require synthesis that separates agreement,
  disagreement, assumptions, and unresolved questions.
- Recommendations must distinguish confirmed evidence from inference.
- Anti-false-consensus: do not continue review until all approve; require an
  evidence delta between critique rounds; force residual uncertainty on
  unfinished load-bearing claims; multi-LLM LGTM alone never banks (different-
  family result review and/or machine-checkable support required).

If a gate fails, record the failure in `iterations.jsonl` and choose one of:

- retry with a narrower objective
- delegate a bounded check
- revise the scope
- stop as blocked

## Formal tools (optional Lean assist)

Lean formalization is **opt-in** via `formal_policy` (default **off**). When
enabled, formal skills assist **stable lemmas** on a formal-track path; they are
not the default discovery primary under single-path recovery.

| Policy | Effect |
|--------|--------|
| `off` | No formal prompt injection (default). |
| `mention-only` | Short optional blurb. |
| `auto` | Checklist only when a stable formal candidate or formal-track path exists. |
| `on` | Binding F1–F7 block when path formal / stable; else short parked note. |
| `force` | Binding + optional host `formal_force_tick` after ok iterations (hygiene credits; **never** stops the ARL loop). |

**Do not confuse** headless **force-driven ARL** (`drive` unattended) with
`formal_policy=force` (host formal hygiene tick).

Wire with:

```bash
… init --dir <loop> --goal "…" --success-criteria "…" --formal-policy on
… drive --dir <loop> --provider <p> --formal-policy on
# aggressive host hygiene (scan-first; no OpenGauss auto-spawn):
… drive … --formal-policy force --formal-force-after-iteration
```

Env: `AAS_AUTOLOOP_FORMAL_POLICY`, `AAS_AUTOLOOP_FORMAL_PROJECT`,
`AAS_AUTOLOOP_FORMAL_FORCE`, `AAS_AUTOLOOP_FORMAL_TYPECHECK`. File:
`<loop>/formal/formal_policy.json` or `loop_state.standing_orders.formal`.

When the committed path is **formal-track**, use positions F1–F7' from
`informal-to-lean-formalization-runbook` (intake → Explore → F2' library reuse
gate via `lean-research-library search` → skeleton → fill → optional
interactive OpenGauss only → strict gate → fresh review → acceptance → F7'
proposal-only library intake; staging/pushes stay user-gated).
Evidence labels (`lean_declaration_search`, `opengauss_run`, `formal_scan`,
`formal_typecheck`) never alone set claim-support. Host force tick reports are
hygiene only (`claim_support_status=not_evaluated`).

**Default scripted force-loop** (all OS; enforce + hard + notify ON): runtime
pack `autonomous-research-loop-runtime/force-loop/` — discovery template
`arl-scripted-force-loop`. Thin formal-env sample for existing supervisors only:
`canonical/templates/sample-arl-headless-driver-with-formal/`
(`formal_env.inc.sh` — no forked driver). Instruction:
`canonical/instructions/autonomous-loop-formal-policy.md`.

Co-install the `formal-research` profile (or the individual Lean skills) for
Explore / gate / skeleton tooling; `serious-research` alone is insufficient.

## Goal Focus v2: reviewed single-path control

> **Current deployment status:** the state/migration/review contracts and an
> explicit trusted-local execution profile are implemented. Set
> `AAS_AUTOLOOP_PROVIDER_TRANSPORT=trusted-local` only for operator-approved
> provider CLIs in a dedicated project. The runtime then applies mandatory
> cgroup/POSIX resource limits, timeouts, bounded output, and descendant cleanup
> while retaining Goal-Focus staging and independent review. Omission or
> `strict-isolated` remains fail-closed; trusted-local does not claim credential,
> filesystem, or network isolation from a hostile provider process.

Trusted-local is also fail-closed until each enabled provider has exact
`AAS_AUTOLOOP_ATTESTED_BIN_<PROVIDER>`, `...SHA256_...`,
`...UPSTREAM_...`, and `...MODEL_...` pins. Resolve launcher symlinks before
hashing; use upstream `anthropic` for Claude, `openai` for Codex, and `deepseek`
for CodeWhale. Re-pin after provider updates. The runtime companion documents
the optional dependency-root pin, validated `AAS_AUTOLOOP_RESOURCE_*`
overrides, default limits, and the pre-exec cgroup/RLIMIT attestation gate.

For an open problem or long-horizon claim, use Goal Focus v2 when the runtime
companion is available. It turns the main goal, obligation structure, approach
portfolio, and selected next action into revisioned control state instead of
letting stale campaign prose decide what runs next.

### Authoritative files and projections

Goal Focus v2 has four authoritative files in the loop directory, plus a permanent
negative-space ledger:

| File | Authority |
|---|---|
| `goal_contract.json` | Main goal, success criteria, scope, insufficient-result rules, and evidence-backed obligation DAG. |
| `approach_registry.json` | Campaigns, approaches, estimates, dependencies, blockers, and reopen conditions. |
| `current_plan.json` | The one active campaign/approach, bounded next action, target obligations, scope lock, falsifier, compute policy, and revision pins. |
| `direction_decisions.jsonl` | Append-only initialization, migration, selection, revision, and outcome provenance. |
| `.goal_focus/negative_space.jsonl` | Append-only `negative_space.v1` failed explorations / blocked routes; never banks claims. Open rows make an approach ineligible and are permanent for the life of the loop — no automated reopen exists, so continue under a new `approach_id`. |

`loop_state.goal`, `loop_state.success_criteria`,
`loop_state.next_preferred_path`, `loop_state.goal_focus_projection`, and the
Goal Focus managed block in `recovery.md` are compatibility views. Reconcile
them from the authoritative files; never use them to override `current_plan`.

Modes are:

- `off`: v2 does not govern dispatch; legacy behavior remains available.
- `monitor`: report drift and replan signals without enforcing an active-plan
  or review-before-bank gate. It preserves legacy dispatch/banking behavior and
  is observational only.
- `enforce`: require coherent, reviewed, unexpired state before dispatch. A
  pending candidate or unresolved replan trigger prevents new primary work.

New goal-focused v2 loops use `enforce`. Existing v1 loops do not change mode
merely because the runtime is upgraded; migrate them explicitly.

### Pre-dispatch selection and replanning

Before an enforce-mode primary action, the host runtime must:

1. recover any interrupted state transaction;
2. regenerate stale compatibility projections;
3. validate goal, registry, plan, ledger, and revision agreement;
4. block on a pending result review; and
5. run a structured strategy review if the plan is missing, stale, ineligible,
   expired, tripped, disputed, or stalled.

Strategy reviewers return `strategy_advice.v1`, including inspected and
uninspected evidence, interval estimates, falsifiers, strongest objections, and
one bounded next action. They receive the complete goal contract, registry, and
current plan so they can assess switching cost and stale state against the
exact bound authority. The prompt gives the incumbent no presumption or
tie-break advantage; list order, active status, and sunk effort are not positive
evidence. The host filters ineligible approaches and scores the rest
conservatively:

```text
+5 goal resolution +3 information gain +2 option value +1 diversity
-2 execution cost -2 verification cost -4 bridge debt
-3 dependency risk -1 correlated redundancy
```

Select a dominant exploitation path only when its conservative lower bound
exceeds every competitor's optimistic upper bound. Otherwise choose a bounded
discriminating experiment from at most three mechanism-diverse live
approaches. A reviewed selection commits exactly one plan revision, and every
direction commit—including retention or a campaign/approach switch—requires a
genuinely different-family review. The strategy review is bound to the complete
goal/registry/plan objects, their semantic fingerprints, and their exact source
hashes; even a same-revision mutation invalidates the commit.

The guarantee is “best-supported registered direction under the current
evidence, budget, and policy,” not “objectively best direction.” Panel votes are
advice, not mathematical evidence.

### Stage, independently review, then finalize

A provider exit does not bank a research result. In enforce mode the primary
proposes one iteration record, and the runtime stages it as
`iteration_candidate.json`, pinned to the dispatched `plan_revision`.

Before launch, the host writes `iteration_dispatch.json`, binding the candidate
id, known executor family, and canonical full-object fingerprints/revisions of
the plan, goal contract, and approach registry. Enforce-mode staging requires
and atomically consumes that exact live intent. A driver-family failover
forces fresh strategy review; `--cmd`, provider command/argument/binary
overrides, and unverified routing gateways are not valid enforce-mode
primaries. Standard model/reasoning pins remain supported. If a host dies before staging, inspect the
in-flight id with `goal-focus recover-dispatch` and cancel it only after the
original worker is confirmed gone.

If a provider directly staged a candidate but the host completion gate later
failed (including timeout, capture/output rejection, or cleanup failure), the
runtime moves it behind a fixed quarantine marker and the supervisor stops
without review, banking, retry, or failover. Inspect with `goal-focus
recover-quarantine --dir <loop>` and release only with the exact reported
candidate fingerprint; the released quarantine is retained under
`.goal_focus/quarantined_candidates/` for audit.

The host then requests a strict `result_review.v1` from a provider family
different from the primary executor. CodeWhale and DeepSeek are the same family
for this gate and are launched with an explicit DeepSeek upstream pin; Codex
ignores configurable provider defaults and pins the OpenAI provider. Kimi,
unknown routes, and multi-family gateways are unverified until the host can
attest their resolved upstream family. Every material staged result needs a
unique claim id and safe loop-relative evidence path. The host no-follow reads
and size-bounds each regular UTF-8 artifact, then embeds its complete content,
path, size, and digest in the immutable candidate; an identifier by itself is
not evidence. Every review binds the canonical hash of the complete candidate.
A passing review must identify inspected paths, support every banked claim
using matching staged evidence that it actually inspected, accept each exact
proposed obligation target with at least one matching staged evidence id, and
contain no failed machine check. Every passing reviewer must cover the complete exact
claim and obligation set; disjoint partial coverage cannot be combined into a
pass. If review is unavailable, leave the candidate pending
without charging a finalized-attempt budget, and launch no new primary
iteration. Only a substantive failed/rejected review finalizes rejected work.

Finalization is one recoverable compare-and-swap transaction: append the final
ledger row, update control and budget post-images, archive the candidate, and
remove the pending file. Interrupted transactions are recovered idempotently;
candidate or same-revision authority mutation fails rather than merging
incompatible inputs.

Trusted-local real panels are available only through the explicit transport
opt-in described above. Their provider-native prompt-only flags disable shell,
filesystem, MCP, browser, memory, custom-instruction, and subagent tools, while
the host applies executable attestation, resource limits, bounded capture, and
review-schema validation. This profile still trusts the local CLI and its host
view. The stronger strict-isolated design additionally requires an allowlisted
runtime/provider filesystem, immutable interpreter closure, credential
blindness, and endpoint-constrained egress; because that complete boundary is
not yet implemented, strict-isolated/default real panels fail closed before
spawn. Injected runners remain available only for offline contract tests.

Rejected work remains auditable: its real iteration, token, and USD deltas count
against budget, while its duration, executor, and compute provenance remain on
the rejected ledger row. It banks no claim, clears obligation transitions,
records `campaign_delta: none` and `global_delta: none`, and moves the default
plan to `needs_replan`. Never describe a rejected candidate as a completed
research result.

For accepted work, record two separate deltas:

- `campaign_delta`: `none | incremental | substantial | closed`
- `global_delta`: `none | reduced | satisfied`

Global progress requires evidence-backed reduction of a named bridge or
terminal obligation. Work under `scope_lock: encoding_only` remains
campaign-local unless it discharges such an obligation. Special cases, elegant
reductions, finite samples, and uncertified counterexamples are progress to
record, not success stops.

The obligation DAG is enforced: transitions cannot skip open dependencies, and
all success-criterion obligations must be satisfied/closed before the host
declares the goal satisfied. Criterion ids are unique, and a pre-completed node
counts only when it has evidence and completed dependency closure. The host,
not the worker's requested decision,
derives successful termination from that reviewed post-state. Reported compute
services must satisfy `current_plan.compute_policy`; with an active restriction,
unreported provenance cannot be banked. A host-derived early success remains
subject to the loop's machine-checkable proof-artifact gate.

### Migration and legacy compatibility

Use the runtime's `goal-focus migrate --dry-run` before `--apply`. The dry run
compares dynamic campaign signals from the current path, recovery, latest
finalized ledger, and hard-replan audit. If those signals disagree, migration
must report ambiguity and refuse to guess. Apply may preserve the disagreement
as an unselected `needs_replan` plan, but enforce mode cannot dispatch until
structured strategy review or an explicit reviewed active-campaign choice
resolves the direction.

Apply creates a timestamped `.goal_focus_backups/` copy of the legacy control
files before transactionally creating v2 state, then reconciles and validates
the compatibility views. Apply refuses when the loop registry identifies a live
owning driver. Its proposal, backup postimages, and commit share one exact
snapshot of every legacy input (including the hard-replan audit); hash/absence
compare-and-swap rejects concurrent mutation with no partial v2 state. Pause a
live loop at an iteration boundary before migration.

Unmigrated `goal_priority.v1` remains supported through
`{loop_dir}/goal_priority.json` or
`loop_state.standing_orders.goal_priority`. Its executable defaults are
`enabled: false` and `discipline_mode: soft`. When explicitly enabled, it adds
goal-EV advice, campaign prompt text, and warnings, but does not provide v2's
dispatch or review-before-bank guarantees. Opt out with `"enabled": false` or
`AAS_AUTOLOOP_GOAL_PRIORITY=off`; env `on` forces enable only when a v1 config
object exists.

The v1 contract remains available as `canonical/templates/goal-priority.md`
and `canonical/templates/goal-priority.example.json`. The v2 contract is
`canonical/templates/goal-focus.md`. Use `decision-doubt-loop` for a
load-bearing close, reopen, or switch decision outside the host strategy-review
path.

## Multi-Agent Use

### Recommended hybrid (parent-owned panel + single-path worker)

For unattended loops that need multi-agent advice/review, prefer the **host
parent** model owned by `autonomous-research-loop-runtime`:

1. **Goal Focus strategy review** — when v2 requires a plan, top-level provider
   CLIs return structured `strategy_advice.v1`; legacy loops continue to use
   `target_advice`.
2. **Drive primary** — exactly one provider runs the reviewed bounded action; it
   must not nest panel CLIs under its sandbox.
3. **Stage** — successful primary output becomes a pending candidate, not a
   banked ledger row.
4. **Different-family result review** — a host-owned reviewer returns strict
   `result_review.v1` for that candidate.
5. **Atomic host finalize** — accept or reject through the transaction layer,
   then notify from finalized state.

Enable with `drive --panel on|auto|off`, `panel.json`, `loop_state.standing_orders.panel`,
or `AAS_AUTOLOOP_PANEL=on`. Run `… panel --smoke` to probe providers. When
`goal_priority` is active, target advice should rank by goal EV (see above).
Panel provider budgets use **adaptive timeouts** by default (prompt size,
provider multipliers, recent elapsed history, hard max); set
`"timeout_mode": "fixed"` in `panel.json` for legacy flat caps.

### Provider credit / usage limits

Agent CLI credit exhaustion (Codex/Claude/Kimi usage limits, rate limits) is
**operational**, not a research stop. Follow instruction
`provider-credit-quota.md`:

- Set `exclude_until_credit` (or shrink `providers`) in `panel.json` /
  `standing_orders.panel` so exhausted providers are not re-invited.
- If the **drive primary** is exhausted, restart `drive --provider <funded>`
  rather than leaving `quota_wait` spinning when a fallback primary exists.
- Cross-agent packets: parent re-targets the recipient; do not embed billing
  recovery (see `cross-agent-delegation`).

Do **not** treat nested “primary agent shells out to four CLIs” as the architecture
(that failed under Codex `workspace-write` sandboxes). For heavy strategy pauses,
use `agent-group-discuss` instead of per-iteration AGD.

When using subagents outside that driver path:

1. Create bounded task packets with objective, evidence required, exclusions,
   and expected output.
2. Limit child workers to the budget in `budget.json`.
3. Require each child result to report inspected and uninspected evidence.
4. Synthesize child outputs before making decisions.
5. Do not let child agents recursively start unbounded cross-provider trees.

For panel-style discussion outside ARL drive, pair this skill with
`agent-group-discuss` or `prose` when available.

## Recovery

After every material iteration, update `recovery.md` with:

- current goal
- last completed iteration
- current status
- next safe action
- remaining evidence gaps
- active blockers
- budget remaining

On resume, read `loop_state.json`, `budget.json`, `iterations.jsonl`, and
`recovery.md` before acting. Validate state before continuing.

## Truly Autonomous Execution

A chat session cannot carry a long loop by itself: context windows and turn
boundaries end it. For unattended multi-day runs, prefer the **force-loop kit** (default pins:
Goal Focus enforce, goal_priority hard, notify auto/on; Linux/macOS/Windows/WSL):

```bash
# --policy-file (or AAS_FORCE_LOOP_POLICY_FILE) names the owner-private host policy
... force-loop/run_force_loop.sh bootstrap --loop <loop_dir> --root <project> --profile formal --goal "…" --policy-file <abs_path>
... force-loop/run_force_loop.sh start --loop <loop_dir> --root <project> --provider <claude|codex|…> --policy-file <abs_path>
```

Raw headless `drive` remains available; it respawns a fresh agent session per
iteration against on-disk loop files and owns stop conditions:

```bash
... run_autonomous_research_loop.sh drive --dir <loop_dir> --provider <claude|codex|deepseek|opencode|copilot|antigravity|grok|kimi>
# notify defaults to --notify auto (remote-bridge when secrets configured)
# enforce-mode external delivery also requires AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS=allow
# silence with --notify off or AAS_AUTOLOOP_NOTIFY=off
```

`agent-cmd --provider all --dir <loop_dir>` prints the per-target iteration
commands and probes binary availability. The driver captures per-iteration
logs, re-checks the stop conditions every cycle, and treats detected
credit/quota outages as pause-and-wait (not failure), resuming when credits
return. **Progress notify** uses the `remote-bridge` skill by default when
Zulip/Telegram secrets exist (`--notify auto` on `arm`/`drive`/`watch`);
failures to send never stop the loop. Interactive sessions on Claude are
additionally governed by the installed `hooks.Stop` entry while a loop is
armed (`arm --dir <loop_dir> --root <project_root>`): the hook blocks turn-end
until a real stop condition fires. Kill switches in both modes:
`touch <loop_dir>/STOP_REQUESTED` and `touch <loop_dir>/PAUSE`.
`AUTOLOOP_DISABLE=1` and `disarm` govern the interactive Stop hook only; neither
stops a running headless `drive` or supervisor.

For an enforce-mode loop, finding configured credentials does not itself grant
network egress. `AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS=allow` is required before
the host sends a notification; without it the runtime records only the local
notification audit/result. External strategy/result panels likewise require
`AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS=allow`. Enabling panel egress authorizes the
complete bounded authority brief and staged evidence snapshot to leave the
host for each selected reviewer.

## Notify v2 operator contract

Progress notifications are operational summaries, not evidence and not a
substitute for result review. New structured events use
`aas.autoloop.notify.v2` and must make the research state understandable without
opening the ledger:

- **Goal** — what main problem the loop is solving.
- **Completed** — what was actually finalized, or an explicit statement that
  nothing was banked.
- **Current** — where the research stands now and the event's plain-language
  outcome.
- **Plan** — the next bounded action or why the loop is waiting.

Every rendering also carries a research-specific title/topic, iteration/review/
loop status, iteration-budget and goal-progress summary, executor, structured
driver/panel/other-agent provenance, structured compute provenance, and finish
time/duration for terminal iteration events. Panel provenance lists agents that
actually returned usable work, not the configured invite list. For every agent
group, an explicit empty list means none was used; `reported: false` means
legacy/unreported.
Compute is explicit: an empty reported run list means none was used;
`reported: false` means legacy/unreported. Do not infer Hetzner, Kaggle, Modal,
GitHub Actions, local, or another service from prose or paths.

Before delivery, dry-run output, transport results, fingerprinting, or dedupe
persistence, configured transport secrets and common credential forms are
redacted recursively from every string value in the event envelope, including
extension fields and event ids. A redacted event id is replaced by a stable
opaque digest-derived id. Redaction retains provider/model/family/role and
compute service/status provenance when those values are non-sensitive.
Common explicit PII forms (email, phone, government id, address/DOB, and
labeled participant/patient/subject data) are likewise redacted. This is a
last-mile guard, not a complete personal-data classifier.

The runtime keeps flat compatibility fields while structured consumers migrate.
Markdown, plain, bounded Telegram HTML, and compact renderings all retain the
mandatory fields. Notification delivery is best-effort and never changes loop
truth. Timestamp-rebuilt but materially equivalent retries share a short-lived
semantic identity and a cross-process check-send-remember lock; materially
changed status, content, agent, or compute fields do not. The runtime records
delivery state only after the selected transport reports success.
Remote endpoints require HTTPS, reject URL credentials and redirects, and
permit localhost HTTP only with its separate explicit development opt-in.
External panel prompts are not silently rewritten because that could change
evidence meaning: detected PII instead blocks the call unless the operator has
approved the SHA-256 of those exact outbound bytes via
`AAS_AUTOLOOP_EXTERNAL_PII_APPROVAL_SHA256`.

## Stop Rules

These rules are governed by the enforcement policy in
`canonical/instructions/autonomous-loop-enforcement.md`. User requirements
override everything; the conditions below are the defaults used only when the
user set no overriding requirement.

Stop immediately and report status when:

- success criteria are satisfied, confirmed by a machine-checkable success check
- any hard budget is exhausted, including credit, a spend cap, wall clock, or
  the iteration count
- the user asks to pause, stop, or switch tasks

A repeated blocker, an unresolved evidence gap, or a next action that would
exceed the approved scope is not, by itself, a stop under these defaults. Record
it, choose an in-scope action, and continue, downgrading the iteration decision
to `revise` or `delegate`. Such a signal ends the loop only when no in-scope
action remains and it also satisfies one of the conditions above, or when the
user set it as an explicit stop condition.

When stopping, report:

- status: complete, stopped, or blocked
- iterations completed
- evidence inspected
- remaining unchecked items
- next recommended action

## Output Contract

For user-visible summaries, use this compact shape:

```text
Scope: ...
Status: ...
Evidence Checked: ...
Iterations: ...
Decision: ...
Remaining Gaps: ...
Style: ...
Next Action: ...
```

If material evidence remains unchecked, explicitly say `incomplete analysis`
before the provisional recommendation.
For finalizable prose artifacts created during the loop, record
`style_profile_ref`, `active_overlays`, `active_requirement_ids`, and
`style_applied` in the loop ledger or artifact-adjacent style record. Do not
count a bare `style_applied: true` value as force-use evidence.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
- `autonomous-research-loop-portfolio-runbook` -- Open-problem, portfolio-first variant of the autonomous research-loop runbook: a rigorous definition-of-done with an insufficient-result disqualification list, an approach registry with blocked-route discipline, and an adversarial audit gate with a concrete-deliverable requirement, keeping the same four stop conditions, cross-agent verification, fresh-agent backtracking, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
- `arl-scripted-force-loop` -- discovery for the **default** scripted force-loop kit (runtime `force-loop/`).
- `goal-focus` -- Goal Focus v2 authoritative-state, selection, review-before-bank, migration, and notification contract.
- `goal-priority` -- legacy `goal_priority.v1` reference (defaults disabled/soft; does not change stop conditions).
- `informal-to-lean-formalization-runbook` -- F1–F7 formalization positions when path is formal-track under `formal_policy`.
- `sample-arl-headless-driver-with-formal` -- thin formal-env layer only (not the force-loop default).

When `formal_policy` is `auto`, `on`, or `force`, wire `lean-research-library` at F2' (search-first) and F7' (user-gated intake); staging and outward-facing actions batch at run boundaries and always wait for the user.
