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

Review dimensions:

- structural correctness
- width-fit contract
- layout hygiene
- maintainability
- traceability
- measured visual review via `review-visual` is additive in phase 0/1 and does not yet imply strong semantic approval

Verdicts:

- `APPROVED`
- `NEEDS_REVISION`
- `REJECTED`

Review output should stay concrete:

- verdict
- failed rules
- file path
- one-line corrective action per failed rule
