---
name: digest-bridge
description: Use when the user wants to extract arXiv IDs or DOIs from research or RSS digests and turn them into getscipapers requests or manifests.
metadata:
  short-description: Bridge digest outputs into paper retrieval
---

# Digest Bridge


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/digest-bridge/run_digest_bridge.bat" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

This uses the managed ai-agents-skills runtime copy of the digest bridge workflow.

## When to use

- scan research digests for papers
- scan RSS digests for papers
- create a getscipapers manifest from digest outputs
- request papers mentioned in recent digests

## Base path

- `$AAS_RUNTIME_WORKSPACE/skills/digest-bridge/`

This is a direct Python entry point, so run it from the managed ai-agents-skills runtime workspace with the workspace-local `PYTHONPATH`.

## Core commands

Use `functions.exec_command`.

```bash
cd "$AAS_RUNTIME_WORKSPACE" && PYTHONPATH="$AAS_RUNTIME_WORKSPACE/.local:${PYTHONPATH:-}" python3 skills/digest-bridge/digest_bridge.py scan
```

```bash
cd "$AAS_RUNTIME_WORKSPACE" && PYTHONPATH="$AAS_RUNTIME_WORKSPACE/.local:${PYTHONPATH:-}" python3 skills/digest-bridge/digest_bridge.py scan --source research --min-score 3
```

```bash
cd "$AAS_RUNTIME_WORKSPACE" && PYTHONPATH="$AAS_RUNTIME_WORKSPACE/.local:${PYTHONPATH:-}" python3 skills/digest-bridge/digest_bridge.py request --source research
```

```bash
cd "$AAS_RUNTIME_WORKSPACE" && PYTHONPATH="$AAS_RUNTIME_WORKSPACE/.local:${PYTHONPATH:-}" python3 skills/digest-bridge/digest_bridge.py request --source rss --watch
```

## Operational notes

- Use this after a digest run, not as a replacement for the digest itself.
- Respect `--source` and `--min-score` filters instead of broad requests when the user wants a narrower batch.
- If the user wants actual external retrieval, follow the manifest or request output into `getscipapers-requester`.
- `scan` is the dry-run discovery step; `request` is the transition into manifest/watch creation.
