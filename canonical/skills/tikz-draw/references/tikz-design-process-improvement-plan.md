# TikZ Draw Design Process Improvement Plan

## Scope

This plan targets the `tikz-draw` skill and runtime in this repository. It is a
planning artifact only: it proposes changes to the skill instructions, schemas,
runtime gates, and regression tests. It does not implement those changes.

The goal is to prevent figures that pass compile, overlap, and symmetry checks
but still fail as mathematical figures because their visual design does not
encode the intended objects, annotations, replacements, labels, or caption
claims clearly.

## Evidence Checked

The parent agent and four specialist agents inspected the following artifacts:

- `canonical/skills/tikz-draw/SKILL.md`
- `canonical/skills/tikz-draw/references/quality-gates.md`
- `canonical/skills/tikz-draw/references/tikz-prevention.md`
- `canonical/skills/tikz-draw/references/backend-routing.md`
- `canonical/skills/tikz-draw/references/tikz-measurement.md`
- `canonical/runtime/skills/tikz-draw/tikz_draw.py`
- `canonical/runtime/skills/tikz-draw/family_verifiers.py`
- `canonical/runtime/skills/tikz-draw/semantic_regression_runner.py`
- `canonical/runtime/skills/tikz-draw/assets/spec-schema/figure-contract.schema.json`
- `canonical/runtime/skills/tikz-draw/assets/spec-schema/diagram.schema.json`
- `canonical/runtime/skills/tikz-draw/assets/examples/semantic-regression/suite.json`
- `manifest/runtime.yaml`
- `tests/test_runtime_integration.py`

The multi-agent discussion used four roles:

- Figure Design Critic
- TikZ Runtime Engineer
- Skill Prompt and UX Reviewer
- Verifier and Test Planner

The discussion had two rounds: independent review, then cross-review of the
combined thesis.

## Current Failure Mode

The current skill already requires a semantic intent contract, graph requests
must remain graph figures, and strict `approve` is the only final gate. However,
the existing contract and diagram spec do not yet make visual design roles
first-class.

Observed gap:

- `figure-contract.schema.json` records required objects, required relations,
  forbidden simplifications, notation, and approval criteria.
- `diagram.schema.json` records nodes, edges, groups, layout constraints,
  validation rules, symmetry, and an optional semantic contract.
- `approve` currently combines static checks, compile, visual overlap review,
  semantic verification, and symmetry verification.
- `review-visual` measures rendered geometry but does not know whether a box is
  a graph object, an annotation, a gadget boundary, a correspondence mark, or a
  callout.

Because of this, a figure can pass mechanical checks while still confusing the
reader. Examples include:

- boxing `L(u)` so a list annotation looks like a graph object;
- using filled or overlapping regions so graph membership is ambiguous;
- showing a gadget label without the gadget graph structure;
- placing port labels near the wrong vertices;
- drawing a generic transformation diagram when the caption claims an edge is
  replaced by a graph gadget;
- changing boxes, fills, arrows, or labels without checking the caption and
  nearby manuscript text.

## Design Target

For manuscript-facing semantic figures, especially graph reductions and proof
figures, a reader should be able to recover the following from the figure plus
caption:

- what is actual graph structure;
- what is a gadget or subgraph region;
- what is annotation or proof metadata;
- which source object corresponds to which constructed object;
- what each visual mark means;
- which caption/prose claim each nonstandard encoding supports.

Mechanical checks remain necessary but not sufficient. The new design layer
should make this distinction explicit.

## Scope Boundary For The New Gate

Do not require the full design gate for every trivial TikZ edit. The heavier
checkpoint should be mandatory for:

- manuscript-facing figures;
- figures with a semantic contract;
- graph, proof, gadget, or reduction figures;
- extracted or adopted figures intended for strict semantic approval;
- figures with visual encodings beyond plain structure, such as regions,
  boxes, fills, callouts, correspondence marks, or emphasized labels.

For simple standalone flowcharts or cosmetic edits with no mathematical claim,
the current static, compile, visual, semantic, and symmetry gates can remain the
main path unless the user or context asks for design review.

## Semantic Design Checkpoint

Before editing or generating a manuscript-facing semantic TikZ figure, require a
short checkpoint. This should be visible in the agent workflow and represented
in runtime artifacts once implemented.

Required fields:

- user request;
- inspected figure source path and, when available, line references;
- inspected caption, figure label/reference, and nearby prose;
- affected visual marks;
- intended semantic role for each affected mark;
- design alternatives considered;
- chosen visual encoding and rationale;
- caption/prose consistency status;
- ambiguity status: clear, ambiguous, disputed, or blocked;
- action: edit, ask the user, or stop as blocked.

Core rule:

> Treat visual edit requests that affect emphasis, grouping, labels, regions,
> callouts, graph objects, or correspondence marks as semantic design requests,
> not mechanical TikZ edits.

User corrections should reopen this checkpoint. The agent should state the
mistaken assumption, re-inspect the relevant context, update the design
rationale, and only then apply or propose the correction.

## Proposed Runtime Design Layer

Add a first-class design artifact between `contract` and `spec`:

- new schema: `figure-design.schema.json`;
- new command: `design`;
- new component gate: `verify-design`;
- new manifest/report fields: `figure_design`, `design_status`,
  `design_review`.

Suggested artifact sequence:

1. `F1.figure-contract.json`
2. `F1.figure-design.json`
3. `F1.figure-brief.json`
4. `F1.diagram.json`
5. `F1.standalone.tex`
6. `F1.figure.tex`
7. `F1.render-semantics.json`
8. `F1.semantic-review.json`
9. `F1.artifacts.json`

`approve` should require `design_status=PASS` for the scoped semantic figure
classes above.

## Proposed Design Schema

Add `figure-design.schema.json` with fields such as:

```json
{
  "schema_version": "figure-design.v1",
  "figure_id": "F1",
  "design_intent": "Show source edge replacement by a concrete gadget.",
  "audience_task": "Recover which edge is replaced and which constructed graph part replaces it.",
  "caption_claims": [],
  "source_prose_claims": [],
  "marks": [],
  "visual_encoding_policy": {},
  "rationale": [],
  "approval_requirements": []
}
```

Caption and prose checks should be bounded and deterministic. The runtime should
verify declared bindings such as "caption claim C1 is represented by mark M3";
it should not try to prove arbitrary natural-language claims.

## Proposed Mark Model

Extend `diagram.schema.json` with a `marks` array. Keep graph semantics in
`nodes` and `edges`; use `marks` for overlays, annotations, callouts, regions,
and correspondences.

Suggested mark fields:

- `id`;
- `role`;
- `semantic_type`;
- `targets`;
- `source_targets`;
- `target_targets`;
- `label`;
- `visual_encoding`;
- `counts_as_graph_object`;
- `caption_claim_ids`;
- `source_prose_claim_ids`;
- `boundary_style`;
- `fill_policy`;
- `label_policy`;
- `rationale`.

Suggested roles:

- `graph_object`: actual graph vertices or edges;
- `annotation`: metadata or explanatory text, not graph structure;
- `callout`: annotation with a leader line to a declared target;
- `correspondence`: source object to constructed object relation;
- `gadget_region`: region enclosing a gadget subgraph;
- `highlight_region`: visual emphasis around an object or relation;
- `legend`: decoding key for styles or notation.

Rules:

- annotations must not be counted as graph objects;
- list constraints such as `L(u)` should default to adjacent text or a callout,
  not boxed graph-object styling;
- gadget regions must declare their member vertices/edges;
- correspondence marks must bind source and target objects;
- fills must not obscure required graph objects or make membership ambiguous;
- overlapping regions should default to outline-only encodings with distinct
  line styles, not filled boxes;
- labels must declare their owner and attachment target.

## Runtime Tasks

1. Add `figure-design.schema.json`.
2. Extend `diagram.schema.json` with `marks`.
3. Add `design` command in `tikz_draw.py`.
4. Add `verify-design` in `tikz_draw.py`.
5. Extend `spec` to accept `--design` and copy design marks into the diagram
   spec.
6. Extend `render` to use role-specific styles for graph objects, annotations,
   callouts, correspondences, gadget regions, and highlights.
7. Extend `approve` so scoped semantic figures fail unless
   `design_status=PASS`.
8. Keep extracted hand-written TikZ fail-closed unless it is adopted into a
   semantic/design/spec target.
9. Add a repair path for extracted figures, such as `adopt` or
   `reverse-design`, that creates a provisional contract, design, and diagram
   spec from existing TikZ plus manuscript context.

Deterministic `verify-design` checks should fail when:

- a caption/prose claim is not bound to a visual-semantic mark;
- an annotation or callout is counted as graph structure;
- metadata is styled like a graph object without explicit rationale;
- a gadget is represented only by a label;
- a required port label is attached to the wrong target;
- a region fill can obscure required graph objects or correspondences;
- a graph/proof/reduction contract is represented by a box-only schematic.

## Skill Documentation Tasks

Update `canonical/skills/tikz-draw/SKILL.md`:

- add the Semantic Design Checkpoint before the current required workflow;
- state that manuscript-facing visual edits can be semantic design changes;
- require source, caption, and nearby prose inspection for existing figures;
- require visible rationale before changing boxes, fills, regions, labels,
  arrows, callouts, or correspondence marks;
- state that `approve` is necessary but not sufficient unless the required
  design checkpoint has also passed.

Update `canonical/skills/tikz-draw/references/quality-gates.md`:

- add a design gate section;
- add review dimensions for visual-semantic roles and manuscript consistency;
- add rule IDs for design-rationale recorded, caption/prose alignment, mark
  role validity, label attachment, region/fill semantics, and reader
  recoverability;
- distinguish "machine-approved" from "semantically consistent with inspected
  manuscript context."

Update `canonical/skills/tikz-draw/references/tikz-prevention.md`:

- forbid adding or removing emphasis marks without recording their intended
  meaning;
- forbid visual simplifications that erase caption/prose distinctions;
- forbid treating user corrections as blind patch instructions when the
  correction reveals a mistaken design model.

## Regression Plan

Extend
`canonical/runtime/skills/tikz-draw/assets/examples/semantic-regression/suite.json`
and `semantic_regression_runner.py`.

Minimum fixtures:

- `graph_reduction_good`: source edge, constructed gadget, ports, blocker,
  labels, correspondence marks, caption/prose claims, and `design_status=PASS`.
- `metadata_boxed_as_graph_object`: compile and visual review pass, but
  `approve` fails because metadata is styled or counted as graph structure.
- `label_only_gadget`: required gadget notation appears, but the gadget graph
  vertices/edges are absent.
- `ambiguous_fill_region`: region fill or overlap makes graph membership or
  replacement ambiguous while primitive overlap checks pass.
- `misplaced_port_label`: required labels exist but attach to the wrong
  vertices or ports.
- `caption_prose_mismatch`: caption/prose claims edge replacement, but the
  figure encodes only a generic transformation or flowchart.
- `extracted_without_design`: extracted TikZ remains blocked for strict
  approval until a design/spec target is attached.
- `adopted_extracted_good`: extracted TikZ can pass only after adoption creates
  contract, design, and spec artifacts.

Stable mismatch codes should be added for:

- `METADATA_RENDERED_AS_GRAPH_OBJECT`;
- `ANNOTATION_COUNTED_AS_GRAPH_OBJECT`;
- `CONTRACT_FORBIDDEN_LABEL_ONLY_GADGET`;
- `FILL_OCCLUDES_GRAPH_STRUCTURE`;
- `AMBIGUOUS_REGION_MEMBERSHIP`;
- `WRONG_LABEL_ATTACHMENT`;
- `WRONG_PORT_LABEL`;
- `CAPTION_CONTRACT_MISMATCH`;
- `CONTRACT_REPLACEMENT_RELATION_MISSING`;
- `DESIGN_STATUS_MISSING`;
- `DESIGN_STATUS_FAIL`.

## Local And CI Verification

Local checks for runtime changes:

- targeted design-verifier unit tests;
- fixture-specific semantic regression runs during development;
- full `semantic_regression_runner.py --platform codex --strict-approval`
  before reporting completion when runtime behavior changes;
- TeX/PDF extraction checks when touching labels, visual primitives, rendered
  semantics, or overlap logic.

Cross-platform checks:

- exercise POSIX and Windows command shapes in the semantic regression runner;
- keep `.sh` launchers LF/executable and `.bat` launchers CRLF/Windows-only;
- test installer manifest coverage for schemas, suite files, scripts, and
  native launchers.

CI split:

- fast lane: schema validation, design verifier unit tests, contract/design
  command smoke, installer manifest tests;
- heavy lane: full TeX/PDF semantic regression, ideally required when
  `tikz-draw` runtime files change and optional/nightly otherwise.

## Acceptance Criteria

The improvement is complete when:

- nontrivial semantic figures cannot be approved without a design artifact;
- visual marks have declared roles and targets;
- metadata cannot silently become graph structure;
- graph reductions cannot degrade into box-only schematics;
- gadget labels cannot substitute for gadget graph structure;
- labels and callouts have checked ownership;
- region and fill encodings are black-and-white-print-suitable unless the
  contract explicitly allows color dependence;
- caption/prose claims are represented by declared visual marks;
- user corrections reopen the design checkpoint instead of triggering blind
  patching;
- at least one regression demonstrates compile and `review-visual` passing
  while strict `approve` fails due to design-role ambiguity.

## Phased Implementation

### Phase 1: Documentation And Prompt Contract

Edit `SKILL.md`, `quality-gates.md`, and `tikz-prevention.md` to require the
Semantic Design Checkpoint for scoped figures.

Acceptance:

- docs define manuscript-facing semantic figures;
- docs define visual-semantic mark roles;
- docs state that `approve` alone is not enough when the required design
  checkpoint is missing;
- tests or static checks confirm the key instruction text is installed.

### Phase 2: Schema Layer

Add `figure-design.schema.json` and extend `diagram.schema.json` with `marks`.

Acceptance:

- schema validation accepts a good graph-reduction design;
- schema validation rejects missing mark roles, missing targets for callouts,
  and illegal graph-object metadata.

### Phase 3: Runtime Design Gate

Implement `design`, `verify-design`, and manifest/report fields. Integrate the
gate into `approve` for graph/proof/reduction contract kinds and adopted
manuscript figures.

Acceptance:

- scoped figures without design artifacts fail closed;
- scoped figures with failed design review cannot be approved;
- unscoped simple figures are not forced through the full design gate.

### Phase 4: Renderer And Repair Path

Teach rendering to preserve role-specific encodings and add an adoption path for
extracted TikZ.

Acceptance:

- extracted TikZ without a design target remains blocked;
- adopted extracted TikZ can pass after contract/design/spec are present;
- role-specific rendering avoids boxing metadata as graph objects by default.

### Phase 5: Regression And Installer Coverage

Add the fixture set, mismatch codes, runner assertions, and installer/runtime
coverage.

Acceptance:

- the semantic regression suite includes design-positive and design-negative
  graph reduction fixtures;
- at least one negative fixture passes compile and visual review but fails
  strict approval;
- cross-platform launcher and manifest tests cover the added runtime assets.

## Risks

- Full caption/prose understanding can become too ambitious. Keep runtime checks
  to declared claim bindings and let the agent checkpoint handle richer
  manuscript interpretation.
- A rigid mark model can make normal TikZ repair painful. Keep graph structure
  in `nodes` and `edges`; use marks for visual semantics and overlays.
- Overfitting to graph reductions can weaken generality. Use generic mark roles
  while adding graph-specific validators where needed.
- False positives are possible for legitimate shaded regions. Use explicit
  `fill_policy`, `allowed_overlap_targets`, and rationale fields rather than a
  blanket ban on fills.
