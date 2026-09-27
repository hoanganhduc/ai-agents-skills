# Independent bounded benchmark

Select a small definition/lemma scope from a source-pinned reference submission.
Independently check that reference scope first. Registration is not an oracle.
Record excluded results so they are not later counted as implementation gaps.

Give a fresh solver only the informal target, fixed definitions/API, allowed
background and required output shape. Withhold the reference proof, strategy
comments, answer-bearing documentation and indirect equivalent target imports.
The evaluator's context and the solver's context must be separate.

Freeze candidate source and inputs before comparing to the reference. Compare
meaning, hypotheses, definition correspondence, obligation closure, annotations,
module inventory, replay and archival recovery. Different proof strategies are
acceptable. A smaller/weaker statement is not an equivalent result.

Introduce negative controls independently: placeholders through imports, rogue
axioms, ungrounded cycles, stale output and altered statements that still compile.
Record the intended reason for each rejection. Report precisely what the small
benchmark covers; do not imply full-repository or live-publication coverage.
