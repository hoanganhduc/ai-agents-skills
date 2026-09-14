# Math Manuscript Style

This overlay applies to mathematical manuscripts, TCS notes, graph-theoretic
drafts, proof sketches, Lean-synchronized papers, and similar technical prose.
It extends `writing-style-settings.md`.

For combinatorics and graph theory, also apply
`graph-combinatorics-style.md`, which carries the terminology and notation
conventions specific to those fields.

Every rule below states what to do and why it helps the reader. A rule you
cannot explain to a student does not belong in a manuscript.

## How Notation Is Written Here

Mathematical notation in this document, and in the overlays that extend it, is
written as LaTeX inside `$...$`. So the rules below say $xy$-path, $\Delta(G)$
and $v \in V(G)$ rather than spelling those out in plain characters or Unicode.
Read every such span as LaTeX source, and reproduce it as LaTeX.

The same applies to the manuscript being written: every mathematical symbol that
appears in running prose goes in math mode, including a bare variable name.

- Wrong: `Let G be a graph with n vertices, and let k be at most n.`
- Right: `Let $G$ be a graph with $n$ vertices, and let $k$ be at most $n$.`

A symbol set as ordinary text gets the wrong font, the wrong spacing, and the
wrong line-breaking, and the difference is visible on the page. Backticks in
these documents mark literal text — a package name, a command, a term being
discussed as a word — and never mathematics.

## Activation

Use this overlay whenever the writing task contains theorem statements, formal
definitions, mathematical notation, proof text, graph-theoretic terminology, or
LaTeX manuscript source. Record it in `active_overlays` as
`math-manuscript-style`.

When the subject is graphs, digraphs, hypergraphs, designs, posets, matroids or
other finite combinatorial structures, load `graph-combinatorics-style.md` as
well and record `graph-combinatorics-style` in `active_overlays` alongside this
one. Route conditional overlays here so consumers do not have to list each
domain overlay individually.

When the requested output is a Mathematical Reviews/MathSciNet or zbMATH
bibliographic review, load `mathscinet-zbmath-review-style.md` and record
`mathscinet-zbmath-review-style` in `active_overlays`. Its provenance gate runs
before any assigned item is opened, retrieved, parsed, quoted, or delegated.

## Definitions And Notation

Define every nonstandard concept and every piece of notation before first use.
A domain overlay may admit a basic field-standard term without an explicit
definition only after its standard meaning and notation have been checked; if
conventions vary or the intended meaning is narrower, define it locally. Put
concepts or notation used many times in preliminaries. Define one-use concepts
locally just before they are needed. Do not define notation inside a theorem,
lemma, proposition, or corollary statement.

Define each variable, at least informally, where it first appears. A reader who
meets an undefined symbol stops reading and starts searching.

Mark a term with `\emph{}` at the point where it is defined, and only there. The
emphasis is what lets a reader scanning backwards find the definition, and
seeing the same term unemphasised later confirms they are not at a second
definition.

- Wrong: `A graph is bipartite if its vertex set admits ...`
- Right: `A graph is \emph{bipartite} if its vertex set admits ...`

Use `\emph{}` rather than `\textit{}`. Emphasis nests: inside an italic context
such as a theorem statement, `\emph{}` switches to upright and stays visible,
while `\textit{}` would change nothing the reader can see.

State an important definition twice, in complementary ways. One phrasing gives
the rule, the other gives the idea, and together they survive a careless first
reading.

- Weaker: $L(C,P)$ is the set of all sums $c + p_1 + \dots + p_m$.
- Better: $L(C,P)$ is the set of all sums $c + p_1 + \dots + p_m$; equivalently,
  it is the smallest set containing $C$ and closed under adding elements of $P$.

Use one notation for one thing, and one thing for one notation. If indices run
$1 \le j \le n$ in one place, do not switch to $1 \le k \le n$ elsewhere without a
reason. Reusing a symbol for two meanings costs the reader more than any saving
in letters.

## Notation Economy

Notation should carry the parameters that matter and hide the ones that do not.
Notation is a tool for suppressing detail, so let it suppress the routine detail
and leave the interesting detail visible.

Introduce notation only for what recurs. An expression used once can be written
out; an expression used three or more times earns a name.

Do not get carried away by subscripts. Naming the elements of a set forces
subscripted subscripts on every later subset, so leave the elements unnamed and
speak of elements $x$ and $y$ of $X$ where that is enough.

- Weaker: Let $X = \{x_1, \dots, x_n\}$, then later $\{x_{i_1}, \dots, x_{i_m}\}$.
- Better: Let $X$ be a finite set, then later a subset of $X$.

Give peripheral objects bland names and save suggestive names for the central
ones. "Type I" and "Type II" tell the reader that nothing turns on the name;
a vivid name for a minor object misdirects attention.

Do not name a concept after yourself or after people close to you. Readers
cannot tell whether the name records a debt or a joke.

Stay compatible with the notation already used in the literature you build on.
A reader arriving from those papers should not have to relearn the alphabet.

Follow the field's implicit conventions about which letters denote what — $x$
for a real, $z$ for a complex number, $n$ for a natural number, $\varepsilon$ for a small
positive quantity. These carry type information for free, so a reader who has
forgotten a definition can still follow the argument; violating them costs
attention for no gain.

Put the quantity being controlled on the left of an inequality and the known
bound on the right, and order a bound with its main term first and its error
terms after.

- Weaker: $5 > x$, and $x < \varepsilon + 1$
- Better: $x < 5$, and $x < 1 + \varepsilon$

## Prose Carries The Argument

Write the argument in words and reserve symbols for computation. Plain English
is more informative than a symbol chain, and it costs nothing in rigour.

Separate symbols belonging to different formulas with words.

- Wrong: Consider $S_q$, $q < p$.
- Right: Consider $S_q$, where $q < p$.

Never begin a sentence with a symbol. The reader cannot tell a sentence
boundary from a subscript.

- Wrong: $x^n - a$ has $n$ distinct zeroes.
- Right: The polynomial $x^n - a$ has $n$ distinct zeroes.

Do not use logical symbols such as $\forall$, $\exists$, $\implies$, or $\iff$ as
abbreviations in running prose. Write the words. Logic papers, where these are
the subject matter, are the exception.

Connective words carry the shape of the argument. "also", "but", "since",
"however" tell the reader how the next step relates to the last one, and
deleting them to save space deletes the relation.

A sentence should still read smoothly when every formula in it is replaced by a
nonsense word. Many readers skim formulas on a first pass, and a sentence that
collapses without them cannot be skimmed.

Do not list formulas the way a homework solution does. Tie consecutive steps
together with a running commentary that says what is being done and why.

Prefer plain words to ornamental ones. The purpose of the writing is to inform,
not to impress, and an unusual word costs the reader a pause.

## Terminology And Jargon

Prefer standard terminology over private names. If a nonstandard term is
necessary, define it, explain why it earns its place, and use it only after the
reader has the underlying object in view.

Before defining a new concept, go and look for an existing one that already
covers it. Check the standard references of the field and the papers you are
citing, and search for the notion under the names it would plausibly carry.
This is a search, not a recollection: a concept that is new to you is often
twenty years old under another name.

If an existing notion covers the case, use it, with its established name and
notation, even when your own framing feels more natural. A second name for an
object that already has one splits the literature, and every later reader has to
work out that the two are the same thing before they can use either.

Coin a new concept only when no existing one fits. When you do, say what it
generalises or specialises, name the closest existing notion, and say why that
notion does not suffice. A definition that arrives without that comparison reads
as though the search was never made.

Do not use jargon that the argument does not need. Specialists also read more
comfortably in a non-specialist's vocabulary; unnecessary jargon narrows the
audience without adding precision.

Choose the simpler and more common word wherever one exists, and keep the
sentence forms ordinary. An unfamiliar word makes the reader stop to decide
whether it carries a technical meaning; an unusual construction makes them
re-read to recover the grammar. Neither pause buys anything, and both are paid
by every reader.

- Wrong: `We elucidate the requisite machinery in the sequel.`
- Right: `We set up the machinery we need in the next section.`

The test is whether a competent reader outside your exact sub-problem would
have to look anything up. If the answer is yes and the word was not a technical
term, replace it.

Avoid long strings of nouns used as adjectives. Each noun in the chain could
modify any later one, so the reader has to try the combinations.

- Wrong: `the packet switched data communication network protocol problem`
- Right: `the protocol problem for packet-switched data networks`

Settle terminology once and keep it. Choosing between "vertex" and "node", or
between "nonnegative" and "non-negative", is less important than making the
same choice every time.

Preserve distinctions that the language still makes: "imply" is not "infer",
"maximal" is not "maximum", "distinct" is not "unique". A lost distinction
cannot be recovered by the reader.

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

Keep statements short. Do not define terms, add discussion, or motivate inside
a theorem or lemma; introduce what is needed before the statement so the
statement itself can be read as one claim.

Use the environment that names the statement's role: theorem, proposition,
lemma, or corollary. Do not hide these distinctions behind a generic statement
environment.

Do not put the role of a result only in a parenthetical theorem title. Add one
to three short sentences before important statements explaining what the result
says and why it is needed.

The sentence immediately before a statement must be a complete sentence, or end
with a colon.

- Wrong: `We now have the following` / Theorem. $H(x)$ is continuous.
- Right: `We can now prove the main result of this section.` /
  Theorem. The function $H(x)$ defined in (5) is continuous.

Capitalize numbered names: Theorem 1, Lemma 2, Algorithm 3.

## Sections And Preliminaries

Each section except an introduction or concluding remarks should begin with a
short outline paragraph. Preliminaries should be selective: keep terminology or
notation only when it prevents ambiguity or reduces real repetition later.

Write in modules of one to three pages, each with a stated purpose. Section and
subsection titles are signposts, and a reader who has lost the thread recovers
it from them.

A section that presents a result usually needs more than the result: what the
section contributes, the statement, an interpretation, the idea of the proof,
the limits of the statement, and an example.

Make the opening paragraph the best paragraph, and its first sentence the best
sentence. A reader who stumbles at the start reads the rest defensively.

- Weaker: `An important method for internal sorting is quicksort.`
- Better: `Quicksort is an important method for internal sorting, because ...`

Motivate before you state. Say what the reader is about to get and why it is
worth the effort; a definition introduced by decree is harder to hold on to
than one introduced by a question it answers.

Before a general result, it often helps to give a toy case or a less technical
special case first, for the flavour of the statement and the shape of the
proof — even when that case is already known.

Include heuristic and motivational reasoning, and mark it as such. Put it in a
remark or a footnote so it is never mistaken for part of the formal argument.

Describe your own results without superlatives. Let the statement carry the
weight.

Start a new section at each turning point in the argument, and keep closely
related facts together in one. Place a lemma close to where it is used,
especially when it is used only once.

Put the milestones early and the technical machinery late. A reader should meet
the statement of a key proposition before the details of its proof, and the
learning curve should rise rather than start steep.

Send to an appendix any material that is necessary but different in nature from
the rest of the paper, and demote a peripheral result to a remark or footnote.

Never save the punch line for the last page. A paper whose point arrives at the
end is read as though it had no point until then.

## Proofs

Start long, technical, or important proofs with a short proof-opening paragraph
that explains the main induction, reduction, counting argument, case split, or
invariant before details begin. If a result has numbered or lettered parts, the
proof should follow the same structure.

Proofs are discovered backwards and must be written forwards. Establish the
lemmas, then combine them, then conclude. A proof that withholds its structure
until the last line reads as a sequence of unmotivated steps.

Keep the argument locally linear. Each sentence should rest on what precedes
it, not on what follows.

- Weaker: By Lemma 1, $2k$ is composite, because $2k$ is even.
- Better: Note that $2k$ is even. By Lemma 1, $2k$ is composite.

Prefer a direct proof to a proof by contradiction when both are available.
Contradiction is often the easiest route to a discovery and the hardest route
for a reader.

Tell the reader when a step is being skipped. A gap that is announced is a
convenience; a gap that is concealed is an error the reader must find.

Move long technical arguments to an appendix, and keep the main text
self-contained: no references to lemmas or notation that live only in the
appendix.

Do not nest proof environments. A second proof label before the first
end-of-proof marker leaves the reader unsure which claim is being closed.

Give the most care to the places where the statements suddenly get stronger —
where something true for one value becomes true for many, or where an argument
in dimension $d$ is converted into one in dimension $d+1$. That is where the
idea powering the proof sits, so it is where the reader most needs help. It is
also where a flawed proof usually fails, which is another reason to make the
step explicit rather than brisk.

Remember that a reader who meets an undefined term or an unexplained step stops
there, exactly as a compiler does. A single typo or a missing definition can
halt comprehension of everything after it, so state the dependency between
consecutive steps rather than leaving it to be inferred.

## Quantifiers

State quantifiers in an unambiguous order. Quantifier scope is the most common
silent ambiguity in mathematical prose, and English word order does not always
fix it.

- Ambiguous: for every $n$, we have $n < c$, for some $c$
- Clear: there exists $c$ such that $n < c$ for every $n$
- Clear: for every $n$ there exists $c$ such that $n < c$

Use "each" or "every" for universal quantification over a singular object. The
word "any" reads as "some" in some contexts and "all" in others.

When an asymptotic bound hides quantifiers, state them. $T = O(n^d)$ means
there is a constant, and the reader needs to know what it may depend on.

Place `only` and every other scope-bearing modifier immediately next to the
expression it modifies. A displaced modifier changes which variable, case, or
claim is being restricted.

## Equations And Numbers

Prefer inline equations for routine notation. Use display equations only when
the equation is important, too long for inline reading, or needs alignment for
clarity.

Display an important formula on a line of its own, and number the ones that are
referred to from elsewhere.

Avoid inline fractions built with a fraction bar; they shrink the type and
disturb line spacing. Write $(x + 2)/(x + 3)$ instead.

Use spacing to help the reader parse a long expression, especially around a
conditioning bar or a set-builder colon.

Spell out small numbers used as adjectives, and keep numerals for numbers being
discussed as numbers.

- Wrong: `The method requires 2 passes.`
- Right: `The method requires two passes.` / `Method 2 requires 17 passes.`

## Figures And Examples

Use a figure when prose leaves a structure, process, or proof idea opaque. Put
the figure near the passage that interprets it, define its nonstandard symbols,
and keep its caption aligned with the claim made in the surrounding prose.
Related examples should build on one another when a cumulative sequence makes
the general idea easier to recover.

This semantic decision does not itself trigger figure generation. When a figure
is requested and can be expressed as TikZ, route its production and verification
through `tikz-draw` as specified below.

## Abstracts

Write the abstract so that it survives being separated from the paper. It is
reproduced on its own by reviewing and indexing services — Zentralblatt and
zbMATH publish authors' abstracts in place of reviews, and arXiv distributes it
as a standalone plain-text field — so anything in it that points back into the
article is lost in transit. That rules out references to numbered sections,
numbered equations, and the bibliography.

State the problem or context, the principal result, what is new, and any
limitation needed to interpret the claim. Mention the method only when it is
part of the contribution. This is a content checklist rather than a required
sentence or paragraph order.

Never cite in an abstract by number or by author-year. If a work must be cited
there, supply the whole reference. Under `biblatex` this is `\fullcite`, which
expands the entry in place; naming the work in words does the same job.

- Wrong: `improving the bound of [3]`
- Wrong: `improving the bound of Smith and Jones (2019)`
- Right: `improving the bound of \fullcite{smith2019}`
- Right: `improving the bound of Smith and Jones, "Title", J. Combin. Theory
  Ser. B 137 (2019), 1--23`

The AMS Author Handbook states the rule and the reason directly: *"The abstract
should contain no text references to the bibliography unless the bibliographic
reference is fully supplied. For example, [3] is meaningless to the reader once
the abstract is separated from the article."*

Two practical consequences. First, prefer an abstract with no citation at all;
a result is usually better described than attributed, and the introduction is
where attribution belongs. Second, the arXiv abstract field is plain text with
a 1920-character limit and no macro expansion, so a `\fullcite` does not reach
it — write that reference out there, using the `arXiv:YYMM.NNNNN` form when
citing a preprint.

## Introductions

The introduction is where the paper is read or put down. Say what the results
are, why they are new, and where they are proved.

State the key point prominently. A contribution buried in a footnote or a
closing remark will be missed, and the reader will conclude the paper has less
in it than it does.

Compare the work against the literature, and say what is new or surprising
given that context. If new difficulties had to be resolved that earlier work did
not face, name them; that is usually the real contribution.

State or paraphrase the main results, and say where each is proved. When the
main result is too technical to state early, state a simpler special case that
is still interesting, and give the general form later.

Give counterexamples that show the result cannot be improved in the obvious
directions. Without them a reader assumes the obvious improvement was missed.

Title section headings by what they contain.

- Weaker: `Section 4. Step 2`
- Better: `Section 4. Proof of the decomposition lemma`

## Describing Results

Neither understate nor overstate what was proved. Overstatement is caught by
referees; understatement means the work is read as smaller than it is.

Say plainly where the result is unsatisfactory — a hypothesis stronger than one
would like, a conclusion weaker than expected — and say what is not known:
"We do not know whether hypothesis H is necessary."

Record the interesting questions left open. They are part of the contribution.

Mark any non-trivial assertion made without proof or citation, so the reader
does not hunt for a justification that is not there.

- Right: `It can be shown that ...`
- Right: `Although we will neither need nor prove this here, ...`

When a famous conjecture is used as motivation, say candidly how far the work
actually goes towards it. Naming a conjecture the paper does not approach reads
as advertising.

## Detail

Dwell on what is important, innovative, and load-bearing, in plenty of prose.
Detail is a budget: spend it where the reader cannot reconstruct the step alone.

Omit what is routine to an expert in the field. The audience is the field, not
the author: having only recently learned a standard technique is not a reason
to reproduce its standard proof.

State an obscure lemma from earlier work in full, with a precise citation, and
sketch its proof when the argument leans on it. A reader should not have to
find an out-of-print paper to check a step.

Whatever is omitted must leave the reader a clearly circumscribed small problem
to solve, not an open-ended one. Two omitted trivialities can add up to an
impasse, because the reader cannot tell where one ends and the next begins.

## Lemmas

Encapsulate an intermediate fact as a lemma when the main argument would
otherwise have to carry it. If A, B and C exist only to give D, state D as a
lemma and hide A, B and C in its proof; the reader then carries one fact
instead of three.

State a lemma so that it is easy to **use**, not easy to prove. Hypotheses
should be natural and easy to check; the conclusion should be manifestly the
thing the main argument wants. Push the awkwardness into the proof.

Restate the running hypotheses and notational conventions inside the lemma. A
reader arriving at Lemma 12 should not have to reconstruct the ambient
assumptions from Section 2.

Merge two lemmas that are only ever useful together. An intermediate technical
statement that is never used on its own belongs inside a proof, not in the
numbered sequence.

## Restraint

A shorter paper is not automatically a simpler one. Removing examples, remarks,
motivation and connecting prose in favour of compressed symbols produces a paper
that reads as harder, not shorter.

Optimising readability is always worth it, except at the expense of rigour or
accuracy. Optimising constants, generality or technical strength is worth it
only when a sequel depends on it, or the paper will be the reference treatment
for years.

Weakening hypotheses and strengthening conclusions past the point of need
lengthens proofs and obscures how the result connects to the rest of the paper.
State one form and note the alternative in a remark.

Include cheap consequences, variants and illustrative counterexamples when they
cost little; they save every later reader from rederiving them. Conversely, cut
a section whose length is out of proportion to what it adds, or reduce it to a
remark.

## Voice And Attribution

Paraphrase and interpret earlier work; do not copy its text. Copying a
paragraph from a prior paper is a plagiarism risk even when the source is
cited, and rewriting text one does not fully understand propagates its errors.

Apply the same transparency to the author's own previously published text and
figures. Cosmetic edits do not make reused material original. Disclose and cite
reuse, and obtain permission when copyright, license, or venue policy requires
it.

Cite the source when paraphrasing an argument: "The proof here is loosely based
on that in [5]."

Reserve direct quotation for a historical point. Quoting to display familiarity
reads as name-dropping.

Write in a voice that is your own rather than an imitation of a particular
author. A draft that mixes several borrowed voices reads strangely, and the
mimicry is often unwelcome to the person imitated.

Assign credit, provenance and precedence accurately, and keep references
current. Separate your opinion from the record: mark a judgement as a judgement.

Choose references for relevance, support, provenance, and credit. Do not add a
reference to influence an editor or referee, inflate a metric, or manufacture
the appearance that a topic is current.

Avoid witty, philosophical or knowingly obscure asides. They date badly, they
distract, and on a second reading they are what the reader remembers instead of
the argument.

## Before Submitting

Do not submit while you are still finding typographical errors, and do not
submit while you are still adding results or commentary. Either means the draft
is not finished.

Check the citations against the primary sources, not against another paper's
reference list. Confirm that no important reference is mentioned only in
passing, cited inaccurately, or missing.

Careless English suggests a careless paper, and a referee who has been given
that impression reads the mathematics differently.

Send colleagues a proofread draft, not an early one. Their time is the scarce
resource.

Run separate passes for mathematical correctness; organization and logic;
prose and notation; and surface copyediting. After editorial, copyediting, or
production changes, recheck equations, citations, cross-references, and the
claim ledger. Polished prose is not evidence that the mathematics survived the
edit.

## LaTeX Manuscripts

Start a new manuscript from the author's article template, at
`https://github.com/hoanganhduc/TeX-Templates`, directory
`A Simple Article Template`. It already carries the conventions below, so
starting elsewhere means re-deriving them and getting some of them wrong:
`cleveref`, `biblatex` with the `biber` backend, `tikz`, `enumerate`, and
theorem environments sharing one counter through `aliascnt`.

Having copied the template, follow the global LaTeX source-preservation rule in
`writing-style-settings.md`. Comment out unused textual source rather than
deleting it, so the material can be restored later if needed. Delete content
from a `.tex` file only when deletion is genuinely necessary. Keep preserved
comments clearly separated from active manuscript content so a co-author, a
submission system, or a later maintainer does not mistake them for part of the
paper. Never preserve secrets, personal or confidential data, restricted
material, text that must be anonymized or redacted, or unsafe executable
content merely to retain a restoration path; apply the mandatory removal
exception in the general policy.

Concretely, in a manuscript started from the template above: the committed
`main.pdf` is the template's own preview and is rebuilt on the first
compilation; `mplainnat.bst` serves the optional BibTeX path, which this policy
does not take; the example figure under `figs/` is a placeholder, not a figure
of this paper; and only one of `Makefile` and `make.bat` is used on a given
machine. Remove the template's own `refs.bib` entries rather than leaving
sample references in the bibliography. Remove unnecessary template files from
the manuscript copy when appropriate; deletion of a whole file is distinct from
deletion of content inside a `.tex` file and follows the ordinary filesystem
safety rules.

This applies to the copy, not to the template repository, which keeps all of
these on purpose.

Make every cross-reference with `\cref`, not with a bare `\ref` and a
hand-written word. `\cref` supplies the object's name, keeps "Theorem" and
"Lemma" consistent everywhere, and cannot fall out of step with the target when
a lemma becomes a proposition.

- Wrong: `by Lemma~\ref{lem:main}`
- Right: `by \cref{lem:main}`

Manage the bibliography with `biblatex` and `biber`. Entries stay in the `.bib`
file in one format, styles are selected by option rather than by a `.bst`, and
the same source compiles for a venue that wants a different citation style.

When preparing a camera-ready version, follow the journal's or conference's
instructions exactly, even where they contradict this policy. Their class file,
citation style, figure format, page limit and metadata requirements take
precedence; record any rule of this policy that was overridden and why.

Draw figures with TikZ whenever the figure can be expressed as one, and produce
them through the `tikz-draw` skill rather than by hand. A TikZ figure carries
the document's fonts and line weights, stays readable when the layout changes,
and can be diffed and corrected; an imported raster cannot.

## Enumerated Statements And Their Proofs

When a statement carries several parts to be proved, or several conditions to be
established, enumerate them explicitly with `\begin{enumerate}[(1)]` or an
equivalent labelled form. A reader can then cite part (2) rather than "the
second half of Theorem 3".

Then give the proof the same structure, with the same labels, so each part of
the proof visibly answers one part of the statement.

```latex
\begin{theorem}\label{thm:main}
Let $G$ be a connected graph. Then
\begin{enumerate}[(1)]
  \item $G$ has a spanning tree, and
  \item every spanning tree of $G$ has $|V(G)| - 1$ edges.
\end{enumerate}
\end{theorem}

\begin{proof}
\begin{enumerate}[(1)]
  \item ... proof of the first part ...
  \item ... proof of the second part ...
\end{enumerate}
\end{proof}
```

A proof whose shape does not match its statement forces the reader to work out
which paragraph settles which claim, and it hides a part that was never proved
at all.

## Voice And Sentences

Prefer the active voice. "We show" is easier to follow than "it is shown", and
it tells the reader who is responsible for the step.

Where the plural "we" would be a formal substitute for "I" rather than an
invitation to the reader, a label or an imperative is often clearer.

- Heavier: `We can now prove the following result:`
- Lighter: `Consequence: A implies B.`
- Lighter: Replace $x$ by 7 throughout.

Keep "that" after "assume" and "suppose" when what follows is a clause; it
marks the clause boundary. Do not write "we have that x = y"; write
"we have x = y".

Break up long sentences. A sentence carrying three subordinate clauses and two
formulas will be read twice.

Every pronoun must point at exactly one thing. If "it" could refer to either of
two objects in the previous clause, name the object.

Use parallel wording for parallel content, and vary wording that is not
parallel.

Write running prose so that it remains grammatical and intelligible when
subsection headings and parenthetical citation markers are temporarily omitted.
This is a readability test only: it does not authorize removing required
citations or attribution from the delivered text.

- Weaker: (a) For all even integers $n$, property $P_n$ holds.
  (b) However, property $Q_n$ holds if $n$ is odd.
- Better: (a) For all even integers $n$, property $P_n$ holds.
  (b) For all odd integers $n$, property $Q_n$ holds.

Do not echo an unusual word in an unrelated passage. Two appearances of a
striking word invite the reader to connect two places you did not mean to
connect.

Put a comma after an introductory word or phrase that opens a sentence. The
comma marks where the connective ends and the claim begins, which is exactly the
boundary a reader scanning a proof needs.

- Wrong: `Therefore the sequence converges.`
- Right: `Therefore, the sequence converges.`

This covers the conjunctive adverbs — `Therefore`, `However`, `Thus`, `Hence`,
`Moreover`, `Nevertheless`, `Consequently`, `Furthermore` — the sequencing words
`First`, `First of all`, `Second`, `Finally`, and the introductory phrases `For
example`, `In particular`, `On the other hand`, `In addition`, `As a result`.

Apply it uniformly. West allows the monosyllables `Hence` and `Thus` to stand
without a comma, on the grounds that a single syllable does not need the pause;
this policy takes the simpler rule instead, because a reader should not have to
count syllables to know whether a comma was omitted deliberately.

## Gaps And Verification

Separate theorem statement, proof idea, verification status, and open gaps. Do
not claim a proof is complete when checks are partial.

## Organization

Organize the material, and do not distract the reader. Every other rule here is
a special case of one of those two.

The order of exposition is rarely the order of discovery, and rarely a straight
line. Decide the order deliberately, then make the transitions visible.
