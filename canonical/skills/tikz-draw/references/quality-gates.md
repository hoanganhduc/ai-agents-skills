# Quality Gates

Prevention rules:

- document-facing output must contain the adjustbox environment wrapper
- standalone output must load `adjustbox`
- standalone output using the required wrapper must use plain `standalone` class
- avoid bare `scale=` as the main width-fit strategy
- prefer explicit label placement on nontrivial edges
- prefer structural placement over absolute coordinates

Shared rule IDs in the semantic-verifier slice:

- `P1_BOXED_NODE_DIMENSIONS`
- `P2_COORDINATE_MAP`
- `P3_BARE_SCALE`
- `P4_DIRECTIONAL_EDGE_LABELS`
- `P5_EXTRACT_FRESHNESS`
- `P7_APPROVAL_PROVENANCE`
- `P8_SYMMETRY_CONTRACT`
- `P9_DESIGN_CONTRACT`

Strict approval gate:

- Only `approve` may provide final figure approval.
- `approve` passes only when static preflight, compile, artifact freshness/provenance, rendered overlap checks, semantic verification, and the declared symmetry contract pass.
- `render`, `extract`, `compile`, `check`, `review --tex`, `review-visual`, and `verify-semantic` are preflight or artifact commands, not final approval.
- `verify-design` is a component gate for visual-semantic design. It is required
  for scoped manuscript-facing semantic figures but does not replace `approve`.
- After every generated, extracted, refactored, or manually edited figure, rerun `approve`; fix failures until it passes or report the blocked state.

Review dimensions:

- structural correctness
- width-fit contract
- layout hygiene
- maintainability
- traceability
- measured visual review via `review-visual` is a component gate and does not imply final approval without `approve`
- rendered overlap checks cover text/text, text/shape, line/text, line/non-incident-shape, and non-group shape overlap where the rendered primitives are measurable
- visual-semantic roles: boxes, fills, outlines, arrows, labels, regions, and
  callouts must declare whether they represent graph structure, annotation,
  correspondence, gadget region, highlight, or legend
- manuscript semantic consistency: nonstandard visual encodings must match the
  inspected caption/prose claims recorded in the design checkpoint
- reader recoverability: graph structure, metadata, gadget boundaries, and
  replacement correspondences must be distinguishable from the figure plus
  caption without inspecting the TikZ source
- declared symmetry-contract satisfaction is required for strict approval

Verdicts:

- `APPROVED`
- `NEEDS_REVISION`
- `REJECTED`
- `BLOCKED_INPUT`
- `BLOCKED_ENVIRONMENT`
- `UNSUPPORTED_FAMILY`

Review output should stay concrete:

- verdict
- failed rules
- file path
- one-line corrective action per failed rule
