# Writing Style Settings

This is the canonical, general writing-style policy for reusable writing tasks.
It applies to writing-producing workflows unless a higher-priority instruction,
project rule, or approved domain overlay says otherwise.

## Scope

Use this policy for drafts, research notes, reports, reviews, digests, role
prompts, summaries, and other user-facing prose. Code style and repository
conventions still come from the target project first.

## Precedence

Apply writing instructions in this order:

1. active system, developer, project, and user instructions for the current
   task;
2. accepted current-task style additions that do not conflict with higher
   priority instructions;
3. skill-specific task instructions;
4. active domain overlays;
5. this general policy;
6. approved reusable session additions mapped to stable active requirement IDs.

Persisted but unapproved session candidates are not active policy.

## Activation

Every writing-producing workflow must identify the active style profile before
final prose is delivered. A finalizable workflow must record at least
`style_profile_ref`, `policy_hash`, `active_overlays`, `active_requirement_ids`,
and `style_applied`.

## Context To Inspect

Before drafting or revising, inspect the relevant draft, notes, source material,
prior examples, templates, house style, venue instructions, and supplied
formatting constraints. If expected context is absent, say so and state the
assumption instead of inventing a house style.

## Claim And Evidence Discipline

Keep claims separate from evidence, assumptions, caveats, and open gaps. Do not
make unsupported claims sound more certain. Preserve caveats unless the user
explicitly asks to change them.

## Audience And Purpose

Choose wording for the reader and task. Prefer direct explanations over
decorative phrasing. Keep background only when it helps the reader understand
the contribution, decision, or result.

## Structure

Organize prose so the reader sees the purpose before the details. Use headings,
short paragraphs, and local transitions where they reduce ambiguity. Avoid
duplicated explanations and unused definitions.

## Sentence-Level Defaults

Use short, precise sentences. Prefer common terminology over private names. When
a non-common concept is necessary, define it before first use and explain its
role briefly.

## Formatting Defaults

Use formatting only when it helps scan or verify the content. Keep lists
parallel. Prefer inline equations or identifiers unless display formatting is
needed for readability or mathematical importance.

## Uncertainty And Gaps

State material assumptions, unchecked evidence, and blocked inspection. Use
`incomplete analysis` when material scope remains uninspected.

## Session Updates

When the user gives a new durable writing requirement, apply it to the active
task if compatible with higher-priority instructions. Capture it as a
`pending record` with source phrase, normalized rule, scope, conflicts, approval
state, and proposed destination. Do not promote it into canonical policy without
explicit approval.

## Domain Overlays

Domain overlays may add or specialize this policy. They must not silently weaken
hard general rules. Any override must record the authority, reason, approver,
and target scope.
