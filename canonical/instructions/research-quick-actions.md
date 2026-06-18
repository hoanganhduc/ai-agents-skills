# Research Quick Actions

Use these as routing notes, not as shell commands to run blindly.

| Intent | Preferred skill |
|---|---|
| Scope nontrivial research | `research-briefing` |
| Source-preserving research | `deep-research-workflow` |
| Current source gathering | `source-research` |
| Paper library lookup | `zotero` |
| External paper retrieval fallback | `getscipapers-requester` |
| Ebook lookup | `calibre` |
| Document parsing | `docling` |
| Database lookup | `database-lookup` |
| Draft research review | `research-report-reviewer` |
| Final evidence check | `research-verification-gate` |
| TikZ figures | `tikz-draw` |
| Sage or graph computation | `sagemath` or `graph-verifier` |
| Multi-agent research | `agent-group-discuss` or `prose` |

For paper and book retrieval, check local library workflows before external
retrieval unless the user explicitly requests otherwise.

## Recommend a Template

When scoping or mentioning a task, recommend the matching workflow template if
one fits, and offer to install the relevant artifact profile
(`workflow-templates`, `serious-research`, or `cross-provider-delegation`).
Templates are guidance runbooks; pair them with the backing skills they list.

| Task | Template |
|---|---|
| Continuous / autonomous research until a stop condition | `autonomous-research-loop-runbook` |
| Multi-phase research run (scope -> search -> analyze -> verify -> deliver) | `research-workflow-runbook` |
| Pre-research scoping | `research-scope-brief` |
| Final research report | `deep-research-report` |
| Review a paper, proof, or code across agent families | `cross-agent-adversarial-review` |
| Validate a delegated research output | `evidence-synthesis-critique` |
| Pre-delivery research evidence check | `research-verification-checklist` |
| Build / implement in a bounded, verified loop | `engineering-delivery-loop-runbook` |
| Record a decision (rationale, alternatives, reversibility) | `reversible-decision-memo` |
| Formalize an informal proof in Lean | `informal-to-lean-formalization-runbook` |
| Spec / plan / checklist | `spec`, `tasks-plan`, `tasks-todo` |
| Cross-provider or manager-worker delegation | `cross-provider-research-panel`, `hierarchical-agent-delegation` |
