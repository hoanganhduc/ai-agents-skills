# Verification

Verification is selective. Only installed and enabled managed artifacts from the
installer state are checked.

Skill checks:

- `L1 file-exists`
- `L2 metadata-valid`
- `L3 agent-visible`
- `L4 runner-doctor`
- `L5 smoke-test`

Settings checks:

- `S1 file-exists`
- `S2 parse-valid`
- `S3 managed-block-present`
- `S4 no-secret-leak`
- `S5 agent-loads-config`
