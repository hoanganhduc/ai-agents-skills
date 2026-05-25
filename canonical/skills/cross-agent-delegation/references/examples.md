# Examples

Each JSON fixture is preceded by a canonical metadata marker:

`<!-- fixture: id=<unique-id> kind=<task|result> valid=<true|false> errors=<comma-separated-error-codes-or-none> -->`

## Valid Inert Task

<!-- fixture: id=valid-inert-task kind=task valid=true errors=none -->
```json
{
  "schema_version": "cross-agent-delegation.task.v1",
  "packet_id": "pkt-valid-inert-001",
  "created_at": "2026-05-16T00:00:00Z",
  "created_by": "parent-session",
  "intended_recipient": "codex-like reviewer",
  "adapter_spec_id": "codex-like-coding-reviewer",
  "recipient_profile": {
    "profile_id": "codex-like-coding-reviewer",
    "profile_version": "v1",
    "execution_status": "reference_only"
  },
  "recipient_capability_snapshot": {},
  "intent": "Review a bounded implementation plan for contradictions.",
  "requested_actions": [
    "review"
  ],
  "side_effects": {
    "writes_files": false,
    "external_service_posts": false,
    "network_calls": false,
    "subprocesses": false
  },
  "success_criteria": [
    "Findings identify exact section refs."
  ],
  "constraints": [
    "Do not edit files."
  ],
  "provenance": [],
  "input_refs": [
    {
      "ref_id": "plan-section-a",
      "kind": "plan_excerpt",
      "source": "artifact:plan-section-a",
      "sensitivity": "public",
      "access_note": "Parent supplies excerpt out of band."
    }
  ],
  "artifact_refs": [],
  "scope_constraints": [
    "Review only supplied excerpts."
  ],
  "out_of_scope": [
    "Live execution."
  ],
  "context_policy": {
    "forward_raw_chat": false,
    "forward_system_instructions": false,
    "summary_context_refs": [],
    "context_refs_to_include": [],
    "context_refs_to_exclude": []
  },
  "confirmation_requirement": "parent_decides_outside_packet",
  "expected_output": {
    "format": "result_packet",
    "required_sections": [
      "findings",
      "limitations"
    ]
  },
  "evidence_requirements": [
    "Each finding cites a provided ref."
  ],
  "failure_policy": "block",
  "audit_notes": []
}
```

## Invalid Missing Confirmation For Side Effect

<!-- fixture: id=invalid-side-effect-confirmation kind=task valid=false errors=SIDE_EFFECT_REQUIRES_CONFIRMATION -->
```json
{
  "schema_version": "cross-agent-delegation.task.v1",
  "packet_id": "pkt-invalid-side-effect-001",
  "created_at": "2026-05-16T00:00:00Z",
  "created_by": "parent-session",
  "intended_recipient": "codex-like reviewer",
  "adapter_spec_id": "codex-like-coding-reviewer",
  "recipient_profile": {
    "profile_id": "codex-like-coding-reviewer",
    "profile_version": "v1",
    "execution_status": "reference_only"
  },
  "recipient_capability_snapshot": {},
  "intent": "Classify a proposed edit.",
  "requested_actions": [
    "review"
  ],
  "side_effects": {
    "writes_files": true,
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
  "expected_output": {
    "format": "result_packet"
  },
  "evidence_requirements": [],
  "failure_policy": "block",
  "audit_notes": []
}
```

## Invalid Overbroad Input

<!-- fixture: id=invalid-overbroad-input kind=task valid=false errors=OVERBROAD_REF -->
```json
{
  "schema_version": "cross-agent-delegation.task.v1",
  "packet_id": "pkt-invalid-overbroad-001",
  "created_at": "2026-05-16T00:00:00Z",
  "created_by": "parent-session",
  "intended_recipient": "claude-like reviewer",
  "adapter_spec_id": "claude-like-research-reviewer",
  "recipient_profile": {
    "profile_id": "claude-like-research-reviewer",
    "profile_version": "v1",
    "execution_status": "reference_only"
  },
  "recipient_capability_snapshot": {},
  "intent": "Review all available context.",
  "requested_actions": [
    "review"
  ],
  "side_effects": {
    "writes_files": false,
    "external_service_posts": false,
    "network_calls": false,
    "subprocesses": false
  },
  "success_criteria": [],
  "constraints": [],
  "provenance": [],
  "input_refs": [
    {
      "ref_id": "everything",
      "kind": "workspace",
      "source": "entire_workspace",
      "sensitivity": "restricted",
      "access_note": "Overbroad and not resolvable as a bounded ref."
    }
  ],
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
  "expected_output": {
    "format": "result_packet"
  },
  "evidence_requirements": [],
  "failure_policy": "block",
  "audit_notes": []
}
```

## Invalid Runtime Dispatch

<!-- fixture: id=invalid-runtime-dispatch kind=task valid=false errors=UNKNOWN_FIELD,FORBIDDEN_AUTHORITY_FIELD -->
```json
{
  "schema_version": "cross-agent-delegation.task.v1",
  "packet_id": "pkt-invalid-dispatch-001",
  "created_at": "2026-05-16T00:00:00Z",
  "created_by": "parent-session",
  "intended_recipient": "codex-like reviewer",
  "adapter_spec_id": "codex-like-coding-reviewer",
  "recipient_profile": {
    "profile_id": "codex-like-coding-reviewer",
    "profile_version": "v1",
    "execution_status": "reference_only"
  },
  "recipient_capability_snapshot": {},
  "intent": "Attempt runtime dispatch.",
  "requested_actions": [
    "review"
  ],
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
  "expected_output": {
    "format": "result_packet"
  },
  "evidence_requirements": [],
  "failure_policy": "block",
  "audit_notes": [],
  "execution_target": "live-agent"
}
```

## Valid Partial Result

<!-- fixture: id=valid-partial-result kind=result valid=true errors=none -->
```json
{
  "schema_version": "cross-agent-delegation.result.v1",
  "result_id": "result-partial-001",
  "task_packet_id": "pkt-valid-inert-001",
  "task_schema_version": "cross-agent-delegation.task.v1",
  "intended_recipient": "codex-like reviewer",
  "adapter_spec_id": "codex-like-coding-reviewer",
  "recipient_profile": {
    "profile_id": "codex-like-coding-reviewer",
    "profile_version": "v1",
    "execution_status": "reference_only"
  },
  "produced_at": "2026-05-16T00:05:00Z",
  "produced_by": "descriptive-reviewer-label",
  "provenance": [
    {
      "ref_id": "plan-section-a",
      "kind": "plan_excerpt",
      "source": "artifact:plan-section-a",
      "sensitivity": "public",
      "access_note": "Parent supplied excerpt out of band."
    }
  ],
  "status": "partial",
  "summary": "One issue found; remaining scope was not inspected.",
  "coverage_scope": "Only the supplied plan-section-a excerpt was inspected; no broader repository or runtime state was checked.",
  "findings": [
    {
      "finding_id": "F1",
      "severity": "minor",
      "claim_or_object_ref": "plan-section-a",
      "evidence_refs": [
        "plan-section-a"
      ],
      "confidence": "medium",
      "validation_status": "unchecked",
      "rationale": "Only a supplied excerpt was available.",
      "recommended_parent_action": "Inspect the full section before applying."
    }
  ],
  "evidence": [
    {
      "evidence_id": "E1",
      "ref_id": "plan-section-a",
      "kind": "excerpt",
      "quote_or_summary": "The excerpt mentions a bounded review.",
      "status": "checked",
      "evidence_disposition": "supports_finding",
      "disposition_rationale": "The excerpt is the only supplied basis for finding F1."
    }
  ],
  "artifacts": [],
  "limitations": [
    "Full artifact was not supplied."
  ],
  "warnings": [],
  "errors": [],
  "parent_action_request": null,
  "next_step": "parent_decides"
}
```

## Invalid Result With Runtime Authority Fields

<!-- fixture: id=invalid-result-runtime-authority kind=result valid=false errors=FORBIDDEN_AUTHORITY_FIELD,FORBIDDEN_MODEL_POLICY_FIELD,UNKNOWN_FIELD -->
```json
{
  "schema_version": "cross-agent-delegation.result.v1",
  "result_id": "result-invalid-runtime-001",
  "task_packet_id": "pkt-valid-inert-001",
  "task_schema_version": "cross-agent-delegation.task.v1",
  "intended_recipient": "codex-like reviewer",
  "adapter_spec_id": "codex-like-coding-reviewer",
  "recipient_profile": {
    "profile_id": "codex-like-coding-reviewer",
    "profile_version": "v1",
    "execution_status": "reference_only"
  },
  "produced_at": "2026-05-16T00:06:00Z",
  "produced_by": "descriptive-reviewer-label",
  "provenance": [],
  "status": "completed",
  "summary": "Attempted to report runtime authority in a packet.",
  "coverage_scope": "Supplied excerpt only.",
  "findings": [
    {
      "finding_id": "F-runtime",
      "severity": "info",
      "claim_or_object_ref": "plan-section-a",
      "evidence_refs": [],
      "confidence": "low",
      "validation_status": "unchecked",
      "rationale": "Runtime fields are not valid packet data.",
      "recommended_parent_action": "Reject packet."
    }
  ],
  "evidence": [
    {
      "evidence_id": "E-runtime",
      "ref_id": "artifact:runtime-log",
      "kind": "runtime_log_ref",
      "quote_or_summary": "Raw runtime logs must stay in parent-owned artifacts.",
      "status": "limited",
      "participant_probe_status": "passed"
    }
  ],
  "artifacts": [
    {
      "artifact_id": "A-runtime",
      "kind": "runtime_log",
      "ref_id": "artifact:runtime-log",
      "description": "Inert ref only.",
      "session_id": "session-123"
    }
  ],
  "limitations": [],
  "warnings": [],
  "errors": [],
  "parent_action_request": null,
  "next_step": "parent_decides"
}
```

## Valid Research Template Task

<!-- fixture: id=valid-research-template-task kind=task valid=true errors=none -->
```json
{
  "schema_version": "cross-agent-delegation.task.v1",
  "packet_id": "pkt-research-template-001",
  "created_at": "2026-05-16T00:10:00Z",
  "created_by": "parent-session",
  "intended_recipient": "claude-like research reviewer",
  "adapter_spec_id": "claude-like-research-reviewer",
  "recipient_profile": {
    "profile_id": "claude-like-research-reviewer",
    "profile_version": "v1",
    "execution_status": "reference_only"
  },
  "recipient_capability_snapshot": {},
  "intent": "Check citation support for provided claim refs.",
  "requested_actions": [
    "citation-integrity-check"
  ],
  "side_effects": {
    "writes_files": false,
    "external_service_posts": false,
    "network_calls": false,
    "subprocesses": false
  },
  "success_criteria": [
    "Each claim receives a status."
  ],
  "constraints": [
    "Use only supplied source refs."
  ],
  "provenance": [],
  "input_refs": [
    {
      "ref_id": "S1",
      "kind": "source",
      "source": "source:S1",
      "sensitivity": "public",
      "access_note": "Parent supplied source excerpt."
    },
    {
      "ref_id": "C1",
      "kind": "claim",
      "source": "claim:C1",
      "sensitivity": "public",
      "access_note": "Parent supplied claim text and locator."
    }
  ],
  "artifact_refs": [],
  "scope_constraints": [
    "Preserve source IDs and claim IDs."
  ],
  "out_of_scope": [
    "External source retrieval."
  ],
  "context_policy": {
    "forward_raw_chat": false,
    "forward_system_instructions": false,
    "summary_context_refs": [],
    "context_refs_to_include": [],
    "context_refs_to_exclude": []
  },
  "confirmation_requirement": "parent_decides_outside_packet",
  "expected_output": {
    "format": "result_packet",
    "template_id": "citation-integrity-check",
    "evidence_status_values": [
      "supported",
      "unsupported",
      "unchecked",
      "contradicted"
    ]
  },
  "evidence_requirements": [
    "Return evidence refs for every supported or contradicted claim."
  ],
  "failure_policy": "partial_allowed",
  "audit_notes": [
    "Unchecked sources remain unchecked."
  ]
}
```

## Invalid Research Handoff

<!-- fixture: id=invalid-research-handoff kind=task valid=false errors=RAW_FORWARDING,UNVERIFIED_SOURCE_CLAIM -->
```json
{
  "schema_version": "cross-agent-delegation.task.v1",
  "packet_id": "pkt-invalid-research-001",
  "created_at": "2026-05-16T00:15:00Z",
  "created_by": "parent-session",
  "intended_recipient": "model-only reviewer",
  "adapter_spec_id": "model-only-api-reviewer",
  "recipient_profile": {
    "profile_id": "model-only-api-reviewer",
    "profile_version": "v1",
    "execution_status": "reference_only"
  },
  "recipient_capability_snapshot": {},
  "intent": "Verify all sources using raw chat and source claims.",
  "requested_actions": [
    "literature-scout-review"
  ],
  "side_effects": {
    "writes_files": false,
    "external_service_posts": false,
    "network_calls": false,
    "subprocesses": false
  },
  "success_criteria": [
    "Claim that sources were verified."
  ],
  "constraints": [],
  "provenance": [],
  "input_refs": [
    {
      "ref_id": "lead-1",
      "kind": "unverified_lead",
      "source": "unverified_lead:lead-1",
      "sensitivity": "public",
      "access_note": "Lead only; not retrieved."
    }
  ],
  "artifact_refs": [],
  "scope_constraints": [],
  "out_of_scope": [],
  "context_policy": {
    "forward_raw_chat": true,
    "forward_system_instructions": false,
    "summary_context_refs": [],
    "context_refs_to_include": [],
    "context_refs_to_exclude": []
  },
  "confirmation_requirement": "parent_decides_outside_packet",
  "expected_output": {
    "format": "result_packet",
    "forbidden_claim": "all candidate sources were searched and verified"
  },
  "evidence_requirements": [
    "Evidence refs are required for source verification claims."
  ],
  "failure_policy": "block",
  "audit_notes": []
}
```

## Valid Research Budget Constraints

<!-- fixture: id=valid-research-budget-constraints kind=task valid=true errors=none -->
```json
{
  "schema_version": "cross-agent-delegation.task.v1",
  "packet_id": "pkt-valid-budget-001",
  "created_at": "2026-05-24T00:00:00Z",
  "created_by": "parent-session",
  "intended_recipient": "codex-like reviewer",
  "adapter_spec_id": "codex-like-coding-reviewer",
  "recipient_profile": {
    "profile_id": "codex-like-coding-reviewer",
    "profile_version": "v1",
    "execution_status": "reference_only"
  },
  "recipient_capability_snapshot": {},
  "intent": "Review supplied research evidence without live execution.",
  "requested_actions": [
    "evidence-synthesis-critique"
  ],
  "side_effects": {
    "writes_files": false,
    "external_service_posts": false,
    "network_calls": false,
    "subprocesses": false
  },
  "success_criteria": [
    "Every finding cites supplied evidence refs."
  ],
  "constraints": [
    "model_policy=same_resolved_model; reasoning=parent_required_highest_available",
    "max_depth=1",
    "max_hops=2"
  ],
  "provenance": [],
  "input_refs": [
    {
      "ref_id": "S1",
      "kind": "source",
      "source": "source:S1",
      "sensitivity": "public",
      "access_note": "Parent supplies excerpt out of band."
    }
  ],
  "artifact_refs": [],
  "scope_constraints": [
    "max_tokens=50000",
    "max_usd=25.00",
    "budget_policy_ref=researchPolicy#default"
  ],
  "out_of_scope": [
    "Live provider dispatch."
  ],
  "context_policy": {
    "forward_raw_chat": false,
    "forward_system_instructions": false,
    "summary_context_refs": [],
    "context_refs_to_include": [],
    "context_refs_to_exclude": []
  },
  "confirmation_requirement": "parent_decides_outside_packet",
  "expected_output": {
    "format": "result_packet"
  },
  "evidence_requirements": [
    "Supported pass or warn findings require evidence refs."
  ],
  "failure_policy": "block",
  "audit_notes": [
    "Budget state stays in the parent runbook."
  ]
}
```

## Invalid Packet Budget Owner

<!-- fixture: id=invalid-packet-budget-owner kind=task valid=false errors=BUDGET_CONSTRAINT_INVALID -->
```json
{
  "schema_version": "cross-agent-delegation.task.v1",
  "packet_id": "pkt-invalid-budget-owner-001",
  "created_at": "2026-05-24T00:01:00Z",
  "created_by": "parent-session",
  "intended_recipient": "codex-like reviewer",
  "adapter_spec_id": "codex-like-coding-reviewer",
  "recipient_profile": {
    "profile_id": "codex-like-coding-reviewer",
    "profile_version": "v1",
    "execution_status": "reference_only"
  },
  "recipient_capability_snapshot": {},
  "intent": "Attempt to move parent budget ownership into a packet.",
  "requested_actions": [
    "review"
  ],
  "side_effects": {
    "writes_files": false,
    "external_service_posts": false,
    "network_calls": false,
    "subprocesses": false
  },
  "success_criteria": [],
  "constraints": [
    "parent_budget_owner=child-agent"
  ],
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
  "expected_output": {
    "format": "result_packet"
  },
  "evidence_requirements": [],
  "failure_policy": "block",
  "audit_notes": []
}
```

## Invalid Packet-Authorized Nested Delegation

<!-- fixture: id=invalid-packet-authorized-nested-delegation kind=task valid=false errors=FORBIDDEN_AUTHORITY_FIELD,FORBIDDEN_MODEL_POLICY_FIELD -->
```json
{
  "schema_version": "cross-agent-delegation.task.v1",
  "packet_id": "pkt-invalid-nested-dispatch-001",
  "created_at": "2026-05-24T00:02:00Z",
  "created_by": "parent-session",
  "intended_recipient": "codex-like reviewer",
  "adapter_spec_id": "codex-like-coding-reviewer",
  "recipient_profile": {
    "profile_id": "codex-like-coding-reviewer",
    "profile_version": "v1",
    "execution_status": "reference_only"
  },
  "recipient_capability_snapshot": {},
  "intent": "Attempt to authorize a child dispatch inside packet content.",
  "requested_actions": [
    {
      "kind": "subtask",
      "execution_target": "child-agent",
      "resolved_model": "gpt-example"
    }
  ],
  "side_effects": {
    "writes_files": false,
    "external_service_posts": false,
    "network_calls": false,
    "subprocesses": false
  },
  "success_criteria": [],
  "constraints": [
    "model_policy=same_resolved_model; reasoning=parent_required_highest_available",
    "max_depth=1"
  ],
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
  "expected_output": {
    "format": "result_packet"
  },
  "evidence_requirements": [],
  "failure_policy": "block",
  "audit_notes": []
}
```
