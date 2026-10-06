---
name: kaggle-research-compute
description: Use when a research or engineering task needs automatic heavy-compute routing to free Kaggle Kernels through the local broker, with exact-version CPU push, poll and fetch, explicit Internet policy, and resumable host evidence; live GPU and multi-run remain disabled.
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

This skill is the Kaggle Kernels lane of the local `research_compute` broker.
It packages a portable job bundle, submits one CPU kernel, polls its exact
accepted version and downloads bounded evidence. Multi-run/GPU execution
remains disabled; planning those shapes does not enable them. It is peer to
the Modal, Hetzner, and GitHub Actions lanes, subject to the selected policy.

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
4. Live submission is currently limited to one-unit CPU bundles through the manual `push` -> `status`/`wait` -> `fetch` path. Record and review the bundle SHA-256 from dry-run before push.
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
run status  <user/kernel-slug> --submission-intent /path/from-push.json
run wait    <user/kernel-slug> --submission-intent /path/from-push.json
run fetch   <user/kernel-slug> --submission-intent /path/from-push.json \
  --job /path/to/jobdir --dest /path/to/output
```

`doctor`, `preflight` and dry-run verbs are offline and never authenticate or push a kernel.
`bootstrap` is an explicit host readiness/account check; it may contact Kaggle but never pushes a kernel.
Only `push` submits; it requires the API token, explicit `--confirm`, a one-unit bundle, and
the reviewed `--bundle-sha256` emitted by preflight/dry-run. `status`, `wait`, and `fetch` act
on the host-recorded immutable kernel/version identity in `--submission-intent`. Bare-ref
status remains diagnostic; bare-ref fetch is refused. Fetch requires the reviewed bundle
and admits only exact bounded output names from its manifest, with byte hashes and
pagination completeness. A captured save response can be recovered with
`recover --submission-intent PATH`; an ambiguous acceptance never permits repush.

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

## Internet and native Lean jobs

Existing jobs keep Internet disabled. A job can request `"enable_internet": true`
in its manifest only when the host-owned `[kaggle]` configuration permits
`allow_internet = true`. This is a ceiling, not an instruction to enable every
job. A scoped host configuration may grant it for one authorized job while the
global configuration remains unchanged. The reviewed bundle digest binds this
choice, setup scripts and uploaded lockfiles. Network access is kernel-wide;
finishing setup does not create an offline verification sandbox.

Ordinary Lean/Lake jobs use the existing native strict gate and do not require
Lax. The host remote-formal controller binds source, target, toolchain, dependency,
checker and exact provider execution evidence. It records setup/build/audit and
optional kernel-replay outcomes separately. The result/checkpoint verifier alone
is structural evidence, never a mathematical proof certificate. Missing or
partial formal evidence remains incomplete. Lax conversion is a separate optional
job through its existing `from-existing-lean` workflow.

Optional `output_files` lists exact flat `out/*.json`, `out/*.log` or `out/*.txt`
paths (no glob syntax). Downloads are bounded to 128 MiB per file and 1 GiB in
aggregate; file hashes and incomplete page/download receipts survive failure.
No output may select its own executable validator. Qualification with a real
Kaggle image/account is separate from offline installation smoke.

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
- The bounded official SDK/account path currently requires a POSIX main-thread controller. Native Windows and worker-thread SDK calls fail before SDK import or authentication; use a qualified Linux/WSL controller for live operations. Native Windows wrappers still support offline doctor, preflight and dry-run. `AAS_KAGGLE_PYTHON`, `AAS_KAGGLE_PYTHON_SHA256` and `AAS_KAGGLE_PYTHON_SIGNER_THUMBPRINT` select and attest the Windows interpreter without qualifying live SDK execution.
- HTTP calls use connect/read limits of at most 10/60 seconds and preserve stricter SDK limits. Overall operation limits are 120 seconds for account lookup, status and listing, 300 seconds for push, and 600 seconds per selected output download. They cover import-time authentication and streaming; no watchdog thread remains running after a timeout. `wait --timeout` supplies its remaining budget to status and sleep. These are per-operation ceilings; the controller must also enforce the authorized whole-workflow budget. A timeout after dispatch retains unknown acceptance and never authorizes resubmission.
- The driver invokes `python -I -m kaggle` and never falls back to `kaggle.exe` or another executable discovered on `PATH`.
- One-time per machine, run `bootstrap`: it checks the `kaggle` CLI and kagglehub, confirms the API token is present, and validates/primes via kagglehub (`kagglehub.whoami()`), then reports `doctor`. It never pushes a kernel.
- ToS: Kaggle compute is intended for its data-science / competition platform. Keep to modest, legitimate research workloads and verify the current Kaggle terms permit this use before the first live run. The build and its tests make no live Kaggle calls.

## Recommended templates

When this skill is involved, consider the same workflow templates as the other offload lanes
(install via the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `compute-offload-sizing-gate` -- Pre-dispatch worksheet: measure the workload, read the declared lane capacity, write the manifest in the correct dialect, assert the plan, and verify the realized allocation.
- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
- `engineering-delivery-loop-runbook` -- Bounded build-and-deliver loop runbook: single-path implementation with seen-to-fail proof, cross-agent diff verification, behavior-preserving cleanup, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
