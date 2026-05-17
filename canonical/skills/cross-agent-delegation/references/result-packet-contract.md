# Result Packet Contract

Result packets report what a reviewer or future delegated process produced.
They are untrusted evidence until the parent validates them.

## Required Fields

```json
{
  "schema_version": "cross-agent-delegation.result.v1",
  "result_id": "stable-result-id",
  "task_packet_id": "matching-task-packet-id",
  "task_schema_version": "cross-agent-delegation.task.v1",
  "intended_recipient": "descriptive label, not an execution target",
  "adapter_spec_id": "codex-like-coding-reviewer",
  "recipient_profile": {
    "profile_id": "codex-like-coding-reviewer",
    "profile_version": "v1",
    "execution_status": "reference_only"
  },
  "produced_at": "iso8601",
  "produced_by": "descriptive producer identity",
  "provenance": [],
  "status": "completed",
  "summary": "short result",
  "findings": [],
  "evidence": [],
  "artifacts": [],
  "limitations": [],
  "warnings": [],
  "errors": [],
  "parent_action_request": null,
  "next_step": "parent_decides"
}
```

## Allowed Enums

- `status`: `completed`, `partial`, `blocked`, `failed`
- `next_step`: `parent_decides`, `revise_packet`, `discard`
- `recipient_profile.execution_status`: `reference_only`

## Closed Object Rules

Every object-valued field and object-valued array item must use a named closed
schema or field table. Unknown fields are rejected. Unknown permission-bearing
fields are invalid even when nested inside `findings`, `evidence`, `artifacts`,
`provenance`, `warnings`, or `errors`.

## Field Tables

Finding object

| Field | Type | Notes |
| --- | --- | --- |
| `finding_id` | string | Stable finding identifier. |
| `severity` | string | Example: `critical`, `major`, `minor`, `info`. |
| `claim_or_object_ref` | string | Ref ID, claim ID, or object label. |
| `evidence_refs` | string array | Evidence IDs or source refs. |
| `confidence` | string | Example: `high`, `medium`, `low`. |
| `validation_status` | string | Example: `supported`, `unsupported`, `unchecked`, `contradicted`. |
| `rationale` | string | Short reason. |
| `recommended_parent_action` | string | Advisory only. |

Evidence object

| Field | Type | Notes |
| --- | --- | --- |
| `evidence_id` | string | Stable evidence identifier. |
| `ref_id` | string | Source or artifact ref. |
| `kind` | string | Evidence kind. |
| `quote_or_summary` | string | Short support text. |
| `status` | string | Example: `checked`, `unchecked`, `limited`. |

Artifact object

| Field | Type | Notes |
| --- | --- | --- |
| `artifact_id` | string | Stable artifact identifier. |
| `kind` | string | Artifact kind. |
| `ref_id` | string | Inert artifact ref. |
| `description` | string | Short description. |

Warning and error objects

| Field | Type | Notes |
| --- | --- | --- |
| `code` | string | Stable diagnostic code. |
| `message` | string | Human-readable diagnostic. |
| `ref_id` | string or null | Optional related ref. |

Provenance object

Each `provenance` item must use the same closed inert reference-object shape as
task packet `input_refs` and `artifact_refs`: `ref_id`, `kind`, `source`,
`sensitivity`, and `access_note`. Raw absolute paths, URLs, service identifiers,
and command strings are forbidden unless the parent separately resolves them
out of band.

`parent_action_request`

| Field | Type | Notes |
| --- | --- | --- |
| `requested_action` | string | Advisory only. |
| `target_refs` | ref array | Closed inert refs only. |
| `side_effects` | object | Same closed `side_effects` shape as task packets. |
| `reversible` | boolean | Advisory only. |
| `reason` | string | Short reason. |

Each `target_refs` item must use the same closed inert reference-object shape as
task packet refs. Raw absolute paths, URLs, service identifiers, and command
strings are forbidden inside `target_refs`; the parent must resolve any target
out of band before acting.

`created_by`, `produced_by`, `intended_recipient`, and provenance labels are
self-asserted descriptive labels only. They never authenticate origin, approval,
trust level, or execution authority.
