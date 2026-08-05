# Scripted force-loop (discovery)

**Primary install path:** runtime pack under

`skills/autonomous-research-loop-runtime/force-loop/`

This markdown file is **discovery only**. The multi-file kit installs via
`manifest/runtime.yaml` runtime-files with the ARL runtime skill (all install
targets; Linux / macOS / Windows / WSL).

## Default operator entry

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/autonomous-research-loop-runtime/force-loop/run_force_loop.sh \
  bootstrap --loop "$LOOP" --root "$ROOT" --profile formal --goal "…"
```

Windows: `run_force_loop.ps1` via `run_skill.ps1`.

## Defaults

- Goal Focus **enforce**
- goal_priority **hard**
- **notify** auto/on
- Foreground start on all OS

Notification defaults remain Zulip-primary with optional Telegram fallback.
For a campaign that must never use Telegram, export
`AAS_REMOTE_STRICT_NOTIFY_CHANNEL=zulip` in the launching environment; the
bridge then enforces Zulip at every send boundary.
The Zulip stream (`zulip.control_stream` in the remote-bridge secrets) must
name an **existing** channel — the bridge never creates streams; verify it and
optionally live-auth in a launch preflight (see the pack `OPERATOR_RUNBOOK.md`,
"Notify channel and launch preflight").

Under trusted-local attestation the whole provider package tree must be
host-controlled (no group/other write, no symlinks); npm installs usually need
a one-time `chmod -R go-w` (see `OPERATOR_RUNBOOK.md`, "Trusted-local
attestation preflight").

See the pack `README.md` and `OPERATOR_RUNBOOK.md`.

## Not this kit

| Term | Meaning |
|------|---------|
| `formal_policy=force` | Host formal hygiene tick — different |
| TikZ `force_loop` | Figure repair credits — different |
| Thin formal sample | `sample-arl-headless-driver-with-formal` — env layer only |
