---
name: lean-axle-adapter
description: Use when designing an offline-only AXLE integration boundary for Lean formalization workflows without live calls or credential lookup.
---

# Lean AXLE Adapter

This is an offline-only adapter surface. It can generate a no-op dry-run
request contract from strict-gate-passing Lean intake artifacts, but it does
not call AXLE, mutate MCP config, start servers, import external code, or read
credentials.

## Runtime Commands

POSIX:

```bash
bash ~/.codex/runtime/run_skill.sh skills/lean-axle-adapter/run_lean_axle_adapter.sh <repo> --gate-json gate.json --intake-json intake.json --output-json axle-dry-run.json
```

Windows:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/lean-axle-adapter/run_lean_axle_adapter.bat" <repo> --gate-json gate.json --intake-json intake.json --output-json axle-dry-run.json
```

## Workflow

1. Run `lean-formalization-intake`.
2. Run `lean-strict-verification-gate`.
3. Use this adapter only when the strict gate output has `gate_status: pass`
   and no warnings.
4. Emit JSON as the primary output. Markdown summaries are optional and must be
   generated from the same JSON.
5. Treat the result as `T1_AXLE_NOOP_DRY_RUN` only. It is not T2 AXLE
   acceptance, theorem-intent review, or proof verification.

## Hard Boundaries

- No default endpoint activation.
- No implicit package install or import side effect.
- No MCP config mutation.
- No background server.
- No credential lookup.
- No live AXLE calls; dry-run output must report `would_call_axle: false`.
- No copied AXLE templates or external-code-derived content.
- No claim that AXLE, Claude, Copilot, DeepSeek, SafeVerify, MCP, or OpenClaw
  executed from packet contracts, reference docs, or external CLI guidance.

## Dry-Run Contract

The runtime helper may record:

- strict-gate source id and commit
- request mode `noop_dry_run`
- empty endpoint and credential allowlists
- `mcp_config_mutation: false`
- `background_server: false`
- `network_access: false`
- hashes of intake-reported `problem.lean` and `solution.lean` files
- theorem-intent review status `not_reviewed`

The helper must fail closed if the strict gate failed, has warnings, or the
intake and gate source ids disagree.

## Candidate Future Live Contract

Future runtime work must record:

- explicit endpoint allowlist
- explicit credential environment-variable allowlist
- request and response hashes
- bounded redacted diagnostics only
- AXLE tool version
- Lean toolchain and Mathlib revision
- formal statement hash
- theorem-intent review status

T2 means AXLE accepted the formal statement as written. It does not imply that
the formal statement matches the informal theorem. T2 does not imply theorem-intent match.

## Default Status

Current live-AXLE status: disabled by construction.

Runtime behavior: incomplete analysis.
