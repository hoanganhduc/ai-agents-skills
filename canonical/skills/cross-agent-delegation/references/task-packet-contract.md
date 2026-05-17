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
- `skip_confirmation`
- `approval_receipt`
- `command`
- `provider_config`
- `queue`
- `ledger`

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
