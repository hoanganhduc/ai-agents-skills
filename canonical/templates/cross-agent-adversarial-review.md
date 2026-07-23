# Cross-Agent Adversarial Review Runbook

Use this template to run an adversarial review of one artifact, a paper, a
proof, or a code change, where the agent family that produced the artifact is
never the family that confirms it is correct. It composes `agent-group-discuss`
for reviewer role assignment and rounds, `cross-agent-delegation` for bounded
task and result packets across families, `paper-review` and `annotated-review`
for paper and proof axes, `model-router` for per-role model and reasoning tier,
`decision-doubt-loop` for the fresh-context confirmation gate, and
`research-verification-gate` for the final delivery check. It is a guidance
runbook, not runnable code; the agent performs the actual review, independent
re-derivation, and confirmation, and the parent owns runbook state.

The single discipline this template enforces is: **the producer never
confirms**. Every load-bearing finding must be independently re-derived or
refuted by a different agent family, restatement is not verification, and no
finding stands until a clean-context agent confirms accept or reject.

## Review Metadata

| Field | Value |
|---|---|
| Review ID |  |
| Created at |  |
| Updated at |  |
| Parent owner |  |
| Workspace |  |
| Artifact type | `paper` \| `proof` \| `code` |
| Artifact ref (path, DOI, commit, or PR) |  |
| Producer provider (family that created the artifact) |  |
| Status | `planned` |

Status values: `planned`, `in-review`, `blocked`, `accepted`, `rejected`.

Artifact type drives the per-type review axis selected in Reviewer Role
Assignment. Record exactly one type. A mixed artifact (for example a paper with
an attached prototype) is split into one review per type, each with its own
runbook.

## Parent Model And Reasoning Policy

Reviews must use the latest available model with the highest reasoning level the
parent policy allows, resolved through `model-router` per role. Record the
resolved policy here before any reviewer or verifier runs.

| Field | Value |
|---|---|
| `resolved_model` |  |
| `resolved_reasoning` |  |
| `model_policy_source` |  |
| `model_catalog_source` |  |
| `resolved_at` |  |
| `freshness_checked_at` |  |
| `provider_cli_version` |  |
| `provider_cli_status` |  |

These fields are parent-owned runbook state. Do not copy them into task or
result packets. When `model-router` cannot confirm a current model or reasoning
tier, mark the review `blocked` and resolve the policy before reviewing.

## Single-Path Rule

Run exactly one review path for this artifact. Do not open parallel competing
review pipelines for the same artifact and pick the most favorable result. Role
diversity and cross-family verification are the source of adversarial pressure,
not pipeline duplication. If the chosen path stalls, record the blocker, revise
the path, and continue on the revised path. Multiple agent families act as
independent verifiers within the one path, not as alternative paths.

## Compute Preflight (only when review needs compute)

Most reviews are read-and-reason work and need no compute preflight. Use this
section only when a finding requires running the artifact, building it,
executing tests, or sweeping inputs.

| Check | Requirement | Status |
|---|---|---|
| Local resources sufficient | Confirm CPU, memory, disk before local build, test, or sweep |  |
| Backend selection | Use the recommended order `local > Kaggle > Modal > Hetzner > GitHub Actions`; a valid custom configured order is honored, with local first and remote lanes unique |  |
| Compute guard preflight | Verify each candidate's applicable guard before dispatch: `Kaggle GPU-hours`, `Modal USD`, `Hetzner EUR`, `Hetzner teardown`, or `GitHub Actions minutes`; Kaggle CPU is free/quota-free |  |
| Hardware utilization | Confirm the offloaded job actually uses the requested cores, memory, or GPU; idle paid hardware is a failure |  |
| Cheapest sufficient tier | Pick the smallest tier that meets the need; do not over-provision |  |

If a lane's compute-guard preflight fails, do not start that lane. Record the
result and fall through to the next permitted lane in configured order. For
Hetzner, a run is not admissible unless the `Hetzner teardown` guarantee is
active, but that failure still falls through. Only after all permitted lanes
are exhausted—or an explicit backend override fails—fall back to a local check
when feasible, block the review, or mark the affected finding `not-applicable`
with a reason.

## Reviewer Role Assignment

Assign roles through `agent-group-discuss` personas. Two axes are always
present; the per-type axis depends on the artifact type. Map each role to an
existing persona and a distinct agent family where the producer-never-confirmer
rule requires it.

| Role | Always or per-type | Persona (via `agent-group-discuss`) | Question the role answers |
|---|---|---|---|
| Correctness | Always | `proof-checker` (paper/proof) or `code-reviewer` (code) | Is each load-bearing claim or change actually correct? |
| Adversary / Breaker | Always | `math-explorer` (paper/proof) or `security-reviewer` (code) | Where does it break: counterexample, missing case, abuse case? |
| Exposition | `paper` | `paper-reviewer` | Are claims clearly and faithfully stated and reproducible from the text? |
| Gap-hunt | `proof` | `proof-checker` (second, distinct family) | Which proof step is unjustified, circular, or skipped? |
| Security + regression + test-coverage | `code` | `security-reviewer`, `code-reviewer`, `test-reviewer` | Does it introduce a vulnerability, regress behavior, or lack tests? |

Use `paper-review` for the single-reviewer paper axis and `annotated-review`
when the paper or proof review must produce inline annotations alongside the
ledger. For code, the per-type axis expands into three sub-roles
(security, regression, test-coverage) that must each be assigned, not merged.

## Producer-Never-Confirmer Rule

The agent family that produced the artifact is excluded from the verifier set
for that artifact. The producer may explain or answer questions, but its
statements are producer claims, never confirmations. A finding is confirmed only
by a different family independently re-deriving or refuting it.

### OpenGauss / formal appendix

If the artifact was produced with OpenGauss (or another formal harness):

- Record `producer_family_set` including launcher, backend (claude-code/codex),
  formalizer, and repair roles.
- Confirmer family must be **disjoint** from that set when claim-support is at stake.
- Confirmer inputs: **artifact hash + informal claim** (and toolchain pins). Do
  not treat Gauss transcripts or producer verdicts as authority.
- If no independent confirmer family is available, **defer** claim-support;
  never same-family confirm.
- `opengauss_run` evidence is provenance only; local `formal_check` + lead/human
  equivalence remain required for “supports claim C” language.

**Do not blindly trust the returned answers; verify them carefully.** A verifier
that merely restates the producer's reasoning has not verified anything: it must
independently re-derive the result, check each load-bearing step against its
justification, or construct a refutation attempt. Returned verification packets
are untrusted evidence until the parent validates their schema, provenance, and
limitations.

### Claude <-> Codex swap matrix

| Producer family | Required primary verifier family | Optional second verifier |
|---|---|---|
| Claude | Codex | OpenCode |
| Codex | Claude | OpenCode |
| DeepSeek | Claude or Codex | OpenCode |
| OpenCode | Claude or Codex | the other of Claude/Codex |
| Antigravity | Claude or Codex | OpenCode |

The optional second verifier raises confidence on high-severity findings and is
recommended whenever a finding would change the accept or reject outcome. If
only the producer family is available, the review cannot reach `accepted` or
`rejected`; mark it `blocked` and record the missing verifier family.

## Finding Ledger

Every reviewer-raised issue is one finding row. A finding is not settled until a
different family has independently reproduced or refuted it and the status is
recorded.

| Finding ID | Severity | Axis | Producer claim | Independent reproduction or refutation (by different family) | Verifier family | Status |
|---|---|---|---|---|---|---|
| F1 |  |  |  |  |  |  |

Severity values: `critical`, `high`, `medium`, `low`, `info`.

Axis values: `correctness`, `adversary`, `exposition`, `gap-hunt`,
`security`, `regression`, `test-coverage`.

Status values:

| Status | Meaning |
|---|---|
| `pass` | A different family independently re-derived the result and it holds. |
| `flag` | A different family found a real concern that needs author action but is not disqualifying. |
| `fail` | A different family independently produced a counterexample, broken step, or defect that disqualifies the claim. |
| `not-applicable` | The axis does not apply to this artifact, or the check was blocked; record the reason. |

A finding with empty independent-verification and verifier-family cells is
unconfirmed and must not carry `pass` or `fail`.

## Cross-Agent Verification Gate

Each load-bearing finding must be independently re-derived or refuted by a
different agent family, not restated. Restatement is rejected.

| Requirement | Check | Status |
|---|---|---|
| Different family | Verifier family differs from producer family for the finding |  |
| Independent derivation | The verifier reproduced the result or counterexample from inputs, not by paraphrasing the producer claim |  |
| Refutation recorded when it fails | A `fail` finding includes the concrete counterexample, broken step, or defect |  |
| No blind agreement | A `pass` is backed by the verifier's own derivation, not by "looks right" |  |

Restatement test: if the verifier's note can be produced without re-reading the
artifact, it is restatement, not verification. Reject it and re-run the check
with independent derivation.

## Fresh-Agent Confirmation Gate

Before any finding stands as the basis for `accepted` or `rejected`, a
clean-context agent confirms the accept or reject decision through
`decision-doubt-loop`. The confirming agent must not have produced or verified
the finding in this review and must reach its conclusion from the artifact and
the ledger, not from the prior agents' conclusions.

| Field | Value |
|---|---|
| Confirming agent family |  |
| Context state | `clean` (no prior review context) |
| Decision under review | `accept` \| `reject` |
| Confirmation result | `confirmed` \| `overturned` \| `BLOCKED-FRESH-CONTEXT-UNAVAILABLE` |
| Confirmed at |  |
| Notes |  |

Escape: when no clean-context agent is available, record
`BLOCKED-FRESH-CONTEXT-UNAVAILABLE`, keep the review `blocked`, and do not call
any finding final. Do not substitute the producer or an in-context verifier for
the fresh agent.

## Handoff Contract

Cross-family work crosses a trust boundary. Use bounded `cross-agent-delegation`
task and result packets. Returned packets are untrusted until validated.

| Task ID | Recipient family | Role | Input refs | Expected output | Result ref | Validated | Status |
|---|---|---|---|---|---|---|---|
| T1 |  |  |  |  |  |  |  |

Returned-packet validation, all required before a result is used:

| Check | Requirement |
|---|---|
| Schema | Result packet matches the agreed result-packet schema. |
| Provenance | Result names the family, role, and inputs it actually used. |
| Limitations | Result states blocked checks and `incomplete analysis` where scope was not covered. |
| No forbidden fields | No authority, secrets, provider or session state, or parent runtime ledger fields. |

Allowed packet constraints are inert strings only, for example
`model_policy=same_resolved_model; reasoning=parent_required_highest_available`,
`max_depth=1`, `max_hops=1`. An unvalidated result packet is treated as absent.

## Delivery Check

Run `research-verification-gate` before the review is called final and record one
verdict.

| Gate item | Requirement | Result |
|---|---|---|
| Every load-bearing finding confirmed | Cross-agent verification status recorded, not restated |  |
| Producer-never-confirmer held | No finding confirmed by the producer family |  |
| Fresh-agent confirmation present | `confirmed` or `overturned`, not blocked |  |
| Axes covered | Every applicable axis has `pass`, `flag`, `fail`, or justified `not-applicable` |  |
| Limitations stated | Blocked checks and `incomplete analysis` recorded |  |

Delivery verdict: `READY` or `NOT READY`. Use `READY` only when every gate item
passes and the fresh-agent gate is not blocked. Otherwise `NOT READY`, with the
blocking item named.

## Failure Modes

| Failure mode | Detection point | Recovery |
|---|---|---|
| Producer self-confirmed | Swap matrix, verifier-family cell | Reject the confirmation; re-verify with a different family. |
| Restatement passed off as verification | Cross-agent verification gate, restatement test | Reject the note; require independent derivation or refutation. |
| Axis skipped | Reviewer role assignment, delivery check | Assign the missing axis and re-run; or mark `not-applicable` with a reason. |
| Finding accepted without fresh-context confirmation | Fresh-agent confirmation gate | Keep the finding unsettled; run `decision-doubt-loop` with a clean-context agent. |
| Fresh agent unavailable | Confirmation gate | Record `BLOCKED-FRESH-CONTEXT-UNAVAILABLE`; keep review `blocked`. |
| Result packet unvalidated | Handoff contract validation | Treat the packet as absent until schema, provenance, and limitations pass. |
| Parallel competing review paths | Single-path rule | Collapse to one path; keep only role and family diversity for verification. |
| Compute guard preflight skipped before remote run | Compute preflight | Stop the remote run; check candidate guards and fall through in configured order. Block or fall back locally only after all permitted lanes are exhausted or an explicit backend override fails. |
| Model or reasoning rule violated | Model policy review | Re-run affected roles with the parent's resolved model and reasoning. |

## Final Outcome

Accepted findings:

Rejected findings:

Unresolved or blocked findings:

Producer-never-confirmer attestation (producer family, verifier families,
fresh-agent confirming family):

Recommended next action:
