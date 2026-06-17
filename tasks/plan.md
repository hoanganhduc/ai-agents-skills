# Plan

## Phases

1. Refresh lifecycle artifacts for the workflow-gates implementation.
2. Add canonical intent, investigation, and risk-confirmation policy.
3. Tighten research, retrieval, review, and writing skill guidance.
4. Register any new portable instruction artifact in manifests.
5. Add tests for policy presence and rendered target propagation.
6. Regenerate generated docs.
7. Run targeted verification, then broader checks if feasible.

## Dependencies

- Existing canonical instruction docs.
- Existing manifest artifact system for portable instruction documents.
- Existing generated-doc pipeline in `installer/ai_agents_skills/docs.py`.
- Existing installer fake-root and render tests.

## Risks

- Risk: stricter confirmation language slows routine work.
  - Mitigation: distinguish `trivial`, `normal`, and `risk-gated` work.
- Risk: target-specific surfaces cannot all enforce the same controls.
  - Mitigation: install the strongest applicable text and document remaining loader gaps.
- Risk: retrieval rules block legitimate external downloads.
  - Mitigation: allow explicit user opt-out after the local-library-first rule is surfaced.
- Risk: generated docs drift.
  - Mitigation: regenerate docs and run `docs-check`.

## Verification Checkpoints

- After policy edits: targeted unit tests for manifest/render coverage.
- After generated docs: `make docs-check`.
- Final repo-only checks: targeted unittest command and any broader checks that complete within the session.
