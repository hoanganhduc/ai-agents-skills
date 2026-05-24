# TikZ Prevention Rules

Shared rule IDs for the semantic-verifier slice:

- `P1_BOXED_NODE_DIMENSIONS`
- `P2_COORDINATE_MAP`
- `P3_BARE_SCALE`
- `P4_DIRECTIONAL_EDGE_LABELS`
- `P5_EXTRACT_FRESHNESS`
- `P7_APPROVAL_PROVENANCE`
- `P8_SYMMETRY_CONTRACT`
- `P9_DESIGN_CONTRACT`

Compatibility rules retained in the runtime helper:

- document-facing output must use the adjustbox environment wrapper
- standalone outputs must load `adjustbox`
- standalone width-fit outputs must not use `standalone[tikz]`
- verification-sensitive graph closures should use explicit final edges instead of `cycle`
- scoped manuscript-facing semantic figures require a visual-semantic design contract
- metadata, notation, callouts, and proof annotations must not be styled or counted as graph objects unless the design contract explicitly requires that
- region fills must not hide graph structure or make membership ambiguous

Phase note:

- Source preflight is not final approval.
- Strict final approval requires `approve` to pass provenance, rendered overlap, design, semantic, and symmetry-contract gates.
