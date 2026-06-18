---
name: tikz-draw
description: Use when the user asks to draw, refactor, extract, compile, or review a TikZ/PGF figure, especially structural diagrams such as flowcharts, DAGs, trees, commutative diagrams, finite graphs, automata, or research-derived summary figures. Prefer this skill when the output should follow a structure-first workflow like figure brief to spec to render to check to compile to review, and when document-facing output should use adjustbox width fitting.
metadata:
  short-description: Draw and refine structural TikZ figures
---

# TikZ Draw


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. For Codex-only installs the runtime is usually `%USERPROFILE%\.codex\runtime`; for multi-agent installs it is usually `%LOCALAPPDATA%\ai-agents-skills\runtime`. Set `$runtime` to the installed runtime root, then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/tikz-draw/run_tikz_draw.bat" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

Use this skill when the task is specifically about producing or repairing TikZ.

Typical cases:

- draw a new TikZ picture to illustrate a statement or research finding
- turn a `figure-brief.json` into a structural diagram spec first
- refactor coordinate-heavy TikZ into structural placement
- extract an existing `tikzpicture`, `forest`, or `tikzcd` block into standalone and embeddable artifacts
- run a deterministic compile and review loop on TikZ output

## Runtime helper

The runtime helper exposes one stable verb set:

- `doctor`
- `contract`
- `design`
- `spec`
- `render`
- `check`
- `compile`
- `review-visual`
- `verify-design`
- `verify-semantic`
- `approve`
- `review`
- `extract`

Run it through the shared runtime wrapper:

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
  --request "Draw a validation pipeline for statement X"
```

If `--out-dir` is omitted in direct mode, the helper allocates:

- Codex: `~/.codex/runs/tikz-draw/<run_id>/`
- other installed targets:
  `${AAS_RUNS_ROOT:-~/.local/share/ai-agents-skills/runs}/tikz-draw/<run_id>/`

For research or mathematical figures, first let the runtime write the intent
contract or provide one explicitly:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/tikz-draw/run_tikz_draw.sh contract \
  --out /abs/path/to/F1.figure-contract.json \
  --request "Draw a graph hardness reduction where an edge is replaced by a gadget"
```

The contract records the inferred figure family, required objects, required
relations, forbidden simplifications, notation that must be preserved, and the
approval criteria. `spec` and `render` enforce this contract. If a request says
to illustrate a graph hardness reduction, the contract must require graph
vertices and graph edges; a box-only flowchart is a contract violation.

## Required workflow

1. Establish the semantic intent contract before raw TikZ. Direct mode may
   infer and write the contract for you, but it must still be present in the
   generated brief and spec.
2. For manuscript-facing semantic figures, establish a semantic design
   checkpoint before drawing or editing visual marks. This is mandatory for
   graph/proof/reduction figures, extracted figures intended for semantic
   approval, and any figure whose boxes, fills, regions, callouts, labels, or
   correspondence marks carry mathematical meaning. The checkpoint records the
   inspected source/caption/prose, affected visual marks, intended role of each
   mark, alternatives considered, chosen encoding, and caption/prose alignment.
3. Route the figure to the right backend:
   - `flowchart`, `dag`: `positioning`
   - `tree`: `forest`
   - `commutative`: `tikz-cd`
   - `graph`: baseline graph path first, with Sage-assisted routing when the request exceeds the baseline shorthand/layout surface
4. Reject a requested or inferred backend family that contradicts the contract.
   Do not downgrade a graph request into a schematic diagram unless the contract
   explicitly says that a schematic is intended.
5. Keep document-facing output inside the `adjustbox` environment with `max width=\textwidth`.
6. For standalone compile targets, use plain `\documentclass[border=...]{standalone}` rather than `standalone[tikz]`.
7. After creating, extracting, refactoring, or modifying any TikZ figure, run the strict approval gate before saying the figure is done, fixed, ready, passed, verified, or approved.

Strict approval command:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/tikz-draw/run_tikz_draw.sh approve \
  --artifacts /abs/path/to/F1.artifacts.json \
  --work-dir /abs/path/to/work-dir
```

On native Windows, use the same verb through `run_skill.bat` and `run_tikz_draw.bat`.

The only final approval is `approve` exiting `0` with:

- `final_verdict=APPROVED`
- `overlap_status=PASS`
- `design_status=PASS` for scoped semantic figures, or `design_status=SKIPPED` when the design gate is out of scope
- `symmetry_status=PASS`

`render`, `extract`, `compile`, `check`, `review --tex`, `review-visual`, and `verify-semantic` are preflight or artifact commands. Never cite them as final approval. Source inspection, compile success, screenshot review, PDF preview, or human visual inspection alone never constitute final approval.

If `approve` fails, fix the reported issue and rerun `approve`. Repeat until it passes, or report the exact blocked state such as `BLOCKED_INPUT`, `BLOCKED_ENVIRONMENT`, or `UNSUPPORTED_FAMILY`. Do not use approval-style wording for blocked or unsupported states.

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

## Strict Approval Surface

- `approve` is the authoritative final gate for supported render-generated figures.
- `review --semantic` delegates to the strict approval path for compatibility.
- `review --tex` remains source-only preflight and must not be treated as approval.
- `review-visual` runs through the rendered-artifact extractor and refreshes `render-semantics.json` from the compiled PDF, but remains a component gate.
- `verify-design` checks the visual-semantic design layer for scoped figures:
  mark roles, graph-object vs metadata separation, region/fill semantics,
  label/callout ownership, and declared caption/prose claim bindings.
- `verify-semantic` now supports the current render-generated `flowchart`, `dag`, `tree`, supported-square `commutative`, and Sage-backed `graph` families.
- `verify-semantic` still fails closed with `UNSUPPORTED_FAMILY` for an unsupported family and unsupported inputs outside the current renderer assumptions.
- Strict approval is fail-closed for unsupported families, arbitrary extracted TikZ without a semantic target/spec, stale extracted sources, missing dependencies, missing render artifacts, failed overlap checks, and missing or failed symmetry contracts.
- Strict approval also fails closed when a generated spec is missing its
  semantic intent contract or contradicts it.
- Strict approval also fails closed when a scoped semantic figure is missing a
  required visual-semantic design contract or when `verify-design` reports
  role, metadata, region, label, correspondence, or caption/prose mismatches.

For semantic design, visual marks are not decorative by default. A box, fill,
outline, color, arrow, brace, callout, label, or region must have a declared
role such as graph object, annotation, callout, correspondence, gadget region,
highlight region, or legend. Metadata such as list constraints should default
to adjacent text or callouts, not graph-object styling. If the user correction
shows that a previous design assumption was wrong, reopen the design checkpoint
before editing again.

Every generated spec carries a `symmetry_contract`. The checker verifies the declared contract:

- `required`: declared pair/axis/alignment symmetry must pass.
- `not_required`: accepted only with a justification.
- `intentionally_asymmetric`: accepted only with a justification.

Comments such as `% Symmetry: ...` are human hints only; they do not satisfy the machine-readable contract.

## Regression runner

For implementation-level verification, use the persistent regression suite instead
of ad hoc `/tmp` smokes:

```bash
python3 ~/.codex/runtime/workspace/skills/tikz-draw/semantic_regression_runner.py --platform both --strict-approval
```

The current suite covers supported good cases for `flowchart`, `dag`, `tree`,
`commutative`, and Sage-backed `graph`, plus mutation cases and intent-contract
cases that guard against graph-hardness requests becoming flowcharts.

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

## Recommended templates

When this skill is involved, consider this workflow template (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `tikz-figure-verification-runbook` -- Bounded draw-compile-verify-redraw loop for a TikZ figure that guarantees it is free of overlap, wrong meaning, and bad layout, with Sage-assisted graph realization and fresh-agent visual confirmation before the strict approval gate.
