# Research Workflow Templates

These templates are packet skeletons for research workflows. They are not
executable workflows, source retrieval tools, or agent invocations.

All templates:

- use task packet version `cross-agent-delegation.task.v1`
- set all `side_effects` values to false by default
- use `confirmation_requirement: parent_decides_outside_packet` by default
- use inert refs for papers, proofs, excerpts, datasets, code, and result
  summaries
- preserve stable source IDs for research-source refs
- separate `findings`, `evidence`, `limitations`, `warnings`, and `errors`
- treat returned critiques as evidence to validate, not instructions to apply
- keep unchecked citations unchecked unless evidence refs support a stronger
  status

Outside source suggestions are `unverified_leads` until a parent workflow
retrieves and verifies them. A template must not claim that a source was
searched, retrieved, read, or verified unless the result packet includes
evidence refs proving that status.

Research templates may include installed research-skill routing guidance only as
advisory text in existing descriptive fields such as `constraints`,
`requested_actions`, `evidence_requirements`, or `expected_output`. Skill names
do not grant read, write, subprocess, network, credential, provider, queue,
retrieval, verification, execution, or agent-spawning authority. If relevant
guidance is unavailable or cannot be used within the packet constraints, record
that step as blocked or unchecked.

## Common Finding Fields

Research findings use at least:

- `finding_id`
- `severity`
- `claim_or_object_ref`
- `evidence_refs`
- `confidence`
- `validation_status`
- `rationale`
- `recommended_parent_action`

Templates that evaluate claims or citations also require claim IDs, citation
locators, claim-to-source mappings, and evidence status values:
`supported`, `unsupported`, `unchecked`, or `contradicted`.

## Template Catalog

### literature-scout-review

- recipient profile family: `claude-like-research-reviewer` or
  `model-only-api-reviewer`
- required input ref kinds: topic brief, provided source list or corpus summary
- source ID policy: preserve parent source IDs; new suggestions are
  `unverified_leads`
- expected findings: missing citation leads, source-quality risks, search-gap
  notes, limitation notes
- forbidden outputs: claims of comprehensive search, source retrieval, source
  reading, or source verification without evidence refs

### citation-integrity-check

- recipient profile family: `claude-like-research-reviewer`
- required input ref kinds: draft excerpt, source manifest, citation map
- source ID policy: preserve source IDs, claim IDs, citation locators, and
  claim-to-source mapping
- expected findings: supported claim, unsupported claim, overstatement,
  contradicted claim, unchecked source, missing citation
- forbidden outputs: fabricated bibliography entries, final publication
  readiness claims, direct manuscript edits

### counterexample-search

- recipient profile family: `codex-like-coding-reviewer` or
  `claude-like-research-reviewer`
- required input ref kinds: claim statement, assumptions, examples or boundary
  cases
- source ID policy: preserve claim IDs and assumption refs
- expected findings: boundary risk, counterexample candidate, assumption gap,
  mutation suggestion, unresolved search space
- forbidden outputs: exhaustive-search claims unless the parent supplied
  complete evidence refs

### proof-gap-review

- recipient profile family: `claude-like-research-reviewer`
- required input ref kinds: proof excerpt, theorem statement, dependency list
- source ID policy: preserve claim IDs, lemma IDs, and dependency refs
- expected findings: hidden assumption, missing direction, circular dependency,
  invalid inference, unclear quantifier, unsupported citation
- forbidden outputs: proof repair applied as final text without parent review

### methodology-critique

- recipient profile family: `model-only-api-reviewer` or
  `claude-like-research-reviewer`
- required input ref kinds: method description, data summary, script summary,
  assumptions
- source ID policy: preserve dataset, method, and script refs
- expected findings: validity threat, sampling issue, reproducibility gap,
  confounder, unsupported generalization
- forbidden outputs: claims that experiments were rerun unless evidence refs
  prove that status

### result-synthesis-review

- recipient profile family: `claude-like-research-reviewer`
- required input ref kinds: synthesis draft, evidence table, limitations list
- source ID policy: preserve source IDs and claim-to-source mapping
- expected findings: faithful synthesis, unsupported synthesis claim,
  missing limitation, overgeneralization, contradiction
- forbidden outputs: new claims not grounded in provided evidence refs

### formalization-readiness-check

- recipient profile family: `codex-like-coding-reviewer` or
  `claude-like-research-reviewer`
- required input ref kinds: theorem statement, definitions, assumptions,
  proof outline
- source ID policy: preserve theorem, definition, and assumption IDs
- expected findings: ambiguous quantifier, missing definition, type issue,
  dependency gap, formal skeleton blocker
- forbidden outputs: claims that a proof assistant accepted the result unless
  the parent supplied evidence refs

### reproducibility-audit

- recipient profile family: `codex-like-coding-reviewer`
- required input ref kinds: script manifest, data manifest, environment notes,
  result summary
- source ID policy: preserve script, data, environment, and result refs
- expected findings: missing script, missing data, missing seed, environment
  gap, rerun ambiguity, artifact mismatch
- forbidden outputs: claims that code was executed or results were reproduced
  unless evidence refs prove that status
