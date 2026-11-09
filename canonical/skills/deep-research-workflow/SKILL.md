---
name: deep-research-workflow
description: Use when a research task benefits from an explicit phased workflow with structured source handoff across search, analysis, and writing, and when preserving citations across phases matters.
metadata:
  short-description: Phased deep research with structured citations
---

# Deep Research Workflow


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/deep-research-workflow/run_deep_research_workflow.ps1" <args>
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

For any writing-producing output, load `writing-style-settings.md` before the
write phase. For mathematical manuscripts, TCS reports, graph-theoretic drafts,
formal-proof prose, or LaTeX output, also load `math-manuscript-style.md`.
Finalizable reports must record `style_profile_ref`, `policy_hash`,
`active_overlays`, `active_requirement_ids`, and `style_applied` in the run
ledger or final artifact metadata.

## Minimal runtime helper

Initialize a deep-research scaffold with:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/deep-research-workflow/run_deep_research_workflow.sh init --dir /path/to/workspace
```

For machine-checkable research runs, initialize structured ledgers too:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/deep-research-workflow/run_deep_research_workflow.sh init --structured --dir /path/to/workspace
```

For research where formal verification may help, initialize the optional v2
formal lane:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/deep-research-workflow/run_deep_research_workflow.sh init --structured --schema-version 2 --formal --dir /path/to/workspace
```

Validate the structured ledgers before delivery with:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/deep-research-workflow/run_deep_research_workflow.sh validate --dir /path/to/workspace/research
```

Validate a v2/formal workspace with:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/deep-research-workflow/run_deep_research_workflow.sh validate --schema-version 2 --dir /path/to/workspace/research
```

Check delivery readiness of a structured run with:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/deep-research-workflow/run_deep_research_workflow.sh delivery-check --dir /path/to/workspace/research
```

`delivery-check` runs `validate` and prints `READY` only when the run is valid,
the delivery decision is `ready` or `ready-with-caveats`, every
`required_for_delivery` formal target has promoted support
(`supports_claim_after_equivalence_review`; formal-statement-only does not
count), and the run's input files did not change during the check. Otherwise it
prints `NOT READY` with the `validate` codes and its own codes
(`DELIVERY_DECISION_NOT_FINALIZABLE`,
`DELIVERY_REQUIRED_FORMAL_TARGET_NOT_PROMOTED`, `DELIVERY_INPUT_CHANGED`,
`DELIVERY_INPUT_UNREADABLE` for an input that cannot be hashed,
`DELIVERY_VALIDATE_CRASHED`) and exits 1. The input files, hashed before and
after `validate` and listed with their digests in `input_files`, are
`research_schema.json`, `sources.jsonl`, `claims.jsonl`, `guards.jsonl`,
`delivery.json`, `evidence.jsonl`, `model_freshness.json`, `formal/*.jsonl`,
the `report_ref` file, the copy behind each computation row that carries
`payload_hash`, and the stamp copies behind each `agd_result` row's
`payload_hash` and `producer_payload_hash`. The verdict and exit code are code-enforced; `delivery-check` adds
its codes without changing what `validate` checks. A Delivery Check quotes this output verbatim and never upgrades
`NOT READY`.

Record a `graph-verifier` or Sage counterexample result as computation evidence
for a claim with:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/deep-research-workflow/run_deep_research_workflow.sh add-computation-evidence --dir /path/to/workspace/research --result /path/to/result.json --claim-id C1 --relation asserts
```

The verb, its refusals and the delivery rule for computation rows are described
under "Computation evidence" below.

Verify the helper setup with:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/deep-research-workflow/run_deep_research_workflow.sh doctor
```

Run the offline strict workflow smoke with:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
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
- requests that only ask to investigate, diagnose, review, audit, verify, or
  report, unless the user explicitly asks for a phased workflow or durable
  research artifacts

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

- installed template `deep-research-sources`

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
- installed template `deep-research-analysis`

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

### Computation evidence

`graph-verifier` and the Sage template `counterexample_search.sage` print a
`computation_evidence` block in their JSON result. Record such a result with
`add-computation-evidence` rather than typing a `computation` row by hand:

- `--result` must be a regular file, not a symbolic link, of at most 16 MiB in
  UTF-8, holding exactly one `computation_evidence` object: at the top level,
  or under `result` as in the record a `research_compute` job leaves. The block
  carries the ten computation fields (`tool_name`, `tool_version`,
  `input_encoding_ref`, `checked_domain`, `graph_model_assumptions`,
  `resource_bounds`, `enumeration_method`, `timeout_status`, `result_status`,
  `coverage_status`), `evaluated_predicate`, a one-line `outcome`,
  `limitations`, and `exhaustiveness_argument` when the coverage is
  `exhaustive` or `bounded_complete`.
- The verb copies the file byte for byte to
  `artifacts/computation/<evidence_id>.json` and appends one row to
  `evidence.jsonl`. The row takes the ten fields, `exhaustiveness_argument` and
  `evaluated_predicate` from the block, `summary` from `outcome`, `limitations`
  from the block followed by each `--limitation`, and `payload_hash`, the
  `sha256:<hex>` of the copy.
- `--relation asserts` means the claim states that `evaluated_predicate` holds
  on `checked_domain`; `--relation refutes` means the claim states that it
  fails there. The verb refuses `asserts` with a `failed` result and `refutes`
  with a `passed` one. It accepts any other result with either relation, but
  such a row never counts for delivery. A counterexample (`result_status`
  `failed`) is recorded with `--relation refutes` against a claim that states
  that the predicate fails on the checked domain, for example the negation of
  the claim under review or its corrected claim, never against the claim it
  refutes.
- `verification_source` records where the block sat: `research_compute_envelope`
  when it is under `result` in an object with `job_id`, `template`,
  `resource_class`, `started_at`, `finished_at`, `result` and `status`
  `completed`, and `submitted_file` otherwise. It is read from the file's
  shape, so it is recorded provenance, not a verified identity. For a job
  fetched from the `research_compute` broker, pass its `manifest.json`: the
  fetched `result.json` keeps only the result, so it reads as `submitted_file`.
- Defaults: the evidence id is the smallest free `E-COMP-<n>` (`--evidence-id`
  sets another), `source_ids` is empty (repeat `--source-id`),
  `inspection_status` is `unchecked`, `redaction_status` `not_reviewed`,
  `sensitivity_class` `unknown` and `created_at` the current UTC time;
  `--inspection-status`, `--redaction-status`, `--sensitivity-class` and
  `--created-at` override them.
- The verb prints a `deep-research.add-computation-evidence.v1` object
  (`status`, `codes`, `errors`, `evidence_id`, `artifact_ref`, `payload_hash`,
  `verification_source`, `claim_relation`). It exits 0 when the row was
  appended, 1 when it refused and wrote nothing, and 2 on a usage error.
  Refusal codes: `NO_COMPUTATION_EVIDENCE_BLOCK` (not JSON, not an object, or
  zero or two blocks), `COMPUTATION_RESULT_UNREADABLE`,
  `COMPUTATION_RELATION_CONTRADICTS_RESULT`, `COMPUTATION_EVIDENCE_NOT_WRITABLE`
  (a link or a file in place of `artifacts/computation/`, an `evidence.jsonl`
  that is not a regular file, or a write that could not finish), and the
  `validate` codes for the claim, source and evidence ids, the timestamp, a
  missing ledger and the block's fields.
- The verb does not edit `claims.jsonl`: add the printed `evidence_id` to the
  claim's `evidence_ids` so the claim cites the row.

`validate` re-reads the copy behind every computation row that carries
`payload_hash`. It reports `COMPUTATION_PAYLOAD_HASH_MISMATCH` when the copy is
missing, unreadable or has other bytes, and
`COMPUTATION_ROW_DIVERGES_FROM_PAYLOAD` when the copy does not hold exactly one
block or the row differs from it (the row's `limitations` must include the
block's). A `claim_relation` other than `asserts` or `refutes` gives
`COMPUTATION_CLAIM_RELATION_INVALID`, and a `claim_relation` without
`evaluated_predicate` gives `COMPUTATION_EVIDENCE_FIELD_REQUIRED`.

For a `ready` or `ready-with-caveats` delivery, a computation row counts only
when its coverage is `exhaustive` or `bounded_complete`, its relation matches
its result (`asserts` with `passed`, `refutes` with `failed`), its
`verification_source` is `research_compute_envelope`, and it has
`payload_hash`. A claim without `source_ids` that cites only uncounted
computation rows gets `READY_CLAIM_RELIES_ONLY_ON_WEAK_COMPUTATION`. Rows
labeled `submitted_file`, hand-typed rows and rows written before this verb
existed therefore stay weak; a host re-run that could make a submitted file count is
deferred. When such a claim cites only computation rows and every counted one
is `bounded_complete`, one delivery caveat must name the claim id and each
counted row's `checked_domain`; otherwise `validate` reports
`READY_BOUNDED_COMPUTATION_WITHOUT_CAVEAT`. These checks are code-enforced.
Whether the checked domain and the predicate fit the claim stays the parent's
judgement.

### AGD result evidence

An `agd_result` row records a delegated participant's verdict. Its optional
`verdict_kind` says what the verdict is: `referee_accepted`,
`referee_rejected`, `independent_rederivation`, `counterexample_reported` or
`consensus`; any other value gives `AGD_VERDICT_KIND_INVALID`.
`reviewer_family` names the model family of the participant that gave the
verdict, `producer_family` the family of whoever produced the claim or
derivation under review, and `family_source` where `reviewer_family` comes
from: `declared` or `launch_stamp`. `host_verified` gives
`AGD_HOST_VERIFIED_UNAVAILABLE` because `validate` cannot look up a host
record yet; any other value gives `AGD_FAMILY_SOURCE_INVALID`.

A side is bound to a byte-for-byte copy of the stamp `delegate-agent` wrote
for that participant (`stamps/<participant_id>.json`), kept in the run, for
example at `artifacts/stamps/<dispatch-run-id>/<participant_id>.json`:
`artifact_ref` and `payload_hash` bind the reviewer side,
`producer_artifact_ref` and `producer_payload_hash` the producer side. On an
`agd_result` row, `payload_hash` always names such a copy. `validate` re-reads
every copy a row names and reports:

- `AGD_STAMP_HASH_MISMATCH` for a ref without its hash or a hash without its
  ref, `family_source` `launch_stamp` without `payload_hash`, and a copy that
  is missing, a symbolic link, larger than 64 KiB, unreadable or not the bytes
  whose `sha256:<hex>` the hash gives;
- `AGD_STAMP_INVALID` for a copy that is not a JSON object with
  `schema_version` `external-agent-dispatch-stamp.v1` and a string
  `model_family`, or that repeats a key;
- `AGD_STAMP_FAMILY_MISMATCH` when that side's family is not the stamp's
  `model_family`.

The reviewer side is bound when `family_source` is `launch_stamp` and its copy
passes these checks; the producer side is bound when its copy passes them. An
unbound side is declared, and a declared family is unverified. An
`independent_rederivation` row needs both sides bound to two different
verified families (`anthropic`, `deepseek`, `google`, `openai`, `xai`);
otherwise `validate` reports `AGD_INDEPENDENCE_UNVERIFIED_FAMILY`. A claim the
parent produced therefore counts only when its producer side was dispatched
and stamped. A stamp is `unverified` when the dispatch ran under a command,
binary or endpoint override, as every research dispatch but grok does today,
and `unverified` never counts. A stamp names the provider the dispatcher was
asked to launch: it is recorded provenance, not a verified model identity.

For a `ready` or `ready-with-caveats` v2 delivery, a supported claim without
`source_ids` whose cited evidence is only `agd_result` rows other than
`independent_rederivation` (rows without `verdict_kind` included), with at
most uncounted computation rows beside them, gets
`READY_CLAIM_RELIES_ONLY_ON_REFEREE_JUDGEMENT`. Cite source, formal or counted
computation evidence or a bound `independent_rederivation`, or mark the claim
`provisional` with a caveat. These checks are code-enforced; whether a
re-derivation really re-derives the claim stays the parent's judgement.

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
- `artifacts/search/leanexplore/` stores optional LeanExplore declaration-search
  records when the user explicitly chooses that manual MCP workflow.
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

OpenGauss harness runs, when present, are recorded as `opengauss_run` evidence
(provenance only). They cannot replace local `formal_check` evidence or promote
formal support. Prefer manual `/prove`/`/draft` after intake; AAS auto-launch
remains fail-closed without `headless_qualified` spike + headless driver.

LeanExplore MCP output, when present, is recorded as `lean_declaration_search`
evidence. It is supplemental retrieval context only: it can help locate
declarations, modules, source links, dependencies, and informalizations before
drafting Lean, but it cannot replace local `formal_check` evidence or promote
formal support on its own.

Use the local helpers as optional gates:

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/lean-formalization-intake/run_lean_formalization_intake.sh assess --claim-id C1 --claim "..."
```
Use the ordinary Lean/Lake verifier for formalization, including paper formalization. Lax discovery and artifact conversion are optional: `lax-paper-workflow` already supports `from-existing-lean`. Verify any selected Lax dependencies independently. Zenodo is an optional archive, and publication remains a separate authorized operation.

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh verify --input formal/artifacts/C1.lean --artifact-stage final_candidate --typecheck
```

These helpers do not install Lean, Lake, Mathlib, MCP servers, AXLE adapters,
LeanExplore packages, local search data, or provider tooling. They report local
availability and fail closed when a required tool is unavailable.

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
- prior posts, templates, style guides, venue instructions, source ledgers, or
  supplied examples when the deliverable must match an existing format or voice
- the active writing-style profile and overlays selected from
  `writing-style-settings.md` and any domain overlay

- produce a structured output
- include only citations that survive from earlier phases
- inspect and follow relevant prior-format/style artifacts before drafting blog
  posts, articles, reports, or other publication-style prose; if they are absent,
  state that assumption instead of inventing a house style
- distinguish observation, inference, and recommendation
- say `incomplete analysis` if material scope remains unchecked

Outputs:

- a final report
- a scoped source list
- optional `delivery.json` decision record
- optional `writing_style.json` or embedded `writing_style_record` with
  `style_profile_ref` and `style_applied`
- optional `F*` figure references with artifact paths
- explicit follow-up items when needed

Output structure guidance:

- `references/output-structure.md`
- `references/research-quality-guards.md`
- installed template `deep-research-report`

## Skill handoffs

- Use `docling` before or between Phases 1 and 2 when local PDFs, HTML exports, or office documents need structure-aware parsing.
- Use `database-lookup` during Phase 1 when the task depends on structured public database records rather than general web synthesis.
- Use `paper-lookup` during Phase 1 when external literature metadata/discovery is needed after the local library-first workflow.
- Use `research-digest-wrapper` or `rss-news-digest` to seed Phase 1 when the task starts from tracked topics, alerts, or feeds.
- Use `tikz-draw` only after Phase 2 when there is an explicit figure request or a clear post-analysis figure brief to execute.
- Use `graph-verifier` or the `sagemath` counterexample template for finite
  graph checks, and record their JSON result with `add-computation-evidence`
  (see Computation evidence).
- Use `formal-skeleton-helper`, `lean-formalization-intake`,
  `lean-explore-mcp`, and `lean-strict-verification-gate` only for optional
  formalization candidates; they supplement the research workflow and do not
  replace source, computation, or human mathematical review.
- Use `lean-explore-mcp` only for manual optional Lean declaration search setup.
  Treat its results as `lean_declaration_search` evidence, not as local formal
  proof evidence.
- Use `axiom-axle-mcp` only for manual optional AXLE MCP setup. Treat its
  results as `axle_remote_check` evidence, not as local formal proof evidence.
- Use `opengauss` for optional OpenGauss readiness, handoffs, and fail-closed
  adapter verbs. Treat results as `opengauss_run` provenance, not local formal
  proof evidence.

## Escalation rules

- Stay in this skill for single-agent phased deep research.
- Escalate to `prose` when the user explicitly wants structured multi-agent research-and-synthesis orchestration.
- Escalate to `agent-group-discuss` when the user wants panel-style discussion, debate, or multi-agent research perspectives.

## Guardrails

- Do not invent sources.
- Do not collapse citations into vague "various sources" language.
- Keep the workflow provider-agnostic.
- Use narrower skills first when the task is really paper retrieval, database lookup, or simple browsing.
- If a task is document-heavy, parse first with `docling` rather than pretending plain-text extraction is equivalent.
- Do not drop a Phase 1 source silently in later phases; if it is excluded, note why.
- Do not reuse one source id for multiple different sources.
- Do not skip the Zotero cross-check for paper-like sources.
- Do not collapse research and drafting into one step when the user asks for a
  new post, article, report, or format-matched deliverable; inspect relevant
  prior context first, then write from the analyzed evidence.

## Verification

- [ ] Phase 1 search results are explicitly recorded with stable `S*` ids
- [ ] Paper-like sources have Zotero verification status
- [ ] Important claims retain source linkage through Phase 3
- [ ] Optional figure briefs preserve `S*` source linkage and assign stable `F*` ids
- [ ] Final output distinguishes sourced fact from inference
- [ ] Missing coverage is disclosed explicitly
- [ ] Prior posts, templates, style guides, or supplied examples were inspected
      before format-matched writing, or their absence was disclosed
- [ ] `writing-style-settings.md` was loaded before final writing
- [ ] Mathematical or LaTeX output loaded `math-manuscript-style.md`
- [ ] Finalizable writing records `style_profile_ref`, `policy_hash`,
      `active_overlays`, `active_requirement_ids`, and `style_applied`
- [ ] Dropped or excluded sources are explained
- [ ] Nontrivial runs include guard outputs with `guard_output_id`
- [ ] Supported `pass` or `warn` guard outputs cite source or evidence IDs
- [ ] V2 claims cite evidence IDs, not only source IDs
- [ ] Computation results a claim cites were recorded with
      `add-computation-evidence`, the claim's `evidence_ids` name the row, and
      a claim resting on a bounded run carries a caveat that names the bound
- [ ] Delegated verdicts are `agd_result` rows with a `verdict_kind`, and an
      `independent_rederivation` row binds both sides to stamp copies
- [ ] Formalized claims have `formal_targets.jsonl`,
      `statement_equivalence_reviews.jsonl`, scan/typecheck artifacts, and lead
      or human review before support is promoted
- [ ] LeanExplore declaration searches, if used, are recorded as supplemental
      `lean_declaration_search` evidence and do not replace local
      `formal_check` evidence
- [ ] AXLE remote checks, if used, are recorded as supplemental
      `axle_remote_check` evidence and paired with local `formal_check` evidence
      before any formal support is promoted
- [ ] OpenGauss runs, if used, are recorded as supplemental `opengauss_run`
      provenance and paired with local `formal_check` + lead/human equivalence
      before any formal support is promoted
- [ ] Budget/model policy state is recorded only in parent-owned runbook artifacts
- [ ] No aggregate research quality score replaces guard outputs

## Sample prompt shapes

- "Do a deep research workflow on X and preserve citations across phases."
- "Research X in three phases: search, analysis, and final report."
