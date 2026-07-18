---
name: kaggle-research-compute
description: Use when a research or engineering task needs automatic heavy-compute routing to free Kaggle Kernels through the local broker, with agent-driven push, poll, fetch, and a multi-run resume loop across concurrent kernels; free CPU (quota-free) and GPU under a self-imposed weekly GPU-hour cap.
metadata:
  short-description: Route heavy compute to free Kaggle Kernels through the local broker (free CPU; GPU under a weekly cap)
---

# Kaggle Research Compute


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/kaggle-research-compute/run_kaggle_research_compute.bat" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

Use this skill when the task is about:

- exhaustive search
- object enumeration
- counterexample hunting
- large parameter sweeps
- long-running CPU or GPU batch work that a throttled local run cannot finish in time

This skill is the Kaggle Kernels lane of the local `research_compute` broker. It packages a
portable job bundle as a kernel, pushes it, polls its status, and downloads its output, and it
runs a multi-run resume loop across concurrent kernels for jobs that need more than one 12h
session. It is peer to the Modal, Hetzner, and GitHub Actions lanes.

## When to prefer this skill

- the workload is CPU-heavy batch work: Kaggle CPU is FREE and does NOT consume the GPU quota, so it is preferred over the paid/quota'd lanes for any CPU job that fits Kaggle's constraints
- the workload wants a GPU and fits within the self-imposed weekly GPU-hour cap (Kaggle GPU is free under the ~30h/week floating quota)
- the job is chunkable and resumable to at most a 12h session per kernel run on ~4 vCPU / ~32 GB
- routing order is `local > Kaggle > Modal > Hetzner > GitHub Actions`, so Kaggle is the FIRST offload tier (right behind local) whenever credentials are present and the job fits

## Unified routing

The umbrella doc `compute-offload-routing.md` explains backend selection across the five lanes
(local, Kaggle, Modal, Hetzner, GitHub Actions), the keep-local rules, and the local
self-preservation veto. The per-lane contract for Kaggle — driver verbs, the multi-run resume
loop, the concurrency fan-out, the free-CPU / weekly-GPU-cap model, and guardrails — is in
`references/kaggle-offload.md`. The broker router is the decision boundary: `plan` and `doctor`
choose the backend; this skill pushes kernels only after that choice lands on Kaggle.

## Core workflow

1. If local resources matter, run `get-available-resources` and let the broker apply the self-preservation veto.
2. Build a portable job bundle (`manifest.json` with `total_units`, `worker`, `run.sh`, `merge`, writable `out/`) — the same bundle runs unchanged on any lane; each completed work unit leaves a checkpoint in `out/` so a re-pushed kernel resumes.
3. Run `preflight` (free, no kernel) to get the Kaggle plan: kind (CPU/GPU), estimated resume rounds and kernel count, concurrency, the 12h session cap, the GPU-hour estimate vs the weekly cap, adequacy, and availability.
4. If the plan fits, `run` executes the multi-run resume loop: it fans out up to ~5 concurrent kernels per round, polls them, fetches checkpoints, and re-pushes the remaining work with the checkpoints re-attached until the job is DONE (bounded by `max_runs`).
5. For manual control, `push` one kernel (a chunk), `status`/`wait` to poll it, and `fetch` to download its output.
6. No teardown: kernels auto-stop at the 12h session cap and cost nothing, so there is no reaper and nothing to destroy.

## Runtime commands

Linux (resolve the installed runtime root for the current agent, then call `run_skill.sh`):

```bash
runtime="${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}"
run() { bash "$runtime/run_skill.sh" skills/kaggle-research-compute/run_kaggle_research_compute.sh "$@"; }
```

```bash
run bootstrap                          # one-time: check kaggle CLI + kagglehub + API token, validate via kagglehub, run doctor
run doctor                             # lane + credentials + kaggle CLI + configured caps (offline)
run preflight --job /path/to/jobdir --json     # the plan the router consumes (no kernel)
run run     --job /path/to/jobdir --confirm    # multi-run resume loop with concurrent fan-out until DONE
run push    --job /path/to/jobdir --confirm    # push one kernel run (a chunk), for manual control
run status  <user/kernel-slug>
run wait    <user/kernel-slug>
run fetch   <user/kernel-slug> --dest /path/to/output
```

Planning verbs (`bootstrap`, `doctor`, `preflight`) are free and never push a kernel.
Lifecycle verbs (`push`, `status`, `wait`, `fetch`, `run`) submit real kernels and require the
new Kaggle API token (`KAGGLE_API_TOKEN`, or `~/.kaggle/access_token`) plus an explicit
`--confirm`. Use `--dry-run` on `push` and `run` to print the exact planned `kaggle` commands
with nothing submitted.

On targets that install a local skill wrapper, that wrapper should forward to the same
runtime command target.

```bash
skills/kaggle-research-compute/run_kaggle_research_compute.sh doctor
```

Windows:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" `
  "skills\kaggle-research-compute\run_kaggle_research_compute.bat" `
  doctor
```

## Operational notes

- The broker is the decision boundary. Push kernels on Kaggle only when the router chose this lane.
- Auth is the new single Kaggle API token, read from `KAGGLE_API_TOKEN` (or `~/.kaggle/access_token`) at runtime (env-first, never argv, never logged) — NOT the legacy `KAGGLE_USERNAME` + `KAGGLE_KEY` pair. `bootstrap` validates/primes via kagglehub (`kagglehub.whoami()` proves the token and yields the username the kaggle CLI uses for kernel ops). Do not write a `kaggle.json` into the repo or a kernel; a redaction filter covers surfaced output.
- Caps live under `[kaggle]` in `research-compute.toml`: `weekly_gpu_hours_cap`, `max_runs`, `concurrency`, `session_hours`, and the free-tier `kernel_cores` / `kernel_ram_gb`. CPU work is free and quota-free; GPU work passes a fail-closed weekly GPU-hour gate that reserves the estimate in a local usage ledger before the first push, so concurrent GPU submits cannot collectively blow the weekly cap.
- The 12h session cap is the reason for the multi-run resume loop. A job that needs more wall time spans multiple kernel runs: push a chunk-batch across up to ~5 concurrent kernels, download checkpoints, and re-push the remaining chunks with the checkpoints re-attached (as a Kaggle Dataset input) until DONE. `max_runs` bounds the loop.
- No reaper, no dead-man's-switch, no teardown: kernels auto-stop at the 12h session cap and cost nothing, so this lane is materially lower-risk than a paid rented-server lane. There is no cost gate — Kaggle is free.
- `doctor` and `preflight` work without a token and without a kernel. `push`, `status`, `wait`, `fetch`, and `run` need the host to be Kaggle-ready (the `kaggle` CLI >=1.8.0 and kagglehub >=0.4.1 installed and `KAGGLE_API_TOKEN` set, or `~/.kaggle/access_token` present).
- One-time per machine, run `bootstrap`: it checks the `kaggle` CLI and kagglehub, confirms the API token is present, and validates/primes via kagglehub (`kagglehub.whoami()`), then reports `doctor`. It never pushes a kernel.
- ToS: Kaggle compute is intended for its data-science / competition platform. Keep to modest, legitimate research workloads and verify the current Kaggle terms permit this use before the first live run. The build and its tests make no live Kaggle calls.

## Recommended templates

When this skill is involved, consider the same workflow templates as the other offload lanes
(install via the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and credit-gated heavy-compute offload.
- `engineering-delivery-loop-runbook` -- Bounded build-and-deliver loop runbook: single-path implementation with seen-to-fail proof, cross-agent diff verification, behavior-preserving cleanup, and credit-gated heavy-compute offload.
