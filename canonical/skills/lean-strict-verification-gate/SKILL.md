---
name: lean-strict-verification-gate
description: Use when checking Lean formalization intake JSON for strict, proof-neutral preflight readiness before any Lean, AXLE, or verifier run.
---

# Lean Strict Verification Gate

Use this skill after `lean-formalization-intake` has produced JSON for an
explicit local Lean repository.

This gate is a static preflight only. It fails closed unless the intake record
has enough repository metadata to justify a later checked verifier phase.

## Runtime Commands

POSIX:

```bash
bash ~/.codex/runtime/run_skill.sh skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh <repo> --intake-json intake.json --output-json gate.json
```

Windows:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/lean-strict-verification-gate/run_lean_strict_verification_gate.bat" <repo> --intake-json intake.json --output-json gate.json
```

## Workflow

1. Run `lean-formalization-intake` first.
2. Pass the same explicit local repo path plus the generated intake JSON to
   this gate.
3. Treat `gate_status: pass` as only `T1_STATIC_PREFLIGHT_READY`.
4. Run actual proof tooling only in a later phase with separately recorded
   evidence.

## Pass Conditions

Default pass requires:

- valid `lean-formalization-intake.v1` contract
- no proof-validity overclaim in the intake JSON
- detected `lean-toolchain`
- detected `lake-manifest.json`
- reported Mathlib revision
- detected `lakefile.toml` or `lakefile.lean`
- detected `problem.lean` and `solution.lean`
- detected source materials
- detected license
- detected CI workflow
- no redacted sensitive material
- no do-not-run command surfaces
- no unchecked intake gaps

Use `--allow-missing-ci` or `--allow-missing-license` only when the downstream
workflow explicitly records why that omission is acceptable.

## Hard Rules

- Do not call Lean, Lake, AXLE, MCP, SafeVerify, CI, package installers,
  notebooks, training scripts, or network APIs.
- Do not read credentials, environment variables, MCP config, or runtime
  provider config.
- Do not claim proof validity, formal verification, theorem-intent match,
  `sorry` freedom, or axiom freedom.
- Advisory static findings from intake remain advisory only.

## Trust Claim

Passing output may claim only:

```json
{
  "tier": "T1_STATIC_PREFLIGHT_READY",
  "claim": "static verifier preflight only; no proof-validity claim"
}
```

Failing output must use `gate_status: fail` and list blockers.
