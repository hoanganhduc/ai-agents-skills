# Research Workflow Runbook

Use this template to run, pause, resume, and audit a multi-phase research task.
It is designed for source-preserving research, with optional delegated work.

## Run Metadata

| Field | Value |
|---|---|
| Run ID |  |
| Created at |  |
| Updated at |  |
| Parent owner |  |
| Workspace |  |
| Status | `planned` |

Status values: `planned`, `running`, `paused`, `blocked`, `completed`,
`abandoned`.

## Parent Model And Budget Policy

Research tasks must use the latest available model with the highest available
thinking or reasoning level required by the parent policy. Record resolved
policy here before delegation or bounded iterations begin.

| Field | Value |
|---|---|
| `resolved_model` |  |
| `resolved_thinking` |  |
| `model_policy_source` |  |
| `resolved_at` |  |
| `policy_ref` |  |
| `model_catalog_source` |  |
| `model_catalog_ref` |  |
| `freshness_checked_at` |  |
| `model_freshness_max_age_seconds` | `86400` |
| `provider_cli_version` |  |
| `provider_cli_status` |  |
| `freshness_source` |  |
| `budget_owner` |  |
| `max_depth` |  |
| `max_hops` |  |
| `max_tokens` |  |
| `max_usd` |  |
| `spent_tokens` |  |
| `spent_usd` |  |
| `depth_used` |  |
| `hops_used` |  |
| `budget_spent` |  |

These fields are parent-owned runbook state. Do not copy them into task or
result packets. Budget state stays in the parent runbook.

For deep-research v2 finalizable delivery, mirror the model freshness fields
into `model_freshness.json`. `ready` and `ready-with-caveats` both fail closed
when model freshness metadata is missing, partial, future-dated, or stale.

## Phase Plan

| Phase | Objective | Inputs | Outputs | Status |
|---|---|---|---|---|
| 1. Scope | Define question, limits, and evidence plan. |  |  |  |
| 2. Search | Gather source ledger with stable IDs. |  |  |  |
| 3. Analyze | Build claim and evidence matrix. |  |  |  |
| 4. Review | Check unsupported claims and scope drift. |  |  |  |
| 5. Verify | Run final delivery checklist. |  |  |  |
| 6. Deliver | Write final answer or report. |  |  |  |

## Artifacts

| Artifact ID | Path or ref | Phase | Purpose | Status |
|---|---|---|---|---|
| A1 |  |  |  |  |

## Source Ledger Contract

| Requirement | Status | Notes |
|---|---|---|
| Every source has one stable ID |  |  |
| Paper-like sources have library verification status when applicable |  |  |
| Final claims with paper-like sources cite library check tool, timestamp, and ref |  |  |
| Dropped sources are explained |  |  |
| Major claims map back to source IDs |  |  |

## Delegation Ledger

Use this section only when work crosses a runner, process, organization, or
trust boundary.

| Task ID | Recipient or family | Input refs | Expected output | Result ref | Status |
|---|---|---|---|---|---|
| T1 |  |  |  |  |  |

Allowed packet constraints are inert strings only. Examples include
`model_policy=same_resolved_model; reasoning=parent_required_highest_available`,
`max_depth=1`, `max_hops=2`, `max_tokens=50000`, `max_usd=25.00`, and
`budget_policy_ref=researchPolicy#default`. The parent run policy decides
whether any nested dispatch is allowed.

## Guard Outputs

Each guard output must use the closed schema from
`deep-research-workflow/references/research-quality-guards.md`.

| `guard_output_id` | Guard | Status | Claim or scope ref | Source IDs | Evidence IDs | Blocking | Gap | Recommended action |
|---|---|---|---|---|---|---|---|---|
| G1 |  |  |  |  |  |  |  |  |

## Iteration Ledger

Use bounded iterations. Stop or revise when budget, evidence, scope, or guard
state says continued work is not justified.

| Field | Value |
|---|---|
| Maximum iterations |  |
| Maximum unresolved `warn` results |  |
| Maximum unresolved `fail` results |  |
| Plateau rule | Stop when an iteration adds no material evidence, narrows no claim, and resolves no guard gap. |

| `iteration_id` | Started at | Ended at | Objective | Evidence IDs | Guard statuses | Decision | Checkpoint ref | Budget spent | Termination reason |
|---|---|---|---|---|---|---|---|---|---|
| I1 |  |  |  |  | `{guard_output_id, status}` | `continue` |  |  |  |

Decision states:

| Decision | Meaning |
|---|---|
| `continue` | The run remains eligible for another iteration. |
| `accept` | `goal_state` and `success_conditions` are satisfied, no blocking guard gaps remain, report review and delivery check pass, and accepted findings are evidence-linked. |
| `revise` | Repairable scope, evidence, guard, or report-review gaps remain. |
| `reject` | A claim, source, subagent output, or run artifact is unusable for the declared scope. |
| `blocked` | Preconditions, unresolved `blocked_by` items, policy denial, missing required evidence, or blocking guard gaps prevent progress. |

Termination mapping:

| Decision | `termination_reason` value |
|---|---|
| `continue` | Empty. |
| `revise` | Empty. |
| `accept` | `accepted`. |
| `blocked` | `blocked`, `policy_denied`, `budget_exhausted`, `max_iterations`, or `plateau`. |
| `reject` | `rejected` when the run terminates; empty when the run continues after rejecting only one claim, source, subagent output, or artifact. |

`scope_change_required` is not a termination reason. Record it in
`blocked_by`, `gap`, or `recommended_action`, and use `blocked` when it stops
the run.

## Recovery Notes

| Checkpoint | Completed work | Open work | Resume instruction |
|---|---|---|---|
|  |  |  |  |

## Verification Summary

| Gate | Artifact | Result | Notes |
|---|---|---|---|
| Briefing |  |  |  |
| Evidence matrix |  |  |  |
| Report review |  |  |  |
| Delivery check |  |  |  |

## Failure Modes

| Failure mode | Detection point | Recovery |
|---|---|---|
| Scope unclear or changed | Scope brief, `ScopeGuard`, iteration decision | Mark `blocked`; revise scope before continuing. |
| Required source unavailable | Evidence plan, source ledger, `EvidenceGuard` | Record the gap, downgrade the claim, or block delivery. |
| Major claim lacks evidence | Claim ledger, `EvidenceGuard` | Add evidence, weaken the claim, or reject it. |
| Guard output malformed | Guard schema check | Reject the run artifact and repair the guard output. |
| Delegation packet contains forbidden fields | Packet validation | Reject the packet and remove authority, secrets, provider/session state, and runtime ledger fields. |
| Nested delegation exceeds policy | Budget/depth/hop ledger | Deny dispatch, record `policy_denied`, and continue locally or block. |
| Budget cap exceeded | `BudgetGuard`, parent runbook ledger | Stop delegated or external work and terminate with `budget_exhausted` unless the parent revises the budget. |
| Model or reasoning rule violated | Model policy review | Rerun affected research with the parent runbook's exact `resolved_model` and `resolved_thinking`. |
| Iterations plateau | Iteration ledger | Stop with `plateau` and summarize remaining uncertainty. |
| Final report or delivery check fails | Report review, verification checklist | Revise claims, evidence, or scope before delivery. |
| Finalizable v2 delivery lacks report evidence, model freshness, or non-blocking EvidenceGuard/VerifyGuard | Deep-research validator | Keep delivery `not-ready`, repair the missing artifacts, then rerun validation. |

## Final Outcome

Accepted findings:

Rejected findings:

Unresolved findings:

Recommended next action:
