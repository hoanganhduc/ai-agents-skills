---
name: autonomous-research-loop
description: Run bounded autonomous research iterations with evidence gates, recovery ledgers, and optional cross-agent handoffs. Use when the user asks to continue research autonomously, run a research loop, integrate autonomous agent loops, or keep improving a research workflow without repeated prompts.
---

# Autonomous Research Loop

Use this skill to run research autonomously while preserving bounded scope,
evidence, recovery state, and explicit stop conditions. It is an orchestration
contract, not an instruction to run indefinitely.

## Core Rule

Every autonomous loop must have:

1. A concrete goal.
2. Success criteria.
3. Hard budgets.
4. A loop ledger.
5. Evidence gates.
6. Recovery notes.
7. Stop conditions.

If any of those are missing, create them before starting the first autonomous
iteration.

## When To Use

Use this skill for:

- Continuing nontrivial research across multiple iterations.
- Running source discovery, synthesis, verification, and revision without asking
  after every step.
- Coordinating bounded subagent research or panel review.
- Integrating autonomous behavior into an existing research workflow.
- Resuming a research loop after interruption or context compaction.

Do not use it for:

- Trivial one-shot lookups.
- User requests that explicitly ask only for a plan or analysis.
- User requests that ask only to investigate, diagnose, review, audit, verify,
  or report, unless the user also explicitly asks for autonomous follow-on work.
- Work that lacks a safe budget or stop condition.
- Blind command execution without inspectable evidence.

## Required Loop Files

For a research workspace, keep these files in the active research directory:

- `loop_state.json`: goal, success criteria, mode, stop flags, current status.
- `budget.json`: iteration, wall-clock, token, cost, depth, and child-agent limits.
- `iterations.jsonl`: append-only record of each loop iteration.
- `recovery.md`: latest resume point, blockers, next safe action, and evidence gaps.

If a runtime helper is available, prefer the companion
`autonomous-research-loop-runtime` skill to initialize and validate these files.
If it is not available, create the files manually in the same structure.

## Loop Modes

Choose the narrowest mode that can satisfy the goal:

- `monitor`: check whether new evidence or tasks exist, then stop if nothing
  changed.
- `bounded-research`: search, analyze, verify, and write within declared
  budgets.
- `implementation-support`: inspect code or docs, propose research-backed
  changes, and verify integration assumptions.
- `panel-loop`: use a bounded multi-agent discussion, then synthesize and
  verify the result.
- `recovery`: resume from `recovery.md`, validate state, and continue only from
  the recorded next action.

## Preflight

Before starting an autonomous run:

1. State the scope and material exclusions.
2. Define success criteria in observable terms.
3. Set hard budgets:
   - maximum iterations
   - maximum wall time or user-visible turns
   - maximum child workers
   - maximum source hops or search depth
   - maximum spend or token budget when applicable
4. Define stop conditions. The enforcement policy in
   `canonical/instructions/autonomous-loop-enforcement.md` governs them: user
   requirements override everything, so capture them into
   `loop_state.stop_conditions` at init. When the user gave no overriding
   requirement, the loop stops only on:
   - the required number of loops reached
   - credit or quota exhausted, or a user-set spend cap hit
   - the stated goal fully resolved, confirmed by a machine-checkable success
     check
   - a user message asking to stop, pause, or switch tasks
   Plateau, evidence-gap, and repeated-blocker signals are not terminal under
   these defaults; record them, then downgrade the iteration decision to
   `revise` or `delegate` and continue. They end the loop only when the user set
   them as an explicit stop condition.
5. Initialize or validate the loop files.

The maximum iteration budget is a hard cap, not a target to exceed while
searching for success. A loop may run fewer iterations when success or a true
hard stop condition occurs, but it must never append more than
`max_iterations` records. A normal early `stop` before the final allowed
iteration is valid only when the success criteria are met and the iteration
cites a machine-checkable proof/success artifact. Otherwise continue, revise,
or delegate; do not mark the loop `blocked` early. A self-marked blocker is not
a stop under the enforcement policy: record it and continue. The decision
`blocked` is reserved for the final allowed iteration, when the budget is
exhausted without success.

## Iteration Protocol

Each iteration must record:

- iteration number
- timestamp
- mode
- objective
- evidence checked
- actions taken
- output produced
- remaining gaps
- budget consumed or estimated
- decision: `continue`, `revise`, `delegate`, `stop`, or `blocked`

Only use a continuing decision (`continue`, `revise`, or `delegate`) when the
next iteration has a concrete objective and remaining budget. The final allowed
iteration must be terminal (`stop` or `blocked`); if success criteria have not
been satisfied by then, stop as budget exhausted instead of leaving the loop
`running`. Before the final allowed iteration, `stop` must mean success/proof
found and must cite at least one evidence id that resolves to a proof artifact,
and `blocked` is not accepted: record the blocker and continue with `revise` or
`delegate`.


### Heavy computation inside iterations

Route heavy computation (exhaustive enumeration, certificate suites, censuses)
through the unified broker exposed by `modal-research-compute`. The recommended
order is `local > Kaggle > Modal > Hetzner > GitHub Actions`; a valid custom order
keeps local first and may reorder or omit unique remote lanes, while explicit
backend overrides are hard pins that still pass lane safety gates. Let the broker's adequacy and
self-preservation policy decide whether work stays local or falls through to an
available remote lane. Use `run plan` as the decision boundary. Execute a selected
Kaggle or Hetzner lane through its corresponding lane skill; `run submit` dispatches
only Modal/GitHub Actions. When the broker is unavailable, use the throttled local
queue defined in
`skills/modal-research-compute/references/local-compute-throttle.md`
(cross-platform: lockfile singleton, idle priority, chunked resumable
checkpoints, load guards). Never launch ad-hoc unthrottled heavy processes. Record run
IDs and quote runner-log lines (not agent summaries) when banking offloaded
results, and treat a red run with zero artifacts as a wiring bug but a red
run with FAIL rows as mathematics to investigate.

## Evidence Gates

Apply the relevant gates before accepting an iteration output:

- Source claims require source IDs or file references.
- Current facts require dated source checks.
- User-facing writing requires `writing-style-settings.md` to be loaded before
  final prose. Mathematical, TCS, graph-theoretic, Lean, or LaTeX writing also
  requires `math-manuscript-style.md`.
- Early proof/success stops require an evidence id backed by a local
  machine-checkable proof artifact, such as
  `proof_artifacts/<evidence_id>.json`, whose checker metadata reports a
  passed check and whose proof file exists in the loop directory.
- Code or workflow changes require local inspection of relevant files.
- Multi-agent conclusions require synthesis that separates agreement,
  disagreement, assumptions, and unresolved questions.
- Recommendations must distinguish confirmed evidence from inference.

If a gate fails, record the failure in `iterations.jsonl` and choose one of:

- retry with a narrower objective
- delegate a bounded check
- revise the scope
- stop as blocked

## Multi-Agent Use

When using subagents:

1. Create bounded task packets with objective, evidence required, exclusions,
   and expected output.
2. Limit child workers to the budget in `budget.json`.
3. Require each child result to report inspected and uninspected evidence.
4. Synthesize child outputs before making decisions.
5. Do not let child agents recursively start unbounded loops.

For panel-style discussion, pair this skill with `agent-group-discuss` or
`prose` when available.

## Recovery

After every material iteration, update `recovery.md` with:

- current goal
- last completed iteration
- current status
- next safe action
- remaining evidence gaps
- active blockers
- budget remaining

On resume, read `loop_state.json`, `budget.json`, `iterations.jsonl`, and
`recovery.md` before acting. Validate state before continuing.

## Truly Autonomous Execution

A chat session cannot carry a long loop by itself: context windows and turn
boundaries end it. For unattended multi-day runs, hand the loop to the
`autonomous-research-loop-runtime` headless driver, which respawns a fresh
headless agent session per iteration against the on-disk loop files and owns
the stop conditions:

```bash
... run_autonomous_research_loop.sh drive --dir <loop_dir> --provider <claude|codex|deepseek|opencode|copilot|antigravity>
```

`agent-cmd --provider all --dir <loop_dir>` prints the per-target iteration
commands and probes binary availability. The driver captures per-iteration
logs, re-checks the stop conditions every cycle, and treats detected
credit/quota outages as pause-and-wait (not failure), resuming when credits
return. Interactive sessions on Claude are additionally governed by the
installed `hooks.Stop` entry while a loop is armed (`arm --dir <loop_dir>
--root <project_root>`): the hook blocks turn-end until a real stop condition
fires. Kill switches in both modes: `touch <loop_dir>/STOP_REQUESTED`,
`touch <loop_dir>/PAUSE`, `AUTOLOOP_DISABLE=1`, or `disarm`.

## Stop Rules

These rules are governed by the enforcement policy in
`canonical/instructions/autonomous-loop-enforcement.md`. User requirements
override everything; the conditions below are the defaults used only when the
user set no overriding requirement.

Stop immediately and report status when:

- success criteria are satisfied, confirmed by a machine-checkable success check
- any hard budget is exhausted, including credit, a spend cap, wall clock, or
  the iteration count
- the user asks to pause, stop, or switch tasks

A repeated blocker, an unresolved evidence gap, or a next action that would
exceed the approved scope is not, by itself, a stop under these defaults. Record
it, choose an in-scope action, and continue, downgrading the iteration decision
to `revise` or `delegate`. Such a signal ends the loop only when no in-scope
action remains and it also satisfies one of the conditions above, or when the
user set it as an explicit stop condition.

When stopping, report:

- status: complete, stopped, or blocked
- iterations completed
- evidence inspected
- remaining unchecked items
- next recommended action

## Output Contract

For user-visible summaries, use this compact shape:

```text
Scope: ...
Status: ...
Evidence Checked: ...
Iterations: ...
Decision: ...
Remaining Gaps: ...
Style: ...
Next Action: ...
```

If material evidence remains unchecked, explicitly say `incomplete analysis`
before the provisional recommendation.
For finalizable prose artifacts created during the loop, record
`style_profile_ref`, `active_overlays`, `active_requirement_ids`, and
`style_applied` in the loop ledger or artifact-adjacent style record. Do not
count a bare `style_applied: true` value as force-use evidence.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
- `autonomous-research-loop-portfolio-runbook` -- Open-problem, portfolio-first variant of the autonomous research-loop runbook: a rigorous definition-of-done with an insufficient-result disqualification list, an approach registry with blocked-route discipline, and an adversarial audit gate with a concrete-deliverable requirement, keeping the same four stop conditions, cross-agent verification, fresh-agent backtracking, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
