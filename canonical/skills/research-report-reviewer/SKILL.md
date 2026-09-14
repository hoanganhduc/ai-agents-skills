---
name: research-report-reviewer
description: Use when a research draft or report exists and needs a pre-final review for unsupported claims, ambiguity, scope drift, or missing evidence before delivery.
metadata:
  short-description: Findings-first review of a research draft
---

# Research Report Reviewer

Use this after a draft exists and before presenting research as final.

## Service-Supplied Review Material

If the draft or requested review names Mathematical Reviews/MathSciNet or
zbMATH, load `mathscinet-zbmath-review-style.md` before assessing or finalizing
the review. The request does not by itself imply that the item was supplied by
either service. Do not require a provenance declaration or stop before content
access unless the user states, or the artifact itself indicates, that it is
service-supplied.

If `mathscinet-zbmath-review-style.md` is unavailable in the current install,
disclose the missing style guidance and do not claim service-format compliance.

## What to inspect

- the stated scope, question, exclusions, and any requested output format
- available structured artifacts such as `sources.jsonl`, `claims.jsonl`,
  `guards.jsonl`, `delivery.json`, source ledgers, analysis matrices, and report
  evidence mappings
- unsupported or weakly supported claims
- claims resting on sources that were read only in part, where the draft does
  not say so — silently truncated tool output, capped payloads, or a summary
  standing in for the full source make a claim unsupported, not merely thin
- missing dates or stale-time ambiguity
- scope drift relative to the original question
- places where observation and inference are blended together
- overconfident language that should be hedged or marked `incomplete analysis`
- whether prior posts, templates, style guides, or supplied examples were
  inspected before a format-matched draft
- whether the draft or workflow records an active writing-style profile from
  `writing-style-settings.md`, plus `math-manuscript-style.md` when applicable,
  including `style_profile_ref`, `policy_hash`, `active_overlays`, and
  `active_requirement_ids`
- whether `style_applied: true` is supported by workflow evidence rather than a
  bare self-assertion

## Output contract

Start with a visible section titled `Review Findings`.

Then give:

- `Verdict` — `BLOCK`, `FLAG`, or `PASS`
- `Findings` — the highest-signal issues first
- `Repairs` — the minimum changes needed before delivery
- `Style` — missing or inconsistent `style_profile_ref`, `policy_hash`,
  `active_overlays`, `active_requirement_ids`, or `style_applied` records when
  relevant

When a repair proposes literal replacement prose, follow the Writing
Recommendation Contract in `writing-review.md`. Bind the recommendation to the
affected frozen claim IDs, evidence refs, and active requirement IDs; flag any
claim, caveat, citation, or support change. Review remains advisory.

If there are no issues, say so explicitly and keep the pass short.

Use `references/reviewer-prompt.md` as the detailed checklist.

## Guardrails

- findings first, summary second
- focus on research quality, not copyediting
- prefer the smallest repair that makes the draft defensible
- if a gap cannot be closed, require explicit disclosure instead of pretending it is solved
- do not apply repairs, patch the reviewed report, create follow-on artifacts,
  or continue into remediation after a review-only request unless the user
  explicitly asks for those actions
