# Math Manuscript Style

This overlay applies to mathematical manuscripts, TCS notes, graph-theoretic
drafts, proof sketches, Lean-synchronized papers, and similar technical prose.
It extends `writing-style-settings.md`.

## Activation

Use this overlay whenever the writing task contains theorem statements, formal
definitions, mathematical notation, proof text, graph-theoretic terminology, or
LaTeX manuscript source. Record it in `active_overlays` as
`math-manuscript-style`.

## Definitions And Notation

Define every concept and notation before first use. Put concepts or notation
used many times in preliminaries. Define one-use concepts locally just before
they are needed. Do not define notation inside a theorem, lemma, proposition,
or corollary statement.

## Common Terminology

Prefer standard graph-theoretic and TCS terminology over private names. If a
nonstandard term is necessary, define it, explain why it is useful, and use it
only after the reader has the underlying object in view.

## Sentence Openings

Use the general research-paper sentence-opening rule from
`writing-style-settings.md`. In mathematical prose, standard openings such as
"Let ... be ...", "Suppose ...", "Assume ...", and "Recall ..." are normal and
should be kept when they give the shortest precise setup. Avoid command-style
openings such as "Set", "Put", "Choose", "Apply", or "Consider" when a full
declarative sentence or a standard setup sentence is clearer.

## Statements

Mathematical statements must be self-contained. All hypotheses, graph classes,
operations, parameters, and reconfiguration rules used in a statement must be
common or defined before the statement.

Do not put the role of a result only in a parenthetical theorem title. Add one
to three short sentences before important statements explaining what the result
says and why it is needed.

## Sections And Preliminaries

Each section except an introduction or concluding remarks should begin with a
short outline paragraph. Preliminaries should be selective: keep terminology or
notation only when it prevents ambiguity or reduces real repetition later.

## Proofs

Start long, technical, or important proofs with a short proof-opening paragraph
that explains the main induction, reduction, counting argument, case split, or
invariant before details begin. If a result has numbered or lettered parts, the
proof should follow the same structure.

## Equations

Prefer inline equations for routine notation. Use display equations only when
the equation is important, too long for inline reading, or needs alignment for
clarity.

## Gaps And Verification

Separate theorem statement, proof idea, verification status, and open gaps. Do
not claim a proof is complete when checks are partial.
