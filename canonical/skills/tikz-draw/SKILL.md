---
name: tikz-draw
description: Use when the user asks to draw, refactor, extract, compile, or review a TikZ/PGF figure, especially structural diagrams such as flowcharts, DAGs, trees, commutative diagrams, finite graphs, automata, or research-derived summary figures. Prefer this skill when the output should follow a structure-first workflow like figure brief to spec to render to check to compile to review, and when document-facing output should use adjustbox width fitting.
metadata:
  short-description: Draw and refine structural TikZ figures
---

# TikZ Draw

Use this skill when the task is specifically about producing or repairing TikZ.

Typical cases:

- draw a new TikZ picture to illustrate a statement or research finding
- turn a `figure-brief.json` into a structural diagram spec first
- refactor coordinate-heavy TikZ into structural placement
- extract an existing `tikzpicture`, `forest`, or `tikzcd` block into standalone and embeddable artifacts
- run a deterministic compile and review loop on TikZ output

## Runtime helper

The Codex runtime helper exposes one stable verb set:

- `doctor`
- `spec`
- `render`
- `check`
- `compile`
- `review-visual`
- `verify-semantic`
- `review`
- `extract`

Run it through the shared Codex wrapper:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/tikz-draw/run_tikz_draw.sh doctor
```

On Windows, use:

```powershell
& "$env:USERPROFILE\.codex\runtime\run_skill.bat" `
  "skills\tikz-draw\run_tikz_draw.bat" doctor
```

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/tikz-draw/run_tikz_draw.sh render \
  --brief /abs/path/to/figure-brief.json
```

Direct bootstrap without prewriting a brief:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/tikz-draw/run_tikz_draw.sh render \
  --diagram-family flowchart \
  --request "Draw a validation pipeline for statement X"
```

If `--out-dir` is omitted in direct mode, the helper allocates:

- `~/.codex/runs/tikz-draw/<run_id>/`

## Required workflow

1. Prefer a structural brief or spec before raw TikZ.
   Direct mode may bootstrap and write the brief for you.
2. Route the figure to the right backend:
   - `flowchart`, `dag`: `positioning`
   - `tree`: `forest`
   - `commutative`: `tikz-cd`
   - `graph`: baseline graph path first, with Sage-assisted routing when the request exceeds the baseline shorthand/layout surface
3. Keep document-facing output inside the `adjustbox` environment with `max width=\textwidth`.
4. For standalone compile targets, use plain `\documentclass[border=...]{standalone}` rather than `standalone[tikz]`.
5. Run `check` before `compile` when you generated or heavily edited the figure.

## Graph routing

- The current graph lane keeps a trusted baseline path for already-supported requests such as Petersen and `J(n,k)`.
- Richer graph requests may route to a Sage-assisted path.
- In the current slice, both paths may still use Sage for graph realization; the difference is in request routing, validation, and reporting.
- For direct graph bootstrap, the helper now accepts optional graph fields such as:
  - `--graph-mode auto|local|sage`
  - `--graph-constructor`
  - `--graph-param`
  - `--graph-layout`
  - `--show-labels true|false`
- Render manifests and semantic-review reports now carry routing fields including baseline vs Sage-assisted path selection and backend used.

## Phase 6 semantic surface

- `review --tex` remains the legacy source-only path.
- `review-visual` now runs through the rendered-artifact extractor and refreshes `render-semantics.json` from the compiled PDF.
- `verify-semantic` now supports the current render-generated `flowchart`, `dag`, `tree`, supported-square `commutative`, and Sage-backed `graph` families.
- `verify-semantic` still fails closed with `UNSUPPORTED_FAMILY` for an unsupported family and unsupported inputs outside the current renderer assumptions.
- Strong semantic approval is still out of scope for unsupported families and arbitrary extracted TikZ.

## Regression runner

For implementation-level verification, use the persistent regression suite instead
of ad hoc `/tmp` smokes:

```bash
python3 ~/.codex/runtime/workspace/skills/tikz-draw/semantic_regression_runner.py --platform both
```

The current suite covers supported good cases for `flowchart`, `dag`, `tree`,
`commutative`, and Sage-backed `graph`, plus mutation cases.

On Windows, use:

```powershell
& "$env:USERPROFILE\.codex\.venv\Scripts\python.exe" `
  "$env:USERPROFILE\.codex\runtime\workspace\skills\tikz-draw\semantic_regression_runner.py" --platform codex
```

## References

Read these when the task needs tighter guardrails:

- [backend-routing.md](<HOME>/.codex/skills/tikz-draw/references/backend-routing.md)
- [quality-gates.md](<HOME>/.codex/skills/tikz-draw/references/quality-gates.md)
- [tikz-prevention.md](<HOME>/.codex/skills/tikz-draw/references/tikz-prevention.md)
- [tikz-measurement.md](<HOME>/.codex/skills/tikz-draw/references/tikz-measurement.md)

## Boundaries

- Use this skill for TikZ-specific work, not for generic image generation.
- Keep the workflow narrow and structural in phase 1.
- Preserve `figure_id` and `source_ids` when the request came from deep research.
- Direct-use bootstrap may emit an empty `source_ids` list; research-driven briefs should keep real `S*` ids.
