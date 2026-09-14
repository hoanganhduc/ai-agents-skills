# Specification: Consolidated Writing And Review Policy

## Goal

Consolidate reusable prose policy into one general policy and three domain
overlays, preserve claim-level revision controls in the existing writing
workflow, and let independent review workflows recommend compliant revisions
without acquiring edit authority.

## Scope

- In scope:
  - retain `writing-style-settings`, `math-manuscript-style`, and
    `graph-combinatorics-style` as canonical policy documents;
  - add a combined `mathscinet-zbmath-review-style` overlay;
  - retire the compatibility-only `claim-preserving-writing` and
    `language-style-rules` documents after clause-level migration;
  - add one thin `writing-review` template that composes existing claim,
    revision, review, citation, delegation, and final-gate mechanisms;
  - add only source-audited general, mathematical, graph, book-review, and
    bibliographic-review rules;
  - update manifests, writing-policy sidecars, generated documentation, and
    focused tests.
- Out of scope:
  - code-writing policy;
  - a new review gate, skill, schema, validator, receipt, registry, dependency
    mechanism, or automatic edit application;
  - submitting reviews, deleting source material, changing code-writing rules,
    editing provider/MCP/hook configuration, committing, or pushing.

## Follow-up: Installed Target Activation

The approved follow-up adds the previously excluded installer upgrade path.
It is limited to the four writing instructions and their existing consumers.

- Add a short managed routing block on targets whose instruction-doc directory
  is not a proven native auto-load surface. Keep Grok on its native rules
  surface.
- Refresh only already-managed writing-policy consumer skills; never create an
  absent consumer as a side effect of selecting instruction documents.
- Retire installed `claim-preserving-writing.md` copies only when their exact
  state records and signatures authorize removal and all replacements are
  active.
- Add sanitized, version-scoped loader checks, with Codex as the primary native
  acceptance target.

## Assumptions

- Current venue guidance outranks historical books and community examples.
- Mathematical-writing books are candidate sources, not blanket authority.
- Review recommendations remain advisory and the parent owns validation and
  acceptance.
- A MathSciNet/zbMATH review request does not by itself classify a local or
  user-provided item as service-supplied or create a content-access precondition.

## Interfaces

- Canonical prose policy and indexes under `canonical/instructions/`
- Writing and review workflows under `canonical/skills/`
- Claim/revision and writing-review templates under `canonical/templates/`
- Artifact declarations and profiles in `manifest/artifacts.yaml`
- Writing-policy hash synchronization in `tools/sync_writing_policy.py`

## Acceptance Criteria

- Four canonical prose instructions and one writing-review template install
  through the existing artifact system.
- A full or previously completed four-document install produces an active
  target-appropriate route without duplicating policy text.
- Partial, modified, or untrusted document sets cannot leave an active managed
  writing router behind.
- Already-managed writing consumers update only when their installed signatures
  still match state; user-modified and absent consumers are preserved.
- Retired compatibility copies are removed last, backed up, rollback-capable,
  and never removed when a live consumer still references them.
- Retired compatibility documents have no live consumer or manifest reference.
- New requirements have contiguous IDs, sources, anchors, tests, matrix rows,
  and registry allocations.
- MathSciNet/zbMATH guidance adds no access, transfer, compilation, delegation,
  retention, or deletion restriction without user or directly verified source
  authority.
- Existing V1 delegation packets carry only inert refs and advisory actions;
  no new authority-bearing fields are introduced.
- Review-driven edits close every reviewer comment and preserve or explicitly
  disclose claim, evidence, caveat, and support changes.
- Focused writing, installer, and delegation tests pass, followed by the full
  repository suite.

## Verification

- Run the new focused regressions before and after implementation.
- Run `python tools/sync_writing_policy.py --check`.
- Regenerate and check documentation.
- Run focused installer/delegation tests and then the full test suite.
- Obtain fresh-context code, test, and boundary review of the final diff.

## Risks

- A review request may be mistaken for evidence that an item was supplied by a
  reviewing service; routing tests must reject that inference and must not add
  a content-access precondition.
- Historical advice may duplicate or contradict current policy; migration rows
  must record merge, supersede, deprecate, or reference-only treatment.
- Review templates must not add review-specific retention or delegation rules
  beyond explicit user instructions or directly verified source terms.
- Citation-removal and figure-use checks may be overread; they are readability
  decisions only and never waive attribution or trigger figure generation.
