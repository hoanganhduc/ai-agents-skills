# Tasks

- [x] Task: Add lifecycle artifacts.
  - Acceptance: `SPEC.md`, `tasks/plan.md`, and `tasks/todo.md` describe this scoped work.
  - Verify: files exist and stay current.
  - Files: `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`

- [x] Task: Add canonical writing workflow content.
  - Acceptance: skill, instruction doc, and two templates exist under canonical source directories.
  - Verify: manifest loader can read referenced sources.
  - Files: `canonical/skills/draft-writing/SKILL.md`, `canonical/instructions/claim-preserving-writing.md`, `canonical/templates/draft-claim-ledger.md`, `canonical/templates/draft-revision-map.md`

- [x] Task: Register manifests.
  - Acceptance: `draft-writing` skill, `writing-workflow` skill profile, and `writing-workflow` artifact profile resolve.
  - Verify: selector tests and `make describe`.
  - Files: `manifest/skills.yaml`, `manifest/profiles.yaml`, `manifest/artifacts.yaml`

- [x] Task: Add installer tests.
  - Acceptance: tests cover profile selection, artifact dependency resolution, Copilot limits, and OpenClaw fake-root behavior.
  - Verify: targeted unittest subset passes.
  - Files: `tests/test_installer.py`

- [x] Task: Regenerate docs and verify.
  - Acceptance: generated docs include the new skill/profile/artifacts.
  - Verify: `make docs`, tests, lifecycle, plan/dry-run gates.
  - Files: `docs/`, `docs/source/`
