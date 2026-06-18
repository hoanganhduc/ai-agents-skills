---
name: lean-strict-verification-gate
description: Use when checking whether a Lean artifact can safely support a research claim.
---

# Lean Strict Verification Gate

## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. For Codex-only installs the runtime is usually `%USERPROFILE%\.codex\runtime`; for multi-agent installs it is usually `%LOCALAPPDATA%\ai-agents-skills\runtime`. Set `$runtime` to the installed runtime root, then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/lean-strict-verification-gate/run_lean_strict_verification_gate.bat" doctor
```

PowerShell runner target:

```powershell
& "$runtime\run_skill.ps1" "skills/lean-strict-verification-gate/run_lean_strict_verification_gate.ps1" doctor
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

Use this skill to prevent overclaiming from generated Lean, skeletons, partial formalizations, or checker output. It separates:

- syntactic/safety scan
- placeholder and trust-base status
- optional local Lean typecheck
- statement-equivalence review, which remains a human/lead review step

## Runtime Helper

Check the local tool status:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh doctor
```

Run non-installing version/toolchain probes when you need reproducibility
metadata:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh doctor --probe
```

Scan a Lean file without running Lean:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh scan \
  --input formal/final/proof.lean \
  --artifact-stage final_candidate
```

Optionally typecheck only when Lean is already installed:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh verify \
  --input formal/final/proof.lean \
  --artifact-stage final_candidate \
  --typecheck
```

For a user-managed Lake workspace, use the explicit Lake environment runner.
The helper requires a project root containing `lakefile.lean` or
`lakefile.toml`, records the project context, and still runs the scanner before
typechecking:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/lean-strict-verification-gate/run_lean_strict_verification_gate.sh verify \
  --input formal/final/proof.lean \
  --artifact-stage final_candidate \
  --typecheck \
  --runner lake-env-lean \
  --project-root /path/to/lean/project
```

Set `AAS_LEAN` or `AAS_LAKE` to select a specific already-installed local
executable. Invalid explicit paths fail closed instead of silently using a
different tool.

The helper never installs Lean, Lake, mathlib, npm packages, Python packages, credentials, services, or MCP servers. Missing Lean reports `tool_unavailable`.

## Blocking Policy

Before any typecheck, the scanner blocks active:

- `#eval`
- `IO.Process`
- `run_cmd`
- `unsafe`
- `initialize`
- `@[extern]`
- foreign/FFI import patterns
- non-allowlisted imports unless explicitly passed with `--allow-import`
- Lake/package files unless explicitly reviewed outside this helper

Final or claim-supporting artifacts also block on active `sorry`, `admit`, unsanctioned `axiom`, unknown trust base, or unreviewed generated proof text. Stubs may contain placeholders only when explicitly marked `artifact_stage = stub`.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `informal-to-lean-formalization-runbook` -- Local-first intake mapping an informal proof to Lean declarations with a scanner-first verification gate separating typecheck status from claim support.
