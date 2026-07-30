# OpenClaw `aas-remote-bridge` adapter

## Source of truth

All dual-route behavior lives in **this repository** under:

```text
canonical/runtime/skills/remote-bridge/
  remote_bridge.py
  dispatch_aas.py
  publish_openclaw_adapter.py          ← inert BLOCK/no-write placeholder
  sync_remote_bridge_paths.py          ← inert upgrade-revocation stub
  openclaw-adapter/SKILL.md   ← this adapter's skill body
  openclaw-adapter/README.md  ← this file
```

`~/.openclaw/workspace/skills/aas-remote-bridge/` is a **published install product**,
not a place to invent skill logic. OpenClaw cannot bind-mount `~/.config`, so the
adapter vendors runtime scripts and uses separate workspace-owned secrets/state.

## Why a separate adapter?

| Layer | Path | Role |
|-------|------|------|
| Canonical skill body | `canonical/skills/remote-bridge/SKILL.md` | Agent skill for Codex/Claude/Grok/… (not OpenClaw) |
| Canonical runtime | `canonical/runtime/skills/remote-bridge/` | Portable engine + `/aas` dispatch |
| Installed runtime | `~/.local/share/ai-agents-skills/runtime/workspace/skills/remote-bridge/` | What host CLIs run |
| OpenClaw workspace adapter | `~/.openclaw/workspace/skills/aas-remote-bridge/` | Sandbox-visible dual route for `/aas` |

Managed installer OpenClaw writes stay limited to
`.openclaw/skills/<skill>/SKILL.md` via `openclaw-target-*`. The workspace
dual-route tree is intentionally separate. Automated publishing is disabled.

## Publish / refresh is blocked

`publish_openclaw_adapter.py` is an inert compatibility placeholder. Dry and
non-dry invocations return `publisher_security_boundary_unavailable`, do not
inspect the destination, and perform no actions. The runtime ships this and the
sync filename temporarily as inert revocation stubs. Replacing older managed
runtime copies requires an explicitly reviewed backup-and-replace upgrade that
preserves recovery data; default installation does not overwrite divergent
copies. The blocked publisher cannot replace or clean up already-published
OpenClaw workspace copies. Publishing remains unsupported until destination
traversal and writes are descriptor-pinned, no-follow, recoverable, and reviewed.

## Workspace-owned secrets and state

The adapter reads and writes only its selected OpenClaw workspace by default:

| Data | Default host path |
|------|-------------------|
| Secrets | `~/.openclaw/workspace/secrets/remote-bridge/secrets.json` |
| State | `~/.openclaw/workspace/.remote-bridge-state` |

Inside the sandbox these are `/workspace/secrets/remote-bridge/secrets.json`
and `/workspace/.remote-bridge-state`. `OPENCLAW_WORKSPACE` or
`AAS_OPENCLAW_WORKSPACE` can select another root for the adapter parent. The
child receives a narrow environment and always uses that workspace's private
secrets/state paths; it does not inherit host path overrides or unrelated
provider/cloud credentials.

There is **no automatic or bidirectional synchronization** with host
remote-bridge config/state. `dispatch_aas.py` and `remote_bridge.py` do not
invoke a sync utility. The installed filename is an inert revocation stub and
the publisher writes nothing. Existing workspace copies remain legacy and must
not be trusted as refreshed artifacts.

## Do not

- Edit only the OpenClaw workspace copy and leave canonical stale
- Treat `~/.openclaw/workspace/skills/*` as the skill repository
- Bind-mount `~/.config` into the OpenClaw sandbox
- Run the legacy sync helper to copy secrets or state between trust domains
