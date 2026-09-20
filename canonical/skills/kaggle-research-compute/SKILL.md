---
name: kaggle-research-compute
description: Use when a research or engineering task needs automatic heavy-compute routing to free Kaggle Kernels through the local broker, with agent-driven push, poll, fetch, and a multi-run resume loop across concurrent kernels; free CPU (quota-free) and GPU under a self-imposed weekly GPU-hour cap.
metadata:
  short-description: Route heavy compute to free Kaggle Kernels through the local broker (free CPU; GPU under a weekly cap)
---

# Kaggle Research Compute


## Python packages

On Linux, the managed launcher uses `~/.agents_skills_venv` (override with
`AAS_SKILL_VENV`). From the repository, run
`make provision-skill-python ARGS="--apply --real-system"`
and check it with `make verify-skill-python`.
If no venv is configured, the launcher uses system Python; any unavailable
third-party imports fail at startup. A venv that is configured but missing,
or present but refused, stops the launch with exit `127` and a reason.

## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/kaggle-research-compute/run_kaggle_research_compute.ps1" <args>
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

Work through `compute-offload-sizing-gate` first. Size against the lane's
**aggregate** capacity — `kernel_cores` x `concurrency`, not one kernel — and
set `total_units` so the fan-out is actually used; sizing to a single kernel
understates the free lane by the concurrency factor. `preflight` reports
`kernel_cores`, `kernel_ram_gb` and `aggregate_cores` alongside `est_kernels`
and `est_rounds`, which are derived from `total_units` as well as `core_hours`,
so the estimate matches the kernels the run loop will actually launch.

1. If local resources matter, run `get-available-resources` and let the broker apply the self-preservation veto.
2. Build a portable job bundle (`manifest.json` with `total_units`, `worker`, `run.sh`, `merge`, writable `out/`) — the same bundle runs unchanged on any lane; each completed work unit leaves a checkpoint in `out/` so a re-pushed kernel resumes.
3. Run `preflight` (free, no kernel) to get the Kaggle plan: kind (CPU/GPU), estimated resume rounds and kernel count, concurrency, the 12h session cap, the GPU-hour estimate vs the weekly cap, adequacy, and availability.
4. Live submission is currently limited to one-unit CPU/GPU bundles through the manual `push` -> `status`/`wait` -> `fetch` path. Record and review the bundle SHA-256 from dry-run before push.
5. `run --dry-run` still reports the bounded multi-run shape, but live multi-run fails closed until crash-safe status-first recovery and verified checkpoint merging are implemented.
6. No teardown: kernels auto-stop at the 12h session cap and cost nothing, so there is no reaper and nothing to destroy.

## Runtime commands

Linux (use the owner-controlled installed runtime for the current agent):

```bash
# Any owner-controlled installed runtime is accepted; execute directly so #!/bin/bash -p applies.
launcher="${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh"
run() { "$launcher" skills/kaggle-research-compute/run_kaggle_research_compute.sh "$@"; }
```

```bash
run bootstrap                          # one-time: check kaggle CLI + kagglehub + API token, validate via kagglehub, run doctor
run doctor                             # lane + credentials + kaggle CLI + configured caps (offline)
run preflight --job /path/to/jobdir --json     # the plan the router consumes (no kernel)
run run     --job /path/to/jobdir --dry-run    # plan only; live multi-run currently fails closed
run push    --job /path/to/jobdir --dry-run    # emits the reviewed bundle_sha256
run push    --job /path/to/jobdir --owner USER --bundle-sha256 HEX --confirm  # one-unit live push
run status  <user/kernel-slug>
run wait    <user/kernel-slug>
run fetch   <user/kernel-slug> --job /path/to/jobdir --dest /path/to/output
```

Planning verbs (`bootstrap`, `doctor`, `preflight`, and `run --dry-run`) never push a kernel.
Only `push` submits; it requires the API token, explicit `--confirm`, a one-unit bundle, and
the reviewed `--bundle-sha256` emitted by preflight/dry-run. `status`, `wait`, and `fetch` act
only on that recorded kernel reference. Fetch requires the original job bundle and accepts
only flat checkpoint/result JSON names before host-side manifest-bound verification.

On targets that install a local skill wrapper, that wrapper should forward to the same
runtime command target.

```bash
skills/kaggle-research-compute/run_kaggle_research_compute.sh doctor
```

Windows:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" `
  "skills/kaggle-research-compute/run_kaggle_research_compute.ps1" `
  doctor
```

## Operational notes

- The broker is the decision boundary. Push kernels on Kaggle only when the router chose this lane.
- Auth is the new single Kaggle API token, projected as `KAGGLE_API_TOKEN` by the guarded runtime launcher (never argv, never logged) — NOT a pathname-read `access_token` and not the legacy `KAGGLE_USERNAME` + `KAGGLE_KEY` pair. `bootstrap` validates/primes via kagglehub (`kagglehub.whoami()` proves the token and yields the username the kaggle CLI uses for kernel ops). Do not write a `kaggle.json` into the repo or a kernel; a redaction filter covers surfaced output.
- When `AAS_COMPUTE_SECRETS_FILE` names the shared protected compute authority,
  the managed wrapper validates its full schema but projects only
  `KAGGLE_API_TOKEN` and `KAGGLE_CONFIG_DIR`; Hetzner values and the pointer are
  removed before the Kaggle child starts.
- Caps live under `[kaggle]` in `research-compute.toml`: `weekly_gpu_hours_cap`, `max_runs`, `concurrency`, `session_hours`, and the free-tier `kernel_cores` / `kernel_ram_gb`. CPU work is free and quota-free; GPU work passes a fail-closed weekly GPU-hour gate that reserves the estimate in a local usage ledger before the first push, so concurrent GPU submits cannot collectively blow the weekly cap.
- The multi-run design remains documented and dry-runnable, but live multi-run is deliberately disabled until ambiguous submissions recover status-first and every resumed checkpoint is manifest-bound.
- `manifest.upload_files` is a required explicit allowlist. Reparse points, hardlinks, secret-like filenames, oversized bundles, and files changed during descriptor-bound snapshotting are rejected.
- No reaper, no dead-man's-switch, no teardown: kernels auto-stop at the 12h session cap and cost nothing, so this lane is materially lower-risk than a paid rented-server lane. There is no cost gate — Kaggle is free.
- `doctor` and `preflight` work without a token and without a kernel. Live one-unit CPU `push`, plus `status`, `wait`, and `fetch`, need the host to be Kaggle-ready: the selected trusted Python must be 3.11+ with `kaggle>=2.2.4,<3` and `kagglehub>=1.0.2,<2`, and the guarded `KAGGLE_API_TOKEN` environment projection must be present. Live GPU push and multi-run remain disabled.
- On native Windows, use `AAS_KAGGLE_PYTHON` for the absolute Kaggle-only interpreter path and pin it with `AAS_KAGGLE_PYTHON_SHA256` plus `AAS_KAGGLE_PYTHON_SIGNER_THUMBPRINT`. The wrapper maps these values process-locally into the managed Python attestation contract; it does not change the default Python for other skills.
- The driver invokes `python -I -m kaggle` and never falls back to `kaggle.exe` or another executable discovered on `PATH`.
- One-time per machine, run `bootstrap`: it checks the `kaggle` CLI and kagglehub, confirms the API token is present, and validates/primes via kagglehub (`kagglehub.whoami()`), then reports `doctor`. It never pushes a kernel.
- ToS: Kaggle compute is intended for its data-science / competition platform. Keep to modest, legitimate research workloads and verify the current Kaggle terms permit this use before the first live run. The build and its tests make no live Kaggle calls.

## Recommended templates

When this skill is involved, consider the same workflow templates as the other offload lanes
(install via the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `compute-offload-sizing-gate` -- Pre-dispatch worksheet: measure the workload, read the declared lane capacity, write the manifest in the correct dialect, assert the plan, and verify the realized allocation.
- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
- `engineering-delivery-loop-runbook` -- Bounded build-and-deliver loop runbook: single-path implementation with seen-to-fail proof, cross-agent diff verification, behavior-preserving cleanup, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
