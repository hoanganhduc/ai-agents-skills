---
name: autonomous-research-loop-runtime
description: Runtime helper for autonomous-research-loop ledgers. Use to initialize, append, validate, inspect, or smoke-test autonomous research loop state files without network, package installation, provider CLI calls, or live agent spawning.
---

# Autonomous Research Loop Runtime

This companion skill provides offline helper scripts for the
`autonomous-research-loop` ledger contract.

It is intentionally runtime-backed and should be installed only for targets that
support runtime skill helpers. It is not an OpenClaw skill-file target.

## Commands

From a configured Codex runtime, prefer:

```bash
bash ~/.codex/runtime/run_skill.sh skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh selftest
```

Common commands:

```bash
bash ~/.codex/runtime/run_skill.sh skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh init --dir research/run --goal "..." --success-criteria "..."
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh append-iteration --dir research/run --mode bounded-research --objective "Check evidence gaps" --decision continue
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh validate --dir research/run
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh status --dir research/run
```

On Windows, use the installed runtime runner with the native launcher target:

```bat
%USERPROFILE%\.codex\runtime\run_skill.bat skills/autonomous-research-loop-runtime/run_autonomous_research_loop.bat selftest
```

```powershell
& "$env:USERPROFILE\.codex\runtime\run_skill.ps1" skills/autonomous-research-loop-runtime/run_autonomous_research_loop.ps1 selftest
```

## Guarantees

The helper:

- uses only the Python standard library
- does not require network access
- does not install packages
- does not start servers
- does not write configuration outside the selected loop directory
- does not call Codex, Claude, Copilot, DeepSeek, or other provider CLIs
- does not spawn subagents

Use the canonical `autonomous-research-loop` skill for orchestration policy and
this helper only for local ledger mechanics.
