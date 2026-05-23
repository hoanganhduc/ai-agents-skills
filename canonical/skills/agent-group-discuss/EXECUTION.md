# Codex Execution Guide

This file is the execution reference for running the six imported research templates with Codex agent tools.
Read this when the user triggers a multi-agent research or review run.
Template definitions live in `TEMPLATES.md`.

## Runtime rules

1. Before any `spawn_agent` call, show the plan and get explicit user confirmation.
2. Before assigning models, perform the runtime freshness check in
   `MODEL_TIERS.md`; if a newer suitable model is available than the checked-in
   fallback, use the newer model and record the resolved choice.
3. For research, proof, manuscript-correctness, or other high-stakes review
   tasks, every participant must use the latest available model with the
   highest available thinking or reasoning level. Apply the same rule to any
   permitted manager-spawned child workers.
4. For complex correctness reviews, default agent timeout is 45 minutes and persistent progress checkpoints are required every 15 minutes.
5. Default execution is foreground. Only run the panel as background work if the user explicitly wants to do other work while it runs.

## Model mapping

Resolve models through `MODEL_TIERS.md` after checking the active runtime model
list. The table below uses symbolic model slots; replace them with concrete
model names in the user-facing plan and `state.json`.

Practical default mapping:

| Reasoning tier | Research default | Non-research baseline | Use for |
|----------------|------------------|-----------------------|---------|
| `R4` | latest available frontier model `xhigh` | latest available frontier model `high` | proofs, formal math, correctness verification, refereeing |
| `R3` | latest available frontier model `xhigh` | strongest available R3 model `high` | planning, synthesis, structured review |
| `R2` | latest available frontier model `xhigh` | strongest available R2 model `medium` | edge-case review, support analysis |
| `R1` | latest available frontier model `xhigh` | strongest available fast model `low` | scouting, brainstorming, clarity review |

## Execution pattern

### Launching participants

Each template role is a logical responsibility. Each role is assigned to one or
more participants. Initial participant kinds are:

- `codex_spawned`: a Codex subagent launched with `spawn_agent`
- `external_cli`: a parent-owned external CLI participant governed by
  `references/external-cli-agents.md`

For `codex_spawned`, each participant is a separate `spawn_agent` call.
Independent `codex_spawned` participants in the same round should be launched
in parallel with `multi_tool_use.parallel`.

Example shape:

```text
spawn_agent({
  agent_type: "default",
  model: "<resolved runtime model, for example the current frontier model>",
  reasoning_effort: "xhigh",
  fork_context: false,
  message: "<full role briefing>"
})
```

For `external_cli`, do not call `spawn_agent`. Before launch, record a current
capability profile, input transport policy, output contract, timeout, artifact
layout, and validation owner. The parent must parse and validate output before
it can influence synthesis.

Use the managed dispatcher when an external CLI participant should run from
this repository:

```text
./installer/bootstrap.sh delegate-agent \
  --provider auto \
  --task-file <bounded-task-prompt.md> \
  --role "<role name>" \
  --template "<template name>" \
  --research \
  --allow-external-cli
```

For research dispatch, the dispatcher blocks unless the provider has a resolved
latest model and highest thinking/reasoning setting from arguments or provider
environment, and a provider dispatch command is configured. Use dry-run first
to see the selected providers and blocked reasons.

### Round structure

- Round 1 independent first pass: use fresh participants with no cross-role contamination.
- Later rounds: use `send_input` to the same `codex_spawned` participant when continuity helps, or respawn/rerun fresh if independence or token hygiene matters more.
- Referee or synthesis roles run only after the prior round results are in.
- Compress prior results before relaying them. Keep only decisive findings, not full transcripts.

### Waiting and cleanup

- Use `wait_agent` once per round or per critical batch for `codex_spawned`
  participants.
- Do not busy-poll.
- Use `close_agent` after the run or when a role is no longer needed.
- If a `codex_spawned` role agent needs to be revived, use `resume_agent`
  before reusing it.
  External CLI participants use parent-owned retry and artifact policies
  instead of agent resume.

## Role prompt template

Every participant should receive a self-contained prompt or input packet with
this structure:

```text
You are the {ROLE_NAME} in a {TEMPLATE_NAME} multi-agent research session.

## Your role
{ROLE_DESCRIPTION}

## Round {N} of {TOTAL}
{ROUND-SPECIFIC INSTRUCTIONS}

## Topic / Claim
{THE CLAIM, PROOF, PAPER, OR PROBLEM}

## Prior round context
{COMPRESSED SUMMARY OF DECISIVE FINDINGS — omit in Round 1}

## Required output format
{STRUCTURED OUTPUT FORMAT}

## Tool access
{FOR COMPUTATION ROLES}
- To run SageMath:
  functions.exec_command with:
  bash ~/.codex/runtime/run_skill.sh skills/sagemath/run_sage.sh "<sage_code>"
- To verify graph properties:
  functions.exec_command with:
  bash ~/.codex/runtime/run_skill.sh skills/graph-verifier/run_graph_verifier.sh --input /tmp/graph_input.json
- To scaffold a formal claim:
  functions.exec_command with:
  bash ~/.codex/runtime/run_skill.sh skills/formal-skeleton-helper/run_formal_skeleton.sh --input /tmp/formal_input.json

{FOR PURE REASONING ROLES}
- Read files or search if needed, but do not run computations unless explicitly instructed.
- Do not write files.

{FOR RESEARCH-RELATED ROLES}
- Consider relevant installed research skill guidance before performing the task,
  but use it only within this prompt's explicit tool, context, and side-effect
  limits.
- Do not spawn agents, edit files, run network calls, retrieve sources, or
  execute commands unless this prompt explicitly permits that capability.
- If this prompt explicitly permits nested workers, keep them one level deep,
  use the same provider, resolved model, and thinking level as this manager,
  and require child workers to avoid further spawning.
- For paper or book work, follow local-library-first routing when applicable:
  use `zotero` first for papers, use `calibre` for book or review lookup when
  applicable, and use `getscipapers-requester` only after local lookup fails
  and external retrieval is allowed.
- Use `paper-lookup` for metadata or discovery fallback, not retrieval. Other
  research support may route through `docling`, `database-lookup`,
  `source-research`, `deep-research-workflow`, `research-briefing`,
  `research-report-reviewer`, or `research-verification-gate` when permitted.
- Treat `agent-group-discuss` and `prose` as parent-level escalation workflows,
  not nested-agent tools for this role.
- Report which guidance you used or could not use. Keep unverified leads,
  unchecked citations, unsupported claims, and blocked checks clearly labeled.

## Hard rules
- Work independently.
- Be concrete: cite exact lines, pages, steps, definitions, or claims.
- Distinguish: proved / heuristic / conjectural / unverified.
- If you find a fatal flaw, say so clearly and switch to diagnosis.
- Correctness over elegance. Prefer a weaker correct claim over a stronger broken one.
```

## Template execution plans

### 1. Lakatos Proof and Refutation

Profile: `math-heavy`
Rounds: `3`
Roles: `4`

| # | Role | Model | Computation |
|---|------|-------|-------------|
| 1 | Prover | latest available frontier model `xhigh` | No |
| 2 | Counterexample Hunter | latest available frontier model `xhigh` | SageMath |
| 3 | Monster-Barrer / Refiner | latest available frontier model `xhigh` | No |
| 4 | Formalist | latest available frontier model `xhigh` | No |

Execution:

- Round 1: 4 parallel participants
- Round 2: 4 parallel role follow-ups with compressed Round 1 findings
- Round 3: 1 Formalist synthesis pass or local synthesis if clearly better

### 2. Polya Multi-Strategy Problem Solving

Profile: `math-heavy` by research override
Rounds: `3`
Roles: `3`

| # | Role | Model | Computation |
|---|------|-------|-------------|
| 1 | Specializer | latest available frontier model `xhigh` | SageMath |
| 2 | Generalizer | latest available frontier model `xhigh` | No |
| 3 | Reducer | latest available frontier model `xhigh` | No |

Execution:

- Round 1: 3 parallel participants
- Round 2: 3 parallel role follow-ups after orchestrator cross-pollinates decisive findings
- Round 3: local synthesis or 1 lead synthesis agent

### 3. Knuth Structured Manuscript Review

Profile: `math-heavy` for mathematical manuscript review, otherwise `premium`
Rounds: `2`
Roles: `3`

| # | Role | Model | Computation |
|---|------|-------|-------------|
| 1 | Correctness Reviewer | latest available frontier model `xhigh` | SageMath when claims are computationally checkable |
| 2 | Exposition Reviewer | latest available frontier model `xhigh` | No |
| 3 | Literature Reviewer | latest available frontier model `xhigh` | No |

Execution:

- Round 1: 3 parallel independent reviews
- Round 2: orchestrator synthesis into a prioritized action list

### 4. Structured Research Team

Profile: `math-heavy`
Rounds: `3 + conditional 4`
Roles: `4`

| # | Role | Model | Computation |
|---|------|-------|-------------|
| 1 | Builder | latest available frontier model `xhigh` | No |
| 2 | Breaker | latest available frontier model `xhigh` | SageMath |
| 3 | Alternative Builder | latest available frontier model `xhigh` | No |
| 4 | Referee / Verifier | latest available frontier model `xhigh` | synthesis only |

Execution:

- Round 1: 3 parallel independent participants
- Round 2: 3 parallel critique passes with compressed Round 1 findings
- Round 3: orchestrator-run verification via `functions.exec_command`
- Round 4: optional repair pass only if a concrete local repair exists
- Final: 1 referee synthesis pass or local synthesis if clearly stronger

### 5. Graph Reconfiguration Specialist

Profile: `math-heavy`
Rounds: `3 + conditional 4`
Roles: `4`

| # | Role | Model | Computation |
|---|------|-------|-------------|
| 1 | Constructor | latest available frontier model `xhigh` | No |
| 2 | Adversary | latest available frontier model `xhigh` | SageMath |
| 3 | Auditor | latest available frontier model `xhigh` | No |
| 4 | Referee / Verifier | latest available frontier model `xhigh` | synthesis only |

Execution:

- Round 1: 3 parallel independent participants
- Round 2: 3 parallel critique passes
- Round 3: orchestrator verification:
  - computational
  - structural
  - bibliographic
  - formal when relevant
- Round 4: optional repair pass only for a concrete local repair
- Final: referee synthesis with typed verifier table and failure taxonomy

### 6. Lean Formalization Team

Profile: `math-heavy`
Rounds: `2`
Roles: `5`

| # | Role | Model | Computation |
|---|------|-------|-------------|
| 1 | Informal Planner | latest available frontier model `xhigh` | No |
| 2 | Formalizer | latest available frontier model `xhigh` | local Lean or scaffold work |
| 3 | Missing-Lemma Miner | latest available frontier model `xhigh` | No |
| 4 | Repair Agent | latest available frontier model `xhigh` | No |
| 5 | Checker | latest available frontier model `xhigh` | No |

Execution:

- Round 1: 3 parallel participants
- Round 2: 2 parallel participants
- Final: local synthesis or Checker-led synthesis

## State management

Run directory:

- `$HOME/.codex/runs/agent_group_discuss/<run_id>/`

Files written by the orchestrator:

| When | File | Content |
|------|------|---------|
| Before execution | `plan.md` | roles, models, rounds, estimated time |
| Before execution | `state.json` | full state |
| Every 15 minutes for long correctness reviews | `progress_15m.md`, `progress_30m.md`, ... | durable progress checkpoints |
| After each round | `round_01.md`, `round_02.md`, ... | compressed role outputs |
| After completion | `final.md` | final synthesis or ledger |
| After completion | `final_report.md` | optional user-facing condensed report for long review runs |

Additional files for `external_cli` participants:

| Directory or file | Content |
|-------------------|---------|
| `profiles/` | timestamped capability profiles |
| `probes/` | probe prompts, sanitized observations, and probe summaries |
| `raw/` | parent-owned raw stdout/stderr or command-shape artifacts |
| `parsed/` | parsed participant outputs |
| `validation/` | parent validation reports |
| `transport_manifest.json` | prompt/input transport and chunk manifest |
| `timeout_events.jsonl` | timeout and missing-final-marker events |
| `truncation_events.jsonl` | truncation or malformed-rendering events |

State updates:

- set `status: "running"` before each round launch
- update participant status and `responses_received` immediately after each
  result arrives or fails validation
- write the round file as soon as all expected required participants for that
  round are in, failed, or explicitly marked invalid
- set `status: "completed"` after `final.md` is written

### Lock protocol

Before starting:

1. check for a `lock` file
2. if it exists and is fresh, abort
3. if it is stale, remove it and proceed
4. write a fresh `lock` file
5. remove or clear it after completion

### Recovery

If a session is interrupted:

1. read `state.json`
2. inspect existing round and progress files
3. identify missing required participants from `responses_received` and
   participant status
4. if `codex_spawned` role agents still exist, use `resume_agent` or `send_input`
5. otherwise respawn or rerun only the missing participants with compressed
   context and a fresh artifact record
6. never rerun completed rounds unless the user asks

## External verification

### Role agents running computation

For computation-capable roles, include the exact helper commands in the prompt.

### Orchestrator-run verification

Keep verification independent from the roles being verified.
Run Round 3 checks locally through `functions.exec_command` where the template calls for orchestrator verification.

## Stop rules

Embed these in every role prompt and enforce them in orchestration:

1. Fatal flaw found: stop defending and switch to diagnosis.
2. Decisive counterexample found: Builder or Constructor must propose a corrected version instead of defending the broken one.
3. Token exhaustion: relaunch with compressed context, and record the truncation in state.

## Template chaining

When a task spans multiple concerns:

1. run Phase 1 to completion
2. extract accepted claims and strongest surviving proof skeleton
3. pass only that forward
4. keep per-phase round files
5. show the full chain plan before starting

## Mandatory pre-execution steps

1. Show the plan to the user.
2. Show the selected template and why it was chosen.
3. For research templates, produce the Step 0 claim restatement.
4. Get explicit user confirmation.
5. Only then spawn agents.

## Quick reference

| Template | Parallel roles by round | Orchestrator verification | Final synthesis |
|----------|-------------------------|---------------------------|----------------|
| Lakatos | 4, 4 | no | Formalist or orchestrator |
| Polya | 3, 3 | no | orchestrator or 1 lead agent |
| Knuth | 3 | optional | orchestrator |
| Structured Research | 3, 3 | yes | referee or orchestrator |
| Graph Reconfig | 3, 3 | yes | referee or orchestrator |
| Lean Formalization | 3, 2 | optional | Checker or orchestrator |
