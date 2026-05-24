# TikZ Measurement

This reference names the measured-review passes used by `review-visual` and consumed by strict `approve`.

Current pass IDs:

- `V1_LABEL_GAP`
  - reserved for label-gap and lane-clearance checks
- `V2_BOUNDARY_CLEARANCE`
  - reserved for label-to-shape and boundary-clearance checks
- `V3_PAGE_MARGIN`
  - reserved for page, slide, or frame-edge margin checks
- `V4_TEXT_TEXT_OVERLAP`
  - text labels must not overlap one another
- `V5_TEXT_SHAPE_OVERLAP`
  - text must not overlap non-containing shapes
- `V6_LINE_TEXT_OVERLAP`
  - linework must not cross text labels
- `V7_LINE_SHAPE_OVERLAP`
  - linework must not cross non-incident shapes
- `V8_SHAPE_SHAPE_OVERLAP`
  - non-group shapes must not overlap

Phase note:

- `review-visual` remains a component gate.
- Final figure approval requires `approve`, which consumes visual status together with compile, semantic, provenance, and symmetry-contract status.
