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
pre-state recheck, an OpenClaw-specific confirmation phrase, and receipt-based
uninstall. If the approved rendered bytes already exist, the target apply path
records a canonical-source/content/identity attestation without rewriting the
file; uninstall later forgets that adopted file rather than deleting it.

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

## Dual-route `/aas` adapter (workspace, not managed skill install)

OpenClaw sandbox agents may need to route messages that start with `/aas` to
**remote-bridge** instead of the OpenClaw LLM. That dual-route adapter is **not**
installed by normal `openclaw-target-*` skill copies.

| Role | Location |
|------|----------|
| Source of truth | `canonical/runtime/skills/remote-bridge/` in this repo |
| Publish status | **Blocked**: installed publisher name is an inert revocation stub |
| Legacy tree | `~/.openclaw/workspace/skills/aas-remote-bridge/` |
| Secrets | `~/.openclaw/workspace/.config/remote-bridge/secrets.json` |
| State | `~/.openclaw/workspace/.remote-bridge-state` |

The adapter has **no automatic or bidirectional synchronization** with host
remote-bridge secrets or state. `/aas` dispatch uses its separate
workspace-owned paths, passes a narrow child environment, and
never imports or runs the legacy sync helper.
Operators must provision the workspace secrets explicitly; changes do not
propagate between host and workspace configurations. Replacing an older managed
runtime stub requires an
explicitly reviewed backup-and-replace upgrade with preserved recovery data;
default installation does not overwrite divergent copies. The blocked publisher
cannot replace or clean up an existing legacy workspace tree.

Do not create or refresh the legacy tree with the old publisher. Publishing is
unsupported until a descriptor-pinned, no-follow, recoverable implementation
passes security review.

**Do not invent skill logic only under `~/.openclaw`.** Edit
`~/ai-agents-skills`, install/runtime-sync as needed, then publish the adapter.
See `canonical/runtime/skills/remote-bridge/publish_openclaw_adapter.py`.
