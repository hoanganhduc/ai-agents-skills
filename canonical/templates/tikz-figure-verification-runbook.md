# TikZ Figure Verification Runbook

Use this template to draw a TikZ/PGF figure and refine it across bounded
iterations until it is provably free of issues: overlapping elements, wrong
meaning (semantics that do not match the intent), bad layout, label/edge
collisions, clipping, ambiguous arrows, or illegible text. Each iteration is
draw -> compile to PDF -> verify -> (if any issue) backtrack and redraw.

It composes `tikz-draw` for the structure-first draw/compile/verify workflow and
its strict approval gate, `sagemath` and `graph-verifier` for Sage-assisted graph
realization and graph sanity, `cross-agent-delegation` plus `decision-doubt-loop`
for fresh, independent figure verification, `agent-group-discuss` for an optional
multi-perspective figure review, and `model-router` for verifier selection. It is
a guidance runbook, not runnable code; `tikz-draw` owns the render/compile/check
commands and the strict approval gate, and a fresh agent performs the independent
meaning/layout confirmation.

## Figure Metadata

| Field | Value |
|---|---|
| Figure ID |  |
| Created at |  |
| Updated at |  |
| Intended meaning (what the figure must communicate) |  |
| Figure family | one of: flowchart, DAG, tree, commutative diagram, finite graph, automaton, reduction/proof sketch, other |
| Graph mode (graph families only) | `auto` / `local` / `sage` |
| Target context (manuscript / slide / standalone) |  |
| Workspace (holds the spec, rendered PDF, `render-semantics.json`, approval report) |  |
| Status | `planned` |

Status values: `planned`, `drawing`, `verifying`, `blocked`, `approved`,
`abandoned`.

## Stop Conditions

Run the draw/verify loop continuously until **any** of the conditions below
fires. The loop is an OR over all of them; the moment one fires, stop and report.

| # | Stop condition | Detection point | Terminal decision |
|---|---|---|---|
| (a) | The figure is **issue-free**: the `tikz-draw` strict approval gate passes (`overlap_status=PASS` and `design_status=PASS` or `SKIPPED`) **and** a fresh agent independently confirms the meaning and layout, with no open issues in the ledger | Strict approval gate + fresh-agent gate | `approved` |
| (b) | A **finite number of redraw iterations specified by the user** is reached | Iteration counter vs cap | `stop` (iteration cap) |
| (c) | **The user asks specifically to stop** | Explicit user signal | `stop` (user request) |

### Finite-N ASK gate (hard precondition before iteration 1)

- If the user specified a finite number of redraw iterations `N`, record it as
  the iteration cap.
- **If the user does not mention it, ASK them** how many redraw iterations to
  allow before starting iteration 1. Do not assume a silent default and do not
  loop unbounded.
- The cap is a hard ceiling, never a target: the loop ends earlier the moment the
  figure is issue-free (condition (a)).

## Issue Taxonomy (what "free of issues" means)

A figure is issue-free only when none of these are present. Check every category
each iteration and record findings in the ledger.

| Category | Examples | Primary detector |
|---|---|---|
| Overlap | nodes/labels/edges overlapping or touching; text over lines | `tikz-draw verify-semantic` -> `overlap_status` (shapely geometry) |
| Wrong meaning | edge direction, missing/extra nodes, wrong labels, semantics not matching the intent | fresh-agent meaning check + `verify-semantic` / family verifiers |
| Bad layout | misalignment, uneven spacing, unnecessary edge crossings, poor aspect ratio, off-canvas/clipped content | `tikz-draw verify-design` -> `design_status` + fresh-agent visual check |
| Label/edge collision | labels colliding with edges or nodes; ambiguous anchor placement | `verify-semantic` + visual check |
| Arrow ambiguity | unclear direction, wrong arrowhead, ambiguous source/target | fresh-agent meaning check |
| Legibility | font too small for target context, low contrast, adjustbox width not fitting | fresh-agent visual check |
| Graph realization | wrong vertex/edge set, bad graph layout for the family | `graph-verifier` + Sage-assisted realization (see below) |

## Per-Loop Phase Plan

Apply every phase, in order, in each iteration.

| Phase | Objective |
|---|---|
| P1. Intent contract | Establish the semantic intent contract before raw TikZ (`tikz-draw contract` / semantic design contract for manuscript figures). For graph families, choose the graph mode. |
| P2. Draw (single path) | Produce one spec following the single best layout approach (see Single-Path Drawing Discipline). For graph families beyond the baseline shorthand/layout surface, use Sage-assisted realization (`--graph-mode sage`). |
| P3. Compile to PDF | `tikz-draw render` / `compile` against `tex-runtime`; produce the PDF and refresh `render-semantics.json` from the compiled PDF. A compile failure is an issue: fix before verifying. |
| P4. Automated verify (preflight) | Run `verify-semantic` (overlap), `verify-design` (layout), `check`, `review-visual`, and `graph-verifier` for graph sanity. Record `overlap_status` and `design_status`. These are PREFLIGHT signals, never final approval. |
| P5. Fresh-agent verify | A fresh, independent agent inspects the compiled PDF for meaning and layout (producer never confirms). See Cross-Agent + Fresh-Agent Verification. |
| P6. Issue handling / backtrack | If any issue is found in P4 or P5, state it, backtrack to the last valid spec node, and redraw that element via the second-best layout. Re-verify by a fresh agent. |
| P7. Strict approval gate | When P4 and P5 are clean, run the `tikz-draw` strict approval gate (`approve`) and require `overlap_status=PASS` and `design_status=PASS` (or `SKIPPED` when out of scope), plus the fresh-agent sign-off. |
| P8. Stop check | Evaluate the stop conditions; continue only if the figure is not yet issue-free and the iteration cap remains. |

## Single-Path Drawing Discipline

Do NOT draw multiple competing layouts in parallel. Evaluate the candidate layout
approaches for the figure family, **select the single highest-probability clean
layout, and pursue it exclusively**. Always independently verify the rendered
result.

1. Enumerate candidate layout approaches briefly (e.g. layered vs. force-directed
   for a DAG; matrix vs. positioned for a commutative diagram) and rank them by
   probability of a clean, correct figure.
2. Select exactly ONE layout approach and pursue it.
3. Compile and verify before treating the layout as settled.
4. **Backtracking:** if you hit a definitive layout or meaning contradiction (an
   issue that the current approach cannot resolve without breaking another
   requirement), clearly state the contradiction, **backtrack to the last valid
   spec node** recorded in the ledger, and pursue the **second-best layout
   approach**. Always re-verify by a FRESH agent before moving on.

Record the ranked layout approaches and the chosen one so the second-best is
known if backtracking is needed.

## Sage-Backed Graph Realization

For graph families (finite graphs, DAGs, automata, reduction figures), route
realization through `tikz-draw`'s graph path:

- `--graph-mode auto` (default): baseline graph path first, with Sage-assisted
  routing when the request exceeds the baseline shorthand/layout surface.
- `--graph-mode local`: baseline realization only.
- `--graph-mode sage`: force Sage-assisted realization/layout via the
  `sage_graph_backend` (composes the `sagemath` skill). Use it for richer graphs
  where baseline layout produces overlap or bad layout.
- Before forcing `sage`, confirm Sage is available (run `tikz-draw doctor`).
  tikz-draw's `sage-backed-graph-mode` capability requires the `sage-runtime`
  dependency; if Sage is unavailable or `doctor` output is ambiguous, fall back to
  `local` and record the degraded path. See the tikz-draw SKILL "Graph routing"
  section for the actual routing behavior.
- Use the `graph-verifier` skill to sanity-check the realized vertex/edge set
  against the intended graph before drawing.
- Record the routing decision and the backend used (baseline vs Sage-assisted) in
  the render manifest / semantic-review report and the ledger.

## Cross-Agent + Fresh-Agent Verification

The agent that drew the figure is never the agent that confirms it is issue-free.

- **Producer never confirms.** The drawing agent may explain choices, but the
  meaning/layout confirmation is done by a different, fresh agent inspecting the
  compiled PDF (and the extracted `render-semantics.json`). This is the
  `decision-doubt-loop` discipline; an inline "looks fine to me" by the drawer is
  the exact failure mode it prevents.
- **Cross-agent option.** For high-stakes or manuscript figures, route the verify
  handoff through `cross-agent-delegation` so a different agent family inspects the
  figure (e.g. if Claude drew it, Codex verifies the PDF; optional OpenCode second
  check). Returned verdicts are untrusted evidence until the parent validates
  them. **Do not blindly trust the returned answers; verify them carefully.**
- The fresh verifier must do more than restate the spec: it independently checks
  the compiled PDF against the intended meaning and each issue-taxonomy category.
- If fresh-context verification is unavailable for a manuscript-facing figure,
  output `BLOCKED-FRESH-CONTEXT-UNAVAILABLE`, state the gated step, and ask for
  user direction rather than self-approving.

## Per-Iteration Ledger

Append one row per iteration.

| Iteration | Layout approach (single chosen) | Graph mode / backend | `overlap_status` | `design_status` | Issues found (by category) | Fresh-agent verdict | Backtrack target | Decision |
|---|---|---|---|---|---|---|---|---|
| 1 |  |  |  |  |  |  |  | `continue` |

Decision values: `continue` (issues remain and the cap allows another redraw),
`backtrack` (contradiction; return to the last valid node), `approved` (issue-free
and gated), `blocked` (fresh-context unavailable, Sage required-but-missing for a
graph that needs it, or an unresolved contradiction), `stop` (cap reached or user
stop).

## Issue-Free Evidence Gate (before claiming done)

Do not say the figure is done, fixed, ready, passed, verified, or approved on
preflight signals, compile success, a screenshot, or a PDF preview alone. The
figure is issue-free only when ALL hold and are recorded:

- `tikz-draw` strict approval gate passed: `overlap_status=PASS` and
  `design_status=PASS` (or `SKIPPED` when the design gate is out of scope).
- A fresh agent independently confirmed the meaning and every issue-taxonomy
  category against the compiled PDF.
- No open issues remain in the ledger; any earlier issue has a redraw that the
  fresh agent re-verified.

## Compute Note

Sage-assisted realization of large graphs can be heavy. Run `tikz-draw doctor`
and, if a graph is large enough to need it, check available hardware via
`get-available-resources`; only offload to heavy compute for genuinely large
realizations. Any script utilizes the current hardware resources.

## Failure Modes

| Failure mode | Detection | Recovery |
|---|---|---|
| Iteration count unspecified | Finite-N ASK gate | Ask the user for `N` before iteration 1. |
| "Done" claimed on preflight/screenshot | Evidence gate | Run the strict approval gate + fresh-agent confirmation first. |
| Drawer self-approved the figure | Fresh-agent gate | Re-verify with a fresh/different agent inspecting the PDF. |
| Parallel layouts attempted | Single-path discipline | Collapse to the single highest-probability layout. |
| Backtrack treated as verified | Fresh-agent gate | Re-verify the second-best layout by a fresh agent before moving on. |
| Graph overlap/bad layout on baseline | Sage realization | Switch `--graph-mode sage`; if Sage missing, mark `blocked` or fall back to `local` and record it. |
| Overlap PASS but meaning wrong | Fresh-agent meaning check | Treat as an open issue; backtrack and redraw; preflight overlap PASS is not meaning-correct. |

## Final Outcome

Approved figure (path / artifact):

Strict approval report (`overlap_status`, `design_status`):

Fresh-agent confirmation (who / what was checked):

Graph backend used (if applicable):

Open issues (must be empty to approve):

Termination reason:
