# Hetzner Cloud offload contract

Patterns for renting a disposable Hetzner Cloud server to run a portable research job
bundle at full cores, then destroying it. This is the Hetzner lane of the `research_compute`
broker, peer to the Kaggle, Modal, and GitHub Actions lanes. Treat the agent itself as the adversary
(looping, crashing, self-approving): every rule below exists to stop a compromised or runaway
agent from leaking a token or leaving a paid server running.

## Preconditions

- A dedicated, least-privilege Hetzner project with a project server-limit the platform
  enforces. `HCLOUD_TOKEN` for that project is set in the environment outside this repo.
- The `hcloud` CLI is installed and on `PATH`. The driver shells out to it; it never writes
  an `hcloud context` file (which would persist the token in plaintext `cli.toml`).
- `[hetzner]` is enabled in `research-compute.toml` with budget caps and allow-lists, and
  `doctor` passes.
- `[hetzner].bundle_root` is an absolute, operator-approved, link-free directory. Job bundles
  are immediate children of this root; agent-selected paths outside it are never uploadable.
- `[hetzner].project_identity`, absolute `reaper_lease_file`, exact `reaper_scheduler_id`,
  and a lease lifetime no greater than 900 seconds are configured.
- A detached systemd/cron reaper has completed successfully and its root-controlled
  post-step has published a fresh lease. Live creates fail closed without current evidence
  bound to the project, install scope, scheduler, and relevant configuration.

## Portable job bundle (backend-agnostic)

One bundle runs unchanged on local, Kaggle, Modal, Hetzner, or GitHub Actions; only the fan-out
harness differs. On a rented Hetzner box the harness is dedicated and disposable, so it runs
at full `nproc` with no throttle.

- `manifest.json` -- queues, remaining counts, core-hour estimate, `deps[]`, `arch`, and `verify` controls.
- `worker <queue> <chunk_idx> <num_chunks>` -- round-robin slice, per-job checkpoint with flush and fsync, and skips ids already present in `out/` (resume).
- `run.sh` -- `CORES` fan-out via `xargs -P`, then merge. Do not use a process pool whose terminate step can orphan workers.
- `merge` -- folds `out/*` into a single result, asserts the `manifest.verify` controls, and exits nonzero on empty, partial, or any FAIL (a vacuity guard: empty inputs must never look like success).
- `out/` -- the only writable, fetch-back, resume surface.

## Driver contract (`hetzner_driver.py`)

Planning verbs are free and never touch a server. Lifecycle verbs may hold a paid server
and require `HCLOUD_TOKEN` plus an explicit confirm.

- `bootstrap` -- check the `hcloud` CLI and token presence; report `doctor`. Never provisions.
- `doctor` -- offline readiness: lane enabled, token present, `hcloud` installed, configured caps and server types. No network call.
- `preflight --job DIR [--bundle-sha256 HEX] [--json]` -- the plan the router consumes: server type, region, estimated wall hours, estimated EUR, arch, budget verdict, and the exact `required_bundle_sha256`. Supplying `--bundle-sha256` also verifies that the approved digest still matches. No provisioning.
- `up --bundle-sha256 HEX` -- create one labelled server from the exact approved immutable bundle snapshot, budget-gated. Refuses without token and confirm.
- `push --bundle-sha256 HEX` -- copy that exact approved snapshot with rsync over pinned SSH, after waiting for sshd and refetching the server by immutable numeric ID.
- `run` -- detached, full-core execution on the server.
- `status` -- server and job state.
- `wait` -- poll until the run finishes or the wall-clock cap is hit.
- `fetch` -- copy results back and verify they are well formed.
- `down [--all|--orphans]` -- DESTROY. `--all` is the project-scoped kill switch;
  `--orphans` removes current-install servers absent from the authoritative active-jobs ledger.
- `oneshot --bundle-sha256 HEX` -- `up -> push -> run -> wait -> fetch -> down` for the exact approved snapshot, under a guaranteed teardown so any exit still destroys.

Use `--dry-run` on `up`, `down`, and `oneshot` with no provisioning or deletion.
`down --all --dry-run` performs a read-only inventory to emit an exact confirmation phrase
bound to the target count and digest; it is intentionally not offline.

## Server selection

`wall_h = core_h / vcpu`, because a rented box runs dedicated at full cores. The router picks
the cheapest configured type whose vCPU meets the requested parallelism and whose RAM meets
the estimate. The default rate card is the current orderable x86 generation: `cpx22` (2 AMD
cores) for small jobs, `cpx62` (16 AMD cores) for up to 16-way fan-out, and `ccx63` (48
dedicated cores) for larger jobs or a wall-time floor. Hetzner ARM (`cax*`) is
supply-constrained and omitted from the defaults; override `[hetzner.server_types]` per
account. GPU jobs are inadequate on this lane in v1, so the router skips Hetzner and
continues to the next GPU-capable lane in the configured order.

`preflight` and `up` availability-check the live datacenter list (`hcloud datacenter list`,
read-only, through the mockable command runner) and provision the cheapest adequate
**orderable** `(type, location)` from the allow-list, falling back to the next combo on a
stock-out. This is the durable fix for a stocked-out type or region (such as ARM's): the lane
degrades to an available combo instead of failing to provision. `location` and
`allowed_locations` default to the current orderable regions (`nbg1`, `hel1`, `sin`; `fsn1`
has no orderable types).

## Lifecycle invariant

A paid server exists only between `PROVISIONING` and `DESTROYED`. Every terminal path
(success, failure, timeout, boot-fail, push-fail, crash) routes through `DESTROYED`. Failure
and timeout paths fetch checkpoints before destroy, so a run is always resumable from `out/`.

## Guardrails

- **Token and tools** -- `HCLOUD_TOKEN` is injected only into the pinned `hcloud` subprocess environment, never on argv (`/proc/<pid>/cmdline` is world-readable), never logged, never on a server, and never in an `hcloud context` file. SSH/rsync/scp receive no Hetzner token, use pinned system binaries and `ssh -F /dev/null`, and run with a minimal environment. A redaction filter covers all agent-readable output.
- **Bundle upload** -- before any copy, the driver bounds and snapshots a child of the approved
  `bundle_root` through no-follow descriptors. It rejects links/reparse points, hard links,
  special files, authority-like filenames, protected-secret inode overlap, unsafe names,
  unsafe ownership/modes, oversized/deep trees, and manifest/job mismatches. One private
  immutable snapshot and deterministic digest are held through `up`, `push`, or the complete
  `oneshot`. `preflight` returns the full SHA-256 for operator approval, and every lifecycle
  entry point verifies that exact approved digest before create or upload. `push` then refetches
  the server by immutable numeric ID and rechecks its complete digest labels and address
  immediately before rsync. Native Windows bundle upload is disabled; use WSL/Linux.
- **Labels** -- every server carries `managed-by`, `project-scope`, `install-scope`, `job-id`,
  `owner`, `ttl`, `bundle-sha256-high`, and `bundle-sha256-low`; the two label halves reconstruct
  the complete approved SHA-256. Broad inventory and deletion require the configured project
  scope; normal job operations additionally require the current install scope.
- **Root SSH** -- `up` accepts only an explicit, nonempty `HCLOUD_SSH_KEYS` allowlist, verifies
  every selected name against a fresh project SSH-key inventory, and refuses missing,
  duplicate, or keyless selections. It never attaches every project key implicitly and does
  not accept the legacy `HETZNER_SSH_KEYS` variable. It separately generates an Ed25519 server
  host identity, embeds it in managed
  cloud-init, and writes an exact private per-job `known_hosts` record. SSH/SCP/rsync require
  that pin with strict checking and disabled host-key updates; TOFU, `accept-new`, global
  known-hosts, and `/dev/null` user-known-hosts are not accepted. `push` waits for sshd before
  copying because `hcloud` can report `running` before cloud-init starts it.
- **Budget** -- a fail-closed gate reserves the pessimistic worst case (`rate x ceil(max_server_hours) x count + IPv4`) in the shared append-only ledger before any create. It refuses above the per-job cap (the auto-approve envelope), above the concurrent-server cap, or when it would push the day past the daily cap.
- **Reservation release** -- local bundle validation, placement, login-key discovery, host-key
  generation, and cloud-init staging complete before budget is reserved. The `job_id` is also
  validated before the gate. After a create attempt fails, a reservation is released only if
  an exact scoped inventory proves no server exists. An unreadable inventory or surviving
  server keeps the reservation: over-reserving costs headroom, while under-reserving permits
  overspend.
- **Confirm** -- `preflight` is free and emits the plan; lifecycle verbs refuse without an explicit confirm. Spend above the auto-approve envelope needs out-of-band human confirmation the agent cannot mint.
- **Teardown** -- a powered-off server still bills; only DELETE stops it. `oneshot` records
  the exact server identity returned by create and tears down that ID, retrying from its
  finalizer. Every delete path refetches the target by exact numeric ID, revalidates project
  and install scope, and, for predicate-based cleanup, rereads the authoritative ledger and
  recomputes the fresh TTL/status/heartbeat/orphan predicate immediately before DELETE.
  `down` requires exactly one selector. Broad `down --all` and reaper `kill`
  require a fresh protected lease plus the exact phrase returned by the corresponding
  read-only dry run; changed target inventory invalidates the phrase.

## Billing-safety guardrails

Four independent arms stop a runaway, crashed, or self-approving agent from leaving a paid
server running. A powered-off Hetzner server still bills; only DELETE stops it, so the reaper
is the load-bearing arm.

- **Arm 1 -- cloud-init dead-man's-switch.** Every `up` auto-attaches a rendered
  `assets/cloud-init.yaml` (boot-relative `shutdown -h +MAX` plus a systemd `RuntimeMaxSec`
  backstop). Custom `--user-data` is rejected, so this control cannot be bypassed. It caps
  COMPUTE even if the driver dies, and carries no token -- a server can only power itself off,
  then Arm 2 deletes the powered-off box.
- **Arm 2 -- detached reaper.** `hetzner_reaper.py` lists the labelled servers and DELETEs any
  that are past-TTL, powered-off, stale-heartbeat, or orphaned (job-id not in the local
  active-jobs ledger). It MUST run detached -- a systemd timer/service or cron entry, never a
  session child, because a background child dies when the agent session restarts and a dead
  reaper is a server that bills forever. The systemd timer/service and cron templates plus a
  step-by-step install guide are in `references/reaper-deployment.md`. A root-controlled
  post-success step publishes a short-lived `0644` lease beneath a fully root-controlled
  parent chain. The non-root driver reads and validates that no-follow descriptor, binding
  evidence to project, install scope, scheduler, configuration, and freshness. This repo
  ships only templates; enabling them is a gated, deploy-time action.
- **Arm 3 -- kill switch.** `down --all` (driver, in-session) and
  `hetzner_reaper kill` (standalone, detached) both DELETE every server in the exact
  configured project scope immediately, ignoring the reap predicate. Each requires fresh
  protected reaper evidence and an inventory-bound confirmation phrase from its dry run.
- **Runaway-loop guard.** Before any create, `up` runs a reconcile-before-create check that
  counts LIVE servers in the configured project scope and aborts if creating one more would exceed
  `max_concurrent_servers` -- so a looping agent cannot fan out servers even if the reservation
  ledger is stale.

Every provision, destroy, reap, and kill writes one redacted JSONL record (event, labels,
estimated EUR, reason) to `hetzner-audit.jsonl` under the broker state root. Secrets are never
written: the records are built without the token and each line is redacted before the write.

`oneshot` and `down --orphans` remain the in-session teardown. This lane reuses the broker's
`research_compute` budget and routing code, which installs with the broker-backed compute
lanes; the complete set is available in the `full-research` profile.
