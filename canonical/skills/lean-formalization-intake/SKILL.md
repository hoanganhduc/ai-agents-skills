---
name: lean-formalization-intake
description: Use when inspecting a local Lean formalization repository for static metadata, trust-claim boundaries, and proof-neutral intake JSON.
---

# Lean Formalization Intake

Use this skill to inspect a local Lean repository or task bundle before any
proof, AXLE, MCP, SafeVerify, or training workflow runs.

The intake is proof-neutral. It reports static metadata and gaps only.

## Runtime Commands

POSIX:

```bash
bash ~/.codex/runtime/run_skill.sh skills/lean-formalization-intake/run_lean_formalization_intake.sh <repo> --output-json intake.json
```

Windows:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/lean-formalization-intake/run_lean_formalization_intake.bat" <repo> --output-json intake.json
```

## Workflow

1. Require an explicit local repository path.
2. Inspect only files under that path.
3. Record Lean/Lake/task metadata, source materials, CI/license files,
   sidecars, candidate commands, do-not-run command surfaces, and advisory
   soundness signals.
4. Emit JSON as the primary output. Markdown summaries are optional and must be
   generated from the same JSON.
5. Keep all proof and runtime claims explicitly incomplete until a later checked
   phase records named runtime evidence.

## Hard Rules

- Do not clone repositories.
- Do not call Lean, Lake, AXLE, MCP, SafeVerify, CI, notebooks, package
  installers, or network APIs.
- Do not read `.env*`, credential files, local runtime config, caches,
  databases, reports, archives, downloaded documents, or paths outside the
  explicit repo root.
- Do not claim proof validity, formal verification, theorem-intent match,
  `sorry` freedom, or axiom freedom.
- Treat lexical findings for `sorry`, `axiom`, `native_decide`,
  `implemented_by`, FFI, opaque declarations, or oracle-like surfaces as
  advisory static signals only.

## Required Trust Claim

Every JSON output must contain:

```json
{
  "runtime_behavior": "incomplete analysis",
  "incomplete_analysis": true,
  "trust_claim": {
    "tier": "T0_STATIC_INTAKE",
    "runtime_behavior": "incomplete analysis",
    "incomplete_analysis": true,
    "checks_run": [],
    "allowed_axioms": "unchecked",
    "sorries_policy": "unchecked",
    "statement_intent_review": {
      "status": "not_reviewed"
    },
    "claim": "static metadata only; no proof-validity claim"
  }
}
```

## Output Status Vocabulary

Use only:

- `detected`
- `missing`
- `reported`
- `statically_checked`
- `unchecked`
- `not_run`
- `not_applicable`

`statically_checked` means file/content inspection only. It does not mean Lean
compiled, AXLE accepted the artifact, or a verifier ran.
