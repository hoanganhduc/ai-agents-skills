# Kaggle Kernels offload contract

Patterns for running a portable research job bundle on free Kaggle Kernels, then collecting
the results. This is the Kaggle lane of the `research_compute` broker, peer to the Modal,
Hetzner, and GitHub Actions lanes. Treat the agent itself as the adversary (looping, crashing,
self-approving): every rule below exists to stop a compromised or runaway agent from leaking a
credential or submitting unreviewed work. Live submission is limited to one-unit CPU
bundles; GPU and multi-run remain planning-only. This lane provisions no paid server.

## Preconditions

- A Kaggle account with the new single Kaggle API token set in the environment outside this
  repo as a guarded `KAGGLE_API_TOKEN` environment projection — NOT a pathname-read token file or the legacy
  `KAGGLE_USERNAME` + `KAGGLE_KEY` pair and not a `kaggle.json`. The guarded launcher
  supplies the token to the host controller's environment; it never enters the
  uploaded bundle or a legacy `kaggle.json` in the repo or kernel.
- The selected trusted Python is 3.11+ and contains `kaggle>=2.2.4,<3` plus
  `kagglehub>=1.0.2,<2`. Enabled kernel push, exact-version status, listing and
  downloads use the official SDK in that host controller. kagglehub validates
  the account (`kagglehub.whoami()`). CLI version and legacy diagnostic helpers
  use `python -I -m kaggle`; they never search `PATH` for a console-script shim.
- `[kaggle]` is enabled in `research-compute.toml` with the caps and the weekly GPU-hour cap,
  and `doctor` passes.
- ToS: Kaggle compute is intended for its data-science / competition platform. Keep to modest,
  legitimate research workloads and verify the current Kaggle terms permit this use before the
  first live run. The build and its tests make NO live Kaggle calls.

## What makes Kaggle different

- **Async kernel-push.** `push` submits a reviewed script bundle, `status`/`wait` poll its exact accepted version, and `fetch` downloads bounded results. No persistent server is provisioned.
- **One-unit CPU live path.** The driver refuses GPU and multi-run execution even when the planner reports them as feasible.
- **Free CPU model.** The broker has no CPU cost or GPU-hour gate. This is not a claim of unlimited provider quota or availability.
- **Configured capacity estimates.** Read `kernel_cores`, `kernel_ram_gb` and `session_hours` from configuration/preflight; qualify actual account/image resources separately. Aggregate concurrency is planning-only.
- **GPU planning.** The planner uses a self-imposed weekly GPU-hour cap and local ledger. Live GPU remains disabled pending atomic reservation across processes.

## Portable job bundle (backend-agnostic)

One bundle runs unchanged on local, Kaggle, Modal, Hetzner, or GitHub Actions; only the
execution harness differs. Live Kaggle bundles require `total_units = 1` and
GPU disabled. The worker/chunk contract below supports the intended future fan-out
design; it does not enable multi-run dispatch.

- `manifest.json` -- `total_units`, core-hour estimate, `parallelism`, `gpu`,
  `checkpoint_glob`, `verify` controls, and an explicit `upload_files` allowlist.
- `worker <chunk_idx> <num_chunks>` -- round-robin slice, per-unit checkpoint with flush and
  fsync, and skips units already present in `out/` (resume).
- `run.sh` -- `CORES` fan-out via `xargs -P`, then merge; reads `CHUNK_IDX` / `NUM_CHUNKS`.
- `merge` -- folds `out/*` into a single result, asserts the `manifest.verify` controls, and
  exits nonzero on empty, partial, or any FAIL (a vacuity guard).
- `out/` -- the only writable, fetch-back, resume surface; one checkpoint file per completed unit.

## Driver contract (`kaggle_driver.py`)

Planning verbs never push a kernel. Live operations require the guarded
`KAGGLE_API_TOKEN` environment projection; only `push` submits and requires
explicit confirmation, owner and reviewed bundle digest.

- `bootstrap` -- check the `kaggle` CLI and kagglehub, confirm the API token is present, and validate/prime via kagglehub (`kagglehub.whoami()`); report `doctor`. Never pushes.
- `doctor` -- offline readiness: lane enabled, API token present, `kaggle` + kagglehub installed, caps. No network call.
- `preflight --job DIR [--json]` -- the plan the router consumes: kind (CPU/GPU), planning-only resume rounds, kernel count and concurrency, the configured session estimate, the GPU-hour estimate vs the weekly cap, adequacy, and availability. No kernel.
- `push --job DIR --dry-run` -- validate/snapshot the allowlisted bundle and emit its SHA-256.
- `push --job DIR --owner USER --bundle-sha256 HEX --confirm` -- verify `whoami()` matches `USER`, retain intent for the reviewed ref before kernel submission, then push one reviewed one-unit CPU kernel.
- `status <ref> --submission-intent PATH` -- exact accepted version state; bare-ref status is diagnostic only.
- `wait <ref> --submission-intent PATH` -- poll that version until it completes / errors or the wall cap hits.
- `fetch <ref> --submission-intent PATH --job DIR --dest DIR` -- fetch only the exact accepted version and manifest-allowlisted bounded JSON/log/text outputs. Structural checkpoint validation is distinct from host formal verification.
- `run --job DIR --dry-run` -- report the multi-run shape. Live multi-run is currently fail-closed pending crash-safe recovery.

Use `--dry-run` on `push` and `run` to print the plan with nothing submitted. A live push must
repeat the reviewed bundle SHA-256 and owner; any changed bundle or existing submission
intent is refused. Pending or ambiguously accepted submissions must be polled/reconciled,
never repeatedly pushed.

## The multi-run resume loop (`run`)

This is the intended multi-run design, currently available for planning only. Live execution
remains disabled until status-first crash recovery and manifest-bound checkpoint merging are
implemented. Each planned ROUND:

1. Compute `remaining = total_units - units_done(out/)` from the checkpoints already fetched.
2. Fan out `min(concurrency, remaining)` kernels, one per remaining chunk, using configured
   per-kernel resource/session estimates after account/image qualification.
3. Poll every kernel to completion, then `output` each kernel's checkpoints into the cumulative
   `out/` tree.
4. If units remain, push the accumulated checkpoints as a private Kaggle Dataset (create on the
   first sync, version after) and re-attach it as the next round's kernel input, so re-pushed
   kernels resume from the checkpoints.

The intended loop ends when every unit has a checkpoint (DONE) or `max_runs`
rounds are used. `concurrency × kernel_cores` describes planning capacity only;
it must not be used to size a live job as if those kernels could be dispatched.

## Availability and adequacy

- **CPU planning** uses credentials/account usability and compares requested RAM with configured `kernel_ram_gb`. Live execution additionally requires a reviewed one-unit CPU bundle, matching owner, and qualified POSIX controller.
- **GPU planning** compares the estimate and local trailing usage against the configured weekly cap. A passing plan cannot bypass the driver's live GPU refusal.
- **Capacity qualification** must establish that the actual kernel can run the measured workload within the authorized budget. Planner estimates are not a provider allocation guarantee.

## Guardrails

- **API token** -- the new single Kaggle API token from the guarded
  `KAGGLE_API_TOKEN` environment projection in the host controller, never on argv
  (`/proc/<pid>/cmdline` is world-readable), never logged, never written to a legacy
  `kaggle.json` or a kernel. The legacy `KAGGLE_USERNAME` + `KAGGLE_KEY` pair is not used;
  kagglehub validates the token and yields the username checked before the SDK
  submits a kernel. A redaction filter covers agent-readable output.
- **GPU refusal** -- live GPU push is disabled pending atomic weekly reservation. Planning checks do not establish concurrent-submit safety.
- **Multi-run refusal** -- configured `concurrency` and `max_runs` bound the intended design; live multi-run remains disabled pending status-first recovery and verified checkpoint merging.
- **Confirm** -- live `push` requires explicit `--confirm`, reviewed `--owner` and `--bundle-sha256`; `status`/`wait`/`fetch` use the returned host submission intent.
- **No paid resources** -- no paid server is provisioned. Bound polling and execution expectations by the authorized workflow budget, not an assumed unlimited CPU entitlement.

This lane reuses the broker's `research_compute` routing and ledger code, which installs with
the Modal lane, so install them together (all are in the `full-research` profile).

## Execution identity and network policy

`push` returns a durable submission-intent path and SDK-observed numeric kernel/version
identity. Use that intent for status, wait and fetch; status/list select `vN`, while
file downloads select numeric N. No recovery path substitutes latest or retries an
ambiguous submission. Preserve partial pagination and download evidence.

Job `enable_internet` defaults false and requires host `[kaggle].allow_internet`.
Bind bootstrap/lockfiles in upload_files and validate downloaded dependency evidence
in the purpose-specific host verifier. Native Lean does not require Lax; Internet
is kernel-wide and native execution does not claim Lax's Docker isolation.

Doctor/preflight/dry-run are offline. Bootstrap may validate account readiness;
it is not a remote dependency installer. Live one-unit CPU execution remains the
only enabled submission shape; planning GPU/multi-run does not authorize execution.

The bounded live SDK/account path requires a POSIX main-thread controller.
Native Windows and worker-thread SDK calls are refused before import/authentication;
use a qualified Linux/WSL controller. Native Windows supports offline doctor,
preflight and dry-run only.
