---
name: lean-formalization-intake
description: Use when deciding whether a research claim should enter the optional Lean formalization lane.
---

# Lean Formalization Intake

## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. For Codex-only installs the runtime is usually `%USERPROFILE%\.codex\runtime`; for multi-agent installs it is usually `%LOCALAPPDATA%\ai-agents-skills\runtime`. Set `$runtime` to the installed runtime root, then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/lean-formalization-intake/run_lean_formalization_intake.bat" doctor
```

PowerShell runner target:

```powershell
& "$runtime\run_skill.ps1" "skills/lean-formalization-intake/run_lean_formalization_intake.ps1" doctor
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

Use this skill before spending effort on Lean formalization. It decides whether a research claim is suitable for the optional formal lane and records a conservative decision:

- `proceed`: definitions and scope look suitable enough to try formalization
- `defer`: formalization is relevant but blocked by definitions, toolchain, library support, semantic alignment, or budget
- `not_applicable`: Lean is not useful for this claim or outside scope
- `blocked`: formal support was required but cannot proceed without clarification or missing tooling

`defer`, `not_applicable`, and missing Lean are not failed theorem evidence. They are only formal-lane status.

## Runtime Helper

Check the local tool status:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/lean-formalization-intake/run_lean_formalization_intake.sh doctor
```

Run non-installing version/toolchain probes when you need reproducibility
metadata:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/lean-formalization-intake/run_lean_formalization_intake.sh doctor --probe
```

Assess a claim:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/lean-formalization-intake/run_lean_formalization_intake.sh assess \
  --claim "Every finite tree has a leaf" \
  --claim-id C1 \
  --output formal/intake-C1.json
```

Set `AAS_LEAN` or `AAS_LAKE` to select a specific already-installed local
executable. Invalid explicit paths are reported as unavailable instead of being
masked by another tool on `PATH`.

The helper never installs Lean, Lake, mathlib, Python packages, Node packages, credentials, services, or MCP servers.

## Output Contract

The runtime emits JSON with:

- `formalization_decision`
- `reason`
- `required_definitions`
- `expected_cost`
- `recommended_next_step`
- `formal_check_requirement`
- `tool_status`
- `limitations`

The result can be copied into a v2 `evidence.jsonl` row or attached as a run artifact, but it does not itself prove the research claim.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `informal-to-lean-formalization-runbook` -- Local-first intake mapping an informal proof to Lean declarations with a scanner-first verification gate separating typecheck status from claim support.
