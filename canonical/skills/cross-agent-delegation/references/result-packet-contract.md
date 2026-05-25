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
  "coverage_scope": "bounded scope inspected; exclusions stated in limitations",
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

Result validation must also reject forbidden JSON object keys recursively at
any nesting level, including inside arrays of findings, evidence, artifacts,
warnings, errors, provenance, and `parent_action_request`.

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

Result validation must reject secret-like string values, including values
matching `sk-...`, `ghp_...`, `github_pat_...`, `AKIA[0-9A-Z]{16}`,
`-----BEGIN ... PRIVATE KEY-----`, or `Bearer <token-like value>`.

Runtime budget state, resolved model policy, provider configuration, session
IDs, credentials, live logs, and raw environment state belong only in
parent-owned runbook artifacts. Result packets may reference parent-created
artifact refs, but they must not carry those values directly.

## Field Tables

Result object additions

| Field | Type | Notes |
| --- | --- | --- |
| `coverage_scope` | string | Describes the bounded scope actually inspected and any high-level exclusions. It does not certify completeness or parent acceptance. |

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
| `evidence_disposition` | string, optional | Example: `supports_finding`, `contradicts_finding`, `context_only`, `limited`, or `unchecked`. Descriptive only. |
| `disposition_rationale` | string, optional | Short reason for the evidence disposition. |

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

Runtime probe logs, raw CLI commands, stdout, stderr, timeout traces, service
identifiers, session IDs, and environment snapshots must stay in parent-owned
run artifacts. Result packets may reference those artifacts only through inert
artifact refs after the parent creates the refs out of band.

`created_by`, `produced_by`, `intended_recipient`, and provenance labels are
self-asserted descriptive labels only. They never authenticate origin, approval,
trust level, or execution authority.
