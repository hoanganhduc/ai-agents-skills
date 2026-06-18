---
name: model-router
description: Use when choosing an appropriate model, reasoning level, and role for subagents or multi-agent research work.
---

# Model Router

Use this skill when a task needs an explicit model, reasoning level, or
subagent-role recommendation. It is a planning aid. It does not change the
current session model and it does not manage provider credentials.

## Routing Questions

Before recommending a route, classify:

- task type: research, proof, implementation, review, extraction, or synthesis
- risk: low, ordinary, correctness-critical, or high-stakes
- context size: small, repo-scale, paper-scale, or multi-source
- latency sensitivity
- verification available: tests, computations, source checks, or human review
- whether work can be delegated safely

## General Guidance

- Use stronger reasoning for proof, algorithms, security, correctness audits,
  literature synthesis, and ambiguous multi-source research.
- Use faster or smaller workers for bounded extraction, file inventory, and
  low-risk parallel exploration.
- Use implementation-focused workers for code changes and tests.
- Use read-only explorer roles for scoped codebase questions.
- Do not spawn agents unless the user explicitly asks for multi-agent or
  delegated work.

## Recommendation Format

Return:

- recommended role or agent type
- recommended model tier or reasoning level using the current system's
  available options
- why that route fits the task
- verification that should gate the result
- fallback if the preferred model or role is unavailable

## Guardrails

- Treat the current tool definitions and agent runtime as the source of truth.
- Avoid provider-specific assumptions unless the user asks for a specific
  provider.
- Do not recommend changing authentication, provider config, hooks, or MCP
  servers from this skill.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and Modal/GitHub Actions credit-gated heavy-compute offload.
- `cross-agent-adversarial-review` -- Producer-never-confirmer adversarial review of a paper, proof, or code artifact across agent families with a fresh-agent confirmation gate.
- `engineering-delivery-loop-runbook` -- Bounded build-and-deliver loop runbook: single-path implementation with seen-to-fail proof, cross-agent diff verification, behavior-preserving cleanup, and credit-gated heavy-compute offload.
- `reversible-decision-memo` -- Evidence-grounded decision record with named alternatives, source-cited rationale, reversibility class and trip-wires, and a fresh-context adversarial confirmation before the decision stands.
