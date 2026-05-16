# TikZ Prevention Rules

Shared rule IDs for the semantic-verifier slice:

- `P1_BOXED_NODE_DIMENSIONS`
- `P2_COORDINATE_MAP`
- `P3_BARE_SCALE`
- `P4_DIRECTIONAL_EDGE_LABELS`
- `P5_EXTRACT_FRESHNESS`

Compatibility rules retained in the runtime helper:

- document-facing output must use the adjustbox environment wrapper
- standalone outputs must load `adjustbox`
- standalone width-fit outputs must not use `standalone[tikz]`
- verification-sensitive graph closures should use explicit final edges instead of `cycle`

Phase note:

- This file defines the shared prevention contract and naming.
- It does not claim that every later semantic rule is implemented yet.
