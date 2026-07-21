---
name: decision-doubt-loop
description: Use in-flight, the moment you are about to let a non-trivial decision stand — a branching or control-flow change, crossing a module/service/agent boundary, an assertion the type system or proof checker cannot see, a high-stakes or irreversible action, or an analytical step a conclusion rests on. Materializes a fresh-context reviewer biased to disprove, while course-correction is still cheap.
metadata:
  short-description: Fresh-context adversarial review of a decision, in-flight
---

# Decision Doubt Loop

A confident answer is not a correct one. Long sessions turn assumptions into "facts"
without anyone noticing. This is the discipline of materializing a fresh-context
reviewer — biased to disprove, not approve — before a non-trivial decision stands.

This is not `research-verification-gate` or `research-report-reviewer`: those judge a
finished artifact at the end. This runs in-flight, per decision, while it is still
cheap to change course.

## When to use

Run it when about to let a decision stand and at least one of these holds:

- it introduces or changes branching / control flow
- it crosses a module, service, or agent boundary
- it asserts a property no checker can see (thread-safety, an invariant, "these are
  equivalent", a reduction step in a proof or argument)
- stakes are high: production, security-sensitive logic, irreversible or
  outward-facing actions, money
- a research conclusion rests on the step (a sourced fact, an inference treated as
  established, a chosen analytical direction)

Skip it for trivial, easily reversible, or already-verified decisions.

## Method — delegate to a fresh context

The point is a reviewer that does **not** share the context that produced the
decision. Do not self-review inline; spawn it.

1. **State the decision** in one line, plus the single load-bearing assumption it
   depends on and what would make it wrong.
2. **Pick the lens(es)** by decision type and delegate to the matching fresh-context
   reviewer persona, instructed to **refute**:
   - correctness / logic → `code-reviewer` (code) or `proof-checker` (a proof or
     formal argument)
   - boundaries, untrusted input, irreversible effects → `security-reviewer`
   - "is this actually verified?" → `test-reviewer`
   - a research inference or sourcing step → `paper-reviewer` or `literature-scout`
3. **For a multi-axis decision**, run the `Single-Decision Doubt Review` template in
   `agent-group-discuss`: independent skeptics, one decision, a refute-or-pass verdict.
4. **Resolve.** If the reviewer refutes the decision or its load-bearing assumption,
   course-correct now. If it survives, record that the decision stands and move on.

If fresh-context review is unavailable for a high-risk, irreversible,
security-sensitive, or outward-facing decision, do not substitute an inline
self-review. Output `BLOCKED-FRESH-CONTEXT-UNAVAILABLE`, state the decision and
why it is gated, and ask for explicit user direction. For lower-risk decisions,
you may proceed only after disclosing that the fresh-context check was unavailable
and either receiving confirmation or narrowing the decision to a reversible local
step.

## Output contract

A short visible note: `Decision`, `Load-bearing assumption`, `Doubt verdict`
(STANDS | REVISED | BLOCKED | BLOCKED-FRESH-CONTEXT-UNAVAILABLE), and the one
change made if revised.

## Guardrails

- fresh context is the mechanism — an inline "let me double-check" is the exact
  failure mode this exists to prevent
- bias the reviewer to disprove; a reviewer that sets out to approve finds nothing
- keep it bounded: one decision, the smallest sufficient reviewer set
- when unsure whether a decision is non-trivial, run it

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
- `cross-agent-adversarial-review` -- Producer-never-confirmer adversarial review of a paper, proof, or code artifact across agent families with a fresh-agent confirmation gate.
- `engineering-delivery-loop-runbook` -- Bounded build-and-deliver loop runbook: single-path implementation with seen-to-fail proof, cross-agent diff verification, behavior-preserving cleanup, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
- `reversible-decision-memo` -- Evidence-grounded decision record with named alternatives, source-cited rationale, reversibility class and trip-wires, and a fresh-context adversarial confirmation before the decision stands.
- `informal-to-lean-formalization-runbook` -- Local-first intake mapping an informal proof to Lean declarations with a scanner-first verification gate separating typecheck status from claim support.
