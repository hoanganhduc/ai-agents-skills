<!-- Managed by ai-agents-skills. Generated target: codex. Source: template:deep-research-sources.md. -->

# Deep Research Sources

Scope: local repository research on reusable research-workflow templates.
Date: 2026-05-19.

| ID | Source | Date | Type | Zotero status | Why it matters | Reliability notes |
|---|---|---|---|---|---|---|
| S1 | `canonical/skills/deep-research-workflow/SKILL.md` | inspected 2026-05-19 | local skill doc | `[NOT_A_PAPER]` | Defines the source-preserving search -> analyze -> write workflow and required stable source IDs. | Primary repo source. |
| S2 | `canonical/skills/research-briefing/SKILL.md` and `references/brief-template.md` | inspected 2026-05-19 | local skill doc | `[NOT_A_PAPER]` | Defines the lightweight scope/evidence-plan gate before expensive research. | Primary repo source. |
| S3 | `canonical/skills/research-report-reviewer/SKILL.md` and `references/reviewer-prompt.md` | inspected 2026-05-19 | local skill doc | `[NOT_A_PAPER]` | Defines pre-final review checks for unsupported claims, ambiguity, and scope drift. | Primary repo source. |
| S4 | `canonical/skills/research-verification-gate/SKILL.md` and `references/checklist.md` | inspected 2026-05-19 | local skill doc | `[NOT_A_PAPER]` | Defines the final delivery readiness gate and required gap disclosure. | Primary repo source. |
| S5 | `canonical/skills/source-research/SKILL.md` | inspected 2026-05-19 | local skill doc | `[NOT_A_PAPER]` | Defines source-gathering routing, primary-source preference, and escalation to deep research. | Primary repo source. |
| S6 | `canonical/skills/cross-agent-delegation/SKILL.md` and references | inspected 2026-05-19 | local skill doc | `[NOT_A_PAPER]` | Defines packet boundaries for cross-agent handoffs and hostile-output handling. | Primary repo source. |
| S7 | `canonical/skills/agent-group-discuss/EXECUTION.md` | inspected 2026-05-19 | local skill doc | `[NOT_A_PAPER]` | Defines multi-agent run state, round files, lock protocol, and recovery expectations. | Primary repo source. |
| S8 | `manifest/artifacts.yaml` | inspected 2026-05-19 | manifest | `[NOT_A_PAPER]` | Registers optional templates and artifact profiles. | Primary repo source. |
| S9 | `canonical/templates/*.md` | inspected 2026-05-19 | local templates | `[NOT_A_PAPER]` | Shows existing template coverage: spec, tasks, deep-research source/analysis/report, and hierarchical delegation. | Primary repo source. |

## Search Log

| Query | Source set | Result | Next step |
|---|---|---|---|
| Existing research workflow templates | `canonical/templates` | Existing coverage is source ledger, analysis, report, generic task plan/todo, spec, and hierarchical delegation. | Add missing research-specific templates. |
| Research workflow gates | `canonical/skills/research-*` | Separate skill docs define scope, review, and verification gates but only deep-research source/analysis/report have installable templates. | Convert high-value gates into reusable templates. |
| Multi-agent run-state conventions | `agent-group-discuss/EXECUTION.md`, `cross-agent-delegation` docs | Existing docs define state, ledgers, packets, and recovery concepts. | Keep new templates generic and inert. |

## Exclusions

| Source or query | Reason excluded |
|---|---|
| External web search | Not needed for this repository-internal template design pass. |
| Paper/library lookup | No paper-like sources were used. |
