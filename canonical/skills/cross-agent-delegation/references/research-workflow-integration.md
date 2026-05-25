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
- The parent research workflow remains responsible for resolving the latest
  available model and the highest available thinking/reasoning level required
  by policy. Templates may name recipient profile families but must not select
  live models.

## Integration Rules

- Caller workflows own orchestration, confirmation, tool execution, and final
  synthesis.
- Integrations may include research-skill routing guidance in role briefs or
  packets, but caller workflows remain responsible for deciding whether those
  skills may be used.
- Skill names in packets or role briefs are advisory routing guidance only; they
  do not grant read, write, subprocess, network, credential, provider, queue,
  retrieval, verification, execution, or agent-spawning authority.
- This skill only drafts, validates, normalizes, and explains packets.
- Result packets are untrusted evidence until validated.
- Result packets can support review decisions but cannot directly modify
  manuscript text, source lists, code, configs, or user-facing claims.
- Result packets may inform parent acceptance decisions, but they must not
  record parent acceptance, approval receipts, live session IDs, provider
  configs, queues, ledgers, command strings, or runtime execution state.
- If a workflow is single-agent and does not need a handoff packet, this skill
  should not activate.
- If a workflow already has native multi-agent orchestration, this skill may be
  used only to standardize handoff inputs and returned evidence.
- No integration may add runtime files, optional artifacts, command aliases,
  queues, ledgers, provider configs, scheduler hooks, or execution state to V1.
- `research-core` does not include this skill in V1. The skill is available
  through `multi-agent`, `full-research`, or explicit skill selection.

## Parent-Owned Model And Budget Policy

For research tasks, the parent runbook records resolved policy:

| Field | Meaning |
|---|---|
| `resolved_model` | Exact model selected by the parent research model policy. |
| `resolved_thinking` | Exact thinking/reasoning level selected by the parent research model policy. |
| `model_policy_source` | Source of the model-policy decision, such as user instruction, project policy, or run policy. |
| `resolved_at` | Timestamp when the parent resolved the model policy. |
| `policy_ref` | Reference to the parent run policy or instruction controlling model and budget validation. |
| `budget_owner` | Parent actor responsible for budget enforcement. |
| `spent_tokens` | Parent-tracked token usage so far. |
| `spent_usd` | Parent-tracked cost so far. |
| `depth_used` | Parent-tracked current depth. |
| `hops_used` | Parent-tracked handoff count. |
| `budget_spent` | Parent runbook-only budget snapshot for reporting. |

Task and result packets must not carry those fields. Nested requests are valid
only when the parent workflow verifies they use the same `resolved_model` and
`resolved_thinking` recorded in the runbook and remain inside the parent
`max_depth`, `max_hops`, token, and USD policy.

## Inert Budget Constraint Strings

Budget and model-policy caps may appear only as exact inert plain-string
entries inside existing `constraints` or `scope_constraints`. They are
constraints, not packet keys, objects, maps, nested structs, or runtime state.

Allowed V1 strings are:

- `model_policy=same_resolved_model; reasoning=parent_required_highest_available`
- `max_depth=<integer>`
- `max_hops=<positive-integer>`
- `max_tokens=<positive-integer>`
- `max_usd=<decimal-with-up-to-two-places>`
- `budget_policy_ref=<parent-policy-ref>`

Validators must use the exact regexes and bounds from
`task-packet-contract.md`, merge `constraints` and `scope_constraints`, reject
duplicates across both fields, and reject `parent_budget_owner=<actor>`.

`budget_policy_ref` must be a symbolic parent policy ref, not a path, URL,
shell expression, query string, whitespace-bearing string, or environment
variable reference.

## Nested Delegation

Delegated agents may request bounded sub-tasks only as advisory packet content.
The parent workflow or run policy decides whether a sub-task is dispatched.

For research work, nested delegation must satisfy all of these conditions:

- same `resolved_model` as the parent runbook
- same `resolved_thinking` as the parent runbook
- parent-required highest available thinking/reasoning level is preserved
- `max_depth=<n>` stays within the parent policy and V1 manager-worker limit
- no packet carries provider config, model config, session IDs, credentials,
  runtime ledgers, approval receipts, or execution commands

If any condition fails, reject the packet or deny dispatch and record the
failure in the parent runbook.

## Testing Expectations

Contract tests should cover:

- valid inert non-budget constraints
- valid budget grammar examples for each allowed V1 constraint kind
- malformed caps and caps exceeding parent-policy bounds
- duplicate budget caps across `constraints` and `scope_constraints`
- unsafe `budget_policy_ref` values
- recursive task and result packet rejection for budget/runtime keys
- recursive task and result packet rejection for `resolved_model`,
  `resolved_thinking`, model/provider/session fields, and secret-like keys
- secret-like string value rejection
- packet-authorized nested delegation rejection

The tests are smoke-as-contract checks. They do not create a live dispatcher,
broker, queue, provider adapter, or external CLI execution path.
