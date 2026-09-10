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

## Exactly one scheduler leg attests

The lease is one file holding one scheduler record, and both `attest` and the provisioner
compare it against the scalar `reaper_scheduler_id`. A second scheduler leg therefore adds
no redundancy: the leg whose identity does not match the configuration fails on every pass
with

```text
reaper scheduler identity does not match configuration
```

Only the attestation fails. That leg's `reap` still runs and still deletes servers, so the
symptom is a scheduler that reports failure forever while the lease stays fresh from the
other leg. Read the leg's own output — `~/.local/state/ai-agents-skills/hetzner-reaper.log`
for the crontab recipe, `systemctl --user status hetzner-reaper.service` for the timer — and
retire one leg rather than trying to reconcile them.

Three scheduler kinds are accepted: `cron`, `systemd-user`, and system-scope `systemd`. The
last is accepted only when the unit runs as the agent account, because `attest` refuses to
publish a lease as root; such a unit must set `User=` to that account and reach the same
`$HOME`.

Editing `project_identity`, `reaper_scheduler_id`, `max_server_hours`, or
`max_concurrent_servers` changes the `config_digest` carried in the lease and invalidates
the live one. Provisioning fails closed until the next successful pass republishes it, so
after such an edit let one scheduler period elapse, or run `attest` once by hand, before the
next `up`.

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

`reaper_lease_max_age_seconds` is clamped to a hard 900-second ceiling whatever the config
says, so the 10-minute period leaves 300 seconds of slack. A pass may run up to five minutes
late without expiring the lease; a wholly skipped pass expires it, and provisioning fails
closed until a later pass succeeds.

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
lease fields and error message.

A previously installed system-scope scheduler is not disabled by these recipes, and it is
not harmless either: if its identity does not match `reaper_scheduler_id` it fails on every
pass as described above, and if it does match it competes for the same single lease record.
Retire it through whatever mechanism installed it before adopting a recipe here, or keep it
as the one attesting leg and skip both recipes.

## Native Windows status (recovery only)

The managed `run_hetzner_reaper.ps1` target supports manual dry-run reaping and
scoped recovery/teardown. Live `up` and `oneshot` fail closed on native Windows
until a durable Task Scheduler reaper and protected lease publisher are attested.
Use WSL/Linux for paid provisioning.
