# TikZ Draw Review Rules

These are the review-stage expectations after `render`, `check`, and usually `compile`.

See also:

- `tikz-measurement.md` for the named `review-visual` pass IDs used by the semantic-verifier slice

## Verdicts

- `APPROVED`
- `NEEDS_REVISION`
- `REJECTED`
- `BLOCKED_INPUT`
- `BLOCKED_ENVIRONMENT`
- `UNSUPPORTED_FAMILY`

Only `approve` may emit final approval. `check`, `compile`, `review --tex`,
`review-visual`, `verify-design`, and `verify-semantic` are preflight/component checks. A figure
is not done until `approve` exits 0 with `final_verdict=APPROVED`,
`overlap_status=PASS`, scoped `design_status=PASS`, and `symmetry_status=PASS`.

## Review dimensions

1. Structural correctness
   - backend matches diagram family
   - family-level semantic approval still depends on later family handlers; extractor-only review does not prove node and edge relationships yet
2. Width-fit contract
   - diagram is wrapped in the `adjustbox` environment with `max width=\textwidth`
   - standalone output loads `adjustbox`
3. Layout hygiene
   - extractor-backed `review-visual` checks page margins, text clearance, rendered overlaps, and line/shape crossings where possible
   - spacing is readable after width-fit scaling
   - no obvious overlap or clipping
4. Maintainability
   - named styles instead of repeated inline fragments
   - semantic node names where possible
   - grouping and alignment use structural libraries
5. Traceability
   - figure outputs preserve `figure_id`
   - research-driven diagrams preserve `source_ids`
6. Visual-semantic design
   - graph objects, annotations, callouts, correspondence marks, regions, and legends have declared roles
   - metadata and notation are not counted as graph structure
   - region and fill encodings do not obscure graph structure
   - declared caption/prose claims are bound to visual-semantic marks

## Review notes format

Each review should be concise and concrete:

- verdict
- failed rules
- file path
- one-line corrective action per failed rule
- final approval evidence from `approve` when claiming a figure is done

## Phase 5 note

- `review-visual` now refreshes `render-semantics.json` from the compiled PDF.
- `verify-design` checks scoped visual-semantic design contracts before final approval.
- `verify-semantic` now supports the current render-generated `flowchart`, `dag`, `tree`, and supported-square `commutative` families.
- `verify-semantic` still fails closed with `UNSUPPORTED_FAMILY` for unsupported families and unsupported inputs.
- `approve` is the strict final gate and additionally requires artifact provenance, scoped design approval, and a declared symmetry contract.

## Width-fit caveat

`adjustbox` scales text as well as geometry. This is expected behavior in phase 1 and should not be flagged as a defect unless the brief explicitly asks to keep text size fixed.
