# TikZ Prevention

This reference names the shared source-preflight rules used by the semantic-verifier slice.

Primary rule IDs:

- `P1_BOXED_NODE_DIMENSIONS`
  - boxed text-bearing nodes should declare explicit width, height, or text width
- `P2_COORDINATE_MAP`
  - nontrivial diagrams should include a coordinate-map comment block
- `P3_BARE_SCALE`
  - bare `scale=` is not acceptable without matching node scaling
- `P4_DIRECTIONAL_EDGE_LABELS`
  - edge labels should include explicit directional or anchoring placement
- `P5_EXTRACT_FRESHNESS`
  - extracted figures must carry freshness metadata and stay aligned with the source-of-truth file
- `P7_APPROVAL_PROVENANCE`
  - strict approval must bind the report to the current generated artifacts and hashes
- `P8_SYMMETRY_CONTRACT`
  - strict approval requires a structured symmetry contract and must fail closed if it is missing or violated
- `P9_DESIGN_CONTRACT`
  - scoped manuscript-facing semantic figures require a visual-semantic design contract and must fail closed if it is missing or violated

Additional compatibility rules currently enforced:

- document-facing output must use the adjustbox environment wrapper
- standalone outputs must load `adjustbox`
- standalone width-fit outputs must avoid `standalone[tikz]`
- verification-sensitive graph closures should use explicit final edges instead of `cycle`
- do not add, remove, or restyle emphasis marks without recording their intended mathematical role
- do not simplify visual encodings in a way that erases a distinction made by the caption or nearby prose
- do not treat user corrections about boxes, fills, labels, arrows, or regions as blind patch instructions; reopen the semantic design checkpoint when the correction changes the design model

Phase note:

- Source-preflight rules alone do not approve a figure.
- After any generated or edited figure, the agent must run `approve` and iterate until it passes or report the blocked state.
