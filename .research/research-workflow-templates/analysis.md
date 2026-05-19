<!-- Managed by ai-agents-skills. Generated target: codex. Source: template:deep-research-analysis.md. -->

# Deep Research Analysis

## Scope

Question: Which generic reusable templates should be added to improve this
repository's research workflow support?

Time boundary: repository state inspected on 2026-05-19.

Exclusions: external web research, paper retrieval, and live agent execution.

## Claims Ledger

| Claim | Evidence IDs | Confidence | Gaps |
|---|---|---|---|
| Existing deep-research templates cover source ledger, analysis, and final report but not pre-research scoping, claim-level evidence matrices, final verification checklists, or full run-state runbooks. | S1, S2, S3, S4, S8, S9 | High | None material for local template design. |
| A generic hierarchical delegation template is useful but should remain separate from ordinary research workflow templates because it coordinates runners and trust boundaries rather than source analysis itself. | S6, S7, S8, S9 | High | No live swarm test in this pass. |
| A reusable scope brief template would convert the `research-briefing` skill's visible gate into an installable artifact. | S2, S8 | High | None. |
| A reusable evidence matrix template would strengthen the deep-research requirement that important claims retain source linkage through analysis and writing. | S1, S3, S4 | High | None. |
| A reusable verification checklist template would make the final `research-verification-gate` actionable as an artifact. | S3, S4 | High | None. |
| A reusable runbook template would help long research runs preserve state, phases, recovery points, and outputs without being tied to a particular agent product. | S1, S6, S7 | Medium-high | Needs real-run validation later. |

## Conflicts

| Conflict | Sources | Resolution or status |
|---|---|---|
| Templates should help execution, but cross-agent packet docs warn that templates must not imply execution authority. | S6, S7 | Keep templates inert and put runner invocation under parent-owned confirmation. |
| Existing generated docs come from manifests, so direct doc edits would drift. | S8 | Register artifacts in `manifest/artifacts.yaml`, then run `make docs`. |

## Provisional Findings

1. Add `research-scope-brief.md` for pre-research scope, constraints, evidence
   plan, risks, and go/no-go status.
2. Add `research-evidence-matrix.md` for claim-to-source support, conflicts,
   confidence, and required repairs.
3. Add `research-verification-checklist.md` for final readiness before a report
   is called complete.
4. Add `research-workflow-runbook.md` for multi-phase research state, artifacts,
   recovery, and handoffs.
5. Keep `hierarchical-agent-delegation.md` as the name of the manager-worker
   delegation template.

## Remaining Checks

- Register new templates as optional artifacts. Completed.
- Regenerate docs. Completed with `make docs`.
- Verify installer visibility and generated docs. Completed with
  `make list-artifacts` and `make plan ARGS="--no-skills --artifact-profile workflow-templates --with-deps"`.
- Check templates for product-specific agent names. Completed; no matches in
  the generic templates.
