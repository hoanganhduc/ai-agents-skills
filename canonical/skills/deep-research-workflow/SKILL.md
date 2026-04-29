---
name: deep-research-workflow
description: Use when a research task benefits from an explicit phased workflow with structured source handoff across search, analysis, and writing, and when preserving citations across phases matters.
metadata:
  short-description: Phased deep research with structured citations
---

# Deep Research Workflow

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

Verify the helper setup with:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/deep-research-workflow/run_deep_research_workflow.sh doctor
```

## Minimal runtime helper

Initialize a deep-research scaffold with:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/deep-research-workflow/run_deep_research_workflow.sh init --dir /path/to/workspace
```

Verify the helper setup with:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/deep-research-workflow/run_deep_research_workflow.sh doctor
```

## When to use

- deep topic research
- report-style synthesis
- research with explicit citation preservation
- tasks where search, interpretation, and final writing should be kept separate

## Routing boundary

Prefer `openclaw-research` for lightweight browse-and-synthesize work.

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
- uncertainty notes
- candidate open problems or next-step questions
- optional figure opportunities with proposed `F*` ids and supporting `S*` ids

Detailed handoff structure:

- `references/source-handoff.md`
- `~/.codex/templates/deep-research-analysis.md`

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
- optional `F*` figure references with artifact paths
- explicit follow-up items when needed

Output structure guidance:

- `references/output-structure.md`
- `~/.codex/templates/deep-research-report.md`

## Skill handoffs

- Use `docling` before or between Phases 1 and 2 when local PDFs, HTML exports, or office documents need structure-aware parsing.
- Use `database-lookup` during Phase 1 when the task depends on structured public database records rather than general web synthesis.
- Use `paper-lookup` during Phase 1 when external literature metadata/discovery is needed after the local library-first workflow.
- Use `research_digest_wrapper` or `rss_news_digest` to seed Phase 1 when the task starts from tracked topics, alerts, or feeds.
- Use `tikz-draw` only after Phase 2 when there is an explicit figure request or a clear post-analysis figure brief to execute.

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

## Sample prompt shapes

- "Do a deep research workflow on X and preserve citations across phases."
- "Research X in three phases: search, analysis, and final report."
