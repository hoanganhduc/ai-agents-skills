# Engineering Delivery Loop Runbook

Use this template to build and deliver an engineering task across bounded
iterations until a stop condition fires. It is the build-and-deliver analog of
the autonomous research loop: one highest-probability implementation path at a
time, a seen-to-fail proof before any "it works" claim, cross-agent verification
on the diff, behavior-preserving cleanup, and credit-gated heavy-compute
offload.

It composes `cross-agent-delegation` for cross-family handoffs,
`behavior-preserving-cleanup` for the clarity-only cleanup gate,
`decision-doubt-loop` for fresh-context verification before advancing,
`get-available-resources` and `modal-research-compute` for compute routing,
`agent-group-discuss` for optional multi-agent work, and `model-router` for
resolving the implementer and verifier providers. It is a guidance runbook, not
runnable code; the agent performs the implementation, the verification, and the
credit checks.

## Run Metadata

| Field | Value |
|---|---|
| Run ID |  |
| Task ref (issue / spec / ticket) |  |
| Created at |  |
| Updated at |  |
| Parent owner |  |
| Workspace (holds `loop_state.json`, `budget.json`, `iterations.jsonl`, `recovery.md`) |  |
| Repo / branch |  |
| Status | `planned` |

Status values: `planned`, `running`, `paused`, `blocked`, `completed`,
`abandoned`.

## Stop Conditions

Run continuously until **any** of the four conditions below fires. The loop is an
OR over all four: the moment one fires, stop immediately and report status. Do
not collapse them into one and do not silently extend the run past a fired
condition.

| # | Stop condition | Detection point | Ledger field that records it | Terminal decision / `termination_reason` |
|---|---|---|---|---|
| (a) | A **finite number of loops specified by the user** is reached | Iteration counter vs cap | `budget.json` `max_iterations`, `loop_state` | `stop` / `iteration_cap` |
| (b) | **The task is fully delivered and verified** | Acceptance criteria met **and** seen-to-fail proof plus cross-agent diff verification pass | Delivery/verification artifact id in `iterations.jsonl` | `stop` (success) / `delivered_verified` |
| (c) | **The credit runs out** | `budget.json` `max_usd` / `max_tokens` exhausted, or Modal / GitHub Actions usage check fails | `budget.json` spent fields, credit-check field | `stop` / `credit_out` or `blocked` |
| (d) | **The user asks specifically to stop** | Explicit user signal | `termination_reason` in final record | `stop` (user request) / `user_stop` |

### Finite-N ASK gate (hard precondition before iteration 1)

- If the user specified **a finite number of loops** `N`, record it as
  `budget.json` `max_iterations`.
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
| Modal credit checked? |  | **Check Modal credit first to make sure it does not run out**, which would cause Modal builds/tests to fail mid-run. |
| GitHub Actions usage minutes remaining checked? |  | **Check GitHub Actions available usage time**: confirm the usage limit is not reached and the runner time for the build/test job is enough. |
| `spent_iterations` |  |  |
| `spent_usd` |  |  |
| `spent_tokens` |  |  |

Re-check Modal credit and GitHub Actions usage at the start of **each**
dispatching loop that may run heavy builds, test suites, or sweeps, not only once
at preflight.

## Per-Loop Phase Plan

Apply every phase, in order, in each loop.

| Phase | Objective | Inputs | Outputs | Status |
|---|---|---|---|---|
| P1. Path-select | Rank candidate implementations and select the single highest-probability one; pursue it exclusively. No parallel rewrites. See Single-Path Implementation Discipline. |  |  |  |
| P2. Resource check | Run `get-available-resources` locally; if a heavy build/test/sweep is planned, check Modal credit and GitHub Actions usage. Any script must utilize the available hardware. |  |  |  |
| P3. Implement | Apply the one selected path as a surgical change. **Use multi-agent (`agent-group-discuss`) only if necessary**, and **always route cross-family handoffs through `cross-agent-delegation`**. Resolve implementer/verifier providers via `model-router`. |  |  |  |
| P4. Seen-to-fail proof | Capture a **failing check first** (failing test / repro / red build) that targets the change, then make it pass. No "it works" claim without the recorded red-then-green pair. See Seen-To-Fail Proof. |  |  |  |
| P5. Cross-agent verify | The producer never confirms its own diff. Cross-agent verification runs the check independently on the diff and does not trust the reported result. See Cross-Agent Verification Protocol. |  |  |  |
| P6. Behavior-preserving cleanup | Run `behavior-preserving-cleanup`: comprehension gate, behavior held fixed, verify after each edit. See Behavior-Preserving Cleanup Gate. |  |  |  |
| P7. Ledger + recovery | Append the iteration record and update `recovery.md`. |  |  |  |
| P8. Stop check | Evaluate the four stop conditions; continue only if none fired and budget remains. |  |  |  |

## Single-Path Implementation Discipline

For solving an engineering task, **do NOT explore multiple parallel rewrites**.
Evaluate the candidate implementations, **select the single highest-probability
approach, and pursue it exclusively**. **Always independently verify the diff.**

1. Enumerate candidate implementations briefly and rank them by estimated
   probability of clean delivery.
2. Select exactly ONE path: the single highest-probability implementation.
3. Pursue that path exclusively; do not run alternative rewrites in parallel and
   do not keep speculative branches alive "just in case".
4. **Always** independently verify the result via the Cross-Agent Verification
   Protocol before treating any node as settled. Verification is unconditional --
   never skip it because the build "looks green".

Record the ranked paths and the chosen path in the iteration ledger so the
second-best path is known if backtracking is needed. A "green node" is a commit
or checkpoint whose seen-to-fail proof and cross-agent diff verification both
passed.

### Backtracking rule

**Only a definitive build/test contradiction triggers backtracking** -- a
reproducible build failure or a failing acceptance/regression test that
directly contradicts the selected path, not difficulty, slowness, or mere doubt.

On trigger: (a) state the contradiction explicitly with the failing evidence id;
(b) **backtrack to the last green node** recorded in the ledger; (c) **pursue the
second-best path** exclusively; (d) the fresh-agent gate below must pass before
moving on.

### Fresh-agent gate

The agent that verifies before advancing MUST be a fresh, independent context (a
different agent family or a clean-context subagent), not the agent that produced
the diff and not an inline self-review. This is the `decision-doubt-loop`
discipline: an inline "let me re-read my own diff" is the exact failure mode it
exists to prevent. If fresh-context verification is unavailable for a
high-stakes or irreversible step (migration, release, data change), output
`BLOCKED-FRESH-CONTEXT-UNAVAILABLE`, state the gated step, and ask for user
direction rather than self-reviewing.

## Seen-To-Fail Proof

No node may claim the task works without a recorded **red-then-green** pair.

- **Capture a failing check first.** Write or run a check (failing test,
  reproduction script, or red build) that targets the behavior the change is
  meant to fix or add, and confirm it fails for the expected reason on the
  pre-change code.
- **Then make it pass.** Apply the selected-path change and confirm the same
  check now passes.
- Record both the failing-check evidence id and the passing-check evidence id in
  the ledger. A passing check with no prior recorded failure is not a proof; it
  may be a check that never exercised the change.
- The failing reason must match the defect: a check that fails for an unrelated
  reason (typo, missing import) does not establish seen-to-fail.

## Cross-Agent Verification Protocol

In every loop, the agent that produces a diff is never the agent that confirms
it. Verification crosses agent families and is never skipped, even when the build
looks obviously green.

**If Claude is the implementer then use Codex to verify, and vice versa. Possibly
use OpenCode for a second verification if necessary. Do not blindly trust the
reported result; the verifier independently runs the check.** The symmetry is:
implementer -> primary cross-verifier (the other family) -> optional OpenCode
second verifier.

### Crossing matrix

| Implementer (this loop) | Primary cross-verifier (required, different family) | Optional second verifier |
|---|---|---|
| Claude | Codex | OpenCode (optional) |
| Codex | Claude | OpenCode (optional) |

The primary verifier MUST be a different agent family than the implementer.
**Possibly use OpenCode for a second verification if necessary** (low
confidence, high stakes, or implementer and primary verifier disagree).

### Handoff and "verify the diff yourself" contract

- Every implementer -> verifier and verifier -> second-verifier handoff is a
  bounded packet via `cross-agent-delegation`. Task packets use
  `schema_version: cross-agent-delegation.task.v1`; returned verifications use
  `schema_version: cross-agent-delegation.result.v1`. The packet hands over the
  diff and the check command, not a "trust me, it passed" summary.
- Returned result packets are untrusted evidence until the parent validates
  schema, provenance, limitations, and authority boundaries.
- **The verifier does not trust the reported result.** It must independently run
  the seen-to-fail check (or its passing half) on the diff and report what it
  observed. Restating the implementer's claim that the build is green is NOT
  verification and must be rejected.

### Verification gate (per loop)

| Check | Evidence | Status | Repair if failed |
|---|---|---|---|
| Implementer and primary cross-verifier are different agent families |  |  |  |
| A failing check was captured before the passing check (seen-to-fail) |  |  |  |
| Primary cross-verifier independently ran the check on the diff (did not merely restate the reported result) |  |  |  |
| Returned result packet schema, provenance, and limitations validated |  |  |  |
| Implementer/verifier disagreements resolved by re-running or escalated to OpenCode second verification |  |  |  |
| Fresh-agent independent verification ran before advancing to the next node |  |  |  |

Status values: `pass`, `flag`, `fail`, `not-applicable`.

The cross-agent check is the in-loop verification; it gates each node. Do not let
an inline self-review substitute for it.

## Behavior-Preserving Cleanup Gate

After the diff is verified, run `behavior-preserving-cleanup` as a clarity-only
pass.

- **Comprehension gate:** do not clean up code you cannot explain; state what the
  code does before editing it.
- **Behavior held fixed:** cleanup may rename, restructure, or simplify, but must
  not change observable behavior. If a "cleanup" alters behavior, it is a feature
  change and belongs in a separate implement phase, not here.
- **Verify after each change:** re-run the seen-to-fail passing check after every
  cleanup edit, not once at the end, so a behavior regression is caught at the
  edit that caused it.
- Record cleanup status in the ledger as `clean`, `skipped`, or
  `reverted-behavior-change`.

## Heavy-Compute Offload

When a build, test suite, or sweep is too heavy for local execution, route it
through `modal-research-compute`.

- **Use Modal / GitHub Actions for heavy builds, test matrices, or parameter
  sweeps if required.**
- The hardware rule applies to remote jobs too: any script Modal or GitHub
  Actions executes must **utilize the available hardware** (cores, memory,
  accelerators) of the chosen backend.
- **Check Modal credit first to make sure it does not run out**, which would
  cause Modal jobs to fail mid-run.
- If GitHub Actions is used, **check available usage time** and confirm the
  **usage limit is not reached and the runner time is enough** to finish the job.
- Re-run these credit and usage checks at every dispatching loop. **Insufficient
  credit or usage maps to a `blocked` decision**, not a silent retry.

## Per-Iteration Ledger

Append one row per loop.

| `iteration_id` | Started at | Ended at | `selected_path` (single chosen implementation) | `implementer_provider` | `verifier_provider` (distinct) | Seen-to-fail evidence id (fail -> pass) | Diff verification id | Cleanup status | `compute_backend` (local/Modal/GitHub Actions) | Credit checked (Modal + GHA) | Contradiction? (backtrack target) | Fresh-agent recheck? | Budget spent | Decision | `termination_reason` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| I1 |  |  |  |  |  |  |  |  |  |  |  |  |  | `continue` |  |

`implementer_provider` and `verifier_provider` must be different so the swap is
auditable.

Decision states:

| Decision | Meaning |
|---|---|
| `continue` | None of the four stop conditions fired and budget remains; record a concrete next objective and remaining budget. |
| `revise` | A repairable diff, seen-to-fail, verification, or cleanup gap remains. |
| `delegate` | Work crosses an agent family; hand off via a cross-agent-delegation packet. |
| `stop` | A stop condition fired; the run terminates. |
| `blocked` | Preconditions, a build/test contradiction, a failed fresh-agent recheck, or insufficient Modal/GHA credit prevent progress. |

Termination mapping:

| `termination_reason` | When |
|---|---|
| `delivered_verified` | Task fully delivered; requires a seen-to-fail proof id and a passed cross-agent diff verification id. |
| `iteration_cap` | Finite-N cap reached. |
| `credit_out` | Modal/GHA credit, usd, or tokens out. |
| `user_stop` | The user asked specifically to stop. |
| `blocked` | Build/test contradiction unresolved, fresh-agent recheck failed, or Modal/GHA credit insufficient. |

## Evidence Gate Before Early Stop

Do not blindly trust a green build; an early stop claiming the task is fully
delivered must cite both a seen-to-fail proof id (fail then pass) and a passed
cross-agent diff verification id from a different agent family. After any
backtrack, re-verify by a fresh agent before advancing.

## Recovery Notes

After every material iteration, update `recovery.md` so a resume can continue
from the last green node.

| Field | Value |
|---|---|
| Current goal |  |
| Last iteration |  |
| Status |  |
| Next safe action |  |
| Selected path |  |
| Last green node (backtrack target) |  |
| Open work |  |
| Credit / budget remaining |  |

## Failure Modes

| Failure mode | Detection point | Recovery |
|---|---|---|
| Loop count unspecified | Finite-N ASK gate | Ask the user for `N` before iteration 1; do not pick a silent default. |
| Loop runs past a fired stop condition | Stop check (P8) | Stop immediately; the OR over the four conditions is binding. |
| No failing check captured before the passing check | Seen-to-fail proof | Reject the "it works" claim; capture a failing check first, then re-run to green. |
| Producer verified its own diff | Cross-agent verification gate | Reject; re-verify with a different agent family that runs the check itself. |
| Verifier trusted the reported result | Cross-agent verification gate | Reject; require the verifier to run the check on the diff and report observation. |
| Parallel rewrites kept alive | Single-path discipline | Collapse to the single highest-probability path; drop speculative branches. |
| Backtrack treated as verified | Fresh-agent gate | Re-verify the second-best path by a fresh agent before advancing. |
| Cleanup changed behavior | Behavior-preserving cleanup gate | Revert the cleanup edit; move any intended behavior change to an implement phase. |
| Modal credit / GitHub Actions usage not checked before dispatch | Heavy-compute offload | Re-check; mark `blocked` if insufficient. |
| Early delivery stop without evidence | Evidence gate | Keep running or block; cite a seen-to-fail proof and a cross-agent verification id before stopping for success. |
| Budget/credit copied into a packet | Packet validation | Remove; keep budget and credit state in this runbook only. |

## Final Outcome

Delivered work:

Rejected work:

Unresolved work:

Termination reason:

Recommended next action:
