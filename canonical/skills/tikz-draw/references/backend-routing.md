# Backend Routing

Use the backend that matches the diagram family instead of forcing everything through raw coordinates.

- `flowchart`, `dag`
  - prefer `positioning`, `fit`, and named node styles
- `tree`
  - prefer `forest`
- `commutative`
  - prefer `tikz-cd`
- `automaton`
  - prefer `automata`
- `mindmap`
  - prefer `mindmap`
- `sequence`
  - prefer `pgf-umlsd`

Phase 1 in this Codex install currently renders these families directly:

- `flowchart`
- `dag`
- `tree`
- `commutative`

Other families should either be normalized into one of those structural patterns or flagged as needing manual extension.

Width-fit rule:

- document-facing output must use `\adjustbox{max width=\textwidth}{...}`
- standalone targets must use plain `standalone` class, not `standalone[tikz]`
