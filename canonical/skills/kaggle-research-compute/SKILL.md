---
name: kaggle-research-compute
description: Use when a research or engineering task needs automatic heavy-compute routing to free Kaggle Kernels through the local broker, with exact-version CPU push, poll and fetch, explicit Internet policy, and resumable host evidence; live GPU and multi-run remain disabled.
metadata:
  short-description: Route heavy compute to free Kaggle Kernels through the local broker (one-unit CPU live; GPU and multi-run planning only)
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
- long-running CPU batch work that a throttled local run cannot finish in time
- planning GPU or multi-run work; those live submission shapes remain disabled

This skill is the Kaggle Kernels lane of the local `research_compute` broker.
It packages a portable job bundle, submits one CPU kernel, polls its exact
accepted version and downloads bounded evidence. Multi-run/GPU execution
remains disabled; planning those shapes does not enable them. It is peer to
the Modal, Hetzner, and GitHub Actions lanes, subject to the selected policy.

## When to prefer this skill

- the workload is CPU-heavy batch work and fits one CPU kernel; the broker treats this as a free lane without a CPU cost gate, not a promise of unlimited provider quota
- the measured workload fits the configured per-kernel RAM and session estimates; qualify the actual account/image before relying on those estimates
- routing policy permits Kaggle: the default order is `local > Kaggle > Modal > Hetzner > GitHub Actions`

## Unified routing

The umbrella doc `compute-offload-routing.md` explains backend selection across the five lanes
(local, Kaggle, Modal, Hetzner, GitHub Actions), the keep-local rules, and the local
self-preservation veto. The per-lane contract for Kaggle — enabled CPU driver verbs,
planning-only GPU/multi-run estimates, and guardrails — is in
`references/kaggle-offload.md`. The broker router is the decision boundary: `plan`
chooses the backend and `doctor` checks readiness; this skill pushes kernels only after that choice lands on Kaggle.

## Core workflow

Work through `compute-offload-sizing-gate` first. Size live work against **one
CPU kernel**, with `total_units = 1`. `preflight` reports configured
`kernel_cores`, `kernel_ram_gb`, `aggregate_cores`, `est_kernels` and `est_rounds`;
aggregate and multi-run figures are planning-only, not enabled dispatch capacity.

1. If local resources matter, run `get-available-resources` and let the broker apply the self-preservation veto.
2. Build a portable one-unit CPU bundle (`manifest.json`, explicit `upload_files`, `worker`, `run.sh`, `merge`, writable `out/`) with bounded outputs.
3. Run `preflight` (no kernel) and review per-kernel adequacy, configured session estimate, and account readiness. A feasible plan does not enable GPU or multi-run submission.
4. Use `push --dry-run` to review the bundle SHA-256, owner and Internet policy, then use the manual `push` -> `status`/`wait` -> `fetch` path below.
5. Retain the host submission intent and use its exact accepted version for polling and fetching. Pending or ambiguous acceptance never permits repush.
6. `run --dry-run` reports the intended bounded multi-run design only; live multi-run remains disabled. No paid server is provisioned or destroyed by this lane.

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
run push    --job /path/to/jobdir --owner USER --dry-run    # emits the reviewed bundle_sha256
run push    --job /path/to/jobdir --owner USER \
  --bundle-sha256 HEX --confirm                # reviewed one-unit CPU push
run status  USER/KERNEL --submission-intent /path/from-push.json
run wait    USER/KERNEL --submission-intent /path/from-push.json
run fetch   USER/KERNEL --submission-intent /path/from-push.json \
  --job /path/to/jobdir --dest /path/to/output
```

`doctor`, `preflight` and dry-run verbs are offline and never authenticate or push a kernel.
`bootstrap` is an explicit host readiness/account check; it may contact Kaggle but never pushes a kernel.
Only `push` submits; it requires the API token, explicit `--confirm`, a one-unit CPU bundle,
the reviewed `--owner`, and the `--bundle-sha256` emitted by dry-run. `status`, `wait`, and `fetch` act
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
- Auth is the new single Kaggle API token, projected as `KAGGLE_API_TOKEN` by the guarded runtime launcher (never argv, never logged) — NOT a pathname-read `access_token` and not the legacy `KAGGLE_USERNAME` + `KAGGLE_KEY` pair. `bootstrap` validates the account via `kagglehub.whoami()`; live push checks that identity against the reviewed owner before SDK submission. Do not write a `kaggle.json` into the repo or a kernel; a redaction filter covers surfaced output.
- When `AAS_COMPUTE_SECRETS_FILE` names the shared protected compute authority,
  the managed wrapper validates its full schema but projects only
  `KAGGLE_API_TOKEN` and `KAGGLE_CONFIG_DIR`; Hetzner values and the pointer are
  removed before the Kaggle child starts.
- Configuration under `[kaggle]` supplies `session_hours`, `kernel_cores` and `kernel_ram_gb` estimates. `weekly_gpu_hours_cap`, `max_runs` and `concurrency` support planning only. Live GPU is refused pending an atomic reservation gate; live multi-run is refused pending hardened recovery. The broker has no CPU cost gate; provider availability and limits still require account/image qualification.
- The multi-run design remains documented and dry-runnable, but live multi-run is deliberately disabled until ambiguous submissions recover status-first and every resumed checkpoint is manifest-bound.
- `manifest.upload_files` is a required explicit allowlist. Reparse points, hardlinks, secret-like filenames, oversized bundles, and files changed during descriptor-bound snapshotting are rejected.
- No paid server or server teardown is involved. Bound polling by the authorized workflow budget; do not treat the configured session estimate as a verified provider guarantee.
- `doctor` and `preflight` work without a token and without a kernel. Live one-unit CPU `push`, plus `status`, `wait`, and `fetch`, need the host to be Kaggle-ready: the selected trusted Python must be 3.11+ with `kaggle>=2.2.4,<3` and `kagglehub>=1.0.2,<2`, and the guarded `KAGGLE_API_TOKEN` environment projection must be present. Live GPU push and multi-run remain disabled.
- The bounded official SDK/account path currently requires a POSIX main-thread controller. Native Windows and worker-thread SDK calls fail before SDK import or authentication; use a qualified Linux/WSL controller for live operations. Native Windows wrappers still support offline doctor, preflight and dry-run. `AAS_KAGGLE_PYTHON`, `AAS_KAGGLE_PYTHON_SHA256` and `AAS_KAGGLE_PYTHON_SIGNER_THUMBPRINT` select and attest the Windows interpreter without qualifying live SDK execution.
- HTTP calls use connect/read limits of at most 10/60 seconds and preserve stricter SDK limits. Overall operation limits are 120 seconds for account lookup, status and listing, 300 seconds for push, and 600 seconds per selected output download. They cover import-time authentication and streaming; no watchdog thread remains running after a timeout. `wait --timeout` supplies its remaining budget to status and sleep. These are per-operation ceilings; the controller must also enforce the authorized whole-workflow budget. A timeout after dispatch retains unknown acceptance and never authorizes resubmission.
- Enabled kernel operations use the official SDK in the host controller. CLI version and legacy diagnostic helpers invoke `python -I -m kaggle` and never fall back to `kaggle.exe` or another executable discovered on `PATH`.
- One-time per machine, run `bootstrap`: it checks the `kaggle` CLI and kagglehub, confirms the API token is present, and validates/primes via kagglehub (`kagglehub.whoami()`), then reports `doctor`. It never pushes a kernel.
- ToS: Kaggle compute is intended for its data-science / competition platform. Keep to modest, legitimate research workloads and verify the current Kaggle terms permit this use before the first live run. The build and its tests make no live Kaggle calls.

## Recommended templates

When this skill is involved, consider the same workflow templates as the other offload lanes
(install via the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `compute-offload-sizing-gate` -- Pre-dispatch worksheet: measure the workload, read the declared lane capacity, write the manifest in the correct dialect, assert the plan, and verify the realized allocation.
- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
- `engineering-delivery-loop-runbook` -- Bounded build-and-deliver loop runbook: single-path implementation with seen-to-fail proof, cross-agent diff verification, behavior-preserving cleanup, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
