# Tasks

- [x] Task: Refresh lifecycle artifacts.
  - Acceptance: `SPEC.md`, `tasks/plan.md`, and `tasks/todo.md` describe repo-only workflow gates.
  - Verify: files are updated in this patch.
  - Files: `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`

- [x] Task: Add canonical workflow gate policy.
  - Acceptance: intent, investigation, and confirmation gates are documented with concrete risk thresholds.
  - Verify: targeted tests inspect canonical text.
  - Files: `canonical/instructions/operating-discipline.md`, `canonical/instructions/engineering-lifecycle.md`, `canonical/instructions/risk-gated-confirmation.md`

- [x] Task: Tighten research and retrieval workflows.
  - Acceptance: local-library-first, disambiguation, dry-run/confirmation, and structured-artifact delivery checks are explicit.
  - Verify: targeted tests inspect skill docs.
  - Files: selected `canonical/skills/*/SKILL.md` and references.

- [x] Task: Add pre-draft context gate for writing.
  - Acceptance: draft-writing requires prior examples/templates/style/context before new prose generation.
  - Verify: targeted tests inspect skill docs.
  - Files: `canonical/skills/draft-writing/SKILL.md`, `canonical/instructions/claim-preserving-writing.md`

- [x] Task: Register portable instruction artifact.
  - Acceptance: risk-gated confirmation can be installed through workflow instruction artifacts.
  - Verify: manifest/render tests pass.
  - Files: `manifest/artifacts.yaml`

- [x] Task: Add tests and regenerate docs.
  - Acceptance: tests and docs reflect new policy.
  - Verify: `make docs-check` and targeted unittest pass.
  - Files: `tests/test_installer.py`, `README.md`, `docs/`, `docs/source/`
