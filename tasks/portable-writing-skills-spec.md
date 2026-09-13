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
  - automatic cleanup of compatibility instruction copies installed by an
    older manifest; the installer has no generic retired-artifact sweep, so
    that upgrade path requires a separately scoped installer change;
  - installing to real agent homes, submitting reviews, deleting source
    material, committing, or pushing.

## Assumptions

- Current venue guidance outranks historical books and community examples.
- Mathematical-writing books are candidate sources, not blanket authority.
- Review recommendations remain advisory and the parent owns validation and
  acceptance.
- Unknown or mixed MathSciNet/zbMATH source provenance fails closed before a
  document is opened or routed to a tool.

## Interfaces

- Canonical prose policy and indexes under `canonical/instructions/`
- Writing and review workflows under `canonical/skills/`
- Claim/revision and writing-review templates under `canonical/templates/`
- Artifact declarations and profiles in `manifest/artifacts.yaml`
- Writing-policy hash synchronization in `tools/sync_writing_policy.py`

## Acceptance Criteria

- Four canonical prose instructions and one writing-review template install
  through the existing artifact system.
- Retired compatibility documents have no live consumer or manifest reference.
- New requirements have contiguous IDs, sources, anchors, tests, matrix rows,
  and registry allocations.
- MathSciNet/zbMATH provenance denial occurs before lookup, parsing,
  delegation, logging, compilation, or writes.
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

- A domain overlay loaded after document retrieval would disclose restricted
  review material; routing tests must prove zero tool calls on denial.
- Historical advice may duplicate or contradict current policy; migration rows
  must record merge, supersede, deprecate, or reference-only treatment.
- Raw reviewer correspondence may contain confidential or personal data;
  templates use minimized paraphrases and restricted inert refs.
- Citation-removal and figure-use checks may be overread; they are readability
  decisions only and never waive attribution or trigger figure generation.
