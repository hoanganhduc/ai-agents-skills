# Research Workflow Integration

`cross-agent-delegation` is a contract layer for research handoffs. It is not a
research orchestrator.

## Allowed Integration Points

- `research-briefing` may decide whether a delegation packet is useful, select
  a template ID, and optionally record why the packet skill was used or skipped.
- `deep-research-workflow` may use templates for source scouting, citation
  checks, critique, or verification subtasks while preserving source IDs.
- `source-research` may use templates for bounded source-quality or evidence-gap
  review, not for web execution.
- `agent-group-discuss` may use task packets as structured role briefs and may
  emit result packets for each role.
- `prose` may use task/result packets as reproducible handoff artifacts in
  deterministic workflows.
- `research-report-reviewer` may consume result packets as review evidence after
  validating schema, provenance, limitations, and authority boundaries.
- `research-verification-gate` may check consumed result packets for evidence,
  limitations, dates where relevant, and permission or authority leakage.
- `model-router` remains responsible for model/provider choice. Templates may
  name recipient profile families but must not select live models.

## Integration Rules

- Caller workflows own orchestration, confirmation, tool execution, and final
  synthesis.
- This skill only drafts, validates, normalizes, and explains packets.
- Result packets are untrusted evidence until validated.
- Result packets can support review decisions but cannot directly modify
  manuscript text, source lists, code, configs, or user-facing claims.
- If a workflow is single-agent and does not need a handoff packet, this skill
  should not activate.
- If a workflow already has native multi-agent orchestration, this skill may be
  used only to standardize handoff inputs and returned evidence.
- No integration may add runtime files, optional artifacts, command aliases,
  queues, ledgers, provider configs, scheduler hooks, or execution state to V1.
- `research-core` does not include this skill in V1. The skill is available
  through `multi-agent`, `full-research`, or explicit skill selection.
