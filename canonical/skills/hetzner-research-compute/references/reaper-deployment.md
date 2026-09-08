# Hetzner reaper — deployment under the agent account

The detached reaper stops billing when in-session cleanup cannot run. A powered-off
Hetzner server still bills; only deletion stops billing. This repository ships the
commands and recipes; it does not install or enable a scheduler.

## Lease and credential boundary

After each successful scheduled pass, `attest` publishes a short-lived lease bound to
project identity, install scope, scheduler identity, and the reaper configuration digest.
The lease expires in at most 15 minutes and must be owner-private 0600 beneath an
owner-controlled parent chain. The default is
`~/.local/state/ai-agents-skills/hetzner-reaper-lease.json`.

The lease proves a scheduler under the same account ran recently and is bound to this
config; it is not evidence outside the agent's authority. The previous root design is
retired because the launcher no longer runs from a root generation.

The protected `AAS_COMPUTE_SECRETS_FILE` pointer supplies the reaper's compute authority.
The wrapper projects only the permitted Hetzner values. Tokens never belong in argv,
unit files, leases, or an `hcloud context` file.

## User crontab (default)

Configure the lease and scheduler identity in the broker's research-compute config:

```toml
[hetzner]
project_identity = "replace-with-stable-dedicated-project-identity"
reaper_lease_file = "~/.local/state/ai-agents-skills/hetzner-reaper-lease.json"
reaper_scheduler_id = "cron:user:<your login>"
reaper_lease_max_age_seconds = 900
```

Replace `<your login>` with the account name returned by `id -un`; it must match
exactly the scheduler ID passed below. Add this single line to that user's crontab:

```cron
*/10 * * * * L="$HOME/.local/share/ai-agents-skills/runtime/run_skill.sh"; W="$HOME/.openclaw/workspace"; S="$HOME/.local/state/ai-agents-skills"; (umask 077; mkdir -p "$S"); AAS_AUTOLOOP_COMPUTE_WORKSPACE="$W" AAS_COMPUTE_SECRETS_FILE="$HOME/.config/ai-agents-skills/compute.env" "$L" skills/hetzner-research-compute/run_hetzner_reaper.sh reap >>"$S/hetzner-reaper.log" 2>&1 && AAS_AUTOLOOP_COMPUTE_WORKSPACE="$W" "$L" skills/hetzner-research-compute/run_hetzner_reaper.sh attest --scheduler-kind cron --scheduler-id "cron:user:$(id -un)" >>"$S/hetzner-reaper.log" 2>&1
```

`&&` is deliberate: only a successful `reap` may renew the lease. The launcher is
executed directly so its privileged-mode Bash shebang applies. Runtime variables
are scoped to each invocation. The subshell's `umask 077` creates private state
directories; existing parent directories must already be owner-controlled.

## systemd --user (alternative)

Set `reaper_scheduler_id = "hetzner-reaper.timer"` in the same config. Save these
complete user unit files:

```ini
# ~/.config/systemd/user/hetzner-reaper.service
[Unit]
Description=Hetzner research-compute reaper (user-level)

[Service]
Type=oneshot
WorkingDirectory=%h/.openclaw/workspace
Environment=AAS_AUTOLOOP_COMPUTE_WORKSPACE=%h/.openclaw/workspace
Environment=AAS_COMPUTE_SECRETS_FILE=%h/.config/ai-agents-skills/compute.env
UMask=0077
ExecStart=%h/.local/share/ai-agents-skills/runtime/run_skill.sh skills/hetzner-research-compute/run_hetzner_reaper.sh reap
ExecStartPost=%h/.local/share/ai-agents-skills/runtime/run_skill.sh skills/hetzner-research-compute/run_hetzner_reaper.sh attest --scheduler-kind systemd-user --scheduler-id hetzner-reaper.timer
```

```ini
# ~/.config/systemd/user/hetzner-reaper.timer
[Unit]
Description=Run the Hetzner research-compute reaper every 10 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
```

Then enable the timer:

```bash
systemctl --user daemon-reload && systemctl --user enable --now hetzner-reaper.timer
```

Without lingering, a user's timers run only while that user has a session.
`loginctl enable-linger` may require an administrator, so the crontab recipe is the
default. Both recipes assume the same broker workspace and private compute authority;
adjust those paths together if the installation uses another workspace.

## Verify the scheduled pass

After installing one of the two schedulers, run the driver doctor and verify that
`reaper_lease.present` and `reaper_lease.fresh` are true:

```bash
AAS_AUTOLOOP_COMPUTE_WORKSPACE="$HOME/.openclaw/workspace" \
  "$HOME/.local/share/ai-agents-skills/runtime/run_skill.sh" \
  skills/hetzner-research-compute/run_hetzner_research_compute.sh doctor
```

Doctor is read-only and exits zero even if the lease is absent or stale; inspect the
lease fields and error message. Any old independently installed root scheduler must
be handled by the user; this recipe does not disable it.

## Native Windows status (recovery only)

The managed `run_hetzner_reaper.ps1` target supports manual dry-run reaping and
scoped recovery/teardown. Live `up` and `oneshot` fail closed on native Windows
until a durable Task Scheduler reaper and protected lease publisher are attested.
Use WSL/Linux for paid provisioning.
