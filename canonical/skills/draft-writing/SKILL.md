---
name: draft-writing
description: Use when drafting, rewriting, polishing, or revising prose while preserving author intent by tracking claims, evidence, caveats, and revision deltas.
metadata:
  short-description: Claim-preserving draft writing workflow
---

# Draft Writing

Use this skill when the user wants help with a draft, section, report, article,
paper prose, grant text, response letter, or other writing where the wording may
change but the intended claims must stay controlled.

Do not use this as an AI detector. This is a procedural workflow for preserving
claims through drafting and revision.

## Trigger Examples

- draft this section from notes
- rewrite this paragraph without changing meaning
- polish this draft but keep my claims intact
- compare two draft versions for claim drift
- identify unsupported claims before revising
- turn this outline into prose while tracking claims

For paper/book review requests, use the relevant review workflow unless the
user is asking to rewrite or prepare draft text.

## Core Workflow

1. Define the writing scope and audience.
2. Extract atomic claims from the current draft, outline, or notes.
3. Classify each claim as contribution, evidence, assumption, caveat,
   comparison, recommendation, definition, result, limitation, or transition.
4. Map each substantive claim to support: source, experiment, theorem, data,
   author note, prior section, or `missing`.
5. Freeze the intended claim ledger before substantial rewriting.
6. Rewrite for structure, clarity, and style without adding unsupported claims.
7. Audit the revision delta:
   - added claim
   - removed claim
   - strengthened claim
   - weakened claim
   - changed caveat
   - unsupported claim introduced
8. Report remaining gaps before presenting the draft as ready.

Use the installed templates when available:

- `draft-claim-ledger.md` for claim extraction and support mapping
- `draft-revision-map.md` for before/after revision audits

Use the instruction doc `claim-preserving-writing.md` for detailed guidance
when the task involves multiple sections, citation-sensitive prose, or repeated
revision rounds.

## Output Rules

- Separate author-provided intent from model-inferred improvements.
- Label unsupported or newly introduced claims instead of smoothing them into
  polished prose.
- Preserve caveats unless the user explicitly asks to remove or revise them.
- When making a rewrite, include a short claim-change note if the change is
  substantive.
- If material evidence remains unchecked, say `incomplete analysis` before any
  final readiness claim.

## Boundary

This workflow tracks what is being said and whether declared support is present.
It does not independently prove claims true unless paired with verification,
review, citation lookup, tests, experiments, or formal checks.
