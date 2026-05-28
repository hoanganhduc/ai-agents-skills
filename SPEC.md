# Spec: Optional Lean Formal Verification Lane

## Objective

- Add an optional local-first Lean/formal evidence lane to the existing research workflow.
- Preserve the default research flow while allowing v2 structured runs to track evidence, formal targets, statement-equivalence reviews, and final readiness.
- Keep remote AXLE/MCP explicit opt-in, disabled by default, and out of provider/delegation routing.

## Assumptions

1. Lean formalization is optional and not available for all graph theory or combinatorics claims.
2. Local Lean/Lake checks may run only when already installed; wrappers must not install toolchains or dependencies.
3. V1 structured research runs remain compatible unless a v2 marker or formal artifact is present.
4. Formal evidence supports a research claim only after typecheck, placeholder/trust-base scan, statement-equivalence review, and lead/human review all pass.
5. AXLE/MCP is a future remote-client adapter, not an installer-managed MCP server or delegation provider.

## Commands

- Targeted tests: `python -m unittest tests.test_research_workflow_integration tests.test_runtime_integration tests.test_cross_agent_delegation`
- Build/docs: `make docs`
- Full tests: `make test`
- Static: `make sanitize-check`
- Runtime smoke: `make runtime-smoke`
- Lifecycle: `make lifecycle-test ARGS="--matrix default --platform-shape all"`

## Project Structure

- `canonical/runtime/skills/deep-research-workflow/deep_research_workflow.py`
- `canonical/skills/lean-formalization-intake/SKILL.md`
- `canonical/skills/lean-strict-verification-gate/SKILL.md`
- `canonical/runtime/skills/lean-formalization-intake/`
- `canonical/runtime/skills/lean-strict-verification-gate/`
- `manifest/skills.yaml`
- `manifest/profiles.yaml`
- `manifest/runtime.yaml`
- `manifest/dependencies.yaml`
- `manifest/system-dependencies.yaml`
- `canonical/skills/agent-group-discuss/*`
- `installer/ai_agents_skills/runtime.py`
- `installer/ai_agents_skills/runtime_smoke.py`
- `tests/test_research_workflow_integration.py`
- `tests/test_runtime_integration.py`
- `tests/test_cross_agent_delegation.py`

## Testing Strategy

- Unit: v2 evidence ledger, readiness, artifact-ref parsing, formal target state machine, computation evidence, and AGD evidence resolution.
- Runtime: formal skill smoke with missing Lean/Lake/elan/npm/npx/pip/package managers proving degraded status without installation.
- Manifest: formal profiles explicit, `research-core` unchanged, AXLE/MCP absent from provider/delegation routing.
- Docs: generated docs reflect manifest source of truth.
- Lifecycle: fake-root profile coverage across Linux, macOS, Windows, and WSL path shapes where existing tooling supports it.

## Boundaries

- Always: keep default research workflow unchanged.
- Always: keep v1 structured runs compatible.
- Always: keep AXLE/MCP out of default profiles, runtime smoke, provider routing, and live PR CI.
- Never: auto-install Lean, Lake, mathlib, npm packages, Python packages, credentials, configs, services, MCP servers, or background daemons.
- Never: treat bounded computation as unrestricted formal theorem evidence.

## Success Criteria

- [ ] V2 structured research runs validate `evidence.jsonl` and formal artifacts.
- [ ] Final readiness fails closed on unsupported, provisional-without-caveat, unverified, unresolved, or invalid formal support.
- [ ] Formal target promotion requires valid state, typecheck, placeholder/trust-base scans, statement-equivalence row, and review.
- [ ] Local formal skills exist and run cross-platform with no auto-install behavior.
- [ ] Formal profiles are explicit and do not change `research-core`.
- [ ] AGD evidence is parent-validated before use.
- [ ] Runtime migration/adopt tests protect unmanaged files.
- [ ] Docs and tests pass.

## Open Questions

- Whether to implement live AXLE integration later after a separate endpoint/security review.
