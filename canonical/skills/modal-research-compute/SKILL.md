---
name: modal-research-compute
description: Use when a research or engineering task needs automatic heavy-compute routing through the unified local broker, including Modal-backed remote CPU, high-memory CPU, or GPU execution.
metadata:
  short-description: Route heavy compute through the unified local broker
---

# Modal Research Compute


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/modal-research-compute/run_modal_research_compute.bat" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

Use this skill when the task is about:

- exhaustive search
- object enumeration
- counterexample hunting
- large parameter sweeps
- remote execution of generated experiment code
- GPU-suitable document, embedding, reranking, or tensor workloads

This skill is the integration layer for the local `research_compute` broker.

## When to prefer this skill

- the user wants Modal involved automatically
- the local machine is CPU, memory, disk, or GPU constrained for the requested workload
- the workload is long-running enough that remote execution is a better fit

## Unified remote offload

Route through the broker's configured `routing_order`; the recommended order is
`local > Kaggle > Modal > Hetzner > GitHub Actions`. A valid custom configured order
is honored, with local fixed first and supported remote lanes unique, reordered, or
omitted. GitHub Actions is the last recommended lane and is appropriate only
for proportionate validation of a private repository's own committed experiment
code when earlier adequate lanes are unavailable. Follow the contract in
`references/github-actions-offload.md` (driver signature, per-config SIGALRM,
no Pool on 2-core runners, merge `needs:` + non-vacuous banked-value controls,
loud-fail semantics, staged pushes, merge-back/resume). The broker checks the
GitHub Actions minutes gate before dispatch.

A failed readiness, quota, or budget gate makes that lane unavailable and the
router continues through the configured order. Lane checks are lazy, so a safe
local decision and an earlier accepted remote lane do not contact later providers. Treat the job as blocked only
after the permitted cascade is exhausted, or when an explicit backend override
fails its selected lane's gate.

When remote offload is unavailable (minutes exhausted, broker down,
unverifiable credit) and computation must run locally, follow
`references/local-compute-throttle.md` (one lockfile-guarded single-process
job, idle priority per OS, subprocess-timeout watchdogs, chunked resumable
checkpoints, load/CPU guards) — its rules are cross-platform (Linux, macOS,
Windows).

## Multi-backend parallel fan-out (v2)

For a LARGE divisible batch job — M independent, resumable chunks (a sweep or
enumeration split into shards) — the fan-out scheduler splits the chunks across
SEVERAL lanes at once (some chunks local, some on the free lane, some on a paid
lane), each lane sized to its spare capacity, to minimise makespan while
minimising cost. It is a scheduler on top of the same per-lane probes; small
jobs keep using the single-lane router. Fan-out is opt-in (`[fanout].enabled`)
and triggers only when the job declares at least `[fanout].min_chunks` chunks.

Each job carries a `policy.speed_cost_weight` in `[0, 1]` (0 cheapest / free
lanes only, 1 fastest / recruit paid lanes, 0.5 blend). Every hard rail still
binds — per-lane budget caps, the €3/day auto-approve envelope, the GitHub
Actions 60% minutes cap, Kaggle's weekly GPU-hour quota, and local's
self-preservation load-cap are enforced as per-lane chunk ceilings the knob can
never breach. See `compute-offload-routing.md` for the full contract. Plan a
fan-out (no dispatch) with `run fanout-plan job.json`; a failed lane's
unfinished chunks are reassigned to a healthy lane so no resumable work is lost.
Completed chunks still consume cumulative lane rails during reassignment, and a
successful retry replaces an earlier failed/vacuous duplicate during merge.

## Core workflow

1. If local resources matter, run `get-available-resources`.
2. Build a broker manifest JSON for the task.
3. Run broker `plan` (or `fanout-plan` for a large divisible job).
4. Use `run plan` as the decision boundary. Execute a selected Kaggle or Hetzner lane
   through its corresponding lane skill; `run submit` dispatches only Modal/GitHub Actions.
   Execute an accepted local plan under the broker's reported worker limits.
5. Use the selected lane's `wait` and `fetch` commands to retrieve results and logs.

## Runtime commands

Linux (resolve the installed runtime root for the current agent, then call `run_skill.sh`):

```bash
runtime="${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}"
run() { bash "$runtime/run_skill.sh" skills/modal-research-compute/run_modal_research_compute.sh "$@"; }
```

```bash
run bootstrap                          # one-time: generate config if absent, authenticate gh, check deps, doctor
run doctor                             # routing warning + Modal and optional GitHub Actions readiness
run plan        /path/to/job.json
run fanout-plan /path/to/job.json      # v2: split a large divisible job across lanes (no dispatch)
run submit /path/to/job.json
run wait   <job_id>
run fetch  <job_id> --dest /path/to/output
```

On targets that install a local skill wrapper, that wrapper should forward to
the same runtime command target.

```bash
skills/modal-research-compute/run_modal_research_compute.sh doctor
```

Windows:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" `
  "skills\modal-research-compute\run_modal_research_compute.bat" `
  doctor
```

## Operational notes

- The broker is the decision boundary. Do not call Modal directly from the normal Codex flow when the broker can handle the task.
- CPU-heavy combinatorial workloads should default to remote CPU or high-memory CPU, not GPU.
- GPU use should be explicit in the manifest or clearly justified by the workload.
- `doctor` and `plan` work without a deployed Modal app. Modal-backed submission and `deploy` need a Modal-authenticated host; GitHub Actions wait/fetch use an attempt-unique dispatch id plus the recorded exact GHA run id and `gh`, not Modal credentials. Unverifiable GHA timing stays reserved, while verified billed-equivalent usage remains accrued through the UTC billing cycle. Kaggle and Hetzner plans hand off to their lane drivers.
- Linux hosts become Modal-ready after `python3 -m pip install --user --upgrade modal` and `modal token set` or `modal token new`.
- Windows hosts should install `modal` into the selected Python environment and ensure `modal.exe` is on `PATH` so broker deploy can find it.
- Broker state persists under the runtime memories tree, while fetched outputs materialize under the caller workspace by default.
- One-time per machine, run `bootstrap`: it generates `research-compute.toml` from the example if absent (never overwriting an existing one), authenticates `gh`, checks deps, and runs `doctor`. Use this to set up a host that does not have the full system installer.
- GitHub Actions ToS compliance: the broker's `gha` lane runs only inside a private research repo, executes that repo's own committed experiment code (parameters are data, never executed), is budget-gated, and is the last recommended automatic backend after local, Kaggle, Modal, and Hetzner — never a general compute pool. Configure it under `[gha]` in `research-compute.toml`.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
- `engineering-delivery-loop-runbook` -- Bounded build-and-deliver loop runbook: single-path implementation with seen-to-fail proof, cross-agent diff verification, behavior-preserving cleanup, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
