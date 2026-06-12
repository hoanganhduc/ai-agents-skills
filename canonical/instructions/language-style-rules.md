# Language And File Style Rules

Follow the target repository's style before any global preference.

General rules:

- Keep generated prose factual and concise.
- Use existing naming and structure in code.
- Prefer explicit assumptions over hidden guesses.
- Use ASCII unless the existing file or task requires otherwise.
- Avoid broad rewrites unrelated to the requested change.

For mathematical manuscripts:

- Define every concept before its first use. Put concepts and notation used
  several times in the preliminaries. Define one-use concepts locally, just
  before they are needed.
- Keep preliminaries selective. Do not keep terminology or notation unless it
  reduces real repetition or prevents ambiguity later.
- Prefer standard graph-theoretic and TCS terminology over private vocabulary.
  If a nonstandard term is necessary, define it, explain why it is useful, and
  use it only after the reader has the underlying object in view.
- For common graph-theoretic notions that the manuscript does not define, cite
  a standard reference. For graph theory, Diestel's *Graph Theory* is an
  appropriate default unless the project names a different source.
- Define the graph class, operation, parameter, and reconfiguration rule
  explicitly when they are part of the problem statement.
- Remove redundant explanations, repeated notation, and unused definitions.
  Replace avoidable local terminology with standard descriptions.
- Use theorem, proposition, lemma, and corollary environments for mathematical
  results. Do not use a generic `Statement` environment unless the manuscript
  has a clear local convention for it.
- Do not put the role of a result only in a short parenthetical title such as
  `Theorem 1 (Handshaking Theorem)`. Add one to three sentences before the
  statement explaining what the result says and why it is needed.
- Make explanatory paragraphs useful. A proof roadmap should explain how each
  step contributes to the claim, not merely list named steps or unexplained
  local terms.
- Start every long or technical proof with a short paragraph giving the proof
  idea. State the main induction, reduction, counting argument, case split, or
  invariant before entering details.
- When a result has parts such as `(a)`, `(b)`, and `(c)`, give the proof the
  same part structure. In LaTeX manuscripts that use the `enumerate` package,
  prefer `\begin{enumerate}[(a)]` for these lists when it matches local style.
- Avoid hidden setup phrases such as "under the standing assumptions" unless
  the assumptions have a named, visible statement nearby. Restate hypotheses in
  major results when doing so makes the result self-contained.
- Keep algorithmic-complexity context only when it supports the manuscript's
  stated problem. Do not add PSPACE-completeness or other TCS background if the
  paper is framed as a purely graph-theoretic result and the context does not
  help the reader understand the contribution.
- Use short, precise sentences. Replace vague phrases, unexplained metaphors,
  and overloaded local names with direct descriptions of the mathematical
  objects and logical dependencies.
- Separate theorem statement, proof idea, verification, and open gaps.
- Do not claim a proof is complete when checks are only partial.
