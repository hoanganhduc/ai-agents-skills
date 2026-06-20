# OpenClaw Target

OpenClaw is a default fake-root-only target for normal installer flows. The
installer detects an existing fake-root `.openclaw` home but must not create
one implicitly, and normal `plan`, `install`, `uninstall`, and `rollback`
commands must not write under a real `.openclaw` tree.

Phase 1 target-gate, target-evidence, and target-manifest scaffolding is
diagnostic only. It records blocked decisions and non-authorizing schemas, but
it does not make real-system OpenClaw writes approval-eligible.

Real-system OpenClaw writes are available only through the separate
`openclaw-target-*` command family. That path supports approved v2 manifests
for `copy` writes to `.openclaw/skills/<skill>/SKILL.md` only, with existing
`.openclaw/skills`, native target evidence, immutable approval, immediate
pre-state recheck, an OpenClaw-specific confirmation phrase, and hash-based
uninstall.

OpenClaw receives only the artifacts that are safe for the fake-root target.
Runtime-backed skills are blocked unless neutral runtime evidence exists, and
instruction blocks remain disabled. Use the OpenClaw inventory, manifest, and
evidence commands for source/import work, and the OpenClaw target commands for
reviewed real-system skill-file installs.

## Autonomous loop enforcement

OpenClaw is fake-root-only for normal flows and has no built-in autonomous-loop
enforcement surface here. Loop stop-conditions remain policy-based via the
installed `autonomous-loop-enforcement` rule. See the repo
[Architecture](../../docs/architecture.md) for the full per-target matrix.
