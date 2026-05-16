# TikZ Draw Review Rules

These are the review-stage expectations after `render`, `check`, and usually `compile`.

See also:

- `tikz-measurement.md` for the named `review-visual` pass IDs used by the semantic-verifier slice

## Verdicts

- `APPROVED`
- `NEEDS_REVISION`
- `REJECTED`

## Review dimensions

1. Structural correctness
   - backend matches diagram family
   - family-level semantic approval still depends on later family handlers; extractor-only review does not prove node and edge relationships yet
2. Width-fit contract
   - diagram is wrapped in the `adjustbox` environment with `max width=\textwidth`
   - standalone output loads `adjustbox`
3. Layout hygiene
   - extractor-backed `review-visual` checks page margins and generic text-to-shape clearance where possible
   - spacing is readable after width-fit scaling
   - no obvious overlap or clipping
4. Maintainability
   - named styles instead of repeated inline fragments
   - semantic node names where possible
   - grouping and alignment use structural libraries
5. Traceability
   - figure outputs preserve `figure_id`
   - research-driven diagrams preserve `source_ids`

## Review notes format

Each review should be concise and concrete:

- verdict
- failed rules
- file path
- one-line corrective action per failed rule

## Phase 5 note

- `review-visual` now refreshes `render-semantics.json` from the compiled PDF.
- `verify-semantic` now supports the current render-generated `flowchart`, `dag`, `tree`, and supported-square `commutative` families.
- `verify-semantic` still fails closed with `UNSUPPORTED_FAMILY` for unsupported families and unsupported inputs.

## Width-fit caveat

`adjustbox` scales text as well as geometry. This is expected behavior in phase 1 and should not be flagged as a defect unless the brief explicitly asks to keep text size fixed.
