# Manifest Schemas

The current installer validates its existing manifests in Python and keeps
manifest files in JSON-compatible YAML so the bootstrap does not need external
dependencies.

OpenClaw integration started with schema contracts only. The schemas under
`openclaw/` define the shape of inventory, denylist, redaction, alias,
evidence, and dry-run/apply manifests. The Phase 1 scanner emits only the
sanitized inventory schema. The Phase 2 manifest builder emits only unreviewed
dry-run manifests. Phase 3 applies only approved manifests to explicit fake
target roots and remains disconnected from real-system installer apply
behavior. Phase 4 records explicit evidence objects for scoped native-support
claims without changing installer behavior. Phase 5 verifies hook and schedule
material remains inert.

Phase 0 schema rules:

- schemas are strict and versioned
- source inventory and target actions stay separate
- dry-run/apply manifests use stable action identifiers
- OpenClaw source data is non-installable by default
- native agent support claims require separate evidence artifacts

Phase 1 scanner rules:

- `openclaw.inventory.v1` is review-only and non-installable
- source roots must be passed explicitly
- filesystem metadata is collected with `lstat`; file contents are not opened
- private categories are counted with reason codes instead of being parsed

Phase 2 manifest rules:

- `openclaw.apply-manifest.v1` is review-only until a later approved apply gate
- manifests are built from saved sanitized inventories, not live rescans
- target roots must be passed explicitly and are inspected read-only
- collisions become `no-op` plus `skip-report`
- manifests remain `unreviewed` by default

Phase 3 fake-root apply rules:

- approval is separate from immutable manifest content
- `approval.approval_hash` must match `manifest_id`
- real-system target roots are refused
- writes are limited to deterministic review files from sanitized metadata
- uninstall removes only unchanged journaled review files

Phase 4 evidence rules:

- evidence records are content-addressed `openclaw.evidence.v1` objects
- native-loader evidence requires an agent version
- fixture and CI evidence do not create native support claims
- agents without native evidence remain reference-only

Phase 5 persistence rules:

- hook and scheduler material is allowed only as inert `no-op` metadata
- enabled persistence writes are blocked
- no shell profile, cron, launchd, systemd, wrapper, or agent hook writes are
  implemented in this OpenClaw pipeline
