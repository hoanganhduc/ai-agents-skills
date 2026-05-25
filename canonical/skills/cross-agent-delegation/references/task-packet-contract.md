# Task Packet Contract

Task packets describe intended work for a parent-controlled handoff. They are
not execution authority.

## Required Fields

```json
{
  "schema_version": "cross-agent-delegation.task.v1",
  "packet_id": "stable-id",
  "created_at": "iso8601",
  "created_by": "descriptive producer label",
  "intended_recipient": "descriptive label, not an execution target",
  "adapter_spec_id": "codex-like-coding-reviewer",
  "recipient_profile": {
    "profile_id": "codex-like-coding-reviewer",
    "profile_version": "v1",
    "execution_status": "reference_only"
  },
  "recipient_capability_snapshot": {},
  "intent": "bounded objective",
  "requested_actions": [],
  "side_effects": {
    "writes_files": false,
    "external_service_posts": false,
    "network_calls": false,
    "subprocesses": false
  },
  "success_criteria": [],
  "constraints": [],
  "provenance": [],
  "input_refs": [],
  "artifact_refs": [],
  "scope_constraints": [],
  "out_of_scope": [],
  "context_policy": {
    "forward_raw_chat": false,
    "forward_system_instructions": false,
    "summary_context_refs": [],
    "context_refs_to_include": [],
    "context_refs_to_exclude": []
  },
  "confirmation_requirement": "parent_decides_outside_packet",
  "expected_output": {},
  "evidence_requirements": [],
  "failure_policy": "block",
  "audit_notes": []
}
```

## Allowed Enums

- `confirmation_requirement`: `parent_decides_outside_packet`,
  `parent_confirmation_required`
- `failure_policy`: `block`, `partial_allowed`, `ask_parent`
- `recipient_profile.execution_status`: `reference_only`

## Closed Object Rules

Every object-valued field and object-valued array item must use a named closed
schema or field table. Unknown fields are rejected.

Unknown permission-bearing fields are always invalid, including:

- `confirmed_by_parent`
- `execute`
- `execution_target`
- `execution_targets`
- `skip_confirmation`
- `approval_receipt`
- `approval_receipts`
- `command`
- `commands`
- `args`
- `cwd`
- `env`
- `environment_variables`
- `provider_config`
- `provider_configs`
- `model_config`
- `model_configs`
- `queue`
- `queues`
- `ledger`
- `session_id`
- `session_ids`
- `resume_token`
- `resume_tokens`
- `budget_envelope`
- `runtime_budget`
- `budget_owner`
- `budget_spent`
- `spent_tokens`
- `spent_usd`
- `depth_used`
- `hops_used`
- `resolved_model`
- `resolved_thinking`
- `model_policy_source`
- `resolved_at`
- `policy_ref`
- `model`
- `provider`
- `reasoning`
- `thinking`
- `api_base`
- `secret`
- `secrets`
- `api_key`
- `apikey`
- `access_token`
- `refresh_token`
- `password`
- `credential`
- `credentials`
- `private_key`
- `ssh_key`

## Research Budget And Model Constraints

Research delegation may use static, parent-owned caps only as inert plain
strings inside existing `constraints` or `scope_constraints`. Packets must not
add budget, model, provider, session, runtime-state, or approval fields.

Closed V1 constraint grammar:

| Constraint kind | Only allowed string or regex | Parent-policy validation |
|---|---|---|
| `model_policy` | `^model_policy=same_resolved_model; reasoning=parent_required_highest_available$` | Nested work must use the parent runbook's exact `resolved_model` and `resolved_thinking`. |
| `max_depth` | `^max_depth=([0-9]+)$` | Parsed integer must satisfy `0 <= n <= parent_policy.max_depth`; V1 also requires `n <= 1` below the parent. |
| `max_hops` | `^max_hops=([1-9][0-9]*)$` | Parsed integer must satisfy `1 <= n <= parent_policy.max_hops`. |
| `max_tokens` | `^max_tokens=([1-9][0-9]*)$` | Parsed integer must satisfy `1 <= n <= parent_policy.max_tokens`. |
| `max_usd` | `^max_usd=([0-9]+)(\\.[0-9]{1,2})?$` | Parsed decimal must satisfy `0 <= amount <= parent_policy.max_usd`; compare as decimal, not binary float. |
| `budget_policy_ref` | `^budget_policy_ref=[A-Za-z][A-Za-z0-9_.-]{0,63}(#[A-Za-z][A-Za-z0-9_.-]{0,63})?$` | Ref must resolve to the parent run policy already recorded in the runbook. |

Validation merges `constraints` and `scope_constraints` before checking
duplicates. At most one entry for each constraint kind may appear across both
fields. Reject duplicates even when values are identical.

`parent_budget_owner=<actor>` is not valid packet content. Budget ownership
belongs only in the parent runbook as `budget_owner`.

Reject unsafe `budget_policy_ref` values containing path traversal, URLs,
whitespace, shell metacharacters, query strings, or environment-variable
syntax.

## Recursive Packet Safety

Task validation must reject forbidden JSON object keys recursively at any
nesting level, including inside `recipient_capability_snapshot`,
`requested_actions`, `expected_output`, `metadata`-like objects, and arrays of
objects.

Reject these key classes:

- Runtime budget/state exact keys: `budget_envelope`, `runtime_budget`,
  `budget_owner`, `max_depth`, `max_hops`, `max_tokens`, `max_usd`,
  `budget_spent`, `spent_tokens`, `spent_usd`, `depth_used`, and `hops_used`.
- Runtime budget/state key regexes: `^max_.*`, `^spent_.*`, `^budget_.*`,
  `^depth_.*`, and `^hops_.*`.
- Policy-resolution and model-config exact keys: `resolved_model`,
  `resolved_thinking`, `model_policy_source`, `resolved_at`, `policy_ref`,
  `model_policy`, `model`, `provider`, `reasoning`, `thinking`, `api_base`,
  and `session_id`.
- Policy-resolution and model-config key regexes: `^resolved_.*`,
  `^model_.*`, `^provider_.*`, and `.*session.*`.
- Secret-like exact keys: `secret`, `secrets`, `api_key`, `apikey`,
  `access_token`, `refresh_token`, `password`, `credential`, `credentials`,
  `private_key`, and `ssh_key`.
- Secret-like key regex, case-insensitive:
  `(^|[_-])(api[_-]?key|secret|token|password|credential|private[_-]?key|ssh[_-]?key)([_-]|$)`.

This key-pattern rejection applies to JSON keys, not to allowed inert strings
inside `constraints` or `scope_constraints`. Constraint string values are
checked by the grammar above.

Task validation must also reject secret-like string values, including values
matching `sk-...`, `ghp_...`, `github_pat_...`, `AKIA[0-9A-Z]{16}`,
`-----BEGIN ... PRIVATE KEY-----`, or `Bearer <token-like value>`.

Nested delegation may be requested only as a bounded advisory task. The packet
must not authorize child dispatch, carry model/provider/session configuration,
or grant approval for further delegation by itself.

## Field Tables

`recipient_profile`

| Field | Type | Notes |
| --- | --- | --- |
| `profile_id` | string | Must equal `adapter_spec_id`. |
| `profile_version` | string | V1 uses `v1`. |
| `execution_status` | string | Must be `reference_only`. |

`side_effects`

| Field | Type | Notes |
| --- | --- | --- |
| `writes_files` | boolean | Descriptive only. |
| `external_service_posts` | boolean | Descriptive only. |
| `network_calls` | boolean | Descriptive only. |
| `subprocesses` | boolean | Descriptive only. |

`context_policy`

| Field | Type | Notes |
| --- | --- | --- |
| `forward_raw_chat` | boolean | Must remain false in V1. |
| `forward_system_instructions` | boolean | Must remain false in V1. |
| `summary_context_refs` | ref array | Inert summary/excerpt refs. |
| `context_refs_to_include` | ref array | Minimization hints, not ACLs. |
| `context_refs_to_exclude` | ref array | Minimization hints, not ACLs. |

Reference object

| Field | Type | Notes |
| --- | --- | --- |
| `ref_id` | string | Stable local identifier. |
| `kind` | string | Example: `draft`, `source`, `claim`, `dataset`. |
| `source` | string | Symbolic source label, not raw access permission. |
| `sensitivity` | string | Example: `public`, `private`, `restricted`. |
| `access_note` | string | How the parent may resolve it out of band. |

Controlled freeform fields may contain strings, numbers, booleans, arrays, or
objects, but must not contain prohibited permission-bearing keys. This applies
to `recipient_capability_snapshot`, `expected_output`, `requested_actions`,
`provenance`, and similar evidence or description fields.

## Confirmation Rules

If any side-effect field is true, `confirmation_requirement` must be
`parent_confirmation_required`. The packet still does not confirm the action.
The parent session must verify confirmation outside the packet before any future
executor acts.

`scope_constraints` and `out_of_scope` describe intended boundaries. They do
not authorize reads, writes, subprocesses, network calls, or service actions.
