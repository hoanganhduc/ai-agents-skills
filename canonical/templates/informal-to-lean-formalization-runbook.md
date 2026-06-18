# Informal-to-Lean Formalization Runbook

Use this template to take an informal proof and turn it into Lean declarations
that both typecheck and actually support the informal claim. It is local-first:
it decides whether and at what granularity to formalize before any Lean is
written, maps every informal step to a Lean declaration, reuses existing Mathlib
results instead of re-proving them, and gates acceptance behind a scanner-first
verification that keeps **typecheck status** and **claim-support status**
strictly separate. A theorem that compiles but escapes via `sorry`, a vacuous
hypothesis, or an added axiom is NOT a supported claim.

It composes `lean-formalization-intake` for the local-first suitability
decision, `formal-skeleton-helper` for minimal statement stubs and namespace
wrappers, `lean-explore-mcp` for reuse search before stating new lemmas,
`lean-strict-verification-gate` for the scanner-first verification, and
`cross-agent-delegation` for the fresh-context cross-check handoff.
`decision-doubt-loop` supplies the fresh-context discipline for the acceptance
decision. It is a guidance runbook, not runnable code; the agent runs the Lean
toolchain, the scanner, and the cross-check.

## Formalization Metadata

| Field | Value |
|---|---|
| Run ID |  |
| Informal statement ref (paper / proof / lemma id) |  |
| Target Lean package (Mathlib version / project) |  |
| Lean toolchain version |  |
| Created at |  |
| Updated at |  |
| Parent owner |  |
| Workspace (holds skeleton, sorry ledger, scanner output, cross-check packet) |  |
| Status | `intake` |

Status values: `intake`, `suitable`, `in-progress`, `blocked`, `verified`,
`abandoned`.

`verified` is reachable only after a clean typecheck AND an independently
confirmed claim-support status from a fresh context. A clean compile alone never
sets `verified`.

## Phase Plan

Apply every phase, in order.

| Phase | Objective | Skill | Inputs | Outputs | Status |
|---|---|---|---|---|---|
| F1. Intake & suitability | Local-first decision on whether to formalize and at what granularity. | `lean-formalization-intake` |  |  |  |
| F2. Declaration map | Map each informal step to a Lean declaration; search for reusable Mathlib results first. | `lean-explore-mcp` |  |  |  |
| F3. Skeleton | Emit minimal statement stubs and namespace wrappers with explicit `sorry` placeholders. | `formal-skeleton-helper` |  |  |  |
| F4. Fill & track | Discharge each `sorry`; track blocking lemmas and candidate Mathlib declarations. | `lean-explore-mcp` |  |  |  |
| F5. Strict verify | Scanner-first verification; report typecheck status and claim-support status separately. | `lean-strict-verification-gate` |  |  |  |
| F6. Fresh-context cross-check | A different context independently confirms both typecheck and claim support. | `cross-agent-delegation`, `decision-doubt-loop` |  |  |  |
| F7. Acceptance | Decide `verified` or `not-ready`; both a clean typecheck and confirmed claim support are required. |  |  |  |  |

## Intake and Suitability Gate (F1)

Run `lean-formalization-intake` before writing any Lean.

- **Local-first.** Resolve the Lean package, toolchain, and any project context
  from the local environment before reaching for external help. Do not start a
  formalization that the local toolchain cannot build.
- **Decide whether to formalize at all.** Some informal proofs are not worth
  formalizing now (too large, depends on unformalized theory, or the informal
  argument has a gap that must be fixed first). Record the decision and reason.
- **Decide the granularity.** Choose the level at which informal steps become Lean
  declarations: a single top-level theorem, a theorem plus a handful of named
  lemmas, or a fuller development. Over-fine granularity invents lemmas Mathlib
  already has; over-coarse granularity hides the gap inside one giant proof.

| Field | Value |
|---|---|
| Formalize? (`yes` / `no` / `defer`) |  |
| Reason |  |
| Chosen granularity |  |
| Known informal gaps to resolve first |  |
| Local toolchain builds? |  |

If intake says `no` or `defer`, set status `blocked` or `abandoned` and stop; do
not produce a skeleton for a proof that should not be formalized yet.

## Declaration Map (F2)

Map each informal step to exactly one Lean declaration. **Before stating any new
lemma, search for an existing Mathlib (or project) declaration that already
proves it** via `lean-explore-mcp`. Re-proving an existing Mathlib result is a
failure mode, not progress.

| Step id | Informal step | Target Lean declaration | Reuse search done? | Existing Mathlib match (name or `none`) | New lemma needed? |
|---|---|---|---|---|---|
| S1 |  |  |  |  |  |

- Use `lean-explore-mcp` `search_summary` to browse, then per-field tools
  (`get_source_code`, `get_docstring`, `get_dependencies`) only on the candidate
  you intend to reuse.
- A step whose informal content matches an existing declaration is **reused, not
  restated**. Record the existing name in the map and cite it in the proof.
- Only steps with `Existing Mathlib match = none` become new lemma statements in
  the skeleton.

## Skeleton Stage (F3)

Run `formal-skeleton-helper` to emit, for every new lemma in the declaration
map, a minimal statement stub and namespace wrapper with an **explicit `sorry`
placeholder**.

- Each stub is a typechecking statement with `sorry` as its body; statements must
  parse and elaborate before any proof work begins.
- Namespace wrappers match the target package's conventions so reused Mathlib
  declarations resolve.
- Every `sorry` introduced here gets a row in the Automation/Sorry Ledger below.
- Do not write proof bodies in this phase; the goal is a compiling skeleton whose
  only gaps are the tracked `sorry` placeholders.

## Automation / Sorry Ledger

One row per `sorry` (or `admit`) in the development. The ledger is the single
source of truth for what remains unproven.

| `sorry_id` | Location (declaration) | Blocking lemma / goal | Candidate Mathlib declaration | Status |
|---|---|---|---|---|
| Z1 |  |  |  | `open` |

Status values: `open`, `in-progress`, `discharged-by-mathlib`,
`discharged-by-proof`, `blocked`.

- `discharged-by-mathlib` means the goal was closed by reusing an existing
  declaration found via `lean-explore-mcp`; record the declaration name.
- A `sorry` is removed from the development only when its row is `discharged-*`.
- **A non-empty ledger of `open` / `in-progress` / `blocked` rows means the claim
  is NOT yet supported, regardless of whether the file compiles.** Lean accepts
  `sorry`-bearing proofs as compiling.

## Strict Verification Gate (F5)

Run `lean-strict-verification-gate`. It is **scanner-first**: a tool scan of the
artifact runs before any human-readable "it's proved" claim, and it reports two
statuses that are never collapsed into one.

### Two separate statuses

| Status | What it means | How it is set |
|---|---|---|
| Typecheck status | The Lean file builds with no errors. | Toolchain build result. |
| Claim-support status | The compiled artifact actually proves the informal claim. | Scanner output: no `sorry`/`admit`, no vacuous hypotheses, no added axioms beyond the allowed set, statement matches the informal claim. |

A clean typecheck with any escape present yields **claim-support = NOT
supported**, even though the file compiles.

### Scanner escape checks

| Escape | Detection | Effect on claim-support |
|---|---|---|
| `sorry` / `admit` present | Scanner over the declaration and its transitive deps | NOT supported |
| Vacuous / contradictory hypotheses (statement provable because the premise is unsatisfiable) | Hypothesis sanity check | NOT supported |
| Added / unexpected `axiom` (or `#print axioms` shows escapes) | Axiom scan vs. allowed set | NOT supported |
| Statement does not match the informal claim (wrong quantifiers, weakened conclusion, missing hypothesis) | Statement-vs-informal comparison | NOT supported |
| `native_decide` / unsafe escape where disallowed by project policy | Scanner over tactic usage | flag; NOT supported unless policy allows |

| Gate field | Value |
|---|---|
| Typecheck status (`pass` / `fail`) |  |
| Claim-support status (`supported` / `not-supported`) |  |
| Scanner output ref |  |
| `#print axioms` ref |  |
| Open sorry-ledger rows at scan time |  |

Do not advance to acceptance while typecheck = `fail` or claim-support =
`not-supported`.

## Fresh-Context Cross-Check (F6)

The agent that wrote the formalization NEVER self-confirms it. A **different
agent family or a clean-context subagent** independently confirms BOTH the
typecheck and the claim-support before acceptance. An inline "let me re-read my
own Lean" is the exact self-confirmation failure this gate prevents
(`decision-doubt-loop` discipline).

- The handoff is a bounded packet via `cross-agent-delegation`. Task packets use
  `schema_version: cross-agent-delegation.task.v1`; the returned confirmation
  uses `schema_version: cross-agent-delegation.result.v1`. The packet hands over
  the Lean artifact, the build command, the scanner command, and the informal
  claim, not a "trust me, it's proved" summary.
- **The cross-checker does not trust the reported result.** It independently runs
  the build and the strict scanner, runs `#print axioms` on the top declaration,
  and confirms the statement matches the informal claim. Restating the
  producer's claim is NOT a cross-check and must be rejected.
- Returned result packets are untrusted evidence until the parent validates
  schema, provenance, and limitations.

| Cross-check field | Value |
|---|---|
| Cross-checker context (different family / clean subagent) |  |
| Independent typecheck result |  |
| Independent claim-support result |  |
| Independent `#print axioms` ref |  |
| Statement-vs-informal-claim confirmed? |  |
| Result packet schema validated? |  |

If a fresh, independent context is unavailable for this acceptance, output
`BLOCKED-FRESH-CONTEXT-UNAVAILABLE`, state the gated step, and ask for user
direction rather than self-confirming.

## Acceptance Decision (F7)

| Decision | Required evidence |
|---|---|
| `verified` | Clean typecheck (producer **and** cross-checker), claim-support = `supported` independently confirmed, sorry ledger has no `open`/`in-progress`/`blocked` rows, no escape flags, statement matches the informal claim. |
| `not-ready` | Any of the above is missing: typecheck fails, claim-support is `not-supported`, an escape is present, the ledger has open rows, or fresh-context confirmation is unavailable. |

`verified` requires **both** a clean typecheck **and** independently confirmed
claim support. A compile alone is never sufficient. On `not-ready`, set status
`in-progress` or `blocked`, record the gap, and continue or stop.

## Single-Path Discipline

For discharging a given `sorry`, do NOT keep multiple speculative proof attempts
alive in parallel. Rank candidate tactics or reuse targets, select the single
highest-probability approach, and pursue it exclusively. Verification is
unconditional: even a proof that "obviously" closes the goal goes through the
strict scanner and the fresh-context cross-check before its ledger row is marked
`discharged-*`. Record the chosen approach so a second-best path is known if the
build contradicts it.

## Heavy-Compute Offload

Most Lean elaboration is local. If a build, large `decide`/`native_decide`
enumeration, or a Mathlib-scale rebuild is too heavy for local execution, route
it through `modal-research-compute` (after local, then Modal, then GitHub
Actions per repo policy).

- **Check Modal credit first** so a build does not fail mid-run for lack of
  credit. If GitHub Actions is used, **check available usage minutes** and
  confirm the runner time is enough for the Lean build.
- Any offloaded build script must **utilize the available hardware** (cores,
  memory) of the chosen backend.
- Re-run the credit/usage check at each dispatching step. Insufficient credit or
  usage maps to a `blocked` decision, not a silent retry.

## Recovery Notes

| Field | Value |
|---|---|
| Current goal |  |
| Last phase completed |  |
| Status |  |
| Next safe action |  |
| Open sorry-ledger rows |  |
| Reused Mathlib declarations so far |  |
| Toolchain / credit remaining |  |

## Failure Modes

| Failure mode | Detection point | Recovery |
|---|---|---|
| Typecheck conflated with claim support | Strict verification gate | Report both statuses separately; a clean compile with an escape is `not-supported`. |
| `sorry`/`admit`-bearing theorem accepted as proved | Scanner escape checks, sorry ledger | Reject; keep the ledger row `open`; do not set `verified` until discharged. |
| Vacuous or contradictory hypothesis makes the statement trivially provable | Hypothesis sanity check | Reject claim-support; fix the statement to match the informal hypotheses. |
| Added axiom slips in (escape via `axiom` / `#print axioms`) | Axiom scan | Reject claim-support; remove the axiom or justify it against the allowed set. |
| Re-proved an existing Mathlib lemma | Declaration map reuse search | Replace the new lemma with the existing declaration; record its name in the map. |
| Formalizer self-confirmed the proof | Fresh-context cross-check | Reject; require a different family / clean context to run the build and scanner itself. |
| Cross-checker merely restated the producer's claim | Cross-check gate | Reject; require an independent build + scanner run and statement-vs-claim check. |
| Statement drifted from the informal claim | Statement-vs-informal comparison | Mark `not-supported`; realign the Lean statement before acceptance. |
| Skeleton written for a proof that should not be formalized yet | Intake gate | Stop at F1; set `blocked`/`abandoned` and record the reason. |
| Parallel speculative proofs kept alive | Single-path discipline | Collapse to the single highest-probability approach; drop the rest. |
| Fresh context unavailable for acceptance | Cross-check gate | Output `BLOCKED-FRESH-CONTEXT-UNAVAILABLE` and ask for direction; do not self-confirm. |
| Modal credit / GitHub Actions usage not checked before a heavy build | Heavy-compute offload | Re-check; mark `blocked` if insufficient. |

## Final Outcome

Verified declarations (with reused Mathlib names):

Not-ready declarations and their gaps:

Open sorry-ledger rows:

Acceptance decision (`verified` / `not-ready`):

Recommended next action:
