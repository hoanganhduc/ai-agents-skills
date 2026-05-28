# Plan

## Phases

1. Implement v2 structured research artifacts: evidence ledger, artifact refs, readiness checks, formal target schema, and formal directories.
2. Add local formal skills and runtime wrappers for intake and strict verification.
3. Register formal skills, profiles, runtime files, and dependency metadata without adding AXLE/MCP to default or delegation routing.
4. Update AGD documentation/tests for parent-owned artifacts and evidence mapping.
5. Add migration/runtime smoke/provider-boundary tests.
6. Add optional AXLE MCP setup helper, runtime wrappers, manifests, and evidence validation as supplemental-only.
7. Regenerate docs and run verification.

## Dependencies

- Existing deep-research runtime validator and structured run files.
- Existing manifest/profile/runtime selectors.
- Existing runtime smoke and fake-root lifecycle tooling.
- Existing cross-agent delegation packet validation.

## Risks

- Risk: v2 validation breaks v1 structured runs.
  - Mitigation: v2 activates only on explicit marker or formal/evidence artifacts; tests preserve unresolved legacy evidence IDs in v1.
- Risk: optional Lean failures block ordinary research delivery.
  - Mitigation: formal support and formal-check requirement are separate from non-formal evidence readiness.
- Risk: generated or remote Lean executes unsafe code during validation.
  - Mitigation: scanner-only preflight blocks execution-capable constructs before typecheck.
- Risk: AXLE/MCP leaks into provider routing.
  - Mitigation: tests assert `manifest/delegation.yaml` providers and auto-provider selection exclude AXLE/MCP/Lean services.
- Risk: AXLE helper accidentally installs packages, writes MCP config, starts a server, calls a live endpoint, or leaks an API key.
  - Mitigation: helper is read-only/offline; tests use fake executables, secret canaries, and temp-cwd snapshots.
- Risk: AXLE remote success is mistaken for local formal proof evidence.
  - Mitigation: evidence validator accepts `axle_remote_check` as supplemental only and promotion requires local `formal_check` evidence.
- Risk: runtime migration adopts stale user-owned runtime files.
  - Mitigation: tests assert `--adopt` skips differing runtime files and only `--backup-replace` replaces them.

## Verification checkpoints

- After phase 1: `python -m unittest tests.test_research_workflow_integration`
- After phase 2: `python -m unittest tests.test_runtime_integration`
- After phase 3: manifest/profile tests and `make docs`
- After phase 4: `python -m unittest tests.test_cross_agent_delegation`
- After phase 6: AXLE runtime helper tests, `make runtime-smoke ARGS="--skills axiom-axle-mcp"`, and research-workflow AXLE evidence tests.
- Final: `make sanitize-check`, `make test`, `make runtime-smoke`, `make lifecycle-test ARGS="--matrix default --platform-shape all"`
