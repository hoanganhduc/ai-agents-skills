---
name: axiom-axle-mcp
description: Use when preparing optional AxiomMath AXLE MCP setup for manual formal-proof assistance.
---

# Axiom AXLE MCP Setup

## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. For Codex-only installs the runtime is usually `%USERPROFILE%\.codex\runtime`; for multi-agent installs it is usually `%LOCALAPPDATA%\ai-agents-skills\runtime`. Set `$runtime` to the installed runtime root, then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/axiom-axle-mcp/run_axiom_axle_mcp.bat" doctor
```

PowerShell runner target:

```powershell
& "$runtime\run_skill.ps1" "skills/axiom-axle-mcp/run_axiom_axle_mcp.ps1" doctor
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

Use this skill only for explicit optional AXLE MCP setup. It never installs packages, starts an MCP server, writes MCP/client config, stores credentials, or calls AxiomMath services. It reports local readiness and emits manual configuration snippets with placeholders.

## Runtime Helper

Check local readiness without running `uvx`:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/axiom-axle-mcp/run_axiom_axle_mcp.sh doctor
```

Emit a manual MCP config snippet:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/axiom-axle-mcp/run_axiom_axle_mcp.sh config-snippet
```

Run offline smoke:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/axiom-axle-mcp/run_axiom_axle_mcp.sh smoke
```

The emitted local stdio snippet uses command `uvx` and args `["--from", "axiom-axle-mcp==0.3.3", "axle-mcp-server"]` with placeholder `<AXLE_API_KEY>`. The hosted URL `https://mcp.axiommath.ai/mcp` is manual setup only.

## Research Evidence Policy

AXLE output is remote supplemental evidence. Record it as `axle_remote_check`, never as `formal_check`. It cannot set local `lean_check_status`, satisfy placeholder or trust-base scans, replace statement-equivalence review, or promote formal support without local Lean/project evidence.
