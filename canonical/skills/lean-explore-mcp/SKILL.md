---
name: lean-explore-mcp
description: Use when preparing optional LeanExplore MCP setup for Lean declaration search and formalization support.
---

# LeanExplore MCP Setup

## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. For Codex-only installs the runtime is usually `%USERPROFILE%\.codex\runtime`; for multi-agent installs it is usually `%LOCALAPPDATA%\ai-agents-skills\runtime`. Set `$runtime` to the installed runtime root, then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/lean-explore-mcp/run_lean_explore_mcp.bat" doctor
```

PowerShell runner target:

```powershell
& "$runtime\run_skill.ps1" "skills/lean-explore-mcp/run_lean_explore_mcp.ps1" doctor
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

Use this skill only for explicit optional LeanExplore MCP setup. It never installs packages, starts an MCP server, writes MCP/client config, stores credentials, downloads local data, or calls LeanExplore services. It reports local readiness and emits manual configuration snippets with placeholders.

## Runtime Helper

Check local readiness without running `lean-explore`:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/lean-explore-mcp/run_lean_explore_mcp.sh doctor
```

Emit a manual MCP config snippet:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/lean-explore-mcp/run_lean_explore_mcp.sh config-snippet --backend api
```

Use `--backend local` only after local data has been prepared outside this repo with LeanExplore's own tooling.

Run offline smoke:

```bash
bash ~/.codex/runtime/run_skill.sh \
  skills/lean-explore-mcp/run_lean_explore_mcp.sh smoke
```

The emitted local stdio snippet uses command `lean-explore` and args `["mcp", "serve", "--backend", "api"]` or `["mcp", "serve", "--backend", "local"]`. API mode uses placeholder `LEANEXPLORE_API_KEY`; local mode assumes a user-managed LeanExplore cache such as `~/.lean_explore/cache/`.

## Research Evidence Policy

LeanExplore output is Lean declaration retrieval evidence. Record it as `lean_declaration_search`, never as `formal_check`. It cannot set local `lean_check_status`, satisfy placeholder or trust-base scans, replace statement-equivalence review, or promote formal support without local Lean/project evidence.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `informal-to-lean-formalization-runbook` -- Local-first intake mapping an informal proof to Lean declarations with a scanner-first verification gate separating typecheck status from claim support.
