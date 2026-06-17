# Spec: Repo-Only Workflow Gates

## Objective

- Strengthen shared agent behavior rules so supported targets plan and investigate before acting.
- Add a concrete risk-gated confirmation policy for important or large modifications.
- Tighten research, retrieval, and writing workflows so agents do what was asked, avoid unasked work, ask when unclear, and inspect prior context before drafting or modifying.
- Keep this implementation repo-only. Do not write to live agent homes during this phase.

## Assumptions

1. `~/ai-agents-skills` remains the source of truth for reusable skills, instruction docs, generated docs, manifests, and tests.
2. Live agent homes such as `~/.codex`, `~/.claude`, `~/.copilot`, `~/.config/opencode`, `~/.gemini`, `~/.deepseek`, and `~/.openclaw` are installation targets only and are not modified in this phase.
3. Some behavior can be enforced by installer/tests, but live agent compliance remains partly policy-based until provider-native loader and transcript tests are added.
4. Existing installer conflict protections remain in force: unmanaged files are skipped by default, managed blocks are scoped, and real-system apply requires explicit confirmation.
5. The new gates should reduce accidental scope expansion without blocking trivial one-line tasks.

## Scope

- Canonical instruction docs under `canonical/instructions/`.
- Research, retrieval, review, and writing skill docs under `canonical/skills/`.
- Manifest entries for portable instruction artifacts.
- Generated docs and tests that verify the new policy text is propagated.
- Lifecycle artifacts in `SPEC.md`, `tasks/plan.md`, and `tasks/todo.md`.

## Out Of Scope

- Applying changes to live agent homes.
- Running provider CLIs or proving native loader behavior.
- Rewriting runtime helper implementations.
- Auditing secrets, local libraries, sessions, logs, caches, or databases.
- Changing OpenClaw real-system target gates beyond clearer documentation.

## Acceptance Criteria

- [x] A unified intent gate exists and names scope, out-of-scope work, evidence to inspect, change risk, and verification.
- [x] A risk-gated confirmation policy exists with concrete thresholds and approval wording.
- [x] Engineering lifecycle requires read-only investigation before modification plans.
- [x] Research and retrieval docs require local-library-first routing unless the user explicitly opts out.
- [x] Library mutations and outward actions require preview/dry-run and explicit confirmation unless already precisely approved.
- [x] Writing workflows require prior examples/templates/style/context inspection before generating new prose such as blog posts.
- [x] Research delivery gates inspect structured artifacts when those artifacts exist.
- [x] Manifest artifacts include the new confirmation policy for portable workflow-instruction installs.
- [x] Tests cover the canonical policy text and target artifact rendering.

## Verification

- Targeted tests: `python3 -m unittest tests.test_installer.ManifestTests tests.test_installer.PlanInstallVerifyTests`
- Generated docs: `make docs-check`
- Broader verification as time allows: `make test`, `make runtime-smoke`, `make lifecycle-test ARGS="--matrix default --platform-shape all"`
