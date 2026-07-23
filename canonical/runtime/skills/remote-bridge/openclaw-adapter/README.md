# OpenClaw `aas-remote-bridge` adapter

## Source of truth

All dual-route behavior lives in **this repository** under:

```text
canonical/runtime/skills/remote-bridge/
  remote_bridge.py
  sync_remote_bridge_paths.py
  dispatch_aas.py
  publish_openclaw_adapter.py
  openclaw-adapter/SKILL.md   ← this adapter's skill body
  openclaw-adapter/README.md  ← this file
```

`~/.openclaw/workspace/skills/aas-remote-bridge/` is a **published install product**,
not a place to invent skill logic. OpenClaw cannot bind-mount `~/.config`, so the
adapter vendors runtime scripts into the workspace and mirrors secrets/state.

## Why a separate adapter?

| Layer | Path | Role |
|-------|------|------|
| Canonical skill body | `canonical/skills/remote-bridge/SKILL.md` | Agent skill for Codex/Claude/Grok/… (not OpenClaw) |
| Canonical runtime | `canonical/runtime/skills/remote-bridge/` | Portable engine + sync + `/aas` dispatch |
| Installed runtime | `~/.local/share/ai-agents-skills/runtime/workspace/skills/remote-bridge/` | What host CLIs run |
| OpenClaw workspace adapter | `~/.openclaw/workspace/skills/aas-remote-bridge/` | Sandbox-visible dual route for `/aas` |

Managed installer OpenClaw writes stay limited to
`.openclaw/skills/<skill>/SKILL.md` via `openclaw-target-*`. The workspace
dual-route tree is intentionally separate and refreshed by
`publish_openclaw_adapter.py`.

## Publish / refresh

From an `ai-agents-skills` checkout (or the installed runtime copy):

```bash
python3 canonical/runtime/skills/remote-bridge/publish_openclaw_adapter.py
# dry-run:
python3 canonical/runtime/skills/remote-bridge/publish_openclaw_adapter.py --dry-run
# custom dest:
python3 canonical/runtime/skills/remote-bridge/publish_openclaw_adapter.py \
  --dest ~/.openclaw/workspace/skills/aas-remote-bridge
```

What gets written:

| Destination | Source |
|-------------|--------|
| `SKILL.md` | `openclaw-adapter/SKILL.md` |
| `scripts/dispatch_aas.py` | `dispatch_aas.py` |
| `scripts/sync_remote_bridge_paths.py` | `sync_remote_bridge_paths.py` |
| `vendor/remote_bridge.py` | `remote_bridge.py` |
| `vendor/sync_remote_bridge_paths.py` | `sync_remote_bridge_paths.py` |

## Secrets / state sync

See `sync_remote_bridge_paths.py` and the skill body. Auto-sync runs on
`/aas` dispatch and on host `remote_bridge` mutators. Manual:

```bash
aas-remote-bridge-sync
# or
python3 …/sync_remote_bridge_paths.py --json
```

Disable: `AAS_REMOTE_BRIDGE_SYNC=0`.

## Do not

- Edit only the OpenClaw workspace copy and leave canonical stale
- Treat `~/.openclaw/workspace/skills/*` as the skill repository
- Bind-mount `~/.config` into the OpenClaw sandbox (blocked; use workspace mirrors)
