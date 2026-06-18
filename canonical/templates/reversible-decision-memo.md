# Reversible Decision Memo

Use this template to record a non-trivial decision as an evidence-grounded memo
that names its alternatives, cites authoritative sources for every
version- or spec-sensitive claim, states the conditions under which the decision
is revisited or rolled back, and passes a fresh-context adversarial confirmation
**before** the decision stands.

It is the decision-record analog of the engineering and research runbooks: one
chosen path at a time, source-grounded rationale, no self-confirmation, and a
fail-closed confirmation gate. It composes `intent-interview` to confirm the
real decision before rationale is written, `source-grounded-decisions` to ground
version- and spec-sensitive claims, `decision-doubt-loop` for the fresh-context
adversarial pass, `cross-agent-delegation` for the bounded reviewer handoff, and
`model-router` to resolve the reviewer provider. It is a guidance memo, not
runnable code; the agent performs the interview, the sourcing, and the
confirmation handoff.

## Decision Metadata

| Field | Value |
|---|---|
| Decision ID |  |
| Title |  |
| Owner (recommender) |  |
| Created at |  |
| Updated at |  |
| Status | `proposed` |
| Reversibility class | `two-way-door` |
| Confirmation gate status | `not-ready` |

Status values: `proposed`, `under-review`, `confirmed`, `reversed`,
`superseded`.

Reversibility class values:

| Class | Meaning | Confirmation requirement |
|---|---|---|
| `two-way-door` | Reversible at low cost; a wrong call can be undone. | Fresh-context confirmation is required but the `BLOCKED-FRESH-CONTEXT-UNAVAILABLE` escape may be accepted with the owner's sign-off. |
| `one-way-door` | Irreversible or reversible only at high cost (migration, release, data deletion, public commitment, contract). | Fresh-context confirmation is **mandatory**; the `BLOCKED` escape stops the decision and the memo stays `not-ready` until a fresh context reviews it. |

A `one-way-door` decision must never be recorded as a reversible `two-way-door`
to skip the harder confirmation path. When in doubt, classify as `one-way-door`.

## Reviewer Model And Policy

The fresh-context reviewer must run on the latest available model at the
reasoning level the parent policy requires, and must be a different context (and,
for `one-way-door` calls, a different agent family) than the recommender. Resolve
the reviewer provider via `model-router` and record it here before the
confirmation pass.

| Field | Value |
|---|---|
| `recommender_provider` |  |
| `reviewer_provider` (distinct context; distinct family for one-way doors) |  |
| `resolved_model` |  |
| `resolved_thinking` |  |
| `model_policy_source` |  |
| `resolved_at` |  |

These fields are memo-owned state. Do not copy them into the reviewer
delegation packet; the packet hands over the decision and the attack scope, not
provider or budget state.

## Phase Plan

Apply every phase, in order. Do not write rationale before the intent is
confirmed, and do not move the status to `confirmed` before the adversarial pass
passes.

| Phase | Objective | Inputs | Outputs | Status |
|---|---|---|---|---|
| P1. Intent confirm | Run `intent-interview`: state the actual decision and its success criteria, one question at a time, and confirm them with the owner **before** any rationale is written. |  |  |  |
| P2. Options | Enumerate the alternatives; name each in <=5 words with what it optimizes for, what it sacrifices, and key evidence refs. |  |  |  |
| P3. Source-grounded rationale | Apply `source-grounded-decisions`: every version- or spec-sensitive claim cites an authoritative source or is flagged `unverified`. |  |  |  |
| P4. Choose path | Select the single chosen path; tie the reasoning to the confirmed success criteria. See Single-Path Decision Discipline. |  |  |  |
| P5. Reversibility + trip-wires | State the reversal cost and the trip-wire conditions that reopen or roll back the decision. |  |  |  |
| P6. Adversarial confirm | Run `decision-doubt-loop`: a fresh-context reviewer attacks the decision via a `cross-agent-delegation` packet before it stands. See Fresh-Context Adversarial Confirmation. |  |  |  |
| P7. Gate + record | Set the confirmation gate, list unresolved objections, and set the decision status. |  |  |  |

## P1 Intent Confirmation (intent-interview)

The recommender must not write rationale for a decision whose real shape is
assumed. Run `intent-interview` first.

- State the **actual decision** in one sentence: what is being chosen, by whom,
  and for what.
- State the **success criteria** the decision must satisfy, as checkable
  conditions, not vibes.
- **Confirm both with the owner before P2.** If intent or success criteria are
  unconfirmed, the memo cannot leave `proposed`.

| Field | Value |
|---|---|
| Decision (one sentence) |  |
| Success criteria (checkable) |  |
| Intent confirmed by owner? |  |
| Confirmed at |  |

## P2 Options Table

List every alternative considered, including the do-nothing / status-quo option.
Name each in five words or fewer.

| Option (<=5 words) | Optimizes for | Sacrifices | Key evidence refs | Reversibility note |
|---|---|---|---|---|
|  |  |  |  |  |

An options table with only the chosen option and no genuine alternatives is a
defect: a decision with no considered alternative was not a decision.

## P3 Source-Grounded Rationale (source-grounded-decisions)

Every claim that depends on a version, an API, a spec, a price, a limit, a date,
or any fact that can drift must cite an authoritative source or be explicitly
flagged `unverified`. Do not state version- or spec-sensitive facts from memory.

| Claim | Version/spec-sensitive? | Source (authoritative ref) | Checked at | Status |
|---|---|---|---|---|
|  |  |  |  | `verified` |

Status values: `verified`, `unverified`, `stale`.

If **material** rationale (a claim the chosen path depends on) is `unverified` or
`stale`, the memo reports `incomplete analysis` and the confirmation gate stays
`not-ready` until it is grounded or the dependency is removed.

## P4 Chosen Path

Record the single chosen option and tie its reasoning to the confirmed success
criteria from P1.

| Field | Value |
|---|---|
| Chosen option |  |
| Why it best meets the confirmed success criteria |  |
| Success criteria it does **not** fully meet (residual risk) |  |
| Second-best option (fallback if reversed) |  |

### Single-Path Decision Discipline

For a decision, do **not** keep multiple chosen paths alive "just in case".
Evaluate the alternatives in P2, **select the single best path, and record it
exclusively.** Keep the second-best option named in the table above so a reversal
has a defined fallback, but the memo commits to one path. The chosen path is
never settled until the fresh-context adversarial pass has attacked it.

## P5 Reversibility And Trip-Wires

State plainly what it costs to undo this decision and what would make the owner
revisit or roll it back.

| Field | Value |
|---|---|
| Reversibility class (mirror metadata) |  |
| Cost of reversal (time / money / risk) |  |
| Who can authorize a reversal |  |

Trip-wires are the conditions under which the decision is reopened or rolled
back. Omitting trip-wires is a defect: a decision with no revisit condition
cannot be reviewed when reality changes.

| Trip-wire condition (observable) | What it signals | Action on trigger (revisit / roll back) |
|---|---|---|
|  |  |  |

## Fresh-Context Adversarial Confirmation (decision-doubt-loop)

Before the decision stands, a reviewer attacks it. The reviewer MUST be a fresh,
independent context (and a different agent family for `one-way-door` calls), not
the recommender and not an inline "let me re-read my own memo" self-review. An
inline self-review is the exact failure mode `decision-doubt-loop` exists to
prevent.

### Handoff contract

- The recommender hands the memo to the reviewer as a bounded packet via
  `cross-agent-delegation`. Task packets use
  `schema_version: cross-agent-delegation.task.v1`; the returned review uses
  `schema_version: cross-agent-delegation.result.v1`. The packet hands over the
  decision, the options, the rationale, and the trip-wires, not a "trust me, this
  is right" summary.
- The returned review is untrusted evidence until the parent validates schema,
  provenance, limitations, and authority boundaries.
- **The reviewer does not rubber-stamp.** It must independently attack the chosen
  path: challenge the intent framing, the dropped alternatives, the unverified
  claims, the reversibility class, and the missing trip-wires, and report what it
  found. Restating the recommender's rationale is NOT confirmation and must be
  rejected.

### BLOCKED escape

If fresh-context review is unavailable:

- For a `one-way-door` decision, output `BLOCKED-FRESH-CONTEXT-UNAVAILABLE`,
  state the gated decision, keep the gate `not-ready`, and ask the owner for
  direction rather than self-confirming. An irreversible call must never stand on
  a self-review.
- For a `two-way-door` decision, the owner may accept the `BLOCKED` escape with
  an explicit recorded sign-off, on the understanding that the decision is
  reversible if the skipped review would have caught a flaw.

### Adversarial confirmation gate

| Check | Evidence | Status | Repair if failed |
|---|---|---|---|
| Reviewer is a fresh, independent context (distinct family for one-way doors) |  |  |  |
| Reviewer attacked the decision and did not merely restate the rationale |  |  |  |
| Dropped alternatives were challenged, not assumed away |  |  |  |
| Every material version/spec-sensitive claim is `verified` or the gap is owned |  |  |  |
| Reversibility class is justified (one-way door not mislabeled reversible) |  |  |  |
| Trip-wires are present and observable |  |  |  |
| Returned review packet schema, provenance, and limitations validated |  |  |  |

Status values: `pass`, `flag`, `fail`, `not-applicable`.

## P7 Confirmation Gate Status

| Field | Value |
|---|---|
| Confirmation gate status (`confirmed` / `not-ready`) |  |
| Unresolved objections (list) |  |
| Material rationale unverified? (if yes -> `incomplete analysis`) |  |
| Decision status set to |  |

Gate rule: the decision may move to `confirmed` **only** when the adversarial
confirmation gate passes, no unresolved blocking objection remains, and no
material rationale is `unverified` or `stale`. Otherwise report `not-ready`, list
the unresolved objections, and report `incomplete analysis` when material
rationale is unverified. The status moves to `reversed` when a trip-wire fires
and the owner rolls the decision back, and to `superseded` when a later decision
replaces it.

## Failure Modes

| Failure mode | Detection point | Recovery |
|---|---|---|
| Decision recorded without source grounding | P3 rationale table | Cite an authoritative source per material claim or flag it `unverified`; keep the gate `not-ready` and report `incomplete analysis`. |
| Recommender confirmed own decision | Adversarial confirmation gate | Reject; re-run confirmation with a fresh, independent context that attacks the decision itself. |
| One-way door treated as reversible | Reversibility class check | Reclassify as `one-way-door`; require mandatory fresh-context confirmation before standing. |
| Trip-wires omitted | P5 trip-wire table | Add observable revisit/rollback conditions before confirming; a decision with no trip-wire cannot be reviewed later. |
| Rationale written before intent confirmed | P1 intent confirmation | Stop; run `intent-interview`, confirm the decision and success criteria, then redo rationale. |
| Only the chosen option in the options table | P2 options table | Add the genuinely considered alternatives, including status quo, or record that none existed. |
| Reviewer rubber-stamped the rationale | Adversarial confirmation gate | Reject; require the reviewer to attack the decision and report independent findings. |
| Fresh context unavailable for an irreversible call | BLOCKED escape | Output `BLOCKED-FRESH-CONTEXT-UNAVAILABLE`, keep the gate `not-ready`, and ask the owner; do not self-confirm. |
| Provider/budget state copied into the reviewer packet | Packet validation | Remove; keep model and policy state in this memo only. |

## Final Outcome

Confirmed decision:

Unresolved objections:

Trip-wires to monitor:

Reversal fallback (second-best path):

Recommended next action:
