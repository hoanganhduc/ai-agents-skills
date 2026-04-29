---
name: get-available-resources
description: Use at the start of computationally intensive local tasks to detect CPU, memory, disk, and optional accelerator availability before planning execution.
---

# Get Available Resources

Use this skill before local work that may be expensive, memory-sensitive, or
parallelized, such as document conversion batches, graph enumeration, SageMath
runs, OCR, local parsing, or large file rearrangement.

## Workflow

1. Decide whether the task is heavy enough to justify a preflight. Skip this
   skill for trivial commands.
2. Prefer an existing local resource checker when the installed agent provides
   one. Otherwise inspect resources with portable system commands or Python.
3. Record the result in a small planning note or `.agent_resources.json` in the
   current workspace when the task will continue for multiple steps.
4. Use the result to choose batch size, parallelism, memory strategy, and
   whether to route the task to SageMath, WSL, remote compute, or a smaller
   local run.

## Minimum Checks

- CPU count and rough CPU model.
- Available memory.
- Free disk space in the working directory.
- GPU or accelerator availability only when relevant and detectable.
- Whether the workload should be split, sampled first, or routed elsewhere.

## Output Shape

For a visible preflight, report:

- resources inspected
- detected limits
- recommended execution strategy
- confidence and any missing probes

## Guardrails

- Do not spend more time on resource detection than the task warrants.
- Do not assume GPU or SageMath availability without checking.
- On Windows, consider WSL-backed tools separately from native Windows tools.
- Treat remote compute credentials and provider configuration as external; do
  not inspect or print secrets.
