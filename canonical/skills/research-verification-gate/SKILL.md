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
  `policy_hash`, `active_overlays`, `active_requirement_ids`, and
  `style_applied`
- mathematical, TCS, graph-theoretic, formal-proof, or LaTeX writing loaded
  `math-manuscript-style.md`
- every input the conclusions rest on was read whole, or its truncation is
  disclosed: tool payloads reporting `complete: false`, capped subprocess
  output, partial retrievals, and summaries standing in for full sources
- remaining gaps are disclosed
- `incomplete analysis` is used when material scope is still unchecked, and when
  a load-bearing source was read only in part
- residual uncertainty is listed for unfinished or disputed load-bearing claims
- multi-LLM LGTM / same-family agreement alone is not bankable supporting
  evidence; require different-family re-derivation and/or a machine-checkable
  artifact for banking language
- review rounds that only reword without an evidence delta do not count as
  progress; do not continue until all reviewers approve
- open negative-space / blocked-route failures stay disclosed (halt and disclose)
- a check that did not conclude is no verdict: a timeout, an output cap, a
  missing tool, `command_failed`, `tool_unavailable`, a broker resource veto, or
  computation coverage `timed_out` or `unavailable` neither refutes nor
  supports the claim; list it under `Checks not run` (for Lean, see the Lean
  formalization notes in `research-job-presets.md` when that template is
  installed)
- feedback, not acceptance: a bare scan, a typecheck without trust-base
  evidence, `exact?` output, and LeanExplore, AXLE or OpenGauss results are
  feedback; a formal result supports a claim only through the promotion that
  deep-research `validate` checks
- when a structured deep-research run exists, run its `delivery-check` verb and
  quote the output verbatim; never upgrade its `NOT READY`
- when a fixed claim statement exists (a research brief's Fixed claim statement
  or an AGD `fixed_claim`), the delivered claim is quoted verbatim and labelled
  `fixed claim`, or `corrected claim (delta: ...)` for a recorded correction

## Output contract

Produce a short visible section titled `Delivery Check`.

Include:

- `Status` — `READY` or `NOT READY`
- `Gate version` — the `version` recorded for `research-verification-gate` in
  `manifest/skills.yaml` (`unversioned` when absent), so past verdicts stay
  interpretable as the gate evolves
- `Confirmed` — the key checks that passed
- `Gaps` — anything still blocking delivery
- `Checks not run` — checks that ended without a verdict, each with its reason
  (timeout, output cap, missing tool, resource veto); they neither refute nor
  support a claim
- `Residual uncertainty` — unfinished / disputed / negative-space items
- `Next step` — deliver now or fix specific gaps first
- `Style` — `style_profile_ref`, `policy_hash`, active overlays,
  `active_requirement_ids`, and whether `style_applied` is supported
- `Formal status` (when formal claims appear) —
  - `opengauss_run`: completed | failed | not_used
  - `lean_check_status` / placeholder / trust-base from strict gate
  - `claim_support_status` from deep-research ladder
  - `statement_relation_status` / `review_status`
  - OpenGauss success alone → **NOT READY** for “proved C”

Use the checklist in `references/checklist.md`.

## Guardrails

- do not silently downgrade a blocker into a caveat
- if material scope is unchecked, require `incomplete analysis`
- treat undisclosed truncation as unchecked scope, not as a formatting nit: a
  conclusion drawn from a silently partial source is unsupported, whatever the
  source appeared to say
- do not treat multi-LLM LGTM as proved or supported without different-family
  or machine-checkable support
- keep the gate short and concrete
- never read a no verdict as a refutation or as support. Known limit: the
  strict Lean gate currently reports a Lean run killed by a signal as
  `typecheck_failed`, so such a result may be a crash rather than a failed
  proof
- staged fail-visible rollout: while enforcement is warn-only, log every
  downgraded blocker or skipped check (in the run ledger when one exists) with
  its reason; a later fail-closed stage may make `NOT READY` a hard stop, and
  the override log is itself research evidence

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
- `cross-agent-adversarial-review` -- Producer-never-confirmer adversarial review of a paper, proof, or code artifact across agent families with a fresh-agent confirmation gate.
