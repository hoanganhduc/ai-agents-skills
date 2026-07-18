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
| `cross-provider-delegation` | Templates and guidance for true cross-provider delegation runs. | `instruction-doc:cross-provider-delegation`, `template:cross-provider-research-panel`, `template:manager-worker-research-review`, `template:repo-comparison-research`, `template:evidence-synthesis-critique`, `template:engineering-delivery-loop-runbook`, `template:reversible-decision-memo`, `template:cross-agent-adversarial-review` |
| `repo-management` | Top-level managed notice blocks for agent instruction files. | `management-notice:repo-management` |
| `research-entrypoints` | Optional command or quick-action aliases that point to backing skills. | `entrypoint-alias:deep-research`, `entrypoint-alias:research-team`, `entrypoint-alias:review`, `entrypoint-alias:tikz`, `entrypoint-alias:sage`, `entrypoint-alias:zotero`, `entrypoint-alias:docling`, `entrypoint-alias:calibre`, `entrypoint-alias:vnthuquan`, `entrypoint-alias:research-compute`, `entrypoint-alias:rss`, `entrypoint-alias:digest`, `entrypoint-alias:getscipapers`, `entrypoint-alias:slides-to-video`, `entrypoint-alias:manim-math-animation`, `entrypoint-alias:url-to-screenshot` |
| `review-personas` | Reviewer and research role personas rendered to each agent's supported format. | `agent-persona:literature-scout`, `agent-persona:math-explorer`, `agent-persona:proof-checker`, `agent-persona:paper-reviewer`, `agent-persona:code-reviewer`, `agent-persona:test-reviewer`, `agent-persona:security-reviewer` |
| `serious-research` | Templates and guidance for source-preserving, validated research runs. | `template:research-scope-brief`, `template:research-evidence-matrix`, `template:research-verification-checklist`, `template:research-workflow-runbook`, `template:evidence-synthesis-critique`, `template:deep-research-sources`, `template:deep-research-analysis`, `template:deep-research-report`, `template:autonomous-research-loop-runbook`, `template:cross-provider-research-panel`, `template:manager-worker-research-review`, `template:informal-to-lean-formalization-runbook`, `template:cross-agent-adversarial-review`, `instruction-doc:research-quick-actions`, `instruction-doc:cross-provider-delegation`, `instruction-doc:writing-style-settings`, `instruction-doc:math-manuscript-style` |
| `workflow-artifacts` | All portable templates, workflow docs, personas, and entrypoint aliases. | `template:spec`, `template:tasks-plan`, `template:tasks-todo`, `template:draft-claim-ledger`, `template:draft-revision-map`, `template:research-scope-brief`, `template:research-evidence-matrix`, `template:research-verification-checklist`, `template:research-workflow-runbook`, `template:hierarchical-agent-delegation`, `template:cross-provider-research-panel`, `template:manager-worker-research-review`, `template:repo-comparison-research`, `template:evidence-synthesis-critique`, `template:deep-research-sources`, `template:deep-research-analysis`, `template:deep-research-report`, `template:autonomous-research-loop-runbook`, `template:engineering-delivery-loop-runbook`, `template:reversible-decision-memo`, `template:informal-to-lean-formalization-runbook`, `template:cross-agent-adversarial-review`, `template:tikz-figure-verification-runbook`, `instruction-doc:engineering-lifecycle`, `instruction-doc:operating-discipline`, `instruction-doc:risk-gated-confirmation`, `instruction-doc:delivery-verification-gate`, `instruction-doc:failure-recovery-discipline`, `instruction-doc:context-discipline`, `instruction-doc:writing-style-settings`, `instruction-doc:math-manuscript-style`, `instruction-doc:claim-preserving-writing`, `instruction-doc:research-quick-actions`, `instruction-doc:cross-provider-delegation`, `instruction-doc:python-quality-gates`, `instruction-doc:modal-offload-routing`, `instruction-doc:github-actions-offload-routing`, `instruction-doc:compute-offload-routing`, `instruction-doc:scrapling-integration`, `instruction-doc:language-style-rules`, `instruction-doc:autonomous-loop-enforcement`, `agent-persona:literature-scout`, `agent-persona:math-explorer`, `agent-persona:proof-checker`, `agent-persona:paper-reviewer`, `agent-persona:code-reviewer`, `agent-persona:test-reviewer`, `agent-persona:security-reviewer`, `entrypoint-alias:deep-research`, `entrypoint-alias:research-team`, `entrypoint-alias:review`, `entrypoint-alias:tikz`, `entrypoint-alias:sage`, `entrypoint-alias:zotero`, `entrypoint-alias:docling`, `entrypoint-alias:calibre`, `entrypoint-alias:vnthuquan`, `entrypoint-alias:research-compute`, `entrypoint-alias:rss`, `entrypoint-alias:digest`, `entrypoint-alias:getscipapers`, `entrypoint-alias:slides-to-video`, `entrypoint-alias:manim-math-animation`, `entrypoint-alias:url-to-screenshot`, `management-notice:repo-management` |
| `workflow-instructions` | Agent-readable workflow guidance documents copied outside skill folders. | `instruction-doc:engineering-lifecycle`, `instruction-doc:operating-discipline`, `instruction-doc:risk-gated-confirmation`, `instruction-doc:delivery-verification-gate`, `instruction-doc:failure-recovery-discipline`, `instruction-doc:context-discipline`, `instruction-doc:writing-style-settings`, `instruction-doc:math-manuscript-style`, `instruction-doc:claim-preserving-writing`, `instruction-doc:research-quick-actions`, `instruction-doc:cross-provider-delegation`, `instruction-doc:python-quality-gates`, `instruction-doc:modal-offload-routing`, `instruction-doc:github-actions-offload-routing`, `instruction-doc:compute-offload-routing`, `instruction-doc:scrapling-integration`, `instruction-doc:language-style-rules`, `instruction-doc:autonomous-loop-enforcement` |
| `workflow-templates` | Reusable research, specification, and task templates. | `template:spec`, `template:tasks-plan`, `template:tasks-todo`, `template:draft-claim-ledger`, `template:draft-revision-map`, `template:research-scope-brief`, `template:research-evidence-matrix`, `template:research-verification-checklist`, `template:research-workflow-runbook`, `template:hierarchical-agent-delegation`, `template:cross-provider-research-panel`, `template:manager-worker-research-review`, `template:repo-comparison-research`, `template:evidence-synthesis-critique`, `template:deep-research-sources`, `template:deep-research-analysis`, `template:deep-research-report`, `template:autonomous-research-loop-runbook`, `template:engineering-delivery-loop-runbook`, `template:reversible-decision-memo`, `template:informal-to-lean-formalization-runbook`, `template:cross-agent-adversarial-review`, `template:tikz-figure-verification-runbook` |
| `writing-workflow` | Claim-preserving writing workflow instructions and templates. | `instruction-doc:writing-style-settings`, `instruction-doc:math-manuscript-style`, `instruction-doc:claim-preserving-writing`, `template:draft-claim-ledger`, `template:draft-revision-map` |

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
| `entrypoint-alias:manim-math-animation` | Route math-animation (Write/morph equations) clip creation to the manim-math-animation skill. | `manim-math-animation` |
| `entrypoint-alias:research-compute` | Route heavy compute planning and offload decisions. | `modal-research-compute` |
| `entrypoint-alias:research-team` | Start template-based multi-agent research or review. | `agent-group-discuss` |
| `entrypoint-alias:review` | Run a review-oriented workflow. | `paper-review` |
| `entrypoint-alias:rss` | Route RSS digest workflows. | `rss-news-digest` |
| `entrypoint-alias:sage` | Route graph theory or algebra computation to SageMath workflow. | `sagemath` |
| `entrypoint-alias:slides-to-video` | Route narrated, captioned slide-video creation to the slides-to-video skill. | `slides-to-video` |
| `entrypoint-alias:tikz` | Route structural figure work to TikZ workflow. | `tikz-draw` |
| `entrypoint-alias:url-to-screenshot` | Route URL-to-PNG screenshot requests to the url-to-screenshot skill. | `url-to-screenshot` |
| `entrypoint-alias:vnthuquan` | Route Vietnam Thu Quan ebook discovery. | `vnthuquan` |
| `entrypoint-alias:zotero` | Route paper library work to Zotero workflow. | `zotero` |
| `instruction-doc:autonomous-loop-enforcement` | Stop policy for autonomous loops: user requirements override everything; otherwise stop only on loops-reached, credit-out, goal-resolved, or a user stop message. Enforcement is fail-open and always escapable. | `autonomous-research-loop` |
| `instruction-doc:claim-preserving-writing` | Guidance for preserving claims, evidence, caveats, and revision deltas during drafting. | `draft-writing` |
| `instruction-doc:compute-offload-routing` | Unified router over local, Kaggle, Modal, Hetzner, and GitHub Actions: priority order, keep-local rules, the self-preservation veto, and budget/teardown discipline. | `kaggle-research-compute`, `hetzner-research-compute`, `modal-research-compute` |
| `instruction-doc:context-discipline` | Load context selectively by persistence, and treat fetched/retrieved content as untrusted data, not instructions. |  |
| `instruction-doc:cross-provider-delegation` | Guidance for template-driven cross-provider delegation with safe nested workers. | `agent-group-discuss`, `cross-agent-delegation`, `model-router` |
| `instruction-doc:delivery-verification-gate` | Pre-delivery gate for code/config/automation changes: prove with a seen-to-fail check, confirm no regression, delegate a multi-axis review. Engineering analog of research-verification-gate. |  |
| `instruction-doc:engineering-lifecycle` | Spec-plan-tasks-implement-verify lifecycle guide. |  |
| `instruction-doc:failure-recovery-discipline` | What to do when a check, test, or claim fails: isolate, hypothesize, minimal fix, re-verify; downgrade a failed claim rather than hide it. |  |
| `instruction-doc:github-actions-offload-routing` | When to route compute to GitHub Actions (private repo, budget-gated, after local and Modal). | `modal-research-compute` |
| `instruction-doc:language-style-rules` | Portable language and file-type style notes adapted from local agent rules. |  |
| `instruction-doc:math-manuscript-style` | Mathematical manuscript, TCS, graph-theoretic, and Lean-synchronized prose style overlay. |  |
| `instruction-doc:modal-offload-routing` | When to keep work local and when to route heavy compute elsewhere. | `modal-research-compute` |
| `instruction-doc:operating-discipline` | Always-on cross-task behaviors (surface assumptions, manage confusion, push back, scope discipline, verify) plus an index of which gate to invoke when. |  |
| `instruction-doc:python-quality-gates` | Suggested Python verification gates for research and installer code. |  |
| `instruction-doc:research-quick-actions` | Short command and routing references for common research actions. |  |
| `instruction-doc:risk-gated-confirmation` | Risk classification thresholds and explicit confirmation contract before large, destructive, outward-facing, or unclear work. |  |
| `instruction-doc:scrapling-integration` | Guidance for browser-heavy extraction when default web tooling is insufficient. |  |
| `instruction-doc:writing-style-settings` | Canonical general writing-style policy for writing-producing workflows. |  |
| `management-notice:repo-management` | Managed notice explaining that ai-agents-skills is the source repo and local agent homes are runtime targets. |  |
| `template:autonomous-research-loop-runbook` | Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and Modal/GitHub Actions credit-gated heavy-compute offload. | `autonomous-research-loop`, `autonomous-research-loop-runtime`, `cross-agent-delegation`, `agent-group-discuss`, `model-router`, `modal-research-compute`, `research-verification-gate`, `decision-doubt-loop`, `get-available-resources` |
| `template:cross-agent-adversarial-review` | Producer-never-confirmer adversarial review of a paper, proof, or code artifact across agent families with a fresh-agent confirmation gate. | `agent-group-discuss`, `cross-agent-delegation`, `paper-review`, `annotated-review`, `decision-doubt-loop`, `research-verification-gate`, `model-router` |
| `template:cross-provider-research-panel` | Cross-provider research panel template with latest-model and highest-thinking constraints. | `agent-group-discuss`, `cross-agent-delegation` |
| `template:deep-research-analysis` | Analysis-phase note template for source-preserving synthesis. | `deep-research-workflow` |
| `template:deep-research-report` | Final report template for research synthesis. | `deep-research-workflow` |
| `template:deep-research-sources` | Source handoff table for phased deep research. | `deep-research-workflow` |
| `template:draft-claim-ledger` | Claim ledger template for preserving author intent before rewriting. | `draft-writing` |
| `template:draft-revision-map` | Revision audit template for detecting claim-level draft drift. | `draft-writing` |
| `template:engineering-delivery-loop-runbook` | Bounded build-and-deliver loop runbook: single-path implementation with seen-to-fail proof, cross-agent diff verification, behavior-preserving cleanup, and credit-gated heavy-compute offload. | `cross-agent-delegation`, `behavior-preserving-cleanup`, `decision-doubt-loop`, `get-available-resources`, `modal-research-compute`, `agent-group-discuss`, `model-router` |
| `template:evidence-synthesis-critique` | Evidence synthesis critique template for validating delegated research outputs. | `research-report-reviewer`, `research-verification-gate` |
| `template:hierarchical-agent-delegation` | Hierarchical manager-worker delegation template for bounded multi-runner workflows. | `cross-agent-delegation` |
| `template:informal-to-lean-formalization-runbook` | Local-first intake mapping an informal proof to Lean declarations with a scanner-first verification gate separating typecheck status from claim support. | `lean-formalization-intake`, `formal-skeleton-helper`, `lean-explore-mcp`, `lean-strict-verification-gate`, `decision-doubt-loop`, `cross-agent-delegation` |
| `template:manager-worker-research-review` | Manager-worker research review template with same-model nested worker constraints. | `agent-group-discuss`, `cross-agent-delegation` |
| `template:repo-comparison-research` | Repository comparison research template with source-preserving evidence requirements. | `deep-research-workflow`, `agent-group-discuss` |
| `template:research-evidence-matrix` | Claim-to-source evidence matrix for research analysis and uncertainty tracking. | `deep-research-workflow` |
| `template:research-scope-brief` | Pre-research scope, assumptions, evidence plan, and start decision template. | `research-briefing` |
| `template:research-verification-checklist` | Final research delivery checklist for evidence coverage, gaps, and readiness. | `research-report-reviewer`, `research-verification-gate` |
| `template:research-workflow-runbook` | Multi-phase research runbook for state, artifacts, delegation, recovery, and delivery gates. | `deep-research-workflow`, `research-verification-gate` |
| `template:reversible-decision-memo` | Evidence-grounded decision record with named alternatives, source-cited rationale, reversibility class and trip-wires, and a fresh-context adversarial confirmation before the decision stands. | `decision-doubt-loop`, `source-grounded-decisions`, `intent-interview`, `cross-agent-delegation`, `model-router` |
| `template:spec` | Project or research specification template. |  |
| `template:tasks-plan` | Planning template for decomposing a scoped task. |  |
| `template:tasks-todo` | Execution checklist template. |  |
| `template:tikz-figure-verification-runbook` | Bounded draw-compile-verify-redraw loop for a TikZ figure that guarantees it is free of overlap, wrong meaning, and bad layout, with Sage-assisted graph realization and fresh-agent visual confirmation before the strict approval gate. | `tikz-draw`, `sagemath`, `graph-verifier`, `cross-agent-delegation`, `decision-doubt-loop`, `agent-group-discuss`, `model-router` |

Artifacts with dependencies are installed only when their backing skill is selected, already managed, or added with `--with-deps`. DeepSeek personas are installed as reference prompts because native persona-file loading has not been verified.

Related pages: [Skills](skills.md), [Profiles](profiles.md), [Agent Locations](agent-locations.md), [Uninstall And Rollback](uninstall-rollback.md).
