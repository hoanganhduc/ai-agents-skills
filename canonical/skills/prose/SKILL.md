---
name: prose
description: Use when the user mentions OpenProse or prose workflows, wants explicit multi-agent research and synthesis, or wants a reusable orchestration pattern. In Codex, emulate OpenClaw OpenProse using spawn_agent, structured decomposition, and workspace artifacts.
metadata:
  short-description: OpenProse-style orchestration in Codex
---

# Prose

This is a Codex adaptation of OpenClaw OpenProse, not the OpenProse VM itself.

## Concept mapping

- OpenProse `agent` / `session` -> Codex `spawn_agent`
- OpenProse `parallel` blocks -> multiple independent spawned agents
- OpenProse file state -> normal workspace files and notes

## When to use

- The user explicitly mentions `prose` or `OpenProse`
- The task is a research pipeline with separate roles
- The task benefits from explicit parallel work and final synthesis

## Workflow

1. Break the task into independent tracks.
2. Keep the immediate blocking step local.
3. Spawn agents only for bounded, non-overlapping subtasks.
4. Ask each spawned agent for concrete output, not vague exploration.
5. Integrate results locally into the final answer or file.

## Good patterns

- Researcher + writer
- Comparator A + comparator B
- Source gathering + synthesis
- Evidence collection + verification

## Constraints

- Do not spawn agents just to duplicate your own immediate next step.
- Reuse or wait on sub-agents only when their result is actually needed.
- Keep ownership clear if multiple agents may write files.

## User expectation

If the user gives an actual `.prose` file, read it and translate its intent into Codex-native orchestration rather than pretending Codex can run the VM directly.
