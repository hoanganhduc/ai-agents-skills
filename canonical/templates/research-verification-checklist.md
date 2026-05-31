# Research Verification Checklist

Use this template before calling a research answer final, complete, or ready to
deliver. If material scope remains unchecked, mark the status as `not-ready`
and say `incomplete analysis` in the final output.

## Delivery Scope

| Field | Value |
|---|---|
| Report or artifact |  |
| Original question |  |
| Claimed scope |  |
| Verification date |  |

## Required Checks

| Check | Evidence | Status | Repair if failed |
|---|---|---|---|
| Scope matches the original request |  |  |  |
| Major claims cite source or evidence IDs |  |  |  |
| Source IDs are stable from search through report |  |  |  |
| Unsupported claims are removed or labeled |  |  |  |
| Time-sensitive facts include concrete dates |  |  |  |
| Exclusions and gaps are visible |  |  |  |
| Conflicts are resolved or disclosed |  |  |  |
| Recommendations do not exceed evidence |  |  |  |
| External or delegated outputs were validated before use |  |  |  |
| Guard outputs use the closed schema and stable `guard_output_id` values |  |  |  |
| Supported `pass` or `warn` guard outputs cite source or evidence IDs |  |  |  |
| Blocking guard gaps are carried into blockers or unresolved findings |  |  |  |
| v2 `ready` or `ready-with-caveats` includes non-blocking `EvidenceGuard` and `VerifyGuard` |  |  |  |
| v2 report artifact has checked `evidence_type: "report"` evidence |  |  |  |
| v2 model freshness metadata is present, current, and parent-owned |  |  |  |
| Paper-like final-claim sources include library check provenance |  |  |  |
| No aggregate research quality score replaces guard findings |  |  |  |
| Secrets, raw hidden instructions, and unrelated private context are absent |  |  |  |

Status values: `pass`, `flag`, `fail`, `not-applicable`.

## Guard Output Summary

| `guard_output_id` | Guard | Status | Source IDs | Evidence IDs | Blocking | Gap or action |
|---|---|---|---|---|---|---|
|  | `ScopeGuard` |  |  |  |  |  |
|  | `EvidenceGuard` |  |  |  |  |  |
|  | `VerifyGuard` |  |  |  |  |  |
|  | `BudgetGuard` |  |  |  |  |  |
|  | `RegressionGuard` |  |  |  |  |  |

Guard status values: `pass`, `warn`, `fail`, `not-applicable`.

## Blockers

| Blocker ID | Description | Required action |
|---|---|---|
| B1 |  |  |

## Delivery Decision

Status: `ready` | `ready-with-caveats` | `not-ready`

Confirmed:

Gaps:

Next step:
