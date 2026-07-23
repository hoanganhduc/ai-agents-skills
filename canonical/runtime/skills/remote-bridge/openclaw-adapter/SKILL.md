---
name: aas-remote-bridge
description: Route /aas control commands for autonomous research loops through remote-bridge (not OpenClaw LLM). Use whenever the user message starts with /aas (optionally after an @bot mention). Works inside the OpenClaw sandbox via vendored runtime + host bind mounts.
user-invocable: true
---

# AAS remote-bridge dual routing

> **Source of truth:** `~/ai-agents-skills` (canonical runtime
> `skills/remote-bridge/`). This OpenClaw workspace copy is a **published
> adapter**. Do not invent behavior only under `~/.openclaw`. Edit canonical,
> then run `publish_openclaw_adapter.py`.

## Secrets / state sync (host ↔ sandbox workspace)

OpenClaw sandbox cannot bind-mount `~/.config`. Secrets and mailbox state are
mirrored under the workspace:

| Side | Secrets | State |
|------|---------|-------|
| Host | `~/.config/remote-bridge/secrets.json` | `~/.local/share/ai-agents-skills/remote-bridge` |
| Workspace | `~/.openclaw/workspace/secrets/remote-bridge/secrets.json` | `~/.openclaw/workspace/.remote-bridge-state` |

**Auto-sync (newer wins, no secret values logged):**

- `dispatch_aas.py` runs sync before every `/aas` dispatch
- host `remote_bridge.py` runs sync before/after send/arm/handle/etc.
- manual: `aas-remote-bridge-sync` (or `python3 …/sync_remote_bridge_paths.py --json`)
- disable: `AAS_REMOTE_BRIDGE_SYNC=0`

## When to use (MANDATORY)

If the **current user message** (after stripping a leading `@bot` mention) **starts with `/aas`**:

1. **Do not** invent research progress from memory or old workspaces.
2. **Immediately** run the dispatch script (paths work on host and in sandbox):

```bash
python3 /workspace/skills/aas-remote-bridge/scripts/dispatch_aas.py \
  --text "$USER_MESSAGE" \
  --principal "$SENDER_ID_OR_EMAIL"
```

On the host (non-sandbox), either path works:

```bash
python3 ~/.openclaw/workspace/skills/aas-remote-bridge/scripts/dispatch_aas.py \
  --text "$USER_MESSAGE" \
  --principal "$SENDER_ID_OR_EMAIL"
```

Or the installed AAS runtime (preferred when available):

```bash
python3 ~/.local/share/ai-agents-skills/runtime/workspace/skills/remote-bridge/dispatch_aas.py \
  --text "$USER_MESSAGE" \
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

```bash
# From ai-agents-skills checkout:
python3 canonical/runtime/skills/remote-bridge/publish_openclaw_adapter.py
# or after runtime install:
python3 ~/.local/share/ai-agents-skills/runtime/workspace/skills/remote-bridge/publish_openclaw_adapter.py
```
