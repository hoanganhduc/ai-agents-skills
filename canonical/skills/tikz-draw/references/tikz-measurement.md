# TikZ Measurement

This reference names the additive measured-review passes used by `review-visual`.

Current pass IDs:

- `V1_LABEL_GAP`
  - reserved for label-gap and lane-clearance checks
- `V2_BOUNDARY_CLEARANCE`
  - reserved for label-to-shape and boundary-clearance checks
- `V3_PAGE_MARGIN`
  - reserved for page, slide, or frame-edge margin checks
- `V4_CURVE_POINT_PLACEMENT`
  - reserved for curve-depth, plotted-point, and geometry-sensitive placement checks

Phase note:

- In phase 0/1 the pass IDs and report fields are being locked.
- The actual rendered-artifact measurement implementation lands later.
- Do not treat `review-visual` as strong semantic approval yet.
