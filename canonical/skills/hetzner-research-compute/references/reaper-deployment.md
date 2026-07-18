# Hetzner reaper -- detached deployment (gated, deploy-time)

The reaper is the durable billing-stopper for the Hetzner lane (plan section 6, Arm 2). A
powered-off Hetzner server still bills; only DELETE stops it. Cloud-init's dead-man's-switch
can only power a box off, and the in-session `oneshot` / `down --orphans` teardown dies with
the agent session. The reaper (`hetzner_reaper.py`, in the runtime tree) deletes any labelled
server that is past-TTL, powered-off, stale-heartbeat, or orphaned (job-id not in the local
active-jobs ledger).

This page ships the deploy templates as copy-paste blocks and the step-by-step install. It is
documentation only: this repo never installs or activates a service. Enabling the reaper is a
deliberate, deploy-time action performed by a human, outside this repo. The templates are kept
here (the skill doc layer), not in the installable runtime tree, because the runtime tree is
kept inert -- it must never carry a live service unit, cron entry, or persistence marker.

## Why detached (the hard rule)

The reaper MUST run detached -- a systemd timer/service or a cron entry -- **never** as a child
of an agent session. Background children started inside a session are killed when the session
restarts, and a dead reaper is a server that bills forever.

## Token handling

The token reaches the reaper ONLY through a root-only environment file, never on argv
(`/proc/<pid>/cmdline` is world-readable) and never in an `hcloud context` file:

```
# /etc/ai-agents-skills/hetzner-reaper.env   (chmod 600, root-owned)
HCLOUD_TOKEN=<token from the dedicated least-privilege Hetzner project>
OPENCLAW_WORKSPACE=<runtime workspace holding config/research-compute.toml>
RUNTIME=/opt/ai-agents-skills/runtime
```

`OPENCLAW_WORKSPACE` lets the reaper find `research-compute.toml`, so orphan detection can read
the active-jobs ledger and the audit log is written. Without it the reaper still enforces TTL,
powered-off, and stale-heartbeat.

## systemd variant (recommended)

Save as `/etc/systemd/system/hetzner-reaper.service` (adjust `RUNTIME` / paths to the install):

```ini
[Unit]
Description=ai-agents-skills Hetzner reaper (delete past-TTL / powered-off / stale / orphaned servers)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/ai-agents-skills/hetzner-reaper.env
Environment=RUNTIME=/opt/ai-agents-skills/runtime
Environment=PYTHONDONTWRITEBYTECODE=1
WorkingDirectory=/tmp
ExecStart=/usr/bin/env PYTHONPATH=${RUNTIME}/workspace:${RUNTIME}/workspace/skills/hetzner-research-compute python3 -m hetzner_reaper reap
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Save as `/etc/systemd/system/hetzner-reaper.timer`:

```ini
[Unit]
Description=Run the ai-agents-skills Hetzner reaper every 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
AccuracySec=15s
Persistent=true
Unit=hetzner-reaper.service

[Install]
WantedBy=timers.target
```

Enable (as root). The `.timer` fires the oneshot `.service` every 2 minutes; `Persistent=true`
re-runs a missed pass after a reboot:

```
sudo systemctl daemon-reload
sudo systemctl enable --now hetzner-reaper.timer
systemctl list-timers hetzner-reaper.timer
journalctl -u hetzner-reaper -f
```

A continuously-running daemon variant is also supported: set the service to `Type=simple`,
`Restart=always`, and `ExecStart=... python3 -m hetzner_reaper reap --loop --interval 120`.

## cron variant

Use where systemd is unavailable; still detached (cron owns it, not a session). Save as
`/etc/cron.d/hetzner-reaper`:

```cron
SHELL=/bin/sh
*/2 * * * * root set -a; . /etc/ai-agents-skills/hetzner-reaper.env; set +a; PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$RUNTIME/workspace:$RUNTIME/workspace/skills/hetzner-research-compute" python3 -m hetzner_reaper reap >> /var/log/hetzner-reaper.log 2>&1
```

## Verify without deleting anything

```
PYTHONPATH="$RUNTIME/workspace:$RUNTIME/workspace/skills/hetzner-research-compute" \
  python3 -m hetzner_reaper reap --dry-run
```

## Emergency kill switch

Delete every managed server immediately, ignoring the reap predicate (the detached peer of the
driver's `down --all`):

```
PYTHONPATH="$RUNTIME/workspace:$RUNTIME/workspace/skills/hetzner-research-compute" \
  python3 -m hetzner_reaper kill
```

## Audit trail

Every provision, destroy, reap, and kill writes a redacted JSONL record to `hetzner-audit.jsonl`
under the broker state root (`memories/research-compute/`). Inspect it to confirm the reaper is
deleting what it should and that no server was left behind.
