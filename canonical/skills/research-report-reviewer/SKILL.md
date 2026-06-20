---
name: research-report-reviewer
description: Use when a research draft or report exists and needs a pre-final review for unsupported claims, ambiguity, scope drift, or missing evidence before delivery.
metadata:
  short-description: Findings-first review of a research draft
---

# Research Report Reviewer

Use this after a draft exists and before presenting research as final.

## What to inspect

- the stated scope, question, exclusions, and any requested output format
- available structured artifacts such as `sources.jsonl`, `claims.jsonl`,
  `guards.jsonl`, `delivery.json`, source ledgers, analysis matrices, and report
  evidence mappings
- unsupported or weakly supported claims
- missing dates or stale-time ambiguity
- scope drift relative to the original question
- places where observation and inference are blended together
- overconfident language that should be hedged or marked `incomplete analysis`
- whether prior posts, templates, style guides, or supplied examples were
  inspected before a format-matched draft
- whether the draft or workflow records an active writing-style profile from
  `writing-style-settings.md`, plus `math-manuscript-style.md` when applicable,
  including `style_profile_ref`, `active_overlays`, and
  `active_requirement_ids`
- whether `style_applied: true` is supported by workflow evidence rather than a
  bare self-assertion

## Output contract

Start with a visible section titled `Review Findings`.

Then give:

- `Verdict` — `BLOCK`, `FLAG`, or `PASS`
- `Findings` — the highest-signal issues first
- `Repairs` — the minimum changes needed before delivery
- `Style` — missing or inconsistent `style_profile_ref`, `active_overlays`,
  `active_requirement_ids`, or `style_applied` records when relevant

If there are no issues, say so explicitly and keep the pass short.

Use `references/reviewer-prompt.md` as the detailed checklist.

## Guardrails

- findings first, summary second
- focus on research quality, not copyediting
- prefer the smallest repair that makes the draft defensible
- if a gap cannot be closed, require explicit disclosure instead of pretending it is solved
