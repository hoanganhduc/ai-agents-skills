# Lean formalization

Ordinary Lean/Lake is the default for a paper's formal artifacts. Keep the
project and its evidence per paper. Search the pinned Mathlib and existing
project dependencies before adding declarations. Lax catalog discovery is
optional; independently verify any Lax result actually reused, including its
statement, definitions, and proof dependencies.

Use the [informal-to-Lean runbook](https://github.com/hoanganhduc/ai-agents-skills/blob/main/canonical/templates/informal-to-lean-formalization-runbook.md)
for intake, declaration mapping, proof work, and acceptance. Record the selected
source revision, Lean/Mathlib pins, theorem targets, assumptions, and open
obligations before verification. Report these evidence layers separately:

| Layer | Evidence and limit |
|---|---|
| Source scan | Inspects proof escapes and suspicious source patterns; does not establish elaboration or semantic correspondence. |
| Build/typecheck | Checks the selected formal project with the recorded toolchain; compilation alone does not establish the paper's claim. |
| Axiom audit | Reports axioms used transitively by the selected declarations and checks the allowed trust base; this is not a full inventory of imported definitions. |
| Optional kernel replay | Runs the separately selected checker on the specified modules; unavailable required replay remains unmet. |
| Correspondence review | Compares the checked declarations, definitions, quantifiers, and assumptions with the informal claim. |

Local execution and an authorized remote executor can produce these checks.
The [native remote Lean reference](https://github.com/hoanganhduc/ai-agents-skills/blob/main/canonical/skills/autonomous-research-loop-runtime/references/native-lean-remote.md)
describes the existing Kaggle CPU path, pinned host request, dependency setup,
and exact-version evidence admission. Network access for setup requires explicit
policy permission; the kernel's Internet setting is not a setup-only sandbox.
Keep setup, build, audit, replay, and correspondence outcomes separate, and
identify which checks actually ran remotely. A job-written PASS does not replace
host admission of the bound evidence. Live executor qualification is distinct
from offline controller tests.

Pending verification preserves the candidate and its evidence. Resume or
reconcile the existing attempt under the job policy; do not treat a pending
result as success or automatically submit a replacement. See
[Research jobs](research-jobs.md) for bounded recovery and stop behavior.

If a Lax artifact is requested later, use the existing
[per-paper workflow](lax-paper-workflow.md) operation `from-existing-lean`.
It adds export, adaptation, independent verification, and readiness requirements;
native Lean success does not imply Lax readiness. Lax submission/registration,
GitHub publication, and Zenodo archival publication remain separate requests.
