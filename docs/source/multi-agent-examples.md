# Multi-Agent Examples And Templates

This page describes how the experimental multi-agent layer is intended to work
in this personal research setup. It is optimized for combinatorics, graph
theory, mathematical writing, and related research workflows. It may not behave
as desired on every agent frontend or model version.

The shared skills involved are:

| Skill | Use |
|---|---|
| `agent-group-discuss` | Template-based multi-agent discussion, review, and research. |
| `prose` | More explicit OpenProse-style decomposition, parallel work, and synthesis. |
| `sagemath` | Optional graph theory, algebra, enumeration, and invariant checks. |
| `graph-verifier` | Lightweight graph sanity checks. |
| `cross-agent-delegation` | Closed packet contracts for parent-controlled handoffs; it does not execute or broker agents. |
| `research-verification-gate` | Final evidence and gap check before delivery. |

Codex has a native `spawn_agent` orchestration model. Claude and DeepSeek get
the same templates and adapter instructions, but their actual process control
depends on the frontend and installed tools. When a frontend cannot spawn
separate agents directly, the templates still serve as a disciplined role and
round protocol for manual or sequential execution.

True cross-provider delegation is parent-owned by `agent-group-discuss`.
`manifest/delegation.yaml` sets the default policy: include Codex as the parent
and spawned-subagent provider; prefer Claude, DeepSeek, Copilot, Antigravity, and Grok when fresh
probes pass; keep OpenClaw reference-only; fall back to Codex-only when
configured; and require latest-model plus highest-thinking for research roles.
This policy is general across supported target-agent installs. External CLI
process launch is handled by the parent-owned `delegate-agent` adapter, not by
the inert packet contract. Target agents receive shared guidance and templates;
live dispatch is still run-specific, probe-gated, and confirmation-controlled.

Dry-run an external provider plan with:

```bash
make delegate-agent ARGS="--provider auto --task-file ./task.md --research --dry-run"
```

Actual launch requires `--allow-external-cli`. Research launch also requires a
resolved latest-model and highest-thinking setting. Most providers also require
an explicit dispatch command, for example `AAS_CLAUDE_DISPATCH_COMMAND`,
`AAS_CLAUDE_LATEST_MODEL`, and `AAS_CLAUDE_HIGHEST_THINKING`. Antigravity
dispatch is CLI-based through `agy --print`; it does not require
`ANTIGRAVITY_LS_ADDRESS`. Grok dispatch is CLI-based through
`grok --prompt-file /dev/stdin` (the prompt is delivered on stdin; grok's
`--single` needs an argv value and does not read stdin) and uses an interactive
OIDC session rather than an API-key environment variable. Grok may use automatic
selection for research because the resolved model gates the route. The
dispatcher first parses only exact anchored available-model rows from bare
`grok models`. On POSIX, model probes, remote readiness checks, and actual Grok
children use umask `0077` so Grok-created cache files remain private. Exact
membership selects bare Grok and pins `--model`;
only bare CLI absence or non-confirmation authorizes the region-correct `grok-remote`
fallback. Without a resolved model, automatic selection stays bare and does not
authorize the proxy. Generic provider prechecks are bare-only and never
version-probe an automatically discovered proxy. Explicit `AAS_GROK` and
`AAS_GROK_DISPATCH_COMMAND` overrides remain authoritative and are not silently
replaced. The dispatcher
invokes a selected proxy route-neutrally: it neither sets `GROK_MULTI_SESSION`
nor adds route flags. A bare proxy command uses its active managed profile. A
parent readiness probe must validate that profile and its model/release
identities before concurrent dispatch. The dispatcher runs
`grok-remote doctor --json`, accepts only an exact
`grok-remote.profile-status.v1` result in `ready` or `degraded` state, and
requires its `model_id` to match the resolved model. Blocked, unconfigured,
invalid, inconsistent, and timed-out probes fail closed; private topology is
not recorded. An exact `--help` feature check prevents older proxies from
receiving `doctor` as ordinary Grok input.

## Orchestration Lifecycle

A normal multi-agent run follows this shape:

1. **Classify the request.** Decide whether the task is discussion, review,
   research, proof stress-testing, manuscript review, graph reconfiguration, or
   formalization.
2. **Select a template.** Choose the most specific matching template and state
   why it was chosen.
3. **Show the plan first.** List roles, models or reasoning tiers, round order,
   verification steps, expected artifacts, and time assumptions.
4. **Wait for confirmation.** Multi-agent execution should not start until the
   user confirms the plan.
5. **Spawn bounded role agents.** The orchestrator launches independent roles
   for the current round. Each role gets a narrow prompt, clear output format,
   and no file-write authority unless it owns a specific write target.
6. **Collect round outputs.** The orchestrator waits once per round or critical
   batch, compresses decisive findings, and records the state.
7. **Cross-pollinate only after Round 1.** Later rounds receive a compressed
   summary of the strongest findings, objections, and unresolved claims.
8. **Run independent verification.** Where useful, the orchestrator runs
   SageMath, graph checks, source checks, or local tests instead of trusting
   role opinions alone.
9. **Synthesize locally or with a referee.** The final answer separates
   accepted, rejected, unresolved, and unverified claims.
10. **Close or recover agents.** Completed role agents are closed. Interrupted
    runs resume from state rather than rerunning completed rounds.

## Spawn And Round Handling

For Codex-style execution, the mapping is:

| Concept | Process |
|---|---|
| Launch role | `spawn_agent` with a concrete role prompt. |
| Launch parallel roles | Multiple independent `spawn_agent` calls in the same round. |
| Continue a role | `send_input` with compressed prior findings. |
| Wait for outputs | `wait_agent` once per round or per critical batch. |
| Recover interrupted role | `resume_agent` when a prior agent must continue. |
| Finish role | `close_agent` after the role is no longer needed. |
| External verification | Orchestrator runs local tools directly, then feeds verified facts into synthesis. |

For research tasks, every parent role, delegated manager, and child worker must
use the latest available model with the highest available thinking or reasoning
level. Nested workers are allowed only for explicitly planned manager roles,
must use the manager's same provider/model/thinking level, and must remain leaf
workers.

Role prompts should include:

- template and role name
- exact task or claim
- round number and round-specific instructions
- prior-round summary when applicable
- required output format
- tool permissions and write boundaries
- hard rules for evidence, uncertainty, and fatal gaps

## Available Templates

| Template | Best for | Default shape |
|---|---|---|
| Lakatos Proof and Refutation | Stress-testing a theorem or proof draft. | 4 roles, 3 rounds, debate. |
| Polya Multi-Strategy Problem Solving | Exploring an open problem or complexity boundary. | 3 roles, 3 rounds, star topology. |
| Knuth Structured Manuscript Review | Reviewing a mathematical paper draft. | 3 roles, 2 rounds, panel synthesis. |
| Structured Research Team | General high-stakes claim, proof, algorithm, or characterization review. | 4 roles, 3 rounds plus optional repair. |
| Graph Reconfiguration Specialist | Token sliding, token jumping, gadgets, reductions, PSPACE/NP-hardness, graph-class preservation. | 4 roles, 3 rounds plus optional repair. |
| Lean Formalization Team | Turning a proved lemma into a Lean scaffold or debugging a formal proof. | 5 roles, 2 rounds. |
| Prose / OpenProse-style workflow | Reproducible decomposition with explicit tracks and artifacts. | Variable tracks, parallel where independent. |

Template chaining is allowed when the task naturally has phases. For example,
a graph reconfiguration reduction can use Graph Reconfiguration Specialist
first, then Knuth Structured Manuscript Review after the proof is stable.

## True Cross-Provider Delegation

Cross-provider runs use three layers:

1. `agent-group-discuss` selects providers, probes capability profiles, and
   owns execution.
2. `cross-agent-delegation` supplies inert task and result packet contracts.
3. `deep-research-workflow` preserves source IDs and evidence mapping.

The default policy is `prefer`: use Codex plus real non-Codex providers when
enough fresh profiles satisfy the research model policy, otherwise disclose
Codex-only fallback. `require` mode can be used later to block runs that cannot
satisfy the provider threshold.

## Example: Graph Theory Proof Stress-Test

User request:

```text
Use a multi-agent panel to stress-test my proof that every graph in class C has
property P under token sliding.
```

Likely process:

1. Select **Lakatos Proof and Refutation** if the main goal is proof attack, or
   **Graph Reconfiguration Specialist** if gadgets and state graphs are central.
2. Show a plan with Prover or Constructor, Counterexample Hunter or Adversary,
   Monster-Barrer or Auditor, and Formalist or Referee.
3. Spawn independent Round 1 role agents.
4. Let the counterexample role use SageMath or graph checks when a finite search
   is meaningful.
5. Run Round 2 with compressed objections and proposed repairs.
6. Return a ledger of accepted, rejected, unresolved, and weakened claims.

Typical final output:

- strongest surviving theorem statement
- proof steps that survived
- hidden assumptions found
- smallest counterexample candidates, if any
- verification limits
- recommended next proof repair

## Example: Graph Reconfiguration Reduction Audit

User request:

```text
Check whether this PSPACE-hardness reduction for token jumping is sound.
```

Likely process:

1. Select **Graph Reconfiguration Specialist**.
2. Split the work into Constructor, Adversary, Auditor, and Referee.
3. Track separate claims for local gadget behavior, soundness, completeness,
   noninterference, graph-class preservation, and polynomial size.
4. Run local verification for small gadgets when possible.
5. Stop defending the original proof if a decisive counterexample is found.

The important distinction is that prose polishing does not happen until the
construction is stable. Correctness comes first.

## Example: Mathematical Manuscript Review

User request:

```text
Run a multi-agent review of this draft before submission.
```

Likely process:

1. Select **Knuth Structured Manuscript Review**.
2. Spawn Correctness Reviewer, Exposition Reviewer, and Literature Reviewer.
3. Ask each reviewer for section-level findings with severity and concrete
   fixes.
4. Merge overlaps into one prioritized action list.

Typical final output:

- critical correctness issues
- significant exposition problems
- missing or questionable citations
- minor issues
- optional cosmetic suggestions

## Example: Open Problem Exploration

User request:

```text
Use multiple agents to explore whether this graph problem is likely fixed-
parameter tractable or hard.
```

Likely process:

1. Select **Polya Multi-Strategy Problem Solving**.
2. Spawn Specializer, Generalizer, and Reducer.
3. Specializer studies restricted cases and small examples.
4. Generalizer searches for known techniques and neighboring dichotomies.
5. Reducer proposes plausible hardness sources and gadget outlines.
6. The final synthesis ranks approaches by promise and expected difficulty.

## Example: Lean Formalization Handoff

User request:

```text
Use a formalization team to turn this lemma into a Lean skeleton.
```

Likely process:

1. Select **Lean Formalization Team**.
2. Spawn Informal Planner, Formalizer, and Missing-Lemma Miner in Round 1.
3. Spawn Repair Agent and Checker in Round 2.
4. Separate mathematical gaps from formalization friction.

The output should say whether the skeleton is complete, blocked by missing
lemmas, or revealing a real gap in the informal proof.

## When To Prefer Prose

Use `prose` instead of `agent-group-discuss` when the user asks for a more
reproducible workflow, explicit tracks, or a reusable process. Good examples:

- source gathering plus independent verification plus synthesis
- comparing two approaches with separate advocates
- producing durable intermediate artifacts
- decomposing a long research task into named phases

`prose` is still an adapter here. It describes the workflow and maps it to the
available agent tools; it is not a bundled OpenProse virtual machine.

Related pages: [Workflow Overview](workflow-overview.md), [Skills](skills.md),
[Profiles](profiles.md), [Verification](verification.md).
