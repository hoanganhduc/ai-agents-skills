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

Cross-platform remote control plane for **claude, codex, grok, deepseek, opencode,
copilot, antigravity** (not OpenClaw).

| Channel | Role |
|---------|------|
| **Zulip** | Default **control** + notify (`aas-remote` / `job/<job_id>`) |
| **Telegram** | Mobile **notify**; inbound only with a dedicated bot |

Does **not** inject messages into live TUI chats. Continuations use the on-disk
mailbox and headless `drive`, or a local PreToolUse gate (Grok; Claude
evidence-gated).

## Windows Runtime Commands

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/remote-bridge/run_remote_bridge.bat" <args>
```

POSIX:

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

## Commands

| Command | Purpose |
|---------|---------|
| `selftest` | Offline smoke (no network) |
| `show-config` | Redacted config |
| `doctor` | State root, jobs, channels (`--live` optional) |
| `arm --job ID --provider P --cwd DIR [--loop DIR]` | Create mailbox job |
| `status` | List jobs + pending requests |
| `send --text "…" [--channel zulip\|telegram\|both] [--dry-run]` | Notify |
| `request-approval --job ID --tool T [--wait --timeout N]` | Create approval + optional wait |
| `instruct --job ID --text "…"` | Push inbox item |
| `handle-command --text "/aas …" --principal USER` | Process one control command |
| `format-inbox --job ID [--consume]` | Claim/format inbox for prompts |
| `check-approval --job ID --digest HEX` | Consume matching allow reply |

Chat control commands (Zulip/Telegram body): `/aas help|status|approve|deny|say|instruct|stop|pause|resume|focus|doctor`.

## ARL / drive integration

```bash
export AAS_REMOTE_JOB_ID=clawfree
export AAS_AUTOLOOP_NOTIFY=zulip   # or telegram|both
# arm job first, then:
… run_autonomous_research_loop.sh drive --dir <loop> --root <proj> \
  --provider grok --notify zulip
```

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
