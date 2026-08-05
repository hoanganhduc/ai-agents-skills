---
name: lean-explore-mcp
description: Use when preparing optional LeanExplore MCP setup for Lean declaration search and formalization support.
---

# LeanExplore MCP Setup

## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/lean-explore-mcp/run_lean_explore_mcp.ps1" doctor
```

PowerShell runner target:

```powershell
& "$runtime\run_skill.ps1" "skills/lean-explore-mcp/run_lean_explore_mcp.ps1" doctor
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

Use this skill only for explicit optional LeanExplore MCP setup. It never installs packages, writes MCP/client config, stores credentials, downloads local data, or calls LeanExplore services during doctor, config, or smoke. On POSIX, its explicit `serve` command can start the reviewed in-process adapter for exactly `lean-explore==1.2.1`.

## Runtime Helper

Check local readiness without running `lean-explore`:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/lean-explore-mcp/run_lean_explore_mcp.sh doctor
```

Emit a manual MCP config snippet:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/lean-explore-mcp/run_lean_explore_mcp.sh config-snippet --backend api
```

Use `--backend local` only after local data has been prepared outside this repo with LeanExplore's own tooling.

Run offline smoke:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/lean-explore-mcp/run_lean_explore_mcp.sh smoke
```

The emitted local stdio snippet uses the absolute managed `run_lean_explore_mcp.sh` wrapper with args `["serve", "--backend", "api"]` or `["serve", "--backend", "local"]`. Set `AAS_LEANEXPLORE_SITE_PACKAGES` to an absolute, owner-protected site-packages directory containing exactly `lean-explore==1.2.1`. API mode also uses the placeholder `LEANEXPLORE_API_KEY`; local mode assumes a user-managed LeanExplore cache such as `~/.lean_explore/cache/`.

Never add `--api-key` or `--api-key=<value>` to the MCP command: process
arguments are observable outside the child. The managed POSIX wrapper captures
and removes `LEANEXPLORE_API_KEY` before helper/interpreter discovery and moves
it to a private inherited descriptor. The Python adapter consumes and closes
that descriptor before importing LeanExplore. It instantiates the 1.2.1 API
client and FastMCP app in-process; it never invokes the broken upstream CLI
bridge. Native Windows `serve` fails explicitly because private-descriptor
credential transport is not implemented there. Doctor, smoke, and placeholder
config generation remain available on Windows.

## Research Evidence Policy

LeanExplore output is Lean declaration retrieval evidence. Record it as `lean_declaration_search`, never as `formal_check`. It cannot set local `lean_check_status`, satisfy placeholder or trust-base scans, replace statement-equivalence review, or promote formal support without local Lean/project evidence.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `informal-to-lean-formalization-runbook` -- Local-first intake mapping an informal proof to Lean declarations with a scanner-first verification gate separating typecheck status from claim support.
