---
name: agent-group-discuss
description: Use when the user asks for a multi-agent discussion, panel review, multi-agent review, or multi-agent research session with role selection, round control, and template-based orchestration.
metadata:
  short-description: Multi-agent discussion, review, and research orchestration
---

# Agent Group Discuss

This imports the OpenClaw multi-agent discussion and research-review templates into Codex and adapts them to Codex agent tools.

## When to use

- group discussion
- panel of agents
- multiple agents with different roles
- multi-agent review
- review-only requests that explicitly ask for multiple agents or a panel
- multi-agent research
- named review templates such as Lakatos, Knuth, Pólya, Graph Reconfiguration Specialist, or Lean Formalization Team

If a request is review-only and does not ask for annotation, stay in this skill for the multi-agent path. Do not route that request to `annotated-review` unless the user also explicitly asks for annotation.

## Supporting files

Read these when relevant:

- `TEMPLATES.md` for the imported research and review templates
- `EXECUTION.md` for the Codex execution pattern and per-template round topology
- `MODEL_TIERS.md` for the live Codex model-tier catalog
- `MODEL_TIERS.example.md` only as a customization template
- `README.md` for the structured request shape

If the user requests a named template, or if the task clearly matches one, open `TEMPLATES.md` and `EXECUTION.md` before spawning any agents.

## Clarification policy

If the request is underspecified, ask only for the minimum needed.

Use this compact question when needed:

`Before I start: do you want discussion, review, or research? How many rounds? Any hard constraints? I can choose roles and models automatically if you want.`

If the user gives no preference, default to:

- mode: infer from task
- rounds: 2
- roles: 3
- interaction: auto
- role/model selection: automatic

## Routing to structured workflows

If the user explicitly wants a more structured, reproducible workflow, prefer `prose` instead.

If the user says:

- structured workflow
- use prose
- compile the workflow
- deterministic

offer `prose`. Otherwise stay in this skill.

## Confirmation gate

Before any actual multi-agent run:

1. produce the plan
2. show the plan to the user
3. get explicit confirmation
4. only then call `spawn_agent`

Do not infer consent from silence.
If the user already gave an explicit affirmative in the same request after seeing the plan, that counts.

## Codex tool mapping

OpenClaw concepts must be translated to Codex tools as follows:

- OpenClaw `sessions_spawn` -> Codex `spawn_agent`
- OpenClaw session follow-up -> Codex `send_input`
- OpenClaw session wait -> Codex `wait_agent`
- OpenClaw resume session -> Codex `resume_agent`
- OpenClaw terminate/cleanup -> Codex `close_agent`
- OpenClaw local execution -> Codex `functions.exec_command`
- OpenClaw proof or graph computation helpers -> Codex `sagemath`, `graph_verifier`, `formal_skeleton_helper`, or local `functions.exec_command`

## Codex execution model

- The main agent is the orchestrator.
- Use `spawn_agent` for each role.
- Use `multi_tool_use.parallel` when launching independent roles in the same round.
- Keep role prompts concrete and bounded.
- Run independent roles in parallel.
- Use `send_input` for later rounds when keeping a role agent alive is helpful.
- Use `wait_agent` once per round or per critical batch, not in a tight polling loop.
- Use `close_agent` after the run finishes or when a role is no longer useful.
- Prefer local synthesis unless a dedicated synthesizer role materially improves the result.

For any actual run, follow `EXECUTION.md` as the detailed orchestration reference.

## Codex-specific constraints

Codex cannot hard-restrict each spawned agent's tools the way OpenClaw can. Compensate by doing all of the following in the role prompt:

- scope the role narrowly
- forbid nested sub-agent spawning unless explicitly needed
- forbid file edits unless the role owns a specific write target
- ask for the exact output format needed for the current round
- pass only the minimum prior context needed

For reasoning-only roles, prefer `agent_type: "default"`.
Use `agent_type: "explorer"` only for tightly scoped codebase fact-finding roles.
Use `fork_context: true` only when the role genuinely needs the current thread context; otherwise prefer a compact summarized handoff.

## High-level goal

Given a task, you must:

1. classify the task
2. decide which roles are needed
3. decide how many subagents are useful
4. assign a model and reasoning effort to each role
5. choose how the subagents interact
6. run the requested number of rounds
7. synthesize the result
8. maintain durable recovery state on disk

## Input format

Accept either free-form text or a structured block such as:

```text
topic: <topic>
mode: discussion | review | research | mixed
rounds: <integer>
max_agents: <integer>
interaction: auto | star | debate | panel_judge
template: <optional template name>
output: <desired final output format>
constraints:
- <constraint>
- <constraint>
```

If the user specifies rounds, obey it.

## Role selection

Prefer 3 roles by default.
Use 4 or more only when the task clearly benefits.

Typical roles by mode:

### Discussion

- Optimist
- Skeptic
- Pragmatist
- Judge

### Review

- Correctness reviewer
- Edge-case reviewer
- Clarity reviewer
- Synthesizer

### Research

- Literature scout
- Hypothesis generator
- Critic / falsifier
- Synthesizer

## Model assignment

Each role gets a `model` and `reasoning_effort` through `spawn_agent`.
Refer to `MODEL_TIERS.md` for the live Codex model catalog and `EXECUTION.md` for per-template execution defaults.

### Reasoning level classification

| Level | Capability | Assign to |
|-------|-----------|-----------|
| R4 | Multi-step proofs, formal math, adversarial reasoning | theorem verification, PSPACE reductions, correctness critique |
| R3 | Strong structured reasoning | planning, synthesis, algorithm design, structured review |
| R2 | Solid general reasoning | edge-case review, specialist analysis, advocacy |
| R1 | Fast generation and summarization | scouting, brainstorming, clarity review |

### Profile selection

| Task signal | Profile | Lead tier |
|-------------|---------|-----------|
| formal proof, theorem, correctness verification, PSPACE, NP-hard | `math-heavy` | R4 |
| research paper review, algorithm design, critical decision | `premium` | R4 |
| general discussion, code review, exploration | `balanced` | R3 |
| quick sanity check, opinion gathering, lightweight summary | `budget` | R2 |

If the user specifies a profile, obey it.
If the user specifies a model for a specific role, override that role only.
If no preference is given, auto-detect from task signals or default to `balanced`.

### Research-task model rule

If the task is a research task, proof verification, mathematical correctness review, manuscript correctness review, or other high-stakes reasoning task:

- default to `math-heavy`
- use the highest reasoning model available for all spawned agents
- use maximum or near-maximum `reasoning_effort` for every role unless the user explicitly requests a cheaper mode

This rule overrides lower-cost defaults for research-mode runs.

### Tier assignment by role type

| Role type | Tier |
|-----------|------|
| planner, judge, synthesizer, critic, correctness reviewer, referee | STRONG_REASONER |
| advocate, specialist reviewer, edge-case reviewer, repair agent | BALANCED_MODEL |
| scout, brainstormer, pragmatist, clarity reviewer | FAST_MODEL |

Record the chosen profile, per-role model, and reasoning effort in `state.json`.

## Token management

Do not promise truncation cannot happen.
Keep prompts compact and summarize prior rounds before relaying them.

If a subagent response is truncated:

1. note the truncation in `state.json`
2. re-prompt the same role with compressed context
3. if needed, switch to a model with more headroom from the same or a stronger tier
4. never silently discard a truncated response

## Interaction design

Choose:

- `star`
- `debate`
- `panel_judge`

If the user requests one explicitly, obey it.

## Round control

If `rounds` is provided, obey it.
Otherwise:

- default to 2 rounds for discussion or review
- default to 2 or 3 rounds for research depending on complexity

Never exceed 5 rounds unless the user explicitly asks.

## Timeouts

Ordinary discussion or lightweight review: 10 minutes per round by default.
Complex correctness review or research verification: 45 minutes per agent by default, with persistent progress checkpoints every 15 minutes.

If a spawned role does not respond in time:

1. mark the role as timed out in `responses_received`
2. add a note to `pending_work`
3. continue the round with available responses
4. do not block the whole run on one unresponsive role

Total run timeout: 30 minutes by default for ordinary runs.
For complex correctness reviews, extend the total run budget as needed and keep writing progress checkpoints.
If exceeded, write the best available synthesis from completed rounds and mark the run incomplete.

## Durable state

Create a run folder under:

- `$HOME/.codex/runs/agent_group_discuss/<run_id>/`

Before spawning any role:

1. create the run directory
2. create a `lock` file with the current timestamp
3. write `plan.md`
4. write `state.json`
5. show the plan to the user
6. get explicit confirmation before executing

After each round, write:

- `round_01.md`
- `round_02.md`
- `round_P1_01.md` and similar names for chained phases when needed

After synthesis, write:

- `final.md`

For long correctness reviews, also write:

- `progress_15m.md`
- `progress_30m.md`
- later progress files at the same cadence as needed
- `final_report.md`

On success, failure, or pause, remove or update the lock state accordingly.

### Plan output format

The plan file and the user-facing plan summary must include:

```markdown
## Run Plan: <run_id>

**Topic:** <topic>
**Mode:** <mode>
**Profile:** <profile>
**Interaction:** <pattern>
**Rounds:** <N>
**Estimated total time:** <X-Y minutes>
**Estimated agent calls:** <parallel batches + follow-up batches + synthesis passes>

### Subagent assignments

| # | Role | Model | Reasoning | Effort | Est. time |
|---|------|-------|-----------|--------|-----------|
| 1 | Judge | gpt-5.4 | R4 | xhigh | 2-4 min |
| 2 | Correctness Reviewer | gpt-5.3-codex | R3 | high | 1-2 min |
| 3 | Edge-case Reviewer | gpt-5.4-mini | R2 | medium | 1-2 min |

### Execution plan

- Round 1: roles 1-3 run in parallel
- Round 2: rebuttal or synthesis pass

### Risk notes
- <token budget concerns>
- <roles that may need stronger models>
```

Time estimation:

- estimate per role from the chosen profile in `MODEL_TIERS.md`
- parallel roles use the slowest role in the batch
- add sequential synthesis time
- multiply by the number of rounds
- add 1-2 minutes for orchestration overhead

### State schema

Use a `state.json` structure like:

```json
{
  "run_id": "string",
  "topic": "string",
  "mode": "discussion | review | research",
  "profile": "math-heavy | premium | balanced | budget",
  "interaction": "star | debate | panel_judge",
  "template": "string or null",
  "roles": ["string"],
  "models": {
    "role_name": {
      "model": "gpt-5.4",
      "reasoning_level": "R4",
      "reasoning_effort": "xhigh"
    }
  },
  "rounds_requested": 2,
  "current_round": 0,
  "status": "planning | running | paused | completed | failed",
  "spawned_agents": {
    "role_name": "agent-id or null"
  },
  "responses_received": {
    "role_name": true
  },
  "pending_work": ["string"],
  "start_time": "ISO 8601 timestamp",
  "agent_timeout_minutes": 45,
  "estimated_duration_minutes": 10,
  "progress_reports_written": ["progress_15m.md"],
  "recovery_needed": false,
  "notes": ["string"]
}
```

## Spawning policy

The main agent is the orchestrator.
Prefer leaf agents spawned directly by the main agent.

Use the role prompt template and per-template execution plans from `EXECUTION.md` instead of improvising prompt structure ad hoc.

Each spawned task must include:

- the role
- the topic
- the role objective
- the expected response format
- the current round number
- only the minimum prior context needed

For opening statements, ask for:

- short position
- strongest argument
- one uncertainty or caveat

For later rounds, ask each role to:

- respond to the strongest counterpoint
- refine or defend its position
- provide one concession or one rebuttal

## Recovery behavior

If a result is missing or the run is disrupted:

1. read `state.json`
2. inspect which round files exist
3. identify missing roles from `responses_received`
4. set `recovery_needed: true`
5. if the old role agents still exist, use `resume_agent` or `send_input`
6. otherwise respawn only the missing roles with compressed context
7. never discard already completed rounds unless the user asks

If a role fails repeatedly, skip it and note the gap explicitly.

If the user pauses the run:

1. mark `status: "paused"`
2. note the reason in `notes`
3. do not spawn new agents
4. wait for in-flight agents to finish if practical
5. tell the user what remains

To resume:

1. read `state.json`
2. verify the run was paused or failed
3. re-open surviving agents if possible
4. continue from the last consistent checkpoint

## Research templates

The imported templates live in `TEMPLATES.md`.
Detailed execution plans live in `EXECUTION.md`.

The user can request a template by name, or the orchestrator can auto-select one.
If auto-selecting, briefly state which template was chosen and why before proceeding.
The user can override.

Template auto-selection:

| Task signal | Recommended template |
|-------------|---------------------|
| "verify my proof", "check this theorem", "stress-test", "find holes" | `Lakatos Proof and Refutation` |
| "attack this problem", "explore complexity", "is this hard or easy", "open problem" | `Polya Multi-Strategy Problem Solving` |
| "review my draft", "pre-submission review", "check exposition", "camera-ready" | `Knuth Structured Manuscript Review` |
| general math or TCS claim, algorithm analysis, combinatorial argument | `Structured Research Team` |
| token sliding, token jumping, gadget verification, reconfiguration, PSPACE reduction | `Graph Reconfiguration Specialist` |
| "formalize this lemma", "Lean proof", "fix this sorry", "formalization" | `Lean Formalization Team` |

If multiple templates match, prefer the more domain-specific one.

Template chaining is allowed when a task spans multiple concerns.
When chaining:

1. run one template per phase
2. pass only surviving claims and strongest proof skeletons forward
3. keep per-phase round files
4. state the planned chain before starting

## Mandatory rules for template runs

Before any template begins:

1. produce the plan output
2. restate the target claim in exact terms
3. list assumptions explicitly
4. separate what is given, to be proved, and conjectured
5. identify notation and definitions
6. obtain explicit user confirmation before spawning any role agents

Show this Step 0 restatement to the user before spawning agents for a high-stakes research template.

If a decisive counterexample or fatal gap is found:

1. stop defending the broken claim
2. switch to diagnosis
3. determine the strongest defensible corrected claim
4. do not keep expanding a broken proof across later rounds

## External verification in Codex

When a template calls for computational or formal checking, prefer:

- `sagemath` for heavy graph-theoretic or algebraic computation
- `graph_verifier` for lightweight graph checks
- `formal_skeleton_helper` for Lean-style scaffolding
- `functions.exec_command` for local scripts or test harnesses

Use the role-vs-orchestrator computation split from `EXECUTION.md` so verification stays independent when the template requires it.

If no external verification is possible, say so explicitly in the final ledger.

## Final output

Return a polished result with:

- Topic
- Mode
- Template used
- Roles used
- Models assigned
- Interaction pattern
- Rounds completed
- Main agreements
- Main disagreements
- Best points by role
- Final synthesis
- Recommended next step

Also include a compact run summary:

- run id
- profile used
- number of agents and their models
- reasoning efforts used
- rounds completed
- actual duration vs estimated duration
- whether recovery was needed
- whether any responses were truncated or models were swapped
- which progress checkpoint files were written
