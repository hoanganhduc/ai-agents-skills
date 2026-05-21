<!-- Managed by ai-agents-skills. Generated target: codex. Source: template:deep-research-report.md. -->

# Deep Research Report

## Scope And Limits

Investigated local repository support for reusable research workflow templates
on 2026-05-19. The scope is limited to repo-local docs, skills, templates, and
manifest registration. External web research and paper/library checks were not
needed; all sources are `[NOT_A_PAPER]`.

## Findings

1. The manager-worker delegation template should be named
   `hierarchical-agent-delegation`.
2. The repository has installable templates for deep-research source, analysis,
   and report phases, but it lacks installable templates for scoping, evidence
   validation, final verification, and long-run recovery.
3. Four generic templates would improve the research workflow without binding
   it to any single agent product: `research-scope-brief`,
   `research-evidence-matrix`, `research-verification-checklist`, and
   `research-workflow-runbook`.
4. The four templates have been added under `canonical/templates`, registered
   in `manifest/artifacts.yaml`, and surfaced through generated artifact docs.

## Evidence

| Finding | Source IDs | Notes |
|---|---|---|
| Existing deep-research workflow needs stable source IDs and evidence preservation. | S1 | Supports evidence-matrix and runbook templates. |
| Research briefing, report review, and verification are separate skill gates but not all have installable templates. | S2, S3, S4, S8 | Supports adding scope and verification templates. |
| Cross-agent and multi-agent docs require inert, auditable handoffs and recovery state. | S6, S7 | Supports keeping delegation and runbook templates generic and non-executing. |
| Optional templates are registered in `manifest/artifacts.yaml`. | S8 | Implementation must update manifest and regenerate docs. |

## Uncertainties

- The proposed runbook has not yet been exercised in a long real research run.
- Template usefulness is inferred from current workflow gaps, not measured from
  repeated user studies.

## Delivery Check

- Evidence coverage: Local primary repo sources inspected and recorded.
- Dates checked: 2026-05-19.
- Verification: `make docs`, `make list-artifacts`, focused `make plan`, and
  product-name neutrality checks completed.
- Remaining gaps: Long-run usability of `research-workflow-runbook` remains
  untested in a real extended research project.
