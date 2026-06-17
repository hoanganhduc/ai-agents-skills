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
- remaining gaps are disclosed
- `incomplete analysis` is used when material scope is still unchecked

## Output contract

Produce a short visible section titled `Delivery Check`.

Include:

- `Status` — `READY` or `NOT READY`
- `Confirmed` — the key checks that passed
- `Gaps` — anything still blocking delivery
- `Next step` — deliver now or fix specific gaps first

Use the checklist in `references/checklist.md`.

## Guardrails

- do not silently downgrade a blocker into a caveat
- if material scope is unchecked, require `incomplete analysis`
- keep the gate short and concrete
