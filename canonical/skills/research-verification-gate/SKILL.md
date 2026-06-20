---
name: research-verification-gate
description: Use immediately before calling a research answer done, final, or complete to verify evidence coverage, dates, remaining gaps, and delivery readiness.
metadata:
  short-description: Final delivery gate for research answers
---

# Research Verification Gate

Use this as the last gate before claiming a research output is ready.

## Required checks

- the stated scope was actually answered
- important claims still have supporting evidence
- available structured artifacts (`sources.jsonl`, `claims.jsonl`,
  `guards.jsonl`, `delivery.json`, source ledgers, evidence maps) were inspected
  when present
- time-sensitive facts include concrete dates when needed
- requested format/style context was inspected for blog, article, report, or
  other format-matched writing
- the active writing-style profile from `writing-style-settings.md` was loaded
  and recorded for finalizable writing, including `style_profile_ref`,
  `active_overlays`, `active_requirement_ids`, and `style_applied`
- mathematical, TCS, graph-theoretic, formal-proof, or LaTeX writing loaded
  `math-manuscript-style.md`
- remaining gaps are disclosed
- `incomplete analysis` is used when material scope is still unchecked

## Output contract

Produce a short visible section titled `Delivery Check`.

Include:

- `Status` — `READY` or `NOT READY`
- `Confirmed` — the key checks that passed
- `Gaps` — anything still blocking delivery
- `Next step` — deliver now or fix specific gaps first
- `Style` — `style_profile_ref`, active overlays, `active_requirement_ids`, and
  whether `style_applied` is supported

Use the checklist in `references/checklist.md`.

## Guardrails

- do not silently downgrade a blocker into a caveat
- if material scope is unchecked, require `incomplete analysis`
- keep the gate short and concrete

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and Modal/GitHub Actions credit-gated heavy-compute offload.
- `cross-agent-adversarial-review` -- Producer-never-confirmer adversarial review of a paper, proof, or code artifact across agent families with a fresh-agent confirmation gate.
