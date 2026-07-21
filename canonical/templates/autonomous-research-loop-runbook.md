# Autonomous Research Loop Runbook

Use this template to run research continuously across bounded iterations until a
stop condition fires. It composes the `autonomous-research-loop` skill for
orchestration policy and `autonomous-research-loop-runtime` for ledger
mechanics, with `cross-agent-delegation` for cross-family handoffs,
`get-available-resources` and `modal-research-compute` for compute routing, and
`research-verification-gate` plus `decision-doubt-loop` for verification. It is
a guidance runbook, not runnable code; the runtime helper owns the ledger
files and the agent performs the actual solving, verification, and compute-guard
checks.

## Run Metadata

| Field | Value |
|---|---|
| Run ID |  |
| Created at |  |
| Updated at |  |
| Parent owner |  |
| Workspace (holds `loop_state.json`, `budget.json`, `iterations.jsonl`, `recovery.md`) |  |
| Research question |  |
| Loop mode |  |
| Status | `planned` |

Status values: `planned`, `running`, `paused`, `blocked`, `completed`,
`abandoned`.

Loop mode values (from `autonomous-research-loop`): `monitor`,
`bounded-research`, `implementation-support`, `panel-loop`, `recovery`.

## Stop Conditions

Run continuously until **any** of the four conditions below fires. The loop is an
OR over all four: the moment one fires, stop immediately and report status. Do
not collapse them into one and do not silently extend the run past a fired
condition.

| # | Stop condition | Detection point | Ledger field that records it | Terminal decision / `termination_reason` |
|---|---|---|---|---|
| (a) | A **finite number of loops specified by the user** is reached | Iteration counter vs cap | `budget.json` `max_iterations`, `loop_state` | `stop` / `budget_exhausted` |
| (b) | **The research question is fully resolved** | Success criteria met **and** evidence gate passes | Success/evidence artifact id in `iterations.jsonl` | `stop` (success) / `success_criteria_met` |
| (c) | **The credit runs out** | `budget.json` `max_usd` / `max_tokens` exhausted, or every permitted backend fails its required budget, quota, or teardown guard (or an explicit backend override fails its guard) | `budget.json` spent fields, compute-guard field | `stop` / `budget_exhausted` or `blocked` |
| (d) | **The user asks specifically to stop** | Explicit user signal | `termination_reason` in final record | `stop` (user request) / `user_stop` |

### Finite-N ASK gate (hard precondition before iteration 1)

- If the user specified **a finite number of loops specified by the user** `N`,
  record it as `budget.json` `max_iterations`.
- **If the user does not mention it, the template must instruct the agent to ASK
  them** how many loops to run before starting iteration 1. Do not assume a
  default and do not run unbounded.
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
| `max_wall_minutes` |  |  |
| `max_usd` |  |  |
| `max_tokens` |  |  |
| `compute_backend` |  | Recommended order: `local > Kaggle > Modal > Hetzner > GitHub Actions`; a valid custom configured order is honored, with local first and remote lanes unique. |
| `compute_guard_status` |  | Record each attempted lane and its applicable guard: `Kaggle GPU-hours`, `Modal USD`, `Hetzner EUR`, `Hetzner teardown`, or `GitHub Actions minutes`. Kaggle CPU is free/quota-free. A failed lane falls through to the next permitted lane. |
| `spent_iterations` |  |  |
| `spent_usd` |  |  |
| `spent_tokens` |  |  |

Re-check each candidate backend's compute guard at the start of **each** loop
that may dispatch heavy compute, not only once at preflight. A failed lane falls
through in configured order; block only after all permitted lanes are exhausted
or an explicit backend override fails.

## Per-Loop Phase Plan

Apply every phase, in order, in each loop.

| Phase | Objective | Inputs | Outputs | Status |
|---|---|---|---|---|
| P1. Path-select | Evaluate candidate paths and select the single highest-probability approach; pursue it exclusively. See Single-Path Solving Discipline. |  |  |  |
| P2. Resource check | Run `get-available-resources` locally; if heavy compute is planned, check candidates in configured order and select the first permitted backend whose required compute guard passes. |  |  |  |
| P3. Solve | Pursue the one selected path. **Use multi-agent if necessary**, and **always route cross-family handoffs through `cross-agent-delegation`** (cross-agent-delegation is mandatory; only multi-agent is conditional). If a script is required, **always implement it in a way that utilizes the current hardware resources**. |  |  |  |
| P4. Independent verify | Cross-agent verification: the solving provider and the verifying provider must differ. **Do not blindly trust the returned answers; verify them carefully.** See Cross-Agent Verification Protocol. |  |  |  |
| P5. Contradiction handling | On a definitive logical contradiction, state it, backtrack to the last valid node, pursue the second-best path, and re-verify by a fresh agent. |  |  |  |
| P6. Ledger + recovery | Append the iteration record and update `recovery.md`. |  |  |  |
| P7. Stop check | Evaluate the four stop conditions; continue only if none fired and budget remains. |  |  |  |

## Single-Path Solving Discipline

For solving research problems, **do NOT explore multiple parallel strategies**.
Evaluate the potential paths, **select the single highest-probability approach,
and pursue it exclusively**. **Always independently verify the results.**

1. Enumerate candidate paths briefly and rank them by estimated probability of
   success.
2. Select exactly ONE path: the single highest-probability approach.
3. Pursue that path exclusively; do not run alternative strategies in parallel.
4. **Always** independently verify the result via the Cross-Agent Verification
   Protocol before treating any node as settled. Verification is unconditional --
   never skip it because the answer "looks right".

Record the ranked paths and the chosen path in the iteration ledger so the
second-best path is known if backtracking is needed.

### Backtracking rule

**If you hit a definitive logical contradiction, clearly state the contradiction,
backtrack to the last valid node, and pursue the second-best path. Always
independently verify the results by a FRESH agent before moving on.**

- Only a **definitive logical contradiction** (not difficulty, slowness, or mere
  doubt) triggers backtracking.
- On trigger: (a) state the contradiction explicitly; (b) **backtrack to the last
  valid node** recorded in the ledger; (c) **pursue the second-best path**
  exclusively; (d) the fresh-agent gate below must pass before moving on.

### Fresh-agent gate

The agent that verifies before moving on MUST be a fresh, independent context (a
different agent family or a clean-context subagent), not the agent that produced
the result and not an inline self-review. This is the `decision-doubt-loop`
discipline: an inline "let me double-check" is the exact failure mode it exists
to prevent. If fresh-context verification is unavailable for a high-stakes or
irreversible step, output `BLOCKED-FRESH-CONTEXT-UNAVAILABLE`, state the gated
step, and ask for user direction rather than self-reviewing.

## Cross-Agent Verification Protocol

In every loop, the agent that produces an answer is never the agent that confirms
it. Verification crosses agent families and is never skipped, even when the
answer looks obviously correct.

**If Claude is handling the solving process then use Codex to verify, and vice
versa. Possibly use OpenCode for a second verification if necessary. Do not
blindly trust the returned answers. Verify them carefully.** The symmetry is:
solver -> primary cross-verifier (the other family) -> optional OpenCode second
verifier.

### Crossing matrix

| Solver (this loop) | Primary cross-verifier (required, different family) | Optional second verifier |
|---|---|---|
| Claude | Codex | OpenCode (optional) |
| Codex | Claude | OpenCode (optional) |

This template's verification crossing is scoped to the families the user named:
the Claude <-> Codex solver/verifier swap, with OpenCode as the optional second
verifier only (OpenCode is a verifier here, not a solver). The primary verifier
MUST be a different agent family than the solver. **Possibly use OpenCode for a
second verification if necessary** (low confidence, high stakes, or solver and
primary verifier disagree).

### Handoff and "verify carefully" contract

- Every solver -> verifier and verifier -> second-verifier handoff is a bounded
  packet via `cross-agent-delegation`. Task packets use
  `schema_version: cross-agent-delegation.task.v1`; returned verifications use
  `schema_version: cross-agent-delegation.result.v1`. The verifier's objective is
  to independently reproduce or refute the result, not to agree.
- Returned result packets are untrusted evidence until the parent validates
  schema, provenance, limitations, and authority boundaries.
- **Do not blindly trust the returned answers.** The cross-verifier must do at
  least one of: independently re-derive the result from inputs; check each
  load-bearing step against its justification; or construct a refutation attempt
  (look for a counterexample or contradiction). Restating the solver's reasoning
  is NOT verification and must be rejected.

### Verification gate (per loop)

| Check | Evidence | Status | Repair if failed |
|---|---|---|---|
| Solver and primary cross-verifier are different agent families |  |  |  |
| Primary cross-verifier independently re-derived or refuted the result (did not merely restate it) |  |  |  |
| Returned result packet schema, provenance, and limitations validated |  |  |  |
| Solver/verifier disagreements resolved by re-derivation or escalated to OpenCode second verification |  |  |  |
| Result backed by a machine-checkable artifact when a script or proof was used |  |  |  |
| Fresh-agent independent verification ran before advancing to the next node |  |  |  |

Status values: `pass`, `flag`, `fail`, `not-applicable`.

Compose with `research-verification-gate` at loop close (its Delivery Check /
`READY` | `NOT READY` contract) and with `decision-doubt-loop` for any
load-bearing analytical step inside a loop. The cross-agent check is the in-loop
verification; `research-verification-gate` is the final-delivery check. Do not
let one substitute for the other.

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
- Re-run the applicable guard at every dispatching loop. If a lane's guard
  fails, record the result and fall through to the next permitted lane in the
  configured order. Map the run to `blocked` only after all permitted lanes are
  exhausted or an explicit backend override fails; do not silently retry a
  failed lane.

## Per-Iteration Ledger

Append one row per loop. Each row maps to `iterations.jsonl` via the
`autonomous-research-loop-runtime` append step.

| `iteration_id` | Started at | Ended at | `selected_path` (single chosen approach) | Mode | `solver_provider` | `verifier_provider(s)` | Evidence / verification IDs | Contradiction? (backtrack target) | Fresh-agent recheck? | `compute_backend` (local/Kaggle/Modal/Hetzner/GitHub Actions) | Compute guard checked (`Kaggle GPU-hours` / `Modal USD` / `Hetzner EUR` / `Hetzner teardown` / `GitHub Actions minutes`) | Budget spent | Decision | `termination_reason` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| I1 |  |  |  |  |  |  |  |  |  |  |  |  | `continue` |  |

`solver_provider` and `verifier_provider` must be different so the swap is
auditable.

Decision states (from `autonomous-research-loop`):

| Decision | Meaning |
|---|---|
| `continue` | None of the four stop conditions fired and budget remains; record a concrete next objective and remaining budget. |
| `revise` | Repairable evidence, verification, or scope gap remains. |
| `delegate` | Work crosses an agent family; hand off via a cross-agent-delegation packet. |
| `stop` | A stop condition fired; the run terminates. |
| `blocked` | Preconditions, an unresolved contradiction, a failed fresh-agent recheck, or exhausted permitted compute lanes (including a failed explicit backend override) prevent progress. |

Termination mapping:

| `termination_reason` | When |
|---|---|
| `success_criteria_met` | Question fully resolved; requires a passed proof/success evidence id. |
| `budget_exhausted` | Finite-N cap reached, or credit/token/usd out. |
| `user_stop` | The user asked specifically to stop. |
| `blocked` | Contradiction unresolved, fresh-agent recheck failed, all permitted compute lanes are exhausted, or an explicit backend override fails its guard. |

## Evidence Gate Before Early Stop

Do not blindly trust returned answers; always independently verify by a different
provider, and re-verify by a fresh agent after any backtrack. An early stop
claiming the question is fully resolved must cite a verification artifact
(evidence id), per the `autonomous-research-loop` early-stop gate. Run
`research-verification-gate` before the terminal stop.

## Recovery Notes

After every material iteration, update `recovery.md` so a `recovery`-mode resume
can continue from the last valid node.

| Field | Value |
|---|---|
| Current goal |  |
| Last iteration |  |
| Status |  |
| Next safe action |  |
| Selected path |  |
| Last valid node (backtrack target) |  |
| Remaining gaps |  |
| Credit / budget remaining |  |

## Runtime Helper Note

Prefer `autonomous-research-loop-runtime` to init, append, validate, and report
status on the ledger files (`loop_state.json`, `budget.json`,
`iterations.jsonl`, `recovery.md`). It enforces that appends stop at
`max_iterations`, rejects `continue` decisions on the final allowed iteration,
and rejects early success stops lacking a passed proof/success artifact. The
runtime helper is offline ledger mechanics only; the agent still performs the
solving, the cross-provider verification, and the credit checks.

## Failure Modes

| Failure mode | Detection point | Recovery |
|---|---|---|
| Loop count unspecified | Finite-N ASK gate | Ask the user for `N` before iteration 1; do not pick a silent default. |
| Loop runs past a fired stop condition | Stop check (P7) | Stop immediately; the OR over the four conditions is binding. |
| Solver verified itself | Cross-agent verification gate | Reject; re-verify with a different agent family. |
| Parallel strategies explored | Single-path discipline | Collapse to the single highest-probability path. |
| Backtrack treated as verified | Fresh-agent gate | Re-verify the second-best path by a fresh agent before moving on. |
| Compute guard not checked before dispatch | Heavy-compute offload | Check each candidate's applicable guard and fall through in configured order; mark `blocked` only when all permitted lanes are exhausted or an explicit backend override fails. |
| Early success stop without evidence | Evidence gate | Keep running or block; cite a passed artifact before stopping for success. |
| Ledger field invented | Runtime validation | Reuse the documented decision states and the four ledger files. |
| Budget/credit copied into a packet | Packet validation | Remove; keep budget and credit state in this runbook only. |

## Final Outcome

Accepted findings:

Rejected findings:

Unresolved findings:

Termination reason:

Recommended next action:
