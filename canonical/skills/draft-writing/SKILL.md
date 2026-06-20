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
2. Inspect the local context needed to write in the requested form: current
   draft, outline, notes, source material, prior posts, templates, house style,
   venue instructions, and supplied examples. If expected context is absent,
   say so and state the style/content assumption before drafting.
3. Extract atomic claims from the current draft, outline, notes, or source
   material.
4. Classify each claim as contribution, evidence, assumption, caveat,
   comparison, recommendation, definition, result, limitation, or transition.
5. Map each substantive claim to support: source, experiment, theorem, data,
   author note, prior section, or `missing`.
6. Freeze the intended claim ledger before substantial rewriting.
7. Identify the active writing-style settings before rewriting:
   - always load `writing-style-settings.md`
   - load `math-manuscript-style.md` for mathematical, TCS, graph-theoretic,
     formal-proof, Lean-synchronized, or LaTeX manuscript prose
   - record `style_profile_ref`, `active_overlays`, `active_requirement_ids`,
     `session_local_additions`, and `style_applied` in the draft ledger or
     revision map
8. Rewrite for structure, clarity, and style without adding unsupported claims.
9. Audit the revision delta:
   - added claim
   - removed claim
   - strengthened claim
   - weakened claim
   - changed caveat
   - unsupported claim introduced
10. Report remaining gaps before presenting the draft as ready.

Use the installed templates when available:

- `draft-claim-ledger.md` for claim extraction and support mapping
- `draft-revision-map.md` for before/after revision audits

Use the instruction doc `claim-preserving-writing.md` for detailed guidance
when the task involves multiple sections, citation-sensitive prose, or repeated
revision rounds.

Always apply the installed instruction doc `writing-style-settings.md`. For
mathematical or LaTeX manuscripts, also apply `math-manuscript-style.md` through
the compatibility router `language-style-rules.md` if older installed targets
still refer to it. In particular, check that concepts are defined before use,
notation is not defined inside statements, unnecessary local terminology is
removed, result introductions explain each statement's role, and long proofs
begin with a clear proof idea.

## Output Rules

- Separate author-provided intent from model-inferred improvements.
- Label unsupported or newly introduced claims instead of smoothing them into
  polished prose.
- Preserve caveats unless the user explicitly asks to remove or revise them.
- Do not generate a blog post, article, report, or other format-matched draft
  before inspecting available prior examples/templates/style artifacts. If the
  repository or workspace has no such artifacts, say that explicitly.
- When making a rewrite, include a short claim-change note if the change is
  substantive.
- For finalizable draft work, record a style block or style record with
  `style_profile_ref`, `policy_hash`, `active_overlays`,
  `active_requirement_ids`, and `style_applied: true`. A bare assertion of
  `style_applied` is not enough when the workflow did not load the policy.
- If material evidence remains unchecked, say `incomplete analysis` before any
  final readiness claim.

## Boundary

This workflow tracks what is being said and whether declared support is present.
It does not independently prove claims true unless paired with verification,
review, citation lookup, tests, experiments, or formal checks.
