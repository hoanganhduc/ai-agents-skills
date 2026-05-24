# TikZ Draw Prevention Rules

These are deterministic source-preflight checks for `check`.

See also:

- `tikz-prevention.md` for the shared named rule IDs used by the semantic-verifier slice

## Hard failures

- Document-facing output must wrap the diagram in:
  - `\begin{adjustbox}{max width=\textwidth}`
  - `...`
  - `\end{adjustbox}`
- Standalone outputs must include `\usepackage{adjustbox}`.
- Standalone outputs that use the required `adjustbox` wrapper must not use `\documentclass[tikz,...]{standalone}`.
  - Use plain `\documentclass[border=...]{standalone}` and load TikZ packages explicitly.
- Boxed nodes in flowcharts, DAGs, and similar structural diagrams must use explicit width information.
- Nontrivial diagrams must not rely on bare `scale=` as the primary way to fit the page.
- Ambiguous edge labels must include explicit placement such as `above`, `below`, `left`, `right`, `near start`, or `near end`.
- Absolute coordinates are not allowed as the default layout mechanism for structural diagrams unless the spec or brief explicitly justifies them.
- Named-node chain paths such as `\draw (a) -- (b) -- (c) -- cycle;` are not allowed for verification-sensitive graph drawings.
  - Use an explicit final edge such as `\draw (a) -- (b) -- (c) -- (a);`.
  - Reason: the rendered closure can differ from the intended last node-to-node edge when TikZ uses node shape borders.
- Strict approval requires current artifact provenance and a structured symmetry contract.
  - Scoped manuscript-facing semantic figures also require a visual-semantic design contract.
  - Boxes, fills, labels, arrows, regions, and callouts must declare whether they are graph structure, metadata, correspondence, gadget region, highlight, or legend.
  - Metadata and notation labels must not be rendered as graph objects unless the design contract explicitly says so.
  - Source-only `check` output is preflight, not approval.
  - Use `approve` as the final gate after every generated or modified figure.

## Soft failures

- Repeated inline style fragments should be moved into named styles.
- Semantic node names should be preferred over `n1`, `n2`, `a`, `b` when the concept has a meaningful name.
- Group boxes should prefer `fit`-style grouping over manual rectangle coordinates.

## Backend routing expectations

- `flowchart`, `dag`:
  - prefer `positioning`, `fit`, `matrix`, or `graphs`
- `tree`:
  - prefer `forest` unless there is a strong reason not to
- `commutative`:
  - prefer `tikz-cd`
- `automaton`:
  - prefer `automata` with structural placement

## Width-fit rule

The wrapper is part of the required output contract, not a post-processing option.

The check stage should fail document-facing output that emits a bare top-level diagram without the required `adjustbox` wrapper.
