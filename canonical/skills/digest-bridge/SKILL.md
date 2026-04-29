---
name: digest-bridge
description: Use when the user wants to extract arXiv IDs or DOIs from research or RSS digests and turn them into getscipapers requests or manifests.
metadata:
  short-description: Bridge digest outputs into paper retrieval
---

# Digest Bridge

This uses the vendored Codex runtime copy of the digest bridge workflow.

## When to use

- scan research digests for papers
- scan RSS digests for papers
- create a getscipapers manifest from digest outputs
- request papers mentioned in recent digests

## Base path

- `~/.codex/runtime/workspace/skills/digest-bridge/`

This is a direct Python entry point, so run it from the vendored Codex runtime workspace with the workspace-local `PYTHONPATH`.

## Core commands

Use `functions.exec_command`.

```bash
cd ~/.codex/runtime/workspace && PYTHONPATH="$HOME/.codex/runtime/workspace/.local:$PYTHONPATH" python3 skills/digest-bridge/digest_bridge.py scan
```

```bash
cd ~/.codex/runtime/workspace && PYTHONPATH="$HOME/.codex/runtime/workspace/.local:$PYTHONPATH" python3 skills/digest-bridge/digest_bridge.py scan --source research --min-score 3
```

```bash
cd ~/.codex/runtime/workspace && PYTHONPATH="$HOME/.codex/runtime/workspace/.local:$PYTHONPATH" python3 skills/digest-bridge/digest_bridge.py request --source research
```

```bash
cd ~/.codex/runtime/workspace && PYTHONPATH="$HOME/.codex/runtime/workspace/.local:$PYTHONPATH" python3 skills/digest-bridge/digest_bridge.py request --source rss --watch
```

## Operational notes

- Use this after a digest run, not as a replacement for the digest itself.
- Respect `--source` and `--min-score` filters instead of broad requests when the user wants a narrower batch.
- If the user wants actual external retrieval, follow the manifest or request output into `getscipapers_requester`.
- `scan` is the dry-run discovery step; `request` is the transition into manifest/watch creation.
