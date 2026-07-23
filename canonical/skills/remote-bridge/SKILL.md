---
name: remote-bridge
description: Cross-target remote control via Zulip (default control) and optional Telegram mobile notify, with mailbox approvals/instructions for autonomous research loops. Not for OpenClaw.
user-invocable: true
disable-model-invocation: false
metadata:
  short-description: Zulip/Telegram remote control mailbox for AAS agents
  requires:
    bins:
      - python3
---

# Remote Bridge

Cross-platform remote control plane for **claude, codex, grok, kimi, deepseek, opencode,
copilot, antigravity** (not OpenClaw).

| Channel | Role |
|---------|------|
| **Zulip** | Default **control** + primary **notify** (`Research` / `job/<job_id>`) |
| **Telegram** | Mobile **notify fallback** only when Zulip send fails; inbound only with a dedicated bot |

### Notify policy (default)

**Zulip first. Telegram only if Zulip fails.** Sends never dual-spam both
channels on success (`stop_on_first_success`).

| Token | Behavior |
|-------|----------|
| `auto` / default | Zulip if configured, else Telegram |
| `zulip` | Try Zulip; fall back to Telegram on failure |
| `both` | Same as Zulip-primary + Telegram-fallback (alias, **not** dual fan-out) |
| `telegram` | Telegram only (explicit) |
| `off` | Silence |

Does **not** inject messages into live TUI chats. Continuations use the on-disk
mailbox and headless `drive`, or a local PreToolUse gate (Grok; Claude
evidence-gated).

## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/remote-bridge/run_remote_bridge.bat" <args>
& "$runtime\run_skill.bat" "skills/remote-bridge/run_remote_bridge.ps1" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

```bash
bash ~/.local/share/ai-agents-skills/runtime/run_skill.sh \
  skills/remote-bridge/run_remote_bridge.sh <command> [args...]
```

## Secrets

Never commit real tokens. Copy the example and fill placeholders:

- Linux/WSL: `~/.config/remote-bridge/secrets.json`
- macOS: `~/Library/Application Support/remote-bridge/secrets.json` or XDG
- Windows: `%APPDATA%\remote-bridge\secrets.json`

Env overrides: `REMOTE_BRIDGE_SECRETS_FILE`, `ZULIP_*`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_ALLOWED_CHAT_IDS`, `AAS_REMOTE_JOB_ID`.

**Do not** auto-read `~/.openclaw`. Prefer a **dedicated** Zulip bot user and a
**dedicated** Telegram bot (not OpenClaw’s).

### Host ↔ OpenClaw workspace secrets/state sync

OpenClaw sandbox cannot bind-mount `~/.config`. When a dual-route OpenClaw
adapter is present, secrets and mailbox state are mirrored (newer-wins) between:

| Side | Secrets | State |
|------|---------|-------|
| Host | `~/.config/remote-bridge/secrets.json` | `~/.local/share/ai-agents-skills/remote-bridge` |
| Workspace | `~/.openclaw/workspace/secrets/remote-bridge/secrets.json` | `~/.openclaw/workspace/.remote-bridge-state` |

- Auto: `dispatch_aas.py` before `/aas`; host `remote_bridge.py` before/after
  send/arm/handle/etc.
- Manual: `aas-remote-bridge-sync` or
  `python3 …/sync_remote_bridge_paths.py --json`
- Disable: `AAS_REMOTE_BRIDGE_SYNC=0`
- Engine: `canonical/runtime/skills/remote-bridge/sync_remote_bridge_paths.py`

## Source of truth and OpenClaw dual-route

Reusable logic lives in **`~/ai-agents-skills`** (this skill +
`canonical/runtime/skills/remote-bridge/`). Agent homes and
`~/.openclaw/workspace/skills/aas-remote-bridge/` are **install products**.

For `/aas` inside OpenClaw, publish the dual-route adapter from canonical:

```bash
python3 canonical/runtime/skills/remote-bridge/publish_openclaw_adapter.py
```

See `canonical/runtime/skills/remote-bridge/openclaw-adapter/README.md`.

## Commands

| Command | Purpose |
|---------|---------|
| `selftest` | Offline smoke (no network) |
| `show-config` | Redacted config |
| `doctor` | State root, jobs, channels (`--live` optional) |
| `arm --job ID --provider P --cwd DIR [--loop DIR]` | Create mailbox job |
| `status` | List jobs + pending requests |
| `send --text "…" [--channel zulip\|telegram\|both\|auto] [--dry-run]` | Notify (Zulip-primary; Telegram fallback) |
| `request-approval --job ID --tool T [--wait --timeout N]` | Create approval + optional wait |
| `instruct --job ID --text "…"` | Push inbox item |
| `handle-command --text "/aas …" --principal USER` | Process one control command |
| `format-inbox --job ID [--consume]` | Claim/format inbox for prompts |
| `check-approval --job ID --digest HEX` | Consume matching allow reply |

Chat control commands (Zulip/Telegram body): `/aas help|status|approve|deny|say|instruct|stop|pause|resume|focus|doctor`.

## ARL / drive integration

**Default-on when secrets are configured.** `arm` and `drive` use `--notify auto`
(default): if Zulip and/or Telegram credentials are present in
`~/.config/remote-bridge/secrets.json` (or env), progress events are sent
without an extra flag. Prefer:

```bash
# one-time arm (persists notify_channel on loop_state + registry)
… run_autonomous_research_loop.sh arm --dir <loop> --root <proj> --notify auto

# drive inherits arm/env/secrets (auto by default)
… run_autonomous_research_loop.sh drive --dir <loop> --root <proj> \
  --provider codex
# equivalent explicit: --notify auto
# silence: --notify off   or   AAS_AUTOLOOP_NOTIFY=off
```

Optional job id for topic routing / channel override:

```bash
export AAS_REMOTE_JOB_ID=example-job
# default when secrets present: Zulip primary, Telegram only if Zulip fails
# export AAS_AUTOLOOP_NOTIFY=zulip     # same as default primary
# export AAS_AUTOLOOP_NOTIFY=telegram  # Telegram only
# export AAS_AUTOLOOP_NOTIFY=both      # alias for primary+fallback (not dual)
# export AAS_AUTOLOOP_NOTIFY=off       # silence
```

Events notified (best-effort, never abort the loop): `drive_start`,
`drive_stop`, `iteration_ok` / `iteration_failed`, `quota_wait`, `paused`,
`terminal`, `driver_dead`. **`iteration_start` is not notified** (it pairs with
`iteration_ok` ~1s later on the same objective and looked like double posts).
Identical notify bodies are also deduped for 15s in-process.

Headless iterations inject a labeled **data-only** inbox block when
`AAS_REMOTE_JOB_ID` is set. Approvals for auto-approve providers (`--yolo`,
full-auto) are **advisory** unless a live PreToolUse gate is installed.

## Grok live gate (optional)

Example hook: `hooks/grok-remote-bridge-gate.json.example`  
Script: `hooks/pretooluse_deny_until_approved.py` (local FS only; deny-until-approved).
Grok hooks **fail-open** on crash/timeout — not hard OS security.

## Security notes

- Allowlist users on Zulip/Telegram.
- Compromised allowlisted chat ≈ operator authority for headless soft path.
- Prefer structured `--notify` over raw `--notify-cmd` (set `AAS_ALLOW_RAW_NOTIFY_CMD=1` only if needed).
- Platform “supported” claims need dated native smoke evidence per OS.
