# Recipient Profiles

Recipient profiles are reference-only adapter specifications. They describe
the shape of a packet recipient, not a live CLI, API, SDK, MCP server, tool
loop, or configured provider.

All V1 profiles have `execution_status: reference_only`.

Runtime CLI capability profiles, probes, command flags, raw logs, session IDs,
and provider-specific execution observations are outside this contract. A
parent orchestrator such as `agent-group-discuss` may maintain those artifacts
out of band and reference only inert artifacts from task or result packets.

## Profiles

### codex-like-coding-reviewer

- intended recipient family: Codex-like coding or planning reviewer
- supported packet versions: `cross-agent-delegation.task.v1`,
  `cross-agent-delegation.result.v1`
- accepted inputs: inert code refs, plan refs, issue refs, summary refs
- expected outputs: result packet with findings, evidence, limitations, warnings,
  and errors
- unsupported task classes: live execution, shell commands, repo mutation,
  credential use, external posting
- symbolic credential requirements: none in V1
- confirmation requirements: parent-owned, outside packet content

### claude-like-research-reviewer

- intended recipient family: Claude-like research or long-context reviewer
- supported packet versions: `cross-agent-delegation.task.v1`,
  `cross-agent-delegation.result.v1`
- accepted inputs: inert paper, excerpt, proof, claim, source, and synthesis refs
- expected outputs: evidence-grounded result packet
- unsupported task classes: live retrieval, hidden memory access, tool execution,
  credential use, external posting
- symbolic credential requirements: none in V1
- confirmation requirements: parent-owned, outside packet content

### deepseek-like-model-reviewer

- intended recipient family: DeepSeek-like model-only reviewer
- supported packet versions: `cross-agent-delegation.task.v1`,
  `cross-agent-delegation.result.v1`
- accepted inputs: minimized prompt-safe refs and summaries
- expected outputs: result packet with explicit limitations
- unsupported task classes: local tools, workspace reads, shell commands,
  provider probing, credential use, external posting
- symbolic credential requirements: none in V1
- confirmation requirements: parent-owned, outside packet content

This packet profile is reference-only. A parent workflow such as
`agent-group-discuss` may route to a live CodeWhale or DeepSeek-like CLI only
after fresh capability probes satisfy the run policy.

### copilot-like-code-reviewer

- intended recipient family: Copilot-like code or repository workflow reviewer
- supported packet versions: `cross-agent-delegation.task.v1`,
  `cross-agent-delegation.result.v1`
- accepted inputs: inert repository, file, diff, issue, and source-summary refs
- expected outputs: evidence-grounded result packet with code or workflow
  findings, limitations, warnings, and blocked checks
- unsupported task classes: direct repo mutation, command execution, credential
  use, external posting, provider probing, or approval handling
- symbolic credential requirements: none in V1
- confirmation requirements: parent-owned, outside packet content

This packet profile does not claim Copilot runtime availability. A parent
workflow must verify CLI, auth/config status, model selection, output contract,
and file-read fidelity before using a live Copilot-like participant.

### model-only-api-reviewer

- intended recipient family: generic model-only reviewer
- supported packet versions: `cross-agent-delegation.task.v1`,
  `cross-agent-delegation.result.v1`
- accepted inputs: minimized summaries and inert refs
- expected outputs: result packet with limitations and evidence references
- unsupported task classes: tool use, file access, command execution, runtime
  dispatch, credential use, external posting
- symbolic credential requirements: none in V1
- confirmation requirements: parent-owned, outside packet content

### openclaw-host-reference

- intended recipient family: OpenClaw interoperability reference
- supported packet versions: `cross-agent-delegation.task.v1`,
  `cross-agent-delegation.result.v1`
- accepted inputs: reference-only notes, not native install artifacts
- expected outputs: descriptive result packet only
- unsupported task classes: OpenClaw native install target support, real
  `.openclaw` writes, runtime helpers, shell hooks, provider config, queues,
  ledgers, or execution state
- symbolic credential requirements: none in V1
- confirmation requirements: parent-owned, outside packet content

OpenClaw is not a V1 `supported_agents` target. If explicitly requested by an
installer plan, V1 must fail closed or skip according to installer policy.
