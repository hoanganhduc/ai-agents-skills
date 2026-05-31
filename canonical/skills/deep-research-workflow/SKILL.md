---
name: deep-research-workflow
description: Use when a research task benefits from an explicit phased workflow with structured source handoff across search, analysis, and writing, and when preserving citations across phases matters.
metadata:
  short-description: Phased deep research with structured citations
---

# Deep Research Workflow


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. For Codex-only installs the runtime is usually `%USERPROFILE%\.codex\runtime`; for multi-agent installs it is usually `%LOCALAPPDATA%\ai-agents-skills\runtime`. Set `$runtime` to the installed runtime root, then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/deep-research-workflow/run_deep_research_workflow.bat" <args>
```

PowerShell-first runners can use the native PowerShell command target:

```powershell
& "$runtime\run_skill.ps1" "skills/deep-research-workflow/run_deep_research_workflow.ps1" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

This skill provides a Codex-native phased research workflow:

1. search
2. analyze
3. write

Use it when the user wants a deeper research pass than a normal quick synthesis and when source preservation matters.

## Minimal runtime helper

Initialize a deep-research scaffold with:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/deep-research-workflow/run_deep_research_workflow.sh init --dir /path/to/workspace
```

For machine-checkable research runs, initialize structured ledgers too:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/deep-research-workflow/run_deep_research_workflow.sh init --structured --dir /path/to/workspace
```

For research where formal verification may help, initialize the optional v2
formal lane:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/deep-research-workflow/run_deep_research_workflow.sh init --structured --schema-version 2 --formal --dir /path/to/workspace
```

Validate the structured ledgers before delivery with:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/deep-research-workflow/run_deep_research_workflow.sh validate --dir /path/to/workspace/research
```

Validate a v2/formal workspace with:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/deep-research-workflow/run_deep_research_workflow.sh validate --schema-version 2 --dir /path/to/workspace/research
```

Verify the helper setup with:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/deep-research-workflow/run_deep_research_workflow.sh doctor
```

Run the offline strict workflow smoke with:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/deep-research-workflow/run_deep_research_workflow.sh selftest
```

`selftest` validates named positive and negative v2 scenarios for finalizable
delivery, AGD evidence, weak computation rejection, formal promotion, and
artifact-ref safety. It is the preferred runtime smoke for serious-research
installs.

## When to use

- deep topic research
- report-style synthesis
- research with explicit citation preservation
- tasks where search, interpretation, and final writing should be kept separate

## Routing boundary

Prefer `source-research` for lightweight browse-and-synthesize work.

Prefer this skill when:

- the user wants an explicit phased workflow
- you need a structured handoff between search, analysis, and writing
- preserving source linkage across phases is part of the task quality bar

## When not to use

- simple factual lookups
- casual current-events questions where a normal browse-and-answer flow is sufficient
- local-paper retrieval tasks already covered by `zotero` or `calibre`

## Workflow

### Phase 1 — Search

Inputs:

- user question or topic
- any seed URLs, papers, datasets, or constraints

- gather relevant sources
- prefer primary sources when practical
- record source metadata with stable `S1`, `S2`, ... identifiers
- separate observed facts from tentative interpretations

Outputs:

- a source ledger
- optional `sources.jsonl` records for machine validation
- stable `S*` source ids
- initial claim candidates
- noted coverage gaps

Use these templates when helpful:

- `~/.codex/templates/deep-research-sources.md`

### Zotero cross-check

Between Phase 1 and Phase 2, treat every paper-like source as a library-check task:

- search the local library with `zotero`
- assign exactly one verification status
- preserve that status in the source ledger

Allowed status values:

- `[IN_LIBRARY]` — confirmed and found in Zotero
- `[NOT_IN_LIBRARY]` — confirmed paper, not present in Zotero
- `[NOT_A_PAPER]` — blog post, docs page, forum thread, dataset page, or similar non-paper source
- `[UNVERIFIED]` — claimed as a paper, but existence or identity could not be confirmed

Do not mark a source as verified based only on appearance or title shape. If identity remains unclear, keep `[UNVERIFIED]`.

For v2 finalizable delivery, every paper-like source that supports a final
claim must also record `library_check_tool`, `library_checked_at`, and
`library_check_ref`.

### Phase 2 — Analyze

Inputs:

- the source ledger from Phase 1, including Zotero verification status
- any extracted document structure or database records

- group findings into themes
- identify conflicts, uncertainties, and gaps
- preserve the source mapping for each important claim
- keep `S*` ids stable across all phases
- note which claims are strongly supported and which are provisional

Outputs:

- a theme matrix
- claim-to-source mapping
- optional `claims.jsonl` records for machine validation
- uncertainty notes
- candidate open problems or next-step questions
- optional figure opportunities with proposed `F*` ids and supporting `S*` ids

Detailed handoff structure:

- `references/source-handoff.md`
- `~/.codex/templates/deep-research-analysis.md`

### Research quality guards

For nontrivial, delegated, or completeness-claiming research, record guard
outputs before final synthesis:

- `ScopeGuard` for scope drift and exclusions
- `EvidenceGuard` for claim-to-source or claim-to-evidence linkage
- `VerifyGuard` for readiness checks separate from final delivery judgment
- `BudgetGuard` for parent-owned token, USD, depth, and hop limits
- `RegressionGuard` for load-bearing workflow text and template contracts

Use the closed schema in `references/research-quality-guards.md`. Do not use a
single aggregate research quality score.
Structured runs record these as `guards.jsonl`.

For v2 `ready` and `ready-with-caveats`, delivery is finalizable only when the
run has non-blocking `EvidenceGuard` and `VerifyGuard` outputs, at least one
supported claim, a checked report evidence record, current model freshness
metadata in `model_freshness.json`, and no blocking guard gaps. Use v1 only for
compatibility workflows that do not claim those serious-research guarantees.

### Optional formal verification lane

Use the formal lane only when it fits the research object. Lean formalization is
optional because many graph theory and combinatorics proofs are too expensive
or under-supported to formalize during a normal research pass.

For v2 structured runs, formal artifacts live under `formal/`:

- `formal_targets.jsonl` records which claims are formalization candidates,
  required formal checks, and promotion state.
- `statement_equivalence_reviews.jsonl` records whether the informal claim and
  formal statement are equivalent enough to use as evidence.
- `artifacts/` stores Lean skeletons, candidate Lean files, typecheck logs, and
  scan records.
- `artifacts/remote/axle/` stores optional AXLE remote-result records when the
  user explicitly chooses that manual MCP workflow.
- `README.md` summarizes the local policy.

A Lean artifact may support a final report claim only after the parent has
recorded typecheck evidence, placeholder/trust-base scan evidence, an accepted
statement-equivalence review, and a lead or human review. Fake transports,
stubs, `sorry`, `admit`, unsafe trust-base growth, or missing statement review
cannot promote support.

AXLE MCP output, when present, is recorded as `axle_remote_check` evidence. It
is supplemental context only: it cannot replace local `formal_check` evidence,
set local Lean typecheck status, satisfy placeholder/trust-base scans, or
promote formal support on its own.

Use the local helpers as optional gates:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/lean-formalization-intake/run_lean_formalization_intake.sh assess --claim-id C1 --statement "..."
```

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh verify --input formal/artifacts/C1.lean --artifact-stage final --typecheck
```

These helpers do not install Lean, Lake, Mathlib, MCP servers, AXLE adapters, or
provider tooling. They report local availability and fail closed when a required
tool is unavailable.

### Optional post-analysis figure handoff

Only do this when the user explicitly asks for a figure or the report would materially benefit from one.

This handoff happens after analysis, not instead of analysis.

Produce a `figure-brief.json` with:

- `figure_id`
- `title`
- `purpose`
- `source_ids`
- `diagram_family`
- `content_requirements`
- `layout_constraints`
- `output_dir`

Use `tikz-draw` after the brief exists.

Keep:

- `figure_id` as `F1`, `F2`, ...
- `source_ids` tied to the supporting `S*` records from earlier phases
- output artifacts under a dedicated `figures/` directory inside the research workspace when practical

### Phase 3 — Write

Inputs:

- the analyzed theme matrix
- preserved source ids and uncertainty notes

- produce a structured output
- include only citations that survive from earlier phases
- distinguish observation, inference, and recommendation
- say `incomplete analysis` if material scope remains unchecked

Outputs:

- a final report
- a scoped source list
- optional `delivery.json` decision record
- optional `F*` figure references with artifact paths
- explicit follow-up items when needed

Output structure guidance:

- `references/output-structure.md`
- `references/research-quality-guards.md`
- `~/.codex/templates/deep-research-report.md`

## Skill handoffs

- Use `docling` before or between Phases 1 and 2 when local PDFs, HTML exports, or office documents need structure-aware parsing.
- Use `database-lookup` during Phase 1 when the task depends on structured public database records rather than general web synthesis.
- Use `paper-lookup` during Phase 1 when external literature metadata/discovery is needed after the local library-first workflow.
- Use `research_digest_wrapper` or `rss_news_digest` to seed Phase 1 when the task starts from tracked topics, alerts, or feeds.
- Use `tikz-draw` only after Phase 2 when there is an explicit figure request or a clear post-analysis figure brief to execute.
- Use `formal-skeleton-helper`, `lean-formalization-intake`, and
  `lean-strict-verification-gate` only for optional formalization candidates;
  they supplement the research workflow and do not replace source, computation,
  or human mathematical review.
- Use `axiom-axle-mcp` only for manual optional AXLE MCP setup. Treat its
  results as `axle_remote_check` evidence, not as local formal proof evidence.

## Escalation rules

- Stay in this skill for single-agent phased deep research.
- Escalate to `prose` when the user explicitly wants structured multi-agent research-and-synthesis orchestration.
- Escalate to `agent_group_discuss` when the user wants panel-style discussion, debate, or multi-agent research perspectives.

## Guardrails

- Do not invent sources.
- Do not collapse citations into vague "various sources" language.
- Keep the workflow provider-agnostic.
- Use narrower skills first when the task is really paper retrieval, database lookup, or simple browsing.
- If a task is document-heavy, parse first with `docling` rather than pretending plain-text extraction is equivalent.
- Do not drop a Phase 1 source silently in later phases; if it is excluded, note why.
- Do not reuse one source id for multiple different sources.
- Do not skip the Zotero cross-check for paper-like sources.

## Verification

- [ ] Phase 1 search results are explicitly recorded with stable `S*` ids
- [ ] Paper-like sources have Zotero verification status
- [ ] Important claims retain source linkage through Phase 3
- [ ] Optional figure briefs preserve `S*` source linkage and assign stable `F*` ids
- [ ] Final output distinguishes sourced fact from inference
- [ ] Missing coverage is disclosed explicitly
- [ ] Dropped or excluded sources are explained
- [ ] Nontrivial runs include guard outputs with `guard_output_id`
- [ ] Supported `pass` or `warn` guard outputs cite source or evidence IDs
- [ ] V2 claims cite evidence IDs, not only source IDs
- [ ] Formalized claims have `formal_targets.jsonl`,
      `statement_equivalence_reviews.jsonl`, scan/typecheck artifacts, and lead
      or human review before support is promoted
- [ ] AXLE remote checks, if used, are recorded as supplemental
      `axle_remote_check` evidence and paired with local `formal_check` evidence
      before any formal support is promoted
- [ ] Budget/model policy state is recorded only in parent-owned runbook artifacts
- [ ] No aggregate research quality score replaces guard outputs

## Sample prompt shapes

- "Do a deep research workflow on X and preserve citations across phases."
- "Research X in three phases: search, analysis, and final report."
