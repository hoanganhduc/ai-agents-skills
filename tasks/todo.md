# Tasks

- [x] Task: Refresh lifecycle artifacts.
  - Acceptance: `SPEC.md`, `tasks/plan.md`, and `tasks/todo.md` describe the formal-lane implementation.
  - Verify: files exist and align with `plan_fixed_v3.md`.
  - Files: `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`

- [x] Task: Implement v2 research artifacts and readiness.
  - Acceptance: v2 marker/evidence/formal artifacts validate; v1 compatibility remains intact.
  - Verify: targeted research workflow tests pass.
  - Files: `canonical/runtime/skills/deep-research-workflow/deep_research_workflow.py`, `tests/test_research_workflow_integration.py`

- [x] Task: Add local formal skills and wrappers.
  - Acceptance: intake and strict verification skills exist, run cross-platform, degrade when Lean is missing, and do not install dependencies.
  - Verify: runtime integration tests and runtime smoke contracts pass.
  - Files: `canonical/skills/lean-formalization-intake/`, `canonical/skills/lean-strict-verification-gate/`, `canonical/runtime/skills/lean-formalization-intake/`, `canonical/runtime/skills/lean-strict-verification-gate/`

- [x] Task: Register manifests and profiles.
  - Acceptance: `formal-research` is local-only, `formal-research-remote` is explicit, and `research-core` is unchanged.
  - Verify: selector/manifest/docs tests pass.
  - Files: `manifest/skills.yaml`, `manifest/profiles.yaml`, `manifest/runtime.yaml`, `manifest/dependencies.yaml`, `manifest/system-dependencies.yaml`

- [x] Task: Add AGD and delegation boundaries.
  - Acceptance: parent-owned AGD artifact rules and evidence mapping are documented and tested; AXLE/MCP is not a provider.
  - Verify: cross-agent delegation tests pass.
  - Files: `canonical/skills/agent-group-discuss/*`, `tests/test_cross_agent_delegation.py`

- [x] Task: Add migration and runtime safety tests.
  - Acceptance: stale runtime `--adopt` skips differing files; no-auto-install and provider-boundary tests pass.
  - Verify: runtime and installer tests pass.
  - Files: `installer/ai_agents_skills/runtime.py`, `installer/ai_agents_skills/runtime_smoke.py`, `tests/test_runtime_integration.py`, `tests/test_installer.py`

- [x] Task: Regenerate docs and verify.
  - Acceptance: generated docs reflect manifest source of truth and verification gates pass.
  - Verify: `make docs`, targeted tests, `make test`, `make runtime-smoke`.
  - Files: `README.md`, `docs/`, `docs/source/`
