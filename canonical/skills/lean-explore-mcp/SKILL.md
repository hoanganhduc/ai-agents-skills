---
name: lean-explore-mcp
description: Use when preparing optional LeanExplore MCP setup for Lean declaration search and formalization support.
---

# LeanExplore MCP Setup

## Python packages

On Linux, the managed launcher uses `~/.agents_skills_venv` (override with
`AAS_SKILL_VENV`). From the repository, run
`make provision-skill-python ARGS="--skills lean-explore-mcp --apply --real-system"`
and check it with `make verify-skill-python`. This skill is opt-in; use the
explicit skill selection shown here, or add `--include-opt-in` to provision
all opt-in skills.
If the venv is absent, the launcher uses system Python; any unavailable
third-party imports fail at startup. A refused venv stops the launch with
exit `127` and a reason.

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
"${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/lean-explore-mcp/run_lean_explore_mcp.sh doctor
```

Emit a manual MCP config snippet:

```bash
"${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/lean-explore-mcp/run_lean_explore_mcp.sh config-snippet --backend api
```

Use `--backend local` only after local data has been prepared outside this repo with LeanExplore's own tooling.

Run offline smoke:

```bash
"${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/lean-explore-mcp/run_lean_explore_mcp.sh smoke
```

## Provisioning (no root)

Provision the shared skill Python venv from the repository:

```bash
make provision-skill-python ARGS="--skills lean-explore-mcp --apply --real-system"
```

Start the adapter through the launcher, which admits the venv before serving:

```bash
"${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/lean-explore-mcp/run_lean_explore_mcp.sh serve --backend api
```

The emitted local stdio snippet uses the absolute managed `run_skill.sh` launcher
with args `["skills/lean-explore-mcp/run_lean_explore_mcp.sh", "serve", "--backend", "api"]`
or the corresponding local backend. API mode uses the `LEANEXPLORE_API_KEY`
placeholder; local mode assumes a user-managed LeanExplore cache such as
`~/.lean_explore/cache/`. Serving requires exactly `lean-explore==1.2.1` in the
admitted venv.

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
