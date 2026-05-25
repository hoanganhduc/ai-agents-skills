# Research Quality Guards

Use these guards for nontrivial research runs before treating a report,
delegated result, or finding as ready. Guards are evidence checks, not scoring
systems.

## Guard Types

| Guard | Purpose |
|---|---|
| `ScopeGuard` | Check that claims, recommendations, and exclusions stay inside the declared scope. |
| `EvidenceGuard` | Check that major claims map to source IDs or local evidence IDs. |
| `VerifyGuard` | Check mechanical readiness separately from final delivery readiness. |
| `BudgetGuard` | Check parent-owned token, USD, depth, and hop limits before continuing work. |
| `RegressionGuard` | Check that load-bearing workflow text, templates, and packet rules remain present. |

## Closed Guard Output Schema

Every guard output must use exactly these fields:

| Field | Meaning |
|---|---|
| `guard_output_id` | Stable ID for this guard output; iteration ledgers reference this ID. |
| `guard` | One of `ScopeGuard`, `EvidenceGuard`, `VerifyGuard`, `BudgetGuard`, or `RegressionGuard`. |
| `status` | `pass`, `warn`, `fail`, or `not-applicable`. |
| `claim_or_scope_ref` | Claim ID, scope item, exclusion, or runbook item being checked. |
| `source_ids` | Source IDs supporting the finding, when source-backed. |
| `evidence_ids` | Local evidence IDs or artifact references supporting the finding. |
| `inspected_artifacts` | Concrete files, logs, templates, diffs, or outputs inspected. |
| `gap` | Missing evidence, unclear scope, or unresolved issue. Empty only when no gap remains. |
| `blocking` | Boolean indicating whether the gap prevents delivery. |
| `recommended_action` | Concrete next action, or `none` when no action is needed. |

## Evidence Rules

- A supported `pass` or `warn` output must include at least one non-empty
  `source_ids` or `evidence_ids` entry.
- Empty `source_ids` and `evidence_ids` are allowed only for a missing-evidence
  `fail` or for `not-applicable`.
- Missing-evidence `fail` and `not-applicable` outputs must still include
  non-empty `gap` and `inspected_artifacts` values.
- Do not invent placeholder refs. If evidence is missing, say so in `gap`.
- Do not collapse guard results into a single aggregate score.

## Runbook Use

Record each guard output before the iteration decision. The iteration ledger
must reference guards by `{guard_output_id, status}` and carry any blocking
gap into the next `revise`, `reject`, or `blocked` decision.

`BudgetGuard` reads parent-owned runbook state only. It must not read budget,
model, provider, token, USD, depth, hop, session, or credential fields from
delegation packets.
