---
name: aas-remote-bridge
description: Route /aas control commands for autonomous research loops through remote-bridge (not OpenClaw LLM). Use whenever the user message starts with /aas (optionally after an @bot mention). Works inside the OpenClaw sandbox via vendored runtime + host bind mounts.
user-invocable: true
---

# AAS remote-bridge dual routing

> **Source of truth:** `~/ai-agents-skills` (canonical runtime
> `skills/remote-bridge/`). This OpenClaw workspace copy is a **published
> adapter**. Do not invent behavior only under `~/.openclaw`. Workspace
> publishing is currently disabled until its filesystem boundary is hardened.

## Workspace-owned secrets and state (no synchronization)

The adapter uses its own OpenClaw workspace paths:

| Data | Sandbox path | Host view of the same workspace |
|------|--------------|---------------------------------|
| Secrets | `/workspace/secrets/remote-bridge/secrets.json` | `~/.openclaw/workspace/secrets/remote-bridge/secrets.json` |
| State | `/workspace/.remote-bridge-state` | `~/.openclaw/workspace/.remote-bridge-state` |

There is **no automatic or bidirectional synchronization** with host
`~/.config/remote-bridge` or `~/.local/share/ai-agents-skills/remote-bridge`.
`dispatch_aas.py` never imports or runs the legacy sync helper. Host and
workspace configurations are independent; an operator must provision the
workspace-owned secrets file explicitly.

`OPENCLAW_WORKSPACE` or `AAS_OPENCLAW_WORKSPACE` may select a different
workspace root for the adapter parent. The child receives a narrow environment
and always uses the selected workspace's private secrets/state paths; ambient
provider/cloud tokens and host remote-bridge path overrides are not inherited.
Do not use the legacy
`sync_remote_bridge_paths.py` name to mirror secrets or state. The installed
file is an inert revocation stub retained for one compatibility release.
Replacing an older managed runtime copy requires an explicitly reviewed
backup-and-replace upgrade that preserves recovery data; default installation
does not overwrite divergent copies.

## When to use (MANDATORY)

If the **current user message** (after stripping a leading `@bot` mention) **starts with `/aas`**:

1. **Do not** invent research progress from memory or old workspaces.
2. **Immediately** run the dispatch script (paths work on host and in sandbox):

```bash
printf '%s' "$USER_MESSAGE" | \
  python3 /workspace/skills/aas-remote-bridge/scripts/dispatch_aas.py \
  --text-stdin \
  --principal "$SENDER_ID_OR_EMAIL"
```

On the host (non-sandbox), either path works:

```bash
printf '%s' "$USER_MESSAGE" | \
  python3 ~/.openclaw/workspace/skills/aas-remote-bridge/scripts/dispatch_aas.py \
  --text-stdin \
  --principal "$SENDER_ID_OR_EMAIL"
```

Or the installed AAS runtime (preferred when available):

```bash
printf '%s' "$USER_MESSAGE" | \
  python3 ~/.local/share/ai-agents-skills/runtime/workspace/skills/remote-bridge/dispatch_aas.py \
  --text-stdin \
  --principal "$SENDER_ID_OR_EMAIL"
```

3. Reply with the script's `human_reply` field **verbatim as Markdown**.
4. Do **not** call other tools unless the script fails to run.

If the message does **not** start with `/aas`, this skill does **not** apply.

## Dual-path policy

| Message | Handler |
|---------|---------|
| Starts with `/aas` | **remote-bridge** (this skill) |
| Anything else | **OpenClaw** normal agent |

Live loop job example: `example-job` → local research loop dir for that job
(set the real host path on the machine; do not commit host home paths)

## Maintaining this adapter

The installed workspace publisher is an intentional revocation stub. It returns
`publisher_security_boundary_unavailable` without inspecting or mutating its
destination. Treat any existing workspace copy as legacy until a separately
reviewed descriptor-pinned/no-follow publisher is implemented. The blocked
publisher cannot replace or clean up that workspace copy.
