# Verification

Verification is selective. Only installed and enabled managed artifacts from the
installer state are checked.

Current skill checks:

- `L1 file-exists`
- `L2 metadata-valid`
- `L3 managed-marker`
- `L4 no-secret-leak`
- `L5 agent-visible`

Current instruction-block checks:

- `S1 file-exists`
- `S2 managed-block-present`
- `S3 no-secret-leak`

Current support-file checks:

- `A1 file-exists`
- `A2 managed-marker`
- `A3 no-secret-leak`

The verifier intentionally skips skills and artifacts that were not installed.
Runtime smoke tests, runner-specific `doctor` commands, and direct
`agent-loads-config` checks are not automatic yet; use `precheck` and the
agent's own diagnostics for those layers.
