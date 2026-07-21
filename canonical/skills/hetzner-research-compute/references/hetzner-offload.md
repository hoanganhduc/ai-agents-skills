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
- `preflight --job DIR [--json]` -- the plan the router consumes: server type, region, estimated wall hours, estimated EUR, arch, and the budget verdict. No provisioning.
- `up` -- create one labelled server from a cloud-init image, budget-gated. Refuses without token and confirm.
- `push` -- copy the bundle to the server (rsync or git over SSH).
- `run` -- detached, full-core execution on the server.
- `status` -- server and job state.
- `wait` -- poll until the run finishes or the wall-clock cap is hit.
- `fetch` -- copy results back and verify they are well formed.
- `down [--all|--orphans]` -- DESTROY. `--all` is the kill switch; `--orphans` removes servers past TTL, powered off, stale-heartbeat, or not in the local active-jobs ledger.
- `oneshot` -- `up -> push -> run -> wait -> fetch -> down` under a guaranteed teardown so any exit still destroys.

Use `--dry-run` on `up`, `down`, and `oneshot` to print the exact planned `hcloud` command
with no provisioning. This is the offline path exercised by the driver tests.

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

- **Token** -- `HCLOUD_TOKEN` from the environment, injected into the `hcloud` subprocess env, never on argv (`/proc/<pid>/cmdline` is world-readable), never logged, never on a server, never in an `hcloud context` file. A redaction filter covers all agent-readable output.
- **Labels** -- every server carries `managed-by`, `job-id`, `owner`, and `ttl` labels so the reaper and `down --orphans` can identify and delete managed servers.
- **Budget** -- a fail-closed gate reserves the pessimistic worst case (`rate x ceil(max_server_hours) x count + IPv4`) in the shared append-only ledger before any create. It refuses above the per-job cap (the auto-approve envelope), above the concurrent-server cap, or when it would push the day past the daily cap.
- **Confirm** -- `preflight` is free and emits the plan; lifecycle verbs refuse without an explicit confirm. Spend above the auto-approve envelope needs out-of-band human confirmation the agent cannot mint.
- **Teardown** -- a powered-off server still bills; only DELETE stops it. `oneshot` guarantees teardown on every exit path (the code wraps the lifecycle in a finally block plus signal handlers, the equivalent of `trap 'down' EXIT INT TERM HUP`). `down --orphans` is the manual kill switch.

## Billing-safety guardrails

Four independent arms stop a runaway, crashed, or self-approving agent from leaving a paid
server running. A powered-off Hetzner server still bills; only DELETE stops it, so the reaper
is the load-bearing arm.

- **Arm 1 -- cloud-init dead-man's-switch.** Every `up` auto-attaches a rendered
  `assets/cloud-init.yaml` (boot-relative `shutdown -h +MAX` plus a systemd `RuntimeMaxSec`
  backstop) unless the operator passes their own `--user-data` file. It caps COMPUTE even if
  the driver dies, and carries no token -- a server can only power itself off, then Arm 2
  deletes the powered-off box.
- **Arm 2 -- detached reaper.** `hetzner_reaper.py` lists the labelled servers and DELETEs any
  that are past-TTL, powered-off, stale-heartbeat, or orphaned (job-id not in the local
  active-jobs ledger). It MUST run detached -- a systemd timer/service or cron entry, never a
  session child, because a background child dies when the agent session restarts and a dead
  reaper is a server that bills forever. The systemd timer/service and cron templates plus a
  step-by-step install guide are in `references/reaper-deployment.md`. This repo ships only the
  templates; enabling them is a gated, deploy-time action.
- **Arm 3 -- kill switch.** `down --all` (driver, in-session) and `hetzner_reaper kill`
  (standalone, detached) both DELETE every managed server immediately, ignoring the reap
  predicate.
- **Runaway-loop guard.** Before any create, `up` runs a reconcile-before-create check that
  counts the LIVE tagged servers and aborts if creating one more would exceed
  `max_concurrent_servers` -- so a looping agent cannot fan out servers even if the reservation
  ledger is stale.

Every provision, destroy, reap, and kill writes one redacted JSONL record (event, labels,
estimated EUR, reason) to `hetzner-audit.jsonl` under the broker state root. Secrets are never
written: the records are built without the token and each line is redacted before the write.

`oneshot` and `down --orphans` remain the in-session teardown. This lane reuses the broker's
`research_compute` budget and routing code, which installs with the broker-backed compute
lanes; the complete set is available in the `full-research` profile.
