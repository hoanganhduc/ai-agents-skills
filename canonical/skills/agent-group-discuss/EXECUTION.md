# Codex Execution Guide

This file is the execution reference for running the six imported research templates with Codex agent tools.
Read this when the user triggers a multi-agent research or review run.
Template definitions live in `TEMPLATES.md`.

## Runtime rules

1. Before any `spawn_agent` call, show the plan and get explicit user confirmation.
2. For research, proof, manuscript-correctness, or other high-stakes review tasks, use the highest reasoning model available for all spawned agents by default.
3. For complex correctness reviews, default agent timeout is 45 minutes and persistent progress checkpoints are required every 15 minutes.
4. Default execution is foreground. Only run the panel as background work if the user explicitly wants to do other work while it runs.

## Model mapping

Resolve models through `MODEL_TIERS.md`.

Practical default mapping:

| Reasoning tier | Research default | Non-research baseline | Use for |
|----------------|------------------|-----------------------|---------|
| `R4` | `gpt-5.4` `xhigh` | `gpt-5.4` `high` | proofs, formal math, correctness verification, refereeing |
| `R3` | `gpt-5.4` `high` | `gpt-5.2` `high` | planning, synthesis, structured review |
| `R2` | `gpt-5.4` `medium` | `gpt-5.4-mini` `medium` | edge-case review, support analysis |
| `R1` | `gpt-5.4` `medium` | `gpt-5.3-codex-spark` `low` | scouting, brainstorming, clarity review |

## Execution pattern

### Launching role agents

Each role is a separate `spawn_agent` call.

Independent roles in the same round should be launched in parallel with `multi_tool_use.parallel`.

Example shape:

```text
spawn_agent({
  agent_type: "default",
  model: "gpt-5.4",
  reasoning_effort: "xhigh",
  fork_context: false,
  message: "<full role briefing>"
})
```

### Round structure

- Round 1 independent first pass: use fresh agents with no cross-role contamination.
- Later rounds: use `send_input` to the same role agent when continuity helps, or respawn fresh if independence or token hygiene matters more.
- Referee or synthesis roles run only after the prior round results are in.
- Compress prior results before relaying them. Keep only decisive findings, not full transcripts.

### Waiting and cleanup

- Use `wait_agent` once per round or per critical batch.
- Do not busy-poll.
- Use `close_agent` after the run or when a role is no longer needed.
- If a role agent needs to be revived, use `resume_agent` before reusing it.

## Role prompt template

Every role agent should receive a self-contained prompt with this structure:

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
| 1 | Prover | `gpt-5.4` `xhigh` | No |
| 2 | Counterexample Hunter | `gpt-5.4` `xhigh` | SageMath |
| 3 | Monster-Barrer / Refiner | `gpt-5.4` `high` | No |
| 4 | Formalist | `gpt-5.4` `xhigh` | No |

Execution:

- Round 1: 4 parallel role agents
- Round 2: 4 parallel role follow-ups with compressed Round 1 findings
- Round 3: 1 Formalist synthesis pass or local synthesis if clearly better

### 2. Polya Multi-Strategy Problem Solving

Profile: `math-heavy` by research override
Rounds: `3`
Roles: `3`

| # | Role | Model | Computation |
|---|------|-------|-------------|
| 1 | Specializer | `gpt-5.4` `xhigh` | SageMath |
| 2 | Generalizer | `gpt-5.4` `xhigh` | No |
| 3 | Reducer | `gpt-5.4` `xhigh` | No |

Execution:

- Round 1: 3 parallel role agents
- Round 2: 3 parallel role follow-ups after orchestrator cross-pollinates decisive findings
- Round 3: local synthesis or 1 lead synthesis agent

### 3. Knuth Structured Manuscript Review

Profile: `math-heavy` for mathematical manuscript review, otherwise `premium`
Rounds: `2`
Roles: `3`

| # | Role | Model | Computation |
|---|------|-------|-------------|
| 1 | Correctness Reviewer | `gpt-5.4` `xhigh` | SageMath when claims are computationally checkable |
| 2 | Exposition Reviewer | `gpt-5.4` `high` | No |
| 3 | Literature Reviewer | `gpt-5.4` `high` | No |

Execution:

- Round 1: 3 parallel independent reviews
- Round 2: orchestrator synthesis into a prioritized action list

### 4. Structured Research Team

Profile: `math-heavy`
Rounds: `3 + conditional 4`
Roles: `4`

| # | Role | Model | Computation |
|---|------|-------|-------------|
| 1 | Builder | `gpt-5.4` `xhigh` | No |
| 2 | Breaker | `gpt-5.4` `xhigh` | SageMath |
| 3 | Alternative Builder | `gpt-5.4` `xhigh` | No |
| 4 | Referee / Verifier | `gpt-5.4` `xhigh` | synthesis only |

Execution:

- Round 1: 3 parallel independent role agents
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
| 1 | Constructor | `gpt-5.4` `xhigh` | No |
| 2 | Adversary | `gpt-5.4` `xhigh` | SageMath |
| 3 | Auditor | `gpt-5.4` `xhigh` | No |
| 4 | Referee / Verifier | `gpt-5.4` `xhigh` | synthesis only |

Execution:

- Round 1: 3 parallel independent role agents
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
| 1 | Informal Planner | `gpt-5.4` `xhigh` | No |
| 2 | Formalizer | `gpt-5.4` `xhigh` | local Lean or scaffold work |
| 3 | Missing-Lemma Miner | `gpt-5.4` `high` | No |
| 4 | Repair Agent | `gpt-5.4` `high` | No |
| 5 | Checker | `gpt-5.4` `xhigh` | No |

Execution:

- Round 1: 3 parallel role agents
- Round 2: 2 parallel role agents
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

State updates:

- set `status: "running"` before each round launch
- update `responses_received` immediately after each result arrives
- write the round file as soon as all expected responses for that round are in
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
3. identify missing roles from `responses_received`
4. if role agents still exist, use `resume_agent` or `send_input`
5. otherwise respawn only the missing roles with compressed context
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
