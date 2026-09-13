# Graph And Combinatorics Style

This overlay applies to writing in graph theory, combinatorics, and closely
related discrete mathematics. It extends `math-manuscript-style.md`, which in
turn extends `writing-style-settings.md`.

The rules below are conventions of the field, not house inventions. Most of
them exist because the careless form is ambiguous, not merely unusual, and each
rule says which. Where a rule records a preference rather than a correctness
requirement, it is marked `soft` in the accompanying index.

Notation here is written as LaTeX inside `$...$`, following
`math-manuscript-style.md`: $xy$-path, $\Delta(G)$, $k$-connected. Backticks
mark a term being discussed as a word, never mathematics.

## Activation

Use this overlay when the writing concerns graphs, digraphs, hypergraphs,
designs, posets, matroids, or other finite combinatorial structures, or when it
uses graph-theoretic vocabulary. Record it in `active_overlays` as
`graph-combinatorics-style`. Do not apply it to analysis, algebra, or geometry
prose that merely happens to mention a graph.

Before omitting a basic definition on the ground that it is standard, check a
verified standard source for the field and confirm that the intended meaning and
notation match. Diestel and West are useful candidate sources for graph theory,
but no author, edition, concept, or locator is canonical until it has been
checked for the current use. This is the only exception supplied here to the
math overlay's define-before-use rule; when conventions vary or the manuscript
uses a narrower meaning, give the definition explicitly.

## Extremal Words

Use `maximal` and `minimal` for comparisons under containment, and `maximum`
and `minimum` for comparisons of size. The two are different objects: a maximal
independent set cannot be enlarged, a maximum independent set is as large as
any other, and a maximal set need not be maximum.

Because the distinction is real, treat a confusion between them as an error and
not a stylistic slip.

## Objects And Their Sets

A graph is not a set of vertices. Write $v \in V(G)$ and $e \in E(G)$ rather than
$v \in G$, because $G$ also carries edges and $v \in G$ leaves open which kind of
object $v$ is.

Use `order` for the number of vertices and `size` for the number of edges, or
introduce $n = |V(G)|$ and $m = |E(G)|$ and use those. "Size" used loosely for
either quantity is the commonest source of unreadable extremal statements.

Distinguish a set from its cardinality. A set cannot be less than an integer.

- Wrong: if $S < k$
- Right: if $|S| < k$

A clique is a set of pairwise adjacent vertices; a complete subgraph is a
subgraph isomorphic to a complete graph. Keep the two apart when the argument
manipulates one or the other.

Use `parts` for the members of a partition. A partition is the whole family; a
part is one member.

## Operators Are Not Numbers

$\Delta$ is a function on graphs, not a number. Write $\Delta(G)$, or set $\Delta = \Delta(G)$ once
and say so. Writing $\Delta$ for a value silently changes what kind of object the
symbol denotes.

Write any operator whose name is more than one character in roman type, so that
$\dim$, $\mathrm{cr}$, $\mathrm{mad}$ are not read as products of variables.

Big-Oh is a set of functions. Write $f(n) \in O(n^2)$, or say in words that $f(n)$
is $O(n^2)$; an equality sign between a function and a class is not reversible
and misleads readers who take it literally.

## Naming Structures

$P_n$ denotes an isomorphism class, not a particular graph. Write "a path with
$n$ vertices" or "a copy of $P_n$", not "a $P_n$".

Do not write `directed edge` or `hyperedge`. Calling the objects `edges` in
each setting is what allows a graph to be stated as a special case of a digraph
or hypergraph; a separate word blocks that.

Say `components`, not `connected components`. Every component is connected by
definition, so the adjective adds length and suggests that disconnected
components exist.

Do not say `vertex-disjoint` when `disjoint` is meant. Reserve the compound for
the case where an edge-analogue is also in play and the contrast is real.

Use `pairwise` rather than `mutually` for a property holding of all pairs.
"Mutually" now carries a reciprocal sense that is weaker than what is meant.

Prefer adjectival forms of personal names: `Hamiltonian cycle`, `Eulerian
circuit`. Established exceptions such as `Fibonacci numbers` and `Catalan
numbers` stay as they are.

Use `degree list` rather than `degree sequence` when the order does not matter.
A sequence is a function on the natural numbers; what is meant is a finite
multiset. This is a preference, and the older term is universally understood.

## Paths And Deletion

Write $xy$-path for a path with endpoints $x$ and $y$. Do not write $x$-$y$ path,
which invites the reader to parse $x-y$ as a difference, and it is not one.
$xy$ already denotes the edge joining $x$ and $y$, so $xy$-path reads as the
same pair used as a qualifier and needs no separator.

West writes $x,y$-path for the same reason — to keep the hyphen away from the
two vertex names. This policy takes the other resolution of that problem. Either
is defensible; what is not defensible is $x$-$y$ path.

Write $G - e$ and $G - v$ for deletion. Reserve set-minus for set difference,
so that the reader can tell an element from a set at a glance.

Use `joining` rather than `between` for an edge and its ends: an edge joins $u$
and $v$. "Between" suggests separation, which is the opposite relation.

## Hyphens

A hyphen binds a parameter to the property it qualifies. $k$-connected graphs
are graphs of connectivity at least $k$; $k$ connected graphs are $k$ graphs,
each connected. The hyphen is doing real work.

Bracket a compound parameter: $(k+1)$-connected graph.

Hyphenate the edge analogues: `edge-connectivity`, `edge-chromatic number`.

Hyphenate a two-word adjective placed before its noun: `polynomial-time
algorithm`. After the noun, no hyphen: `the algorithm runs in polynomial time`.

An adverb modifying an adjective takes no hyphen: `strongly connected`. Treat
`well-known` as the established exception.

Drop the hyphen from familiar negations (`nonzero`, `nontrivial`,
`nonsingular`) and keep it on unfamiliar ones (`non-edge`).

## Set And Sequence Notation

Use a colon in set-builder notation: $\{3n + 1 : n \in \mathbb{N}\}$. The vertical bar is
already overloaded by divisibility, conditioning, restriction, and cardinality.

Avoid `:=`. It encourages a definition and an assertion to be made in one
breath, which is the error the next rule forbids.

Write an indexed list as $v_1, \dots, v_n$. Including $v_2$ adds nothing, since
consecutive indices are the default reading.

Write a membership range as for $m \in \{1, \dots, n\}$ or for $1 \le m \le n$, not
for $m = 1, \dots, n$, which asserts that $m$ equals a list.

## Definitions And Statements

Do not define notation and assert something about it in the same breath.

- Wrong: The neighborhood of a vertex $v$ is $N(v) = \{u : uv \in E(G)\}$.
- Right: The neighborhood $N(v)$ of a vertex $v$ is $\{u : uv \in E(G)\}$.

The same applies to lists constrained by a relation.

- Wrong: Let $x_1 \le \dots \le x_n$ be a list of integers.
- Right: Let $x_1, \dots, x_n$ be integers such that $x_1 \le \dots \le x_n$.

Use `where` when notation is being introduced and `such that` when
already-defined notation is being restricted. The two clauses do different
jobs, and swapping them hides which one is happening.

Use `such that` to impose a condition on an object, and `so that` to say how an
action is performed. "So that" needs a verb.

Say proper $k$-coloring when adjacent elements must differ. In combinatorics a
bare "coloring" carries no such constraint.

## Sentence Construction

Do not place two formulas next to each other separated only by a comma. Put
words between them: "it follows that", "we have", "where".

Treat a notational expression as a noun. A sentence that uses a formula as a
verb or a clause forces the reader to re-parse it.

Do not compare a word with a relational symbol.

- Wrong: a graph $G$ with maximum degree $\le k$
- Right: a graph $G$ with maximum degree at most $k$

Write a hypothesis and conclusion as one sentence: `If ..., then ...`. The two
sentence form `Let ... . Then ... .` leaves the hypothesis without a main
clause.

Where dropping "then" reads better, begin with `When` or `For` instead.

Write out a restriction rather than parenthesizing it: for $k \le m$ with $k$ even,
not for $k \le m$ ($k$ even).

Write the neighbors of $v$, not $v$'s neighbors. A possessive on a symbol reads
as part of the symbol.

Use induction on $n$, and cite `the induction hypothesis`. "We induct on n" and
"by induction" name the method where the hypothesis is meant.

Say a result `is best possible`, without an article.

Use `fewer` for counts of objects and `less` for quantities and numbers.

## Attribution

Place a bibliography reference immediately after the name it belongs to, not
after the statement of the result. The reference identifies whose work it is,
and at the end of the sentence it can attach to the wrong clause.
