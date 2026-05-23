# Optional Artifacts

Artifacts are opt-in files outside normal skill directories. They add supporting workflow material such as templates, instruction docs, reviewer personas, entrypoint aliases, and repository-management notices. They are not installed by default because they can change agent behavior outside a single skill folder.

Use artifacts after deciding which skills or profiles you want. If an artifact depends on a skill, the installer creates it only when that skill is selected, already managed, adopted, migrated, or added with `--with-deps`.

Common commands:

```bash
make list-artifacts
make describe-artifact ARGS="entrypoint-alias:zotero"
make plan ARGS="--no-skills --artifact-profile workflow-templates"
make plan ARGS="--no-skills --artifact entrypoint-alias:zotero --with-deps"
make install ARGS="--no-skills --artifact-profile repo-management --dry-run"
```

| Artifact Profile | Description | Artifacts |
|---|---|---|
| `cross-provider-delegation` | Templates and guidance for true cross-provider delegation runs. | `instruction-doc:cross-provider-delegation`, `template:cross-provider-research-panel`, `template:manager-worker-research-review`, `template:repo-comparison-research`, `template:evidence-synthesis-critique` |
| `repo-management` | Top-level managed notice blocks for agent instruction files. | `management-notice:repo-management` |
| `research-entrypoints` | Optional command or quick-action aliases that point to backing skills. | `entrypoint-alias:deep-research`, `entrypoint-alias:research-team`, `entrypoint-alias:review`, `entrypoint-alias:tikz`, `entrypoint-alias:sage`, `entrypoint-alias:zotero`, `entrypoint-alias:docling`, `entrypoint-alias:calibre`, `entrypoint-alias:vnthuquan`, `entrypoint-alias:research-compute`, `entrypoint-alias:rss`, `entrypoint-alias:digest`, `entrypoint-alias:getscipapers` |
| `review-personas` | Reviewer and research role personas rendered to each agent's supported format. | `agent-persona:literature-scout`, `agent-persona:math-explorer`, `agent-persona:proof-checker`, `agent-persona:paper-reviewer`, `agent-persona:code-reviewer`, `agent-persona:test-reviewer`, `agent-persona:security-reviewer` |
| `workflow-artifacts` | All portable templates, workflow docs, personas, and entrypoint aliases. | `template:spec`, `template:tasks-plan`, `template:tasks-todo`, `template:research-scope-brief`, `template:research-evidence-matrix`, `template:research-verification-checklist`, `template:research-workflow-runbook`, `template:hierarchical-agent-delegation`, `template:cross-provider-research-panel`, `template:manager-worker-research-review`, `template:repo-comparison-research`, `template:evidence-synthesis-critique`, `template:deep-research-sources`, `template:deep-research-analysis`, `template:deep-research-report`, `instruction-doc:engineering-lifecycle`, `instruction-doc:research-quick-actions`, `instruction-doc:cross-provider-delegation`, `instruction-doc:python-quality-gates`, `instruction-doc:modal-offload-routing`, `instruction-doc:scrapling-integration`, `instruction-doc:language-style-rules`, `agent-persona:literature-scout`, `agent-persona:math-explorer`, `agent-persona:proof-checker`, `agent-persona:paper-reviewer`, `agent-persona:code-reviewer`, `agent-persona:test-reviewer`, `agent-persona:security-reviewer`, `entrypoint-alias:deep-research`, `entrypoint-alias:research-team`, `entrypoint-alias:review`, `entrypoint-alias:tikz`, `entrypoint-alias:sage`, `entrypoint-alias:zotero`, `entrypoint-alias:docling`, `entrypoint-alias:calibre`, `entrypoint-alias:vnthuquan`, `entrypoint-alias:research-compute`, `entrypoint-alias:rss`, `entrypoint-alias:digest`, `entrypoint-alias:getscipapers`, `management-notice:repo-management` |
| `workflow-instructions` | Agent-readable workflow guidance documents copied outside skill folders. | `instruction-doc:engineering-lifecycle`, `instruction-doc:research-quick-actions`, `instruction-doc:cross-provider-delegation`, `instruction-doc:python-quality-gates`, `instruction-doc:modal-offload-routing`, `instruction-doc:scrapling-integration`, `instruction-doc:language-style-rules` |
| `workflow-templates` | Reusable research, specification, and task templates. | `template:spec`, `template:tasks-plan`, `template:tasks-todo`, `template:research-scope-brief`, `template:research-evidence-matrix`, `template:research-verification-checklist`, `template:research-workflow-runbook`, `template:hierarchical-agent-delegation`, `template:cross-provider-research-panel`, `template:manager-worker-research-review`, `template:repo-comparison-research`, `template:evidence-synthesis-critique`, `template:deep-research-sources`, `template:deep-research-analysis`, `template:deep-research-report` |

| Artifact | Description | Depends On Skills |
|---|---|---|
| `agent-persona:code-reviewer` | Reviews code for bugs, regressions, security risks, and missing tests. |  |
| `agent-persona:literature-scout` | Finds relevant literature, missing citations, and source-quality risks. |  |
| `agent-persona:math-explorer` | Explores examples, counterexamples, invariants, and graph-theory structure. |  |
| `agent-persona:paper-reviewer` | Reviews papers and drafts for correctness, evidence, organization, and contribution clarity. |  |
| `agent-persona:proof-checker` | Audits mathematical proofs for gaps, hidden assumptions, and invalid reductions. |  |
| `agent-persona:security-reviewer` | Reviews security-sensitive changes and configuration boundaries. |  |
| `agent-persona:test-reviewer` | Reviews test plans and coverage for meaningful behavioral protection. |  |
| `entrypoint-alias:calibre` | Route ebook and Calibre-library work. | `calibre` |
| `entrypoint-alias:deep-research` | Start source-preserving phased research. | `deep-research-workflow` |
| `entrypoint-alias:digest` | Route tracked-topic research digest workflows. | `research-digest-wrapper` |
| `entrypoint-alias:docling` | Route document parsing to Docling workflow. | `docling` |
| `entrypoint-alias:getscipapers` | Route external paper retrieval fallback. | `getscipapers-requester` |
| `entrypoint-alias:research-compute` | Route heavy compute planning and offload decisions. | `modal-research-compute` |
| `entrypoint-alias:research-team` | Start template-based multi-agent research or review. | `agent-group-discuss` |
| `entrypoint-alias:review` | Run a review-oriented workflow. | `paper-review` |
| `entrypoint-alias:rss` | Route RSS digest workflows. | `rss-news-digest` |
| `entrypoint-alias:sage` | Route graph theory or algebra computation to SageMath workflow. | `sagemath` |
| `entrypoint-alias:tikz` | Route structural figure work to TikZ workflow. | `tikz-draw` |
| `entrypoint-alias:vnthuquan` | Route Vietnam Thu Quan ebook discovery. | `vnthuquan` |
| `entrypoint-alias:zotero` | Route paper library work to Zotero workflow. | `zotero` |
| `instruction-doc:cross-provider-delegation` | Guidance for template-driven cross-provider delegation with safe nested workers. | `agent-group-discuss`, `cross-agent-delegation`, `model-router` |
| `instruction-doc:engineering-lifecycle` | Spec-plan-tasks-implement-verify lifecycle guide. |  |
| `instruction-doc:language-style-rules` | Portable language and file-type style notes adapted from local agent rules. |  |
| `instruction-doc:modal-offload-routing` | When to keep work local and when to route heavy compute elsewhere. | `modal-research-compute` |
| `instruction-doc:python-quality-gates` | Suggested Python verification gates for research and installer code. |  |
| `instruction-doc:research-quick-actions` | Short command and routing references for common research actions. |  |
| `instruction-doc:scrapling-integration` | Guidance for browser-heavy extraction when default web tooling is insufficient. |  |
| `management-notice:repo-management` | Managed notice explaining that ai-agents-skills is the source repo and local agent homes are runtime targets. |  |
| `template:cross-provider-research-panel` | Cross-provider research panel template with latest-model and highest-thinking constraints. | `agent-group-discuss`, `cross-agent-delegation` |
| `template:deep-research-analysis` | Analysis-phase note template for source-preserving synthesis. | `deep-research-workflow` |
| `template:deep-research-report` | Final report template for research synthesis. | `deep-research-workflow` |
| `template:deep-research-sources` | Source handoff table for phased deep research. | `deep-research-workflow` |
| `template:evidence-synthesis-critique` | Evidence synthesis critique template for validating delegated research outputs. | `research-report-reviewer`, `research-verification-gate` |
| `template:hierarchical-agent-delegation` | Hierarchical manager-worker delegation template for bounded multi-runner workflows. | `cross-agent-delegation` |
| `template:manager-worker-research-review` | Manager-worker research review template with same-model nested worker constraints. | `agent-group-discuss`, `cross-agent-delegation` |
| `template:repo-comparison-research` | Repository comparison research template with source-preserving evidence requirements. | `deep-research-workflow`, `agent-group-discuss` |
| `template:research-evidence-matrix` | Claim-to-source evidence matrix for research analysis and uncertainty tracking. | `deep-research-workflow` |
| `template:research-scope-brief` | Pre-research scope, assumptions, evidence plan, and start decision template. | `research-briefing` |
| `template:research-verification-checklist` | Final research delivery checklist for evidence coverage, gaps, and readiness. | `research-report-reviewer`, `research-verification-gate` |
| `template:research-workflow-runbook` | Multi-phase research runbook for state, artifacts, delegation, recovery, and delivery gates. | `deep-research-workflow`, `research-verification-gate` |
| `template:spec` | Project or research specification template. |  |
| `template:tasks-plan` | Planning template for decomposing a scoped task. |  |
| `template:tasks-todo` | Execution checklist template. |  |

Artifacts with dependencies are installed only when their backing skill is selected, already managed, or added with `--with-deps`. DeepSeek personas are installed as reference prompts because native persona-file loading has not been verified.

Related pages: [Skills](skills.md), [Profiles](profiles.md), [Agent Locations](agent-locations.md), [Uninstall And Rollback](uninstall-rollback.md).
