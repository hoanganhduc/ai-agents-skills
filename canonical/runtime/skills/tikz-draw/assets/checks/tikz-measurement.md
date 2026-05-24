# TikZ Measurement Passes

Shared pass IDs for `review-visual`:

- `V1_LABEL_GAP`
- `V2_BOUNDARY_CLEARANCE`
- `V3_PAGE_MARGIN`
- `V4_TEXT_TEXT_OVERLAP`
- `V5_TEXT_SHAPE_OVERLAP`
- `V6_LINE_TEXT_OVERLAP`
- `V7_LINE_SHAPE_OVERLAP`
- `V8_SHAPE_SHAPE_OVERLAP`

Phase note:

- `review-visual` is a component gate.
- `approve` is the final gate and requires the visual pass plus compile, semantic, provenance, and symmetry-contract checks.
