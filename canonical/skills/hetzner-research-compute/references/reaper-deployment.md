# Hetzner reaper -- detached deployment (gated, deploy-time)

The reaper is the durable billing-stopper for the Hetzner lane. A powered-off Hetzner
server still bills; only DELETE stops billing. The in-session `oneshot` finalizer and
cloud-init shutdown backstop are important, but neither replaces a detached reaper.

This repository ships documentation and inert runtime commands only. It does not install,
enable, or attest a system service. A human administrator must perform this deployment.

## Security boundary

Live provisioning requires a short-lived lease produced after a successful detached reaper
pass. The lease replaces the old static environment attestation, which an agent could copy.
It is bound to the configured project identity, install scope, scheduler identity, and
reaper-relevant configuration digest, and expires in at most 15 minutes.

The deployment must satisfy all of these conditions:

- The runtime and every parent directory used by the privileged `attest` command are
  root-owned and not group/world writable. Never execute an agent-writable script as root.
- `[hetzner].reaper_lease_file` is absolute and its complete parent chain is root-owned and
  not group/world writable. `/etc/ai-agents-skills/hetzner-reaper-lease.json` is recommended.
- The lease is root-owned mode `0644`: it contains no credential and must be readable by the
  non-root provisioner, but the agent must not be able to write or replace it.
- The actual reaper pass runs as the ordinary agent account so audit and reservation files
  do not become root-owned. Only the post-success `attest` step runs as root.
- The root attester runs only after a successful reaper pass. A failed pass must not renew
  the lease.
- Native Windows remains recovery-only in this release because no equivalent Task Scheduler
  deployment and DACL-bound lease publisher has been natively attested.

Configure a stable, non-secret identity unique to the dedicated Hetzner project and the
exact scheduler identity. The values below must also appear in the root-owned runtime config:

```toml
[hetzner]
project_identity = "replace-with-stable-dedicated-project-identity"
reaper_lease_file = "/etc/ai-agents-skills/hetzner-reaper-lease.json"
reaper_scheduler_id = "hetzner-reaper.timer"
reaper_lease_max_age_seconds = 900
```

Create the protected lease directory before enabling the service:

```bash
sudo install -d -o root -g root -m 755 /etc/ai-agents-skills
```

## Token handling

The reaper process receives only the protected `AAS_COMPUTE_SECRETS_FILE` pointer. The
managed wrapper reads that absolute private file through the strict loader, permits only the
compute schema, and projects only `HCLOUD_TOKEN` and optional `HCLOUD_SSH_KEYS` into the
Hetzner process. The token is never placed on argv, in the unit, in the lease, or in an
`hcloud context` file.

The example below assumes:

- root-owned launcher resolver: `/usr/local/sbin/aas-credential-launcher`
  (installed below; a root-owned `/opt/ai-agents-skills/runtime` tree cannot be
  used here -- see *Why the scheduler cannot name a runtime path*)
- ordinary account: `REPLACE_AGENT_USER`
- home: `REPLACE_AGENT_HOME` (for example, `/srv/aas-agent`)
- private compute authority:
  `REPLACE_AGENT_HOME/.config/ai-agents-skills/compute.env`
- writable broker state:
  `REPLACE_AGENT_HOME/.local/share/ai-agents-skills/memories/research-compute`

Replace every placeholder and confirm the runtime/config parent chain is root-controlled
before enabling the unit.

## Why the scheduler cannot name a runtime path

`run_hetzner_reaper.sh` is a credential-bearing command, so `run_skill.sh` arms its
credential contract from the command path alone and then refuses to run unless it is itself
executing from a root-owned component generation:

```text
/usr/local/libexec/coding-system/components/ai-agents-skills/<40-hex>/canonical/runtime
```

The gate tests the launcher's own location, walks the entire parent chain for root
ownership, rejects symlinks, and has no override. A root-owned `/opt/ai-agents-skills/runtime`
tree therefore fails exactly as the per-user `~/.local/share/ai-agents-skills/runtime` copy
does -- `credential-bearing launch requires a root-owned exact AAS component generation`,
exit 127, for every verb including `reap --dry-run`.

Naming one `<40-hex>` in the unit holds only until that generation is pruned, and the
directory name is a git commit id, so it sorts in no useful order. Install this resolver
instead and let the scheduler call it. It is a per-host operator file; the installer does
not place it.

```bash
#!/usr/bin/bash
# Resolve the ai-agents-skills component generation that run_skill.sh's credential
# gate accepts, then exec its launcher with the arguments given.
#
# The gate requires the launcher to sit under
#   /usr/local/libexec/coding-system/components/ai-agents-skills/<40-hex>/canonical/runtime
# on a root-owned, symlink-free chain, and it is not overridable. No other layout
# satisfies it -- neither the per-user ~/.local copy nor a root-owned /opt tree.
# Pinning one <40-hex> in a unit file holds only until that generation is pruned,
# so resolve on every invocation instead.
#
# The directory name is a git commit id and carries no ordering. The store also
# normalises every file mtime to the epoch, which leaves the generation
# directory's own mtime as the one recency signal, so selection is by publish
# time: an older generation can carry a stale driver and fail in the lane rather
# than at the gate.
set -u

store=/usr/local/libexec/coding-system/components/ai-agents-skills
launcher=""
newest=0

for gen in "$store"/*/; do
    gen="${gen%/}"
    [ -f "$gen/manifest/credential-runtime.json" ] || continue
    [ -x "$gen/canonical/runtime/runners/run_skill.sh" ] || continue
    stamp="$(stat -c %Y "$gen" 2>/dev/null || stat -f %m "$gen" 2>/dev/null)" || continue
    [ "${stamp:-0}" -gt "$newest" ] || continue
    newest="$stamp"
    launcher="$gen/canonical/runtime/runners/run_skill.sh"
done

if [ -z "$launcher" ]; then
    printf 'aas-credential-launcher: no component generation under %s carries manifest/credential-runtime.json\n' \
        "$store" >&2
    exit 78
fi

# Operator check: report the resolved generation without launching anything.
if [ "${1-}" = "--print-launcher" ]; then
    printf '%s\n' "$launcher"
    exit 0
fi

exec /usr/bin/bash "$launcher" "$@"
```

Any manifest-bearing generation passes the gate, which is why the resolver ranks by publish
time rather than stopping at the first match: an older generation launches cleanly and then
fails in the driver, where the cause is far harder to see.

Install it root-owned and confirm which generation it resolves before enabling any
scheduler:

```bash
sudo install -o root -g root -m 0755 aas-credential-launcher /usr/local/sbin/
/usr/local/sbin/aas-credential-launcher --print-launcher
```

## Recommended systemd deployment

Save as `/etc/systemd/system/hetzner-reaper.service`:

```ini
[Unit]
Description=ai-agents-skills Hetzner reaper
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=PYTHONDONTWRITEBYTECODE=1
WorkingDirectory=/tmp
ExecStart=/usr/sbin/runuser --user REPLACE_AGENT_USER -- /usr/bin/env HOME=REPLACE_AGENT_HOME USER=REPLACE_AGENT_USER LOGNAME=REPLACE_AGENT_USER AAS_COMPUTE_SECRETS_FILE=REPLACE_AGENT_HOME/.config/ai-agents-skills/compute.env /usr/local/sbin/aas-credential-launcher skills/hetzner-research-compute/run_hetzner_reaper.sh reap
ExecStartPost=/usr/local/sbin/aas-credential-launcher skills/hetzner-research-compute/run_hetzner_reaper.sh attest --scheduler-kind systemd --scheduler-id hetzner-reaper.timer
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=REPLACE_AGENT_HOME/.local/share/ai-agents-skills/memories/research-compute /etc/ai-agents-skills
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

First validate the ordinary-account credential path and deletion plan without mutating
servers:

```bash
sudo -u REPLACE_AGENT_USER \
  env HOME=REPLACE_AGENT_HOME \
  AAS_COMPUTE_SECRETS_FILE=REPLACE_AGENT_HOME/.config/ai-agents-skills/compute.env \
  /usr/local/sbin/aas-credential-launcher \
  skills/hetzner-research-compute/run_hetzner_reaper.sh reap --dry-run
```

Then enable the timer and wait for one successful scheduled pass:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hetzner-reaper.timer
systemctl list-timers hetzner-reaper.timer
journalctl -u hetzner-reaper.service -n 50 --no-pager
```

Verify the scheduler succeeded and the lease is root-owned, `0644`, recent, and located
under the protected chain. Do not create the lease manually; the driver validates its
structured contents and bindings, not merely its existence.

## Root cron alternative

Where systemd is unavailable, configure these values instead:

```toml
[hetzner]
reaper_scheduler_id = "cron:/etc/cron.d/hetzner-reaper"
```

Save the following as `/etc/cron.d/hetzner-reaper` after replacing the account paths. The
`&&` is deliberate: the root attester runs only after the ordinary-account reap succeeds.

```cron
SHELL=/bin/sh
*/2 * * * * root /usr/sbin/runuser --user REPLACE_AGENT_USER -- /usr/bin/env HOME=REPLACE_AGENT_HOME USER=REPLACE_AGENT_USER LOGNAME=REPLACE_AGENT_USER AAS_COMPUTE_SECRETS_FILE=REPLACE_AGENT_HOME/.config/ai-agents-skills/compute.env /usr/local/sbin/aas-credential-launcher skills/hetzner-research-compute/run_hetzner_reaper.sh reap >> /var/log/hetzner-reaper.log 2>&1 && /usr/local/sbin/aas-credential-launcher skills/hetzner-research-compute/run_hetzner_reaper.sh attest --scheduler-kind cron --scheduler-id cron:/etc/cron.d/hetzner-reaper >> /var/log/hetzner-reaper.log 2>&1
```

## Native Windows status (recovery only)

The managed PowerShell target supports planning, manual dry-run reaping, and scoped
recovery/teardown. Live `up` and `oneshot` fail closed on native Windows because a durable
Task Scheduler reaper and protected lease publisher have not been natively attested. Use
WSL/Linux for paid provisioning; do not bypass the driver gate.

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
$env:AAS_COMPUTE_SECRETS_FILE = "$env:USERPROFILE\.config\ai-agents-skills\compute.env"
& "$runtime\run_skill.ps1" `
  "skills/hetzner-research-compute/run_hetzner_reaper.ps1" `
  reap --dry-run
```

## Emergency project-wide deletion

Broad deletion is constrained to servers carrying both the AAS management label and the
configured `project-scope` label. It requires a fresh protected lease and an exact phrase
bound to the current target count and digest. First obtain the read-only inventory:

```bash
# Credential-bearing lanes must launch from a root-owned AAS component
# generation; the per-user runtime copy is refused by the credential gate.
# The generation directory is named for a git commit, so its name carries no
# ordering -- pick the newest publish, which the store records as its mtime.
launcher="${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh"
newest=0
for gen in /usr/local/libexec/coding-system/components/ai-agents-skills/*/; do
  gen="${gen%/}"
  [ -f "$gen/manifest/credential-runtime.json" ] || continue
  [ -x "$gen/canonical/runtime/runners/run_skill.sh" ] || continue
  stamp="$(stat -c %Y "$gen" 2>/dev/null || stat -f %m "$gen" 2>/dev/null)" || continue
  [ "${stamp:-0}" -gt "$newest" ] || continue
  newest="$stamp"
  launcher="$gen/canonical/runtime/runners/run_skill.sh"
done
AAS_COMPUTE_SECRETS_FILE="$HOME/.config/ai-agents-skills/compute.env" \
  bash "$launcher" \
  skills/hetzner-research-compute/run_hetzner_reaper.sh \
  kill --dry-run
```

Review every target, then copy the exact `required_confirmation` string from that output:

```bash
AAS_COMPUTE_SECRETS_FILE="$HOME/.config/ai-agents-skills/compute.env" \
  bash "$launcher" \
  skills/hetzner-research-compute/run_hetzner_reaper.sh \
  kill --confirm-project-wide 'DELETE-AAS-HETZNER project=... count=... digest=...'
```

If the target set changes between the two commands, the live command rejects the old phrase.

## Audit trail

Every provision, destroy, reap, and kill attempts a redacted JSONL record in
`hetzner-audit.jsonl` under the broker state root. Audit or reservation-reconciliation
failure is reported without skipping later billable servers. The detached reaper spans
install scopes for TTL, powered-off, and stale-heartbeat cleanup, but only the exact current
install scope can use or reconcile the local reservation ledger.
