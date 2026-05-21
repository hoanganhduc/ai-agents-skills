# Lean Verifier Policy

Runtime behavior: incomplete analysis.

This policy defines trust tiers for Lean verification evidence. It is a design
template only. It does not run Lean, AXLE, SafeVerify, `lean4checker`,
Comparator, CI, notebooks, or training workflows.

## Trust Tier Enum

- `T0_STATIC_INTAKE`: static metadata only; no proof-validity claim.
- `T1_LOCAL_BUILD`: local Lean/Lake command completed under a pinned
  environment; no theorem-intent claim by itself.
- `T2_AXLE_ACCEPTED`: AXLE accepted the formal statement as written; no
  theorem-intent match claim by itself. T2 does not imply that the formal
  statement matches the informal theorem.
- `T3_STRICT_VERIFIER`: strict verifier run with full evidence set.

## Conjunctive Claim Table

| Claim | Required evidence |
|---|---|
| static intake detected metadata | T0 JSON and fixture coverage |
| local artifact compiled | T1 command, toolchain, Mathlib commit, hashes |
| AXLE accepted statement as written | T2 AXLE evidence, request/response hashes, tool version |
| strict verifier accepted artifact | T3 verifier evidence plus theorem-intent review |
| relation to informal theorem | theorem-intent review status `source_pinned_matched` plus artifact hash |

No final proof claim is allowed when any required evidence is missing, stale, or
caveated.

## Two-Axis Trust State

- formal artifact state:
  - unchecked
  - statically_inspected
  - compiled
  - axle_accepted_as_written
  - strict_verified
- theorem-intent state:
  - not_reviewed
  - source_missing
  - source_pinned_unmatched
  - source_pinned_matched
  - ambiguous

## Theorem-Intent Review Schema

- reviewer:
- date:
- informal source path:
- informal source hash:
- formal statement path:
- formal statement hash:
- relation:
- status:
- limitations:

## T3 Hash Set

T3 requires:

- full transitive import/dependency closure
- resolved Mathlib commit
- Lean toolchain
- verifier version
- formal statement hash
- proof artifact hash
- theorem-intent review record hash
- command line and normalized environment hash

## Allowed-Axiom Policy

- T0: `unchecked`
- T1: reported from local tooling only if explicitly queried
- T2: AXLE-reported and non-adversarial unless independently checked
- T3: observed axioms must be inside the default allowlist or the claim is
  rejected or caveated

## Sorry Policy

Any permitted `sorry` blocks uncaveated final claims. Static lexical scans are
advisory only.

## Unsafe Declaration Policy

`native_decide`, `implemented_by`, FFI, `@[extern]`, opaque declarations, and
oracle-style surfaces default to rejection for uncaveated final claims unless a
separate trust-boundary record explicitly justifies them.

## Comparator Status

Comparator is inactive in the default verifier matrix pending primary citation
and implementation evidence.

## Cross-Tier Environment Invariant

T1, T2, and T3 evidence must record the same intended Lean toolchain and
resolved Mathlib commit or explicitly explain the drift. Silent drift blocks
promotion between trust tiers.
