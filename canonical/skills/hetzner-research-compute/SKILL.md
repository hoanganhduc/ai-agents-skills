---
name: hetzner-research-compute
description: Use when a research or engineering task needs automatic heavy-compute routing to a disposable Hetzner Cloud CPU or high-memory server through the local broker, with agent-driven provision, run, collect, and destroy under hard cost caps.
metadata:
  short-description: Route heavy CPU compute to a disposable Hetzner Cloud server through the local broker
---

# Hetzner Research Compute


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/hetzner-research-compute/run_hetzner_research_compute.bat" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

Use this skill when the task is about:

- exhaustive search
- object enumeration
- counterexample hunting
- large parameter sweeps
- long-running CPU or high-memory batch work that a throttled local run cannot finish in time

This skill is the Hetzner Cloud lane of the local `research_compute` broker. It rents a
disposable server, runs a portable job bundle on it at full cores, fetches the results,
and destroys the server. It is peer to the Kaggle, Modal, and GitHub Actions lanes.

## When to prefer this skill

- the local machine is CPU, memory, or disk constrained for the requested workload, or must stay responsive so a local run would trip the self-preservation veto
- the workload is CPU-heavy or high-memory (GPU work is out of scope in v1, so the router skips Hetzner and continues to the next GPU-capable lane)
- a dedicated, disposable, full-core box is a better fit than a throttled local run
- the recommended routing order is `local > Kaggle > Modal > Hetzner > GitHub Actions`, so Hetzner follows the free Kaggle lane and Modal for non-GPU work when a token and budget are available; a valid custom order keeps local first and may reorder or omit unique remote lanes

## Unified routing

The umbrella doc `compute-offload-routing.md` explains backend selection across the five
lanes (local, Kaggle, Modal, Hetzner, GitHub Actions), the keep-local rules, and the local
self-preservation veto. The per-lane contract for Hetzner — driver verbs, guardrails, the
lifecycle invariant, budget, and teardown — is in `references/hetzner-offload.md`. The
broker router is the decision boundary: `plan` and `doctor` choose the backend; this skill
provisions only after that choice lands on Hetzner.

## Core workflow

1. If local resources matter, run `get-available-resources` and let the broker apply the self-preservation veto.
2. Build a portable job bundle (`manifest.json`, `worker`, `run.sh`, `merge`, writable `out/`) — the same bundle runs unchanged on any lane.
3. Run `preflight` (free, no server) to get the Hetzner plan: server type, region, estimated wall hours, estimated EUR, arch, and the budget verdict.
4. If the plan stays within policy, `up` (create a labelled server), `push` the bundle, and `run` it at full cores. Use `oneshot` to do all of this under a guaranteed teardown.
5. Use `wait` and `fetch` to poll and copy results back to local storage, verifying they are well formed.
6. `down` DESTROYS the server. A powered-off server still bills; only DELETE stops it, so teardown must run on every terminal path.

## Runtime commands

Linux (resolve the installed runtime root for the current agent, then call `run_skill.sh`):

```bash
runtime="${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}"
run() { bash "$runtime/run_skill.sh" skills/hetzner-research-compute/run_hetzner_research_compute.sh "$@"; }
```

```bash
run bootstrap                          # one-time: check hcloud CLI + token, run doctor
run doctor                             # lane + token + hcloud CLI + configured caps (offline)
run preflight --job /path/to/jobdir --json     # the plan the router consumes (no server)
run up      --job /path/to/jobdir --confirm    # create a labelled server (budget-gated)
run push    <job_id>                            # copy the bundle to the server
run run     <job_id>                            # detached, full-core execution
run status  <job_id>
run wait    <job_id>
run fetch   <job_id> --dest /path/to/output
run down    <job_id> --confirm                  # DESTROY (the only thing that stops billing)
run down    --orphans --confirm                 # kill-switch cleanup of stale/expired servers
run oneshot --job /path/to/jobdir --confirm     # up -> push -> run -> wait -> fetch -> down, teardown guaranteed on any exit
```

Planning verbs (`bootstrap`, `doctor`, `preflight`) are free and never touch a server.
Lifecycle verbs (`up`, `push`, `run`, `status`, `wait`, `fetch`, `down`, `oneshot`) may
hold a paid server and require `HCLOUD_TOKEN` plus an explicit `--confirm`. Use `--dry-run`
on `up`, `down`, and `oneshot` to print the exact planned `hcloud` command with no provisioning.

On targets that install a local skill wrapper, that wrapper should forward to the same
runtime command target.

```bash
skills/hetzner-research-compute/run_hetzner_research_compute.sh doctor
```

Windows:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" `
  "skills\hetzner-research-compute\run_hetzner_research_compute.bat" `
  doctor
```

## Operational notes

- The broker is the decision boundary. Provision on Hetzner only when the router chose this lane.
- `HCLOUD_TOKEN` is read from the environment at runtime (env-injection, never argv, never logged). Do not write an `hcloud context` file and never place the token on a server. Use a dedicated, least-privilege Hetzner project with a project server-limit.
- Budget caps live under `[hetzner]` in `research-compute.toml`: `max_eur_per_job`, `max_eur_per_day`, `max_server_hours`, `max_concurrent_servers`, and `allowed_locations` / `allowed server types` allow-lists. The gate reserves the pessimistic worst case (`rate x ceil(max_server_hours) x count + IPv4`) before any create, so concurrent submits cannot collectively overspend.
- Within the auto-approve envelope (worst case at or below `max_eur_per_job`, at or below `max_concurrent_servers`, allow-listed types) the agent may submit alone; a larger spend needs out-of-band human confirmation the agent cannot mint.
- Teardown must run on every terminal path (success, failure, timeout, boot-fail, push-fail, crash). Failure and timeout paths fetch checkpoints before destroy so work is resumable. A detached reaper (systemd timer or cron, never a session child) is the durable billing-stopper: deploy it from `references/reaper-deployment.md`. `oneshot` and `down --orphans` are the in-session teardown, and every `up` auto-attaches a cloud-init dead-man's-switch that caps compute even if the driver dies.
- Billing-safety guardrails are on by default: a reconcile-before-create runaway-loop guard aborts `up` if live tagged servers would exceed `max_concurrent_servers`, and every provision/destroy/reap/kill is written to a redacted append-only audit log (`hetzner-audit.jsonl`). The standalone kill switch is `hetzner_reaper kill` (peer of `down --all`).
- CPU-heavy or high-memory combinatorial workloads are the target. GPU work is out of scope in v1, so the router skips Hetzner and continues to the next GPU-capable lane.
- `doctor` and `preflight` work without a token or a server. `up`, `push`, `run`, `wait`, `fetch`, and `down` need the host to be Hetzner-ready (the `hcloud` CLI installed and `HCLOUD_TOKEN` set).
- One-time per machine, run `bootstrap`: it checks the `hcloud` CLI and token presence and reports `doctor`. It never provisions.

## Recommended templates

When this skill is involved, consider the same workflow templates as the other offload lanes
(install via the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
- `engineering-delivery-loop-runbook` -- Bounded build-and-deliver loop runbook: single-path implementation with seen-to-fail proof, cross-agent diff verification, behavior-preserving cleanup, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
