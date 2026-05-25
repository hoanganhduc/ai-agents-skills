# Spec: Claim-Preserving Draft Writing

## Objective

- Add a reusable `draft-writing` workflow to `ai-agents-skills`.
- Help agents preserve author intent across draft polishing by separating claims from wording.
- Make the workflow installable as a skill plus optional instruction/template artifacts for supported agents.

## Assumptions

1. The mechanism is procedural guidance and templates, not executable enforcement.
2. Default real-system targets remain Codex, Claude, and DeepSeek.
3. Copilot support is explicit and skill-adapter-only for this feature.
4. OpenClaw remains explicit fake-root-only until native target evidence exists.

## Commands

- Build/docs: `make docs`
- Test: `make test`
- Static: `make sanitize-check`
- Runtime smoke: `make runtime-smoke`
- Lifecycle: `make lifecycle-test ARGS="--matrix default --platform-shape all"`
- Stress: `make lifecycle-test ARGS="--matrix stress --platform-shape linux"`

## Project Structure

- `canonical/skills/draft-writing/SKILL.md`
- `canonical/instructions/claim-preserving-writing.md`
- `canonical/templates/draft-claim-ledger.md`
- `canonical/templates/draft-revision-map.md`
- `manifest/skills.yaml`
- `manifest/profiles.yaml`
- `manifest/artifacts.yaml`
- `tests/test_installer.py`
- generated `docs/` and `docs/source/`

## Testing Strategy

- Unit: selector, artifact, dependency, Copilot, and OpenClaw tests.
- Integration: fake-root lifecycle and installer plan/dry-run checks.
- Manual: review generated plan output before any real-system apply.

## Boundaries

- Always: keep canonical source in the repo and propagate through installer-managed files.
- Ask first: real-system apply to agent homes if plan output is not clean.
- Never: write real `.openclaw` targets in this phase.

## Success Criteria

- [ ] `draft-writing` is a valid skill in manifests and docs.
- [ ] `writing-workflow` profile selects `draft-writing`.
- [ ] `writing-workflow` artifact profile selects the instruction doc and templates.
- [ ] Copilot receives only the skill adapter for this feature.
- [ ] OpenClaw remains explicit fake-root-only.
- [ ] Relevant tests and lifecycle checks pass.

## Open Questions

- Whether to add a future executable verifier for claim-ledger consistency.
