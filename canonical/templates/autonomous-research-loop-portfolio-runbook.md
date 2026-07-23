# Autonomous Research Loop Portfolio Runbook

Use this template to attack an **open or hard research problem** across bounded
iterations until a stop condition fires. It is the open-problem variant of
`autonomous-research-loop-runbook`: where that runbook commits to a single
highest-probability path from the start, this one presumes a **diverse
multi-approach portfolio**, adds a rigorous definition-of-done, an approach
registry with blocked-route discipline, and an adversarial audit gate. It keeps
the same operational spine: the four stop conditions, cross-agent verification,
fresh-agent backtracking, and five-lane broker-routed heavy-compute offload with
per-lane safety gates.

Prefer this runbook when the problem is genuinely open, no single approach is
obviously correct, or premature convergence on an "attractive but incomplete"
reduction is the main risk. Prefer the single-path
`autonomous-research-loop-runbook` when one approach is clearly dominant and
cheap to verify. Within this runbook you may still collapse to single-path
exploitation, but only after recording a dominance justification (see Approach
Triage).

It composes the `autonomous-research-loop` skill for orchestration policy and
`autonomous-research-loop-runtime` for ledger mechanics, with
`cross-agent-delegation` for cross-family handoffs, `agent-group-discuss` for the
diverse portfolio and adversarial audit, `model-router` for resolving solver and
verifier providers, `get-available-resources` and `modal-research-compute` for
compute routing, and `research-verification-gate` plus `decision-doubt-loop` for
verification. It is a guidance runbook, not runnable code; the runtime helper
owns the ledger files and the agent performs the actual solving, verification,
and credit checks.

## Run Metadata

| Field | Value |
|---|---|
| Run ID |  |
| Created at |  |
| Updated at |  |
| Parent owner |  |
| Workspace (holds `loop_state.json`, `budget.json`, `iterations.jsonl`, `recovery.md`, `approach_registry.md`) |  |
| Research question |  |
| `loop_mode` (skill mode) |  |
| Status | `planned` |

Status values: `planned`, `running`, `paused`, `blocked`, `completed`,
`abandoned`. `paused` is used for a credit/quota outage or a user-gated block
(resume when cleared); `abandoned` means the parent or user cancelled the run
before a terminal stop condition fired.

`loop_mode` is the skill-level mode (`monitor`, `bounded-research`,
`implementation-support`, `panel-loop`, `recovery`) and is distinct from
`path_mode` (`single-path` / `portfolio`), which is chosen per loop in Approach
Triage. This runbook usually runs `loop_mode: panel-loop` while in portfolio
`path_mode`, and `bounded-research` after it collapses to single-path.

## Definition of Done

State the success criteria before iteration 1 in observable, machine-checkable
terms. The loop is resolved only when the artifact below exists and passes the
adversarial audit gate.

| Field | Value |
|---|---|
| Exact claim to establish |  |
| Success artifact (proof / construction / counterexample / dataset + checker) |  |
| Machine-checkable success check (`proof_artifacts/<id>.json`, test, or script) |  |
| Scope and material exclusions |  |

### Insufficient results (do not count as done)

Partial progress counts only if it **strictly implies** the exact claim above.
The following are progress worth recording, but MUST NOT be reported as
resolution and MUST NOT trigger an early success stop:

- A proof of a special case, weaker hypothesis, or sub-class only.
- A **reduction to another unproved statement**, especially one equivalent in
  strength to the original claim (see the elegant-reduction trap below).
- An isolated missing lemma stated as "routine", "standard", or "it clearly
  follows" without a proof.
- Computational verification up to some finite size or sample bound.
- A candidate counterexample without a complete, verified refutation certificate.
- A "best-effort" summary or an explanation of why the problem is hard.

Record any of these with decision `revise` or `delegate`, never as a `stop` with
`termination_reason: success_criteria_met`.

## Stop Conditions

Run continuously until **any** of the four conditions below fires. The loop is an
OR over all four: the moment one fires, stop immediately and report status. Do
not collapse them into one and do not silently extend the run past a fired
condition.

| # | Stop condition | Detection point | Ledger field that records it | Terminal decision / `termination_reason` |
|---|---|---|---|---|
| (a) | A **finite number of loops specified by the user** is reached | Iteration counter vs cap | `budget.json` `max_iterations`, `loop_state` | `stop` / `budget_exhausted` |
| (b) | **The research question is fully resolved** | Definition of Done met **and** adversarial audit gate passes | Success/evidence artifact id in `iterations.jsonl` | `stop` / `success_criteria_met` |
| (c) | **A hard user-set spend cap is exhausted** | `budget.json` `max_usd` / `max_tokens` / `max_wall_minutes` reached | `budget.json` `spent_usd` / `spent_tokens` fields | `stop` / `budget_exhausted` |
| (d) | **The user asks specifically to stop** | Explicit user signal | `termination_reason` in final record | `stop` / `user_stop` |

**Credit/quota outage is NOT a terminal stop.** A candidate backend's failed
budget/quota guard falls through to the next permitted lane in configured order.
Only after all permitted lanes are exhausted—or an explicit backend override
fails—does the outage trigger **pause-and-wait**: set status `paused`, record the
outage, and resume when a guard clears (matching the
`autonomous-research-loop-runtime` driver, which treats outages as pause, not
failure). Only condition (c) — a hard spend cap the user set — is terminal. The
compute-guard preflight in condition (c)'s row is separate from the user spend
cap: check it before each dispatch candidate (see Heavy-Compute Offload) so a
doomed job is never launched.

### Finite-N ASK gate (hard precondition before iteration 1)

- If the user specified **a finite number of loops** `N`, record it as
  `budget.json` `max_iterations`.
- **If the user did not specify N, ASK the user how many loops to run before
  starting iteration 1.** Do not assume a default and do not run unbounded.
- This is a hard gate: the loop cannot start until `max_iterations` is set.

`max_iterations` is a **hard cap, never a target**. The loop may end earlier on
any of the four stop conditions, but must never append more than
`max_iterations` records, and the final allowed iteration must be terminal
(`stop` or `blocked`).

## Budget / Credit Preflight

Parent-owned state. Do not copy these fields into cross-agent-delegation packets;
budget and credit state stay in this runbook.

| Field | Value | Notes |
|---|---|---|
| `max_iterations` (= user `N`) |  | Set via the ASK gate above. |
| `max_portfolio_iterations` |  | Hard cap on loops spent in portfolio `path_mode` before collapsing to single-path. Must be `<= max_iterations`; default `ceil(max_iterations / 3)`. |
| `max_portfolio_approaches` |  | Max distinct live approaches held at once in portfolio mode. |
| `max_child_workers` |  | Max concurrent subagents per loop (portfolio fan-out cap; from `budget.json`). |
| `max_wall_minutes` |  |  |
| `max_usd` |  | Hard spend cap; hitting it is terminal condition (c). |
| `max_tokens` |  | Hard token cap; hitting it is terminal condition (c). |
| `compute_backend` |  | Recommended order: `local > Kaggle > Modal > Hetzner > GitHub Actions`; a valid custom configured order is honored, with local first and remote lanes unique. |
| `compute_guard_status` |  | Record each attempted lane and its applicable guard: `Kaggle GPU-hours`, `Modal USD`, `Hetzner EUR`, `Hetzner teardown`, or `GitHub Actions minutes`. Kaggle CPU is free/quota-free. A failed lane falls through to the next permitted lane. |
| `credit_checked_at` |  | Timestamp of the last applicable budget, quota, or teardown-guard check. |
| `spent_iterations` |  |  |
| `spent_portfolio_iterations` |  |  |
| `spent_usd` |  |  |
| `spent_tokens` |  |  |

Re-check `compute_guard_status` (and update `credit_checked_at`) for candidates
in configured order at the start of **each** loop that may dispatch heavy
compute, not only once at preflight.

## Per-Loop Phase Plan

Apply every phase, in order, in each loop. No phase is skipped; `path_mode` only
changes what P1 and P3 do, not whether they run.

| Phase | Objective | Inputs | Outputs | Status |
|---|---|---|---|---|
| P1. Triage / path-select | Set this loop's `path_mode` via Approach Triage (portfolio is the presumption; single-path only with a recorded dominance justification). Update the approach registry. |  |  |  |
| P2. Resource check | Run `get-available-resources` locally; if heavy compute is planned, re-check candidates in configured order and select the first permitted backend whose `compute_guard_status` passes. |  |  |  |
| P3. Solve | Advance the selected approach(es): one path in single-path mode, or the live portfolio (bounded by `max_child_workers` / `max_portfolio_approaches`) in portfolio mode. **Always route cross-family handoffs through `cross-agent-delegation`** (mandatory; multi-agent fan-out is conditional on portfolio mode). If a script is required, **always implement it in a way that utilizes the current hardware resources** (see Heavy-Compute Offload for the concrete criterion). |  |  |  |
| P3b. Formal execution (optional) | Only for approaches that registered formalization: intake → skeleton → manual OpenGauss or fail-closed `opengauss` adapter → strict gate. Auto-spawn remains blocked without `headless_qualified` spike + headless driver. `opengauss_run` is provenance only. |  |  |  |
| P4. Adversarial audit | Cross-agent verification plus the failure-mode checklist, per advanced approach. The solving family and the verifying family must differ. **Do not blindly trust the returned answers; verify them carefully.** See Adversarial Audit Gate. |  |  |  |
| P5. Contradiction / refutation / stall handling | On a definitive logical contradiction, a verified refutation, or a theorem-strength stall, mark that route `blocked` in the registry, backtrack to the last valid node, pursue the next-best path, and re-verify by a fresh agent. This is route-level blocking, not loop termination. |  |  |  |
| P6. Registry + ledger + recovery | Update `approach_registry.md`, append the iteration record, and update `recovery.md`. |  |  |  |
| P7. Stop check | Evaluate the four stop conditions; continue only if none fired and budget remains. |  |  |  |

## Approach Triage

At P1 of each loop, set `path_mode` and choose the narrowest configuration that
fits. For this runbook **portfolio is the presumption**; single-path is the
exception and must be justified in writing.

1. **Assess dominance.** Enumerate candidate approaches briefly, grouped by their
   underlying idea (not surface wording), and rank them by estimated probability
   of success.
2. **Default to portfolio** unless one approach is clearly dominant. Collapse to
   `path_mode: single-path` only when you record a **dominance justification** in
   the ledger (why one approach is clearly strongest and cheap to verify). In
   single-path mode, pursue that one path exclusively — skip portfolio branching
   only, then still run P2 (resource check) and P3 (solve) on that path before
   the P4 audit. Never skip a phase.
3. **Portfolio mode** (the default for genuinely open problems): hold a bounded
   set of live approaches (at most `max_portfolio_approaches`), develop each only
   far enough to expose its real gap, and record per-approach evidence. Exit
   portfolio mode — collapse to single-path on the strongest survivor — as soon
   as one survivor clearly dominates, or when `spent_portfolio_iterations`
   reaches `max_portfolio_iterations`, whichever comes first. The portfolio is a
   bounded divergence phase, not the steady state.

Record the ranked approaches, the chosen `path_mode`, any dominance
justification, and the selected path in the iteration ledger so the next-best
path is known if backtracking is needed.

### Portfolio discipline (portfolio mode only)

- **Diversity first.** The portfolio should span substantially different
  formulations: invariants, reductions, algebraic viewpoints, structural
  induction, decomposition, flow/transition-system formulations, embeddings,
  extremal arguments, and computational sanity checks. Superficially different
  wordings of the same idea do not count as diversity.
- **Preserve independence early.** Do not tell every agent the currently favored
  approach; independent agents must not all converge on the same attractive but
  incomplete reduction before its real gap is exposed.
- **Per-approach evidence.** Each advanced approach needs its own evidence id and
  its own audit row (P4). No approach counts as advanced until it is audited; do
  not log one synthesis that hides an unverified approach as progress.
- **Bounded fan-out.** Hold at most `max_portfolio_approaches` live approaches and
  spawn at most `max_child_workers` subagents per loop.
- **Approach registry.** Maintain `approach_registry.md`, keyed by underlying
  idea, each entry tagged `live` / `blocked` / `abandoned` with the reason. If
  many entries collapse to one family, redirect effort toward an underexplored
  formulation.
- **Blocked-route discipline.** When an approach stalls at a theorem-strength
  missing lemma, mark it `blocked`. Reopen it ONLY when a genuinely new
  mechanism, invariant, or construction is proposed — not to retry the same idea.
- **Keep incompatible routes alive.** Do not collapse the portfolio to one route
  merely because it gives elegant reductions. Cross-pollinate ideas only after
  independent agents have developed them far enough to expose their strengths and
  gaps.

### Elegant-reduction trap

A route that ends at a lemma **equivalent in strength to the original claim** is
not close to completion; it has relabeled the hard part, not solved it. Do not
let such a route dominate merely because the reduction is clean. Treat a
reduction as progress only when it lands on a genuinely easier, independently
provable statement, or when it supplies a new proof of the equivalent lemma.

## Backtracking rule

**On a definitive logical contradiction, a verified refutation, or a
theorem-strength stall, clearly state the trigger, backtrack to the last valid
node, and pursue the next-best path. Always re-verify by a FRESH agent before
moving on** (see the Fresh-agent gate).

Backtrack triggers (any one):

- a **definitive logical contradiction** in the current route;
- a **verified refutation**: a checked counterexample, disproof, or
  inconsistency with an established theorem;
- a **theorem-strength stall**: the route needs a missing lemma as hard as the
  goal and no new mechanism is on offer.

Difficulty, slowness, or mere doubt are NOT triggers. On a trigger: (a) state it
explicitly; (b) mark the current route `blocked` in the registry (route-level,
non-terminal); (c) **backtrack to the last valid node** recorded in the ledger;
(d) **pursue the next-best path** from the ranked list; (e) the Fresh-agent gate
must pass before moving on. If no live route remains, do not fabricate progress —
follow the Return Policy's no-live-routes case.

### Fresh-agent gate

This is the single canonical fresh-agent protocol; P5 and the Backtracking rule
reference it rather than re-specifying it. The agent that verifies before moving
on MUST be a fresh, independent context (a different agent family or a
clean-context subagent), not the agent that produced the result and not an
inline self-review — the `decision-doubt-loop` discipline. It is required after
any backtrack and before any load-bearing or irreversible step; it is
`not-applicable` on a routine loop with no such step. If fresh-context
verification is unavailable for a step that requires it, output
`BLOCKED-FRESH-CONTEXT-UNAVAILABLE`, set status `paused`, state the gated step,
and ask for user direction rather than self-reviewing.

## Adversarial Audit Gate

In every loop, the agent that produces a result is never the agent that confirms
it. Verification crosses agent families and is never skipped, even when the
result looks obviously correct.

**General rule:** the primary cross-verifier is any active agent family
*different from the solver's family*. Record `solver_family` and
`verifier_family` in the ledger so the mismatch is auditable (a provider name
alone does not prove family separation). The canonical pairing is Claude and
Codex swapping solver/verifier roles; add a second verifier when confidence is
low, stakes are high, or the solver and primary verifier disagree.

### Crossing matrix

| Solver family (this loop) | Primary cross-verifier (required, different family) | Optional second verifier |
|---|---|---|
| Claude | Codex | OpenCode / Grok / CodeWhale |
| Codex | Claude | OpenCode / Grok / CodeWhale |
| Grok | Claude or Codex | any remaining family |
| CodeWhale | Claude or Codex | any remaining family |
| OpenCode | Claude or Codex | any remaining family |

Rows list the families this project uses; for any solver not listed, apply the
general rule (primary verifier = any active family different from the solver).

### Concrete-deliverable requirement

Reject status reports, vague optimism, and claims that a global compatibility
step is "routine". Every accepted result must return a concrete artifact: a
lemma with a proof, a construction, an equation, a dataset with a checker, or a
counterexample to a proposed sublemma. A claim that "it all fits together" must
be proven, not asserted.

### Failure-mode checklist (apply every audit)

The verifier must actively try to break the result and check, at minimum:

- **precise success condition** (e.g. exact-two multiplicity for a graph-theory
  problem, or the domain analogue): the artifact meets the Definition of Done
  exactly, not approximately;
- **no circular reasoning**: no use of an equivalent restatement of the goal, and
  no reduction that merely relabels the hard part;
- **assumptions explicit and satisfied**: no hidden hypotheses sneaked in (e.g.
  cubicity, planarity, connectivity, or higher edge-connectivity for graph
  problems, or the corresponding extra assumptions in this domain);
- **edge / boundary / degenerate cases**: empty, trivial, and extreme inputs
  hold; reductions introduce no new violations (e.g. bridges or cut vertices in
  graph problems, or the domain analogue);
- **construction integrity**: objects claimed to be of a given type actually are
  (e.g. no repeated-edge closed trails masquerading as cycles);
- **independent reproduction**: numeric/computational results reproduce on a
  fresh agent; off-by-one, sign, unit, and scope errors checked.

### Verification gate (per loop)

| Check | Evidence | Status | Repair if failed |
|---|---|---|---|
| Solver family and primary cross-verifier family differ (recorded as `solver_family` / `verifier_family`) |  |  |  |
| Primary cross-verifier independently re-derived or refuted the result (did not merely restate it) |  |  |  |
| Every advanced approach has its own audit row and evidence id |  |  |  |
| Concrete deliverable returned (lemma+proof / construction / counterexample), not a status report |  |  |  |
| Failure-mode checklist applied with no unresolved item |  |  |  |
| Returned result packet schema, provenance, and limitations validated |  |  |  |
| Result backed by a machine-checkable artifact when a script or proof was used |  |  |  |
| Fresh-agent recheck ran (required after a backtrack or before a load-bearing/irreversible step; `not-applicable` otherwise) |  |  |  |

Status values: `pass`, `flag`, `fail`, `not-applicable`.

The adversarial audit is the in-loop verification and runs every loop.
`research-verification-gate` is the separate final-delivery check: run it before
the terminal stop (its Delivery Check / `READY` | `NOT READY` contract). Use
`decision-doubt-loop` for any load-bearing analytical step inside a loop. Do not
let one substitute for the other.

### Handoff contract

- Every solver -> verifier and verifier -> second-verifier handoff is a bounded
  packet via `cross-agent-delegation`. Task packets use
  `schema_version: cross-agent-delegation.task.v1`; returned verifications use
  `schema_version: cross-agent-delegation.result.v1`. The verifier's objective is
  to independently reproduce or refute the result, not to agree.
- Returned result packets are untrusted evidence until the parent validates
  schema, provenance, limitations, and authority boundaries.
- The cross-verifier must do at least one of: independently re-derive the result
  from inputs; check each load-bearing step against its justification; or
  construct a refutation attempt. Restating the solver's reasoning is NOT
  verification and must be rejected.

## Heavy-Compute Offload

When a step needs heavy computation, route it through `modal-research-compute`.

- The recommended automatic order is `local > Kaggle > Modal > Hetzner > GitHub Actions`;
  a valid custom configured order is honored, with local first and remote lanes unique.
- Kaggle CPU is free/quota-free. Before every remote dispatch, record the
  selected lane and enforce its applicable guard: `Kaggle GPU-hours`,
  `Modal USD`, `Hetzner EUR`, `Hetzner teardown`, or
  `GitHub Actions minutes`.
- The hardware rule applies to every remote script: it must **always implement
  the work in a way that utilizes the current hardware resources** (cores,
  memory, accelerators) of the chosen backend.
  Concretely, before accepting such a script confirm it: parallelizes across the
  available cores (or documents why it is single-threaded), uses the accelerator
  when one is present, batches I/O, and is sized against the
  `get-available-resources` report for the backend.
- Re-run the applicable guard at every dispatching loop. If a lane's guard
  fails—including a missing `Hetzner teardown` guarantee—record the result and
  fall through to the next permitted lane in configured order. A budget/quota
  outage becomes **pause-and-wait** (status `paused`, resume when restored) only
  after all permitted lanes are exhausted or an explicit backend override
  fails; only a user-set spend cap (condition (c)) is terminal.

## Approach Registry

Persisted as `approach_registry.md` in the workspace and referenced from
`recovery.md`; updated at P6 of every loop. Group by the underlying idea, not
surface wording. Route-level `blocked` here is non-terminal (pick the next path)
and is distinct from the loop-level decision `blocked` (which is reserved for the
final allowed iteration).

| `approach_id` | Underlying idea / family | Status (`live` / `blocked` / `abandoned`) | Reason / blocking lemma | New mechanism required to reopen |
|---|---|---|---|---|
| A1 |  | `live` |  |  |

## Per-Iteration Ledger

Append one record per loop; each maps to `iterations.jsonl` via the
`autonomous-research-loop-runtime` append step. To keep the record legible it is
split into a compact core table plus a per-iteration detail list.

Core:

| `iteration_id` | `path_mode` | `solver_family` | `verifier_family` | Decision | `termination_reason` |
|---|---|---|---|---|---|
| I1 | `portfolio` |  |  | `continue` |  |

`solver_family` and `verifier_family` must differ so the cross-family swap is
auditable.

Detail fields recorded per iteration:

- `started_at`, `ended_at`
- `selected_path` / live approaches (with per-approach evidence ids)
- dominance justification (only when `path_mode` collapses to single-path)
- evidence / verification ids
- backtrack trigger (contradiction / refutation / stall) and backtrack target, if any
- fresh-agent recheck result (`pass` / `not-applicable`)
- `compute_backend` (local / Kaggle / Modal / Hetzner / GitHub Actions)
- attempted-lane compute guards: `Kaggle GPU-hours`, `Modal USD`, `Hetzner EUR`,
  `Hetzner teardown`, `GitHub Actions minutes`, plus `credit_checked_at` and the
  selected lane after fall-through
- budget spent this loop, including `spent_portfolio_iterations`

Decision states (from `autonomous-research-loop`):

| Decision | Meaning |
|---|---|
| `continue` | None of the four stop conditions fired and budget remains; record a concrete next objective and remaining budget. |
| `revise` | Repairable evidence, verification, or scope gap remains; includes any of the insufficient results above. |
| `delegate` | Work crosses an agent family; hand off via a cross-agent-delegation packet. |
| `stop` | A stop condition fired; the run terminates. |
| `blocked` | Reserved for the **final allowed iteration** when the budget is exhausted without success. |

Per the enforcement policy, a self-marked blocker mid-run is **not** a loop
terminal: record it, mark the affected route `blocked` in the registry, and
continue with `revise` or `delegate`. A credit/quota outage pauses the run; it
does not produce a `blocked` decision. The loop-level decision `blocked` appears
only on the final allowed iteration.

Termination mapping:

| `termination_reason` | When |
|---|---|
| `success_criteria_met` | Question fully resolved per the Definition of Done; requires a passed proof/success evidence id. |
| `budget_exhausted` | Finite-N cap (a) reached, or a user spend cap (c) — `max_usd` / `max_tokens` / `max_wall_minutes` — hit. |
| `user_stop` | The user asked specifically to stop. |
| `blocked` | Final allowed iteration reached without success (budget exhausted). |

## Return Policy

**While budget remains**, do not stop and return a partial result, a reduction
that relabels the hard part, an isolated unproved lemma, a "best-effort" summary,
or an explanation of why the problem is hard. When an approach fails or hits a
theorem-strength gap, mark it route-`blocked` and continue with the next path or
a fresh formulation; reopen a blocked route only for a genuinely new mechanism.

Return or pause only in these cases:

- **Full resolution** (condition b): the success artifact exists and passes the
  adversarial audit gate. Return the complete result plus its machine-checkable
  success check.
- **Budget or spend cap exhausted** (conditions a, c): return the strongest
  rigorously-verified result achieved and the **exact remaining gap**, clearly
  labeled unresolved, with `termination_reason` `budget_exhausted` (or `blocked`
  on the final iteration). Name the blocked approaches and the mechanism each
  would need. This is the only case in which a partial result is returned.
- **User stop** (condition d): return current verified state and the next safe
  action.
- **Credit/quota outage**: do not return — set status `paused` and wait for
  credit to return, then resume.
- **No live route remains, or fresh-context verification is unavailable**
  (`BLOCKED-FRESH-CONTEXT-UNAVAILABLE`): if budget remains, this is not a
  terminal stop — keep searching for a fresh formulation or a new mechanism. Only
  when no in-scope action remains, set status `paused` and ask the user for
  direction. Never launder a block into a fake success.

## Evidence Gate Before Terminal Stop

Do not blindly trust returned answers; always verify by a different family, and
re-verify by a fresh agent after any backtrack. An early stop claiming the
question is fully resolved must cite a verification artifact (evidence id) that
resolves to a passed proof/success artifact, per the `autonomous-research-loop`
early-stop gate. Run `research-verification-gate` before the terminal stop.

## Recovery Notes

After every material iteration, update `recovery.md` so a `recovery`-mode resume
can continue from the last valid node.

| Field | Value |
|---|---|
| Current goal |  |
| Last iteration |  |
| Status |  |
| Next safe action |  |
| `path_mode` (single-path / portfolio) |  |
| Selected path / live approaches |  |
| Last valid node (backtrack target) |  |
| `approach_registry.md` summary |  |
| Remaining gaps |  |
| Credit / budget remaining |  |

## Runtime Helper Note

Prefer `autonomous-research-loop-runtime` to init, append, validate, and report
status on the ledger files (`loop_state.json`, `budget.json`,
`iterations.jsonl`, `recovery.md`). It enforces that appends stop at
`max_iterations`, rejects `continue` decisions on the final allowed iteration,
rejects early success stops lacking a passed proof/success artifact, and treats
credit/quota outages as pause-and-wait. The runtime helper is offline ledger
mechanics only; the agent still performs the solving, the cross-provider
verification, and the credit checks.

## Failure Modes

| Failure mode | Detection point | Recovery |
|---|---|---|
| Loop count unspecified | Finite-N ASK gate | Ask the user for `N` before iteration 1; do not pick a silent default. |
| A phase skipped in single-path mode | Per-Loop Phase Plan | Single-path skips portfolio branching only; still run P2 and P3 before P4. |
| Loop runs past a fired stop condition | Stop check (P7) | Stop immediately; the OR over the four conditions is binding. |
| Portfolio never converges (stays open all N loops) | `max_portfolio_iterations` | Collapse to single-path on the strongest survivor once the portfolio cap is hit. |
| Premature convergence on one reduction | Portfolio discipline | Preserve independence; keep incompatible routes alive until gaps are exposed. |
| Elegant reduction to an equally-hard lemma treated as progress | Elegant-reduction trap | Reject as done; count only as `revise` unless it lands on an easier provable statement. |
| Blocked route retried with no new idea | Blocked-route discipline | Keep it `blocked`; reopen only for a genuinely new mechanism. |
| Route-level `blocked` mistaken for a run stop | Approach Registry / Decision states | Route `blocked` is non-terminal; continue the next path. Loop `blocked` is only the final iteration. |
| Solver verified itself, or same family via a different provider | Adversarial audit gate | Reject; re-verify with a different family; record `solver_family` / `verifier_family`. |
| One approach advanced without its own audit | Per-approach evidence | Reject; audit each advanced approach before logging progress. |
| Status report accepted as a result | Concrete-deliverable requirement | Reject; require a lemma+proof, construction, or counterexample. |
| Backtrack treated as verified | Fresh-agent gate | Re-verify the next-best path by a fresh agent before moving on. |
| Credit/quota outage treated as terminal | Stop Conditions note | Fall through all permitted lanes, then pause-and-wait (status `paused`) if none pass; resume when a guard clears. Only a user spend cap is terminal. |
| Compute guard not checked before dispatch | Heavy-compute offload | Re-check each candidate's `compute_guard_status` and fall through in configured order; pause only after all permitted lanes are exhausted or an explicit backend override fails. |
| Early success stop without evidence | Evidence gate | Reject the stop; continue with `revise`/`delegate` citing the missing evidence id. |
| Ledger field invented | Runtime validation | Reuse the documented decision states and the loop files. |
| Budget/credit copied into a packet | Packet validation | Remove; keep budget and credit state in this runbook only. |

## Final Outcome

Accepted findings:

Rejected findings:

Unresolved findings (exact remaining gap):

Blocked approaches and the mechanism each would need:

Termination reason:

Recommended next action:
