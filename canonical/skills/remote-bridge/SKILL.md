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
& "$runtime\run_skill.ps1" "skills/remote-bridge/run_remote_bridge.ps1" <args>
& "$runtime\run_skill.ps1" "skills/remote-bridge/run_remote_bridge.ps1" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" \
  skills/remote-bridge/run_remote_bridge.sh <command> [args...]
```

## Secrets

Never commit real tokens. Copy the example and fill placeholders:

- Linux/WSL: `~/.config/remote-bridge/secrets.json`
- macOS: `~/Library/Application Support/remote-bridge/secrets.json` or XDG
- Windows: `%APPDATA%\remote-bridge\secrets.json`

The selected secrets file must be an owner-private, single-link regular file
inside an owner-controlled, non-writable-by-others directory; symlinked,
hard-linked, permissive, oversized, or changing files are rejected. On POSIX,
use mode `0600` for the file.

Env overrides: `REMOTE_BRIDGE_SECRETS_FILE`, `ZULIP_*`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_ALLOWED_CHAT_IDS`, `AAS_REMOTE_JOB_ID`.

**Do not** auto-read `~/.openclaw`. Prefer a **dedicated** Zulip bot user and a
**dedicated** Telegram bot (not OpenClaw’s).

### OpenClaw compatibility boundary

The host CLI does **not** import from, export to, or synchronize with
`~/.openclaw` before or after commands. Host secrets and mailbox state remain
host-owned. `sync_remote_bridge_paths.py` is shipped temporarily only as an
inert revocation stub. Replacing a previously managed copy requires an
explicitly reviewed backup-and-replace upgrade that preserves recovery data;
the default installer does not overwrite divergent copies. The stub never
inspects paths and is not part of normal `remote_bridge.py` execution.
Any future compatibility transfer requires an
explicit, separately reviewed one-way export that excludes secrets and treats
workspace content as untrusted.

## Source of truth and OpenClaw dual-route

Reusable logic lives in **`~/ai-agents-skills`** (this skill +
`canonical/runtime/skills/remote-bridge/`). Agent homes and
`~/.openclaw/workspace/skills/aas-remote-bridge/` are **install products**.

OpenClaw workspace publishing is currently fail-closed: the installed
revocation stub performs no destination reads or writes. Do not refresh or
create a workspace adapter until a descriptor-pinned, no-follow, recoverable
publisher has passed security review. The blocked publisher cannot replace or
clean up an already-published OpenClaw workspace copy.

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
| `handle-command --text "/aas …" --principal USER` or `--text-stdin` | Process one control command |
| `format-inbox --job ID [--consume]` | Claim/format inbox for prompts |
| `check-approval --job ID --digest HEX` | Consume matching allow reply |

Chat control commands (Zulip/Telegram body): `/aas help|status|approve|deny|say|instruct|stop|pause|resume|focus|doctor`.

## ARL / drive integration

`arm` and `drive` use `--notify auto` (default): if Zulip and/or Telegram credentials are present in
`~/.config/remote-bridge/secrets.json` (or env), progress events are sent
without an extra channel flag for legacy/off/monitor loops. Enforce mode also
requires `AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS=allow`; without that explicit
egress consent it produces local notification audit only. Prefer:

Notification and panel egress consent is independent of provider execution
trust. Selecting `trusted-local` never implies permission to send notification,
panel, PII, or secret-bearing payloads externally; the existing exact consent
and admission gates still apply.

```bash
# one-time arm (persists notify_channel on loop_state + registry)
… run_autonomous_research_loop.sh arm --dir <loop> --root <proj> --notify auto

# drive inherits arm/env/secrets (auto by default)
… run_autonomous_research_loop.sh drive --dir <loop> --root <proj> \
  --provider codex
# equivalent explicit: --notify auto
# enforce-mode network consent: AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS=allow
# silence: --notify off   or   AAS_AUTOLOOP_NOTIFY=off
```

Optional job id for topic routing / channel override:

```bash
export AAS_REMOTE_JOB_ID=example-job
# default when secrets present: Zulip primary, Telegram only if Zulip fails
# export AAS_AUTOLOOP_NOTIFY=zulip     # same as default primary
# export AAS_REMOTE_STRICT_NOTIFY_CHANNEL=zulip  # forbid every Telegram fallback
# export AAS_AUTOLOOP_NOTIFY=telegram  # Telegram only
# export AAS_AUTOLOOP_NOTIFY=both      # alias for primary+fallback (not dual)
# export AAS_AUTOLOOP_NOTIFY=off       # silence
```

`AAS_REMOTE_STRICT_NOTIFY_CHANNEL=zulip` is a send-boundary restriction, not
just a launch preference: explicit Telegram requests and Telegram credentials
added later are ignored while it is set. An invalid strict-channel value fails
closed.

Events notified (best-effort, never abort the loop): `drive_start`,
`drive_stop`, `iteration_ok` / `iteration_failed`, `quota_wait`, `paused`,
`terminal`, `driver_dead`.

**Not remote-notified** (local progress only):

- `iteration_start` — pairs with `iteration_ok` ~1s later
- watch ledger tick `iteration` — drive already owns completion notifies; sending
  both produced duplicate Zulip posts when `drive` and `watch` ran together

Identical bodies are deduped for 15s (in-process + per-loop disk). Materially
equivalent retries whose event ids or timing fields were rebuilt are deduped for
120s. Remote Bridge locks the semantic retry identity across the complete
check -> send -> remember sequence, so concurrent processes deliver it once;
material changes to status, content, agents, or compute remain deliverable.

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
- Zulip/Telegram endpoints require HTTPS, reject embedded URL credentials and
  redirects, and allow localhost HTTP only with
  `AAS_REMOTE_BRIDGE_ALLOW_HTTP_LOCALHOST=1`.
- Structured event redaction covers every nested string value before transport
  output or delivery-registry persistence; secret-bearing event ids are
  replaced by opaque digest-derived ids. Common explicit email, phone,
  government-id, address/DOB, and participant/patient/subject data is also
  redacted; the heuristic is not a completeness guarantee.
- Notification sends fail closed when the v2 secret/PII redactor cannot be
  loaded or raises; exact configured-secret replacement is not an acceptable
  fallback for unconfigured credentials or personal data.
- Delivery locks and the dedupe registry require private host-owned regular
  files and no-follow directory traversal.
- Prefer structured `--notify` over raw `--notify-cmd` (set `AAS_ALLOW_RAW_NOTIFY_CMD=1` only if needed).
- Platform “supported” claims need dated native smoke evidence per OS.
