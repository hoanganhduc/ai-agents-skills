# OpenGauss handoff schemas

## Intake → OpenGauss (`opengauss.intake_handoff.v1`)

Fields: `claim_id`, `formalization_decision`, `informal_statement_ref`, `target_project_root`, `allowed_workflows`, `no_claim_support=true`.

Emit via `handoff-intake` helper.

## OpenGauss → strict gate (`opengauss.gate_handoff.v1`)

Fields: `run_id`, `project_root`, `workflow`, `gauss_exit`, `no_claim_support=true`, `next_gate=lean-strict-verification-gate`.

Emit via `handoff-gate` helper.

## Evidence row (`opengauss_run`)

Required by deep-research validator: `tool_name=opengauss`, `run_id`, `workflow`, `result_status`, `input_encoding_ref`, `payload_hash`, limitations stating provenance-only / not formal_check.
