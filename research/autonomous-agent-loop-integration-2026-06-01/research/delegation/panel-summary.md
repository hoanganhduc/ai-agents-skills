# Multi-Agent Panel Summary

Run date: 2026-06-01

Template: Structured Research Team adapted for workflow planning.

Participants:

- Workflow Architect
- Safety and Failure-Mode Critic
- Target Adapter Specialist
- Research Workflow Integrator

## Consensus

1. Use `autonomous-research-loop` as the v1 canonical skill name.
2. Keep `autonomous-agent-loop` and `continuous-agent-loop` only as aliases or discovery text, not as the primary v1 scope.
3. V1 must be a bounded research-loop controller, not a free-running agent launcher.
4. Runtime helpers should be offline-only: scaffold/init, append iteration, validate, status, and selftest.
5. Live provider CLI dispatch must remain parent-owned through existing `agent-group-discuss` / `delegate-agent` pathways, with explicit confirmation and runtime probes.
6. Provider support claims must be asymmetric:
   - Codex: reference skill adapter and Codex-facing guidance.
   - Claude: symlink/native-command-friendly surface where supported.
   - Copilot: personal adapter/profile only; no repo-level `.github/*` writes in v1.
   - DeepSeek: DeepSeek API plus CodeWhale harness caveats; no native DeepSeek skill-loader claim.

## V1 Loop Modes

- `scout-loop`: bounded source discovery and candidate `S*` records.
- `analysis-loop`: claim/evidence refinement and guard updates.
- `review-repair-loop`: draft review, parent-approved repair, and recheck.
- `panel-loop`: prepare and validate parent-owned `agent-group-discuss` artifacts; no runtime-executed multi-agent dispatch.

## Required Runtime State

Minimum artifacts:

- `loop_state.json`
- `iterations.jsonl`
- `budget.json`
- `recovery.md`
- refs to deep-research `sources.jsonl`, `claims.jsonl`, `guards.jsonl`, `delivery.json`, and optional evidence/delegation artifacts.

Minimum iteration fields:

- `iteration_id`
- `mode`
- `objective`
- `input_refs`
- `actions_taken`
- `outputs`
- `source_ids_added_or_changed`
- `claim_ids_added_or_changed`
- `evidence_ids_added_or_changed`
- `guard_refs`
- `budget_snapshot_ref`
- `decision`
- `stop_reason`
- `remaining_gaps`

## Safety Requirements

- Require max iterations, wall time, token/USD budget, max depth, max hops, plateau rule, and blocking-gap behavior before work starts.
- Stop on budget exhaustion, policy denial, stale provider profile, probe failure, missing final marker, output parse failure, truncation, missing evidence, or blocking guard failure.
- Never copy runtime authority, commands, environment, provider config, model config, session IDs, credentials, or approval receipts into delegation packets.
- Treat panel and external outputs as unchecked evidence until parent validation maps them to claim/evidence records.

## Open Decisions

- Whether `panel-loop` belongs in the MVP. Panel consensus allows it only as a handoff/ledger mode, not as runtime dispatch.
- Exact profile membership. Suggested: `workflow-tools`, `serious-research`, `full-research`, and possibly `multi-agent` if `panel-loop` is included.
- Whether to create optional personas/entrypoints in the first implementation or defer them after the core skill/runtime is stable.
