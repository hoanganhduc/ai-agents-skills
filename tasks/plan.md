# Plan

## Phases

1. Add canonical writing workflow content.
2. Register skill, profile, and artifacts in manifests.
3. Add targeted installer tests for selection and target boundaries.
4. Regenerate docs.
5. Run verification gates and inspect plan/dry-run output.

## Dependencies

- Existing installer manifest loading and artifact rendering.
- Existing target support rules for Codex, Claude, DeepSeek, Copilot, and OpenClaw.

## Risks

- Risk: overstating Copilot/OpenClaw support.
  - Mitigation: tests assert Copilot artifact skips and OpenClaw fake-root-only behavior.
- Risk: optional artifacts are not verified by narrow skill filters.
  - Mitigation: use unfiltered verify and plan assertions.
- Risk: generated docs drift.
  - Mitigation: run `make docs` after manifest edits.

## Verification checkpoints

- After phase 2: `make list-skills`, `make list-artifacts`, `make describe`.
- After phase 3: targeted unittest subset.
- After phase 4: docs diff inspected.
- After phase 5: lifecycle and dry-run status reported.
