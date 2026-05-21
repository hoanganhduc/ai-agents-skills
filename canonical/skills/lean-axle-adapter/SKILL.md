---
name: lean-axle-adapter
description: Use when designing an offline-only AXLE integration boundary for Lean formalization workflows without live calls or credential lookup.
---

# Lean AXLE Adapter

This is a design-only adapter surface. It documents how a future AXLE
integration should be bounded, but it does not call AXLE, mutate MCP config,
start servers, import external code, or read credentials.

## Hard Boundaries

- No default endpoint activation.
- No implicit package install or import side effect.
- No MCP config mutation.
- No background server.
- No credential lookup.
- No live AXLE calls.
- No copied AXLE templates or external-code-derived content.
- No claim that AXLE, Claude, Copilot, DeepSeek, SafeVerify, MCP, or OpenClaw
  executed from packet contracts, reference docs, or external CLI guidance.

## Candidate Future Contract

Future runtime work must record:

- explicit endpoint allowlist
- explicit credential environment-variable allowlist
- request and response hashes
- bounded redacted diagnostics only
- AXLE tool version
- Lean toolchain and Mathlib revision
- formal statement hash
- theorem-intent review status

T2 means AXLE accepted the formal statement as written. It does not imply that
the formal statement matches the informal theorem. T2 does not imply theorem-intent match.

## Default Status

Current status: disabled by construction.

Runtime behavior: incomplete analysis.
