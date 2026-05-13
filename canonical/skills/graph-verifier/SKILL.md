---
name: graph-verifier
description: Use when the user wants a quick sanity check for a finite graph claim, construction, or encoding using the lightweight OpenClaw verifier.
metadata:
  short-description: Lightweight graph claim verification
---

# Graph Verifier

This uses the managed ai-agents-skills runtime copy of the graph verifier workflow.

## When to use

- sanity-check a small graph claim
- inspect a finite construction
- validate an edge list, adjacency map, or graph encoding
- check simple properties such as connectivity or bipartiteness

For heavier graph-theoretic or algebraic computations, route to `sagemath` instead.

## Base path

- `$AAS_RUNTIME_ROOT/workspace/skills/graph-verifier/`

Use the managed runtime runner rather than invoking `run_graph_verifier.sh` directly.
If `AAS_RUNTIME_ROOT` is not already set, the default Codex-only install root is
`$HOME/.codex/runtime`.

Shared runner:

- `runtime_root="${AAS_RUNTIME_ROOT:-$HOME/.codex/runtime}"; bash "$runtime_root/run_skill.sh"`

## Workflow

1. Save JSON input to `/tmp/graph_input.json`.
2. Run the verifier.
3. Read the JSON result from stdout.

Supported shapes include `graph_data`, `edges`, `adjacency`, and optional `expected` values.

## Core command

```bash
runtime_root="${AAS_RUNTIME_ROOT:-$HOME/.codex/runtime}"
bash "$runtime_root/run_skill.sh" skills/graph-verifier/run_graph_verifier.sh --input /tmp/graph_input.json
```
