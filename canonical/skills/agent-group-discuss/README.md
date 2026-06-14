# agent-group-discuss

This is the Codex-adapted multi-agent discussion and review orchestrator.

Before a real run, also read:

- `EXECUTION.md` for the Codex execution pattern
- `MODEL_TIERS.md` for the live role-to-model mapping
- `TEMPLATES.md` for named research and review templates

Use it through normal language or a structured request.

Example:

```text
topic: Should we use retrieval or long-context for internal docs?
mode: research
rounds: 3
max_agents: 4
interaction: panel_judge
output: decision memo
constraints:
- keep it practical
- compare reliability, cost, and complexity
```

Optional participant block:

```text
participants:
- id: judge-1
  kind: codex_spawned
  role: Judge
  failure_policy: fail_closed
- id: cli-checker-1
  kind: external_cli
  role: Edge-case Reviewer
  capability_profile: artifact:profiles/cli-checker-1.json
  output_contract: parseable_envelope
```

Named-template example:

```text
topic: Review this draft before submission
mode: review
template: Knuth Structured Manuscript Review
rounds: 2
constraints:
- prioritize correctness over style
- produce a prioritized fix list
```

The orchestrator should show a plan before running. For actual multi-agent
runs, the orchestrator must still ask for explicit confirmation before
launching participants.
