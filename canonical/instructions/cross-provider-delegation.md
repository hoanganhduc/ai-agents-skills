# Cross-Provider Delegation

Use this guidance when a multi-agent research run should involve more than one
agent provider or runner family.

This is a general repository policy for supported target agents and systems.
Installed target-agent files get the same guidance and templates. Live external
dispatch is available through the parent-owned `delegate-agent` adapter, but it
still happens only inside an orchestrated run after run-specific probes and
confirmation.

## Policy

- For research tasks, always use the latest available model with the highest
  available thinking or reasoning level for every parent, manager, and worker.
- Treat Codex as the parent runtime and as an active provider for spawned
  subagents.
- Treat Claude, DeepSeek, and Copilot as active external providers only after
  fresh capability probes pass.
- Treat OpenClaw as reference-only until a separate native execution safety
  gate approves it.
- Prefer installed templates for repeatable delegation plans.
- Fall back to Codex-only only when the configured mode allows it, and disclose
  the fallback reason.

## Required Probes

Before dispatching to an external provider, record a fresh capability profile
for the current run:

- CLI or handoff availability
- auth/config availability by status only
- latest model selection support
- highest thinking or reasoning selection support
- smoke prompt
- output contract and final marker
- timeout behavior
- file-read fidelity when local files are part of the task
- same-model nested worker support when manager-worker delegation is requested

Do not store raw provider commands, credentials, stdout, stderr, provider
config, session IDs, or raw prompts in cross-agent packets.

## Managed Dispatch

Use `./installer/bootstrap.sh delegate-agent` for external CLI participants.
Default to `--dry-run` while planning. Actual external process launch requires
`--allow-external-cli`.

For research roles, dispatch is blocked unless the provider has:

- an explicit dispatch command such as `AAS_CLAUDE_DISPATCH_COMMAND`
- a resolved latest model from `--resolved-model` or `AAS_<PROVIDER>_LATEST_MODEL`
- a resolved highest thinking/reasoning setting from `--resolved-thinking` or
  `AAS_<PROVIDER>_HIGHEST_THINKING`
- a passing smoke/output-contract probe

The dispatcher writes parent-owned run artifacts under
`.ai-agents-skills/delegation-runs/<run-id>/` and returns parsed, validated
results to the orchestrator.

## Nested Delegation

Nested delegation is allowed only for manager roles when the parent run plan
explicitly enables it.

- Maximum depth is one manager-worker layer below the parent.
- Child workers must use the same provider, resolved model, and thinking level
  as their manager.
- If same-model child dispatch cannot be confirmed, the manager should return
  proposed child task packets for the parent orchestrator to dispatch.
- Child workers must not spawn further agents.

## Packet Boundary

Use `cross-agent-delegation.task.v1` for task handoffs and
`cross-agent-delegation.result.v1` style output for returned evidence.
Packets are inert contracts. They do not grant filesystem access, network
access, credentials, subprocess authority, provider routing, or user approval.
