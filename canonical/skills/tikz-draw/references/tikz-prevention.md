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

Additional compatibility rules currently enforced:

- document-facing output must use the adjustbox environment wrapper
- standalone outputs must load `adjustbox`
- standalone width-fit outputs must avoid `standalone[tikz]`
- verification-sensitive graph closures should use explicit final edges instead of `cycle`

Phase note:

- In phase 0/1 these rules define the shared contract and initial checker surface.
- They are intentionally narrower than the later rendered-artifact semantic verifier.
