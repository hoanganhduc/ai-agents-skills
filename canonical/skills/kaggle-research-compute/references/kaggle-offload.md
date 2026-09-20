# Kaggle Kernels offload contract

Patterns for running a portable research job bundle on free Kaggle Kernels, then collecting
the results. This is the Kaggle lane of the `research_compute` broker, peer to the Modal,
Hetzner, and GitHub Actions lanes. Treat the agent itself as the adversary (looping, crashing,
self-approving): every rule below exists to stop a compromised or runaway agent from leaking a
credential or over-consuming the GPU quota. Kaggle is materially lower-risk than the paid lanes
because kernels auto-stop at the 12h session cap and cost nothing.

## Preconditions

- A Kaggle account with the new single Kaggle API token set in the environment outside this
  repo as a guarded `KAGGLE_API_TOKEN` environment projection — NOT a pathname-read token file or the legacy
  `KAGGLE_USERNAME` + `KAGGLE_KEY` pair and not a `kaggle.json`. The driver injects the token
  into the `kaggle` subprocess env; it never writes a legacy `kaggle.json` into the repo or a
  kernel.
- The selected trusted Python is 3.11+ and contains `kaggle>=2.2.4,<3` plus
  `kagglehub>=1.0.2,<2`. The driver executes `python -I -m kaggle`; it never searches
  `PATH` for a user-writable console-script shim. kagglehub validates the token
  (`kagglehub.whoami()`) and the module runs kernel operations.
- `[kaggle]` is enabled in `research-compute.toml` with the caps and the weekly GPU-hour cap,
  and `doctor` passes.
- ToS: Kaggle compute is intended for its data-science / competition platform. Keep to modest,
  legitimate research workloads and verify the current Kaggle terms permit this use before the
  first live run. The build and its tests make NO live Kaggle calls.

## What makes Kaggle different

- **Async kernel-push, not SSH/serverless.** You upload a script + `kernel-metadata.json`,
  `push` runs it on Kaggle, `status` polls, `output` downloads results. No persistent server.
- **12h max session (CPU and GPU).** A job needing more wall time spans MULTIPLE kernel runs.
- **CPU is free and quota-free.** The weekly ~30h floating quota is GPU-only; CPU sessions do
  not consume it. So CPU batch is preferred over the paid/quota'd lanes.
- **No clean quota API.** GPU availability uses a self-imposed weekly GPU-hour cap read from a
  local usage ledger (the same "un-queryable balance -> local ledger + self-cap" pattern as
  Hetzner's balance and GHA's minutes), not an API query.
- **Free + auto-stops.** No persistent billing => no reaper, no dead-man's-switch, no teardown.

## Portable job bundle (backend-agnostic)

One bundle runs unchanged on local, Kaggle, Modal, Hetzner, or GitHub Actions; only the
fan-out harness differs. On Kaggle each kernel runs the bundle's `run.sh` at the kernel's cores
over one chunk's slice.

- `manifest.json` -- `total_units`, core-hour estimate, `parallelism`, `gpu`,
  `checkpoint_glob`, `verify` controls, and an explicit `upload_files` allowlist.
- `worker <chunk_idx> <num_chunks>` -- round-robin slice, per-unit checkpoint with flush and
  fsync, and skips units already present in `out/` (resume).
- `run.sh` -- `CORES` fan-out via `xargs -P`, then merge; reads `CHUNK_IDX` / `NUM_CHUNKS`.
- `merge` -- folds `out/*` into a single result, asserts the `manifest.verify` controls, and
  exits nonzero on empty, partial, or any FAIL (a vacuity guard).
- `out/` -- the only writable, fetch-back, resume surface; one checkpoint file per completed unit.

## Driver contract (`kaggle_driver.py`)

Planning verbs are free and never push a kernel. Lifecycle verbs submit real kernels and
require the guarded `KAGGLE_API_TOKEN` environment projection plus an
explicit confirm.

- `bootstrap` -- check the `kaggle` CLI and kagglehub, confirm the API token is present, and validate/prime via kagglehub (`kagglehub.whoami()`); report `doctor`. Never pushes.
- `doctor` -- offline readiness: lane enabled, API token present, `kaggle` + kagglehub installed, caps. No network call.
- `preflight --job DIR [--json]` -- the plan the router consumes: kind (CPU/GPU), estimated resume rounds and kernel count, concurrency, the 12h session cap, the GPU-hour estimate vs the weekly cap, adequacy, and availability. No kernel.
- `push --job DIR --dry-run` -- validate/snapshot the allowlisted bundle and emit its SHA-256.
- `push --job DIR --owner USER --bundle-sha256 HEX --confirm` -- write intent for the exact reviewed ref before any network call, verify `whoami()` matches `USER`, then push one reviewed one-unit kernel.
- `status <ref>` -- kernel run state.
- `wait <ref>` -- poll a kernel until it completes / errors or the wall cap hits.
- `fetch <ref> --job DIR --dest DIR` -- download only flat allowlisted JSON outputs and verify them against the original manifest.
- `run --job DIR --dry-run` -- report the multi-run shape. Live multi-run is currently fail-closed pending crash-safe recovery.

Use `--dry-run` on `push` and `run` to print the plan with nothing submitted. A live push must
repeat the reviewed bundle SHA-256; any changed bundle or existing submission intent is refused.

## The multi-run resume loop (`run`)

This is the intended multi-run design, currently available for planning only. Live execution
remains disabled until status-first crash recovery and manifest-bound checkpoint merging are
implemented. Each planned ROUND:

1. Compute `remaining = total_units - units_done(out/)` from the checkpoints already fetched.
2. Fan out `min(concurrency, remaining)` kernels (up to ~5), one per remaining chunk, each a
   `<=12h` kernel run over its slice at the kernel's ~4 cores.
3. Poll every kernel to completion, then `output` each kernel's checkpoints into the cumulative
   `out/` tree.
4. If units remain, push the accumulated checkpoints as a private Kaggle Dataset (create on the
   first sync, version after) and re-attach it as the next round's kernel input, so re-pushed
   kernels resume from the checkpoints.

The loop ends when every unit has a checkpoint (DONE) or `max_runs` rounds are used. A
`~65-90 core-h` job over ~20 aggregate cores (5 kernels x 4 cores) is ~3.5-4.5 wall-h, so it
usually finishes in ONE round; multi-run only kicks in for jobs larger than one round's ~240
core-h (5 kernels x 4 cores x 12h) or for a single chunk that needs more than 12h.

## Availability and adequacy

- **CPU jobs** -- available on credentials + account-usable alone (free, quota-free). Adequate
  when the job fits one kernel's ~32 GB and is chunkable/resumable to `<=12h` per run.
- **GPU jobs** -- available only while the trailing-7-day GPU-hours plus this job's estimate
  stay within the self-imposed weekly GPU-hour cap (a local usage ledger). Adequate because the
  job is GPU-requested and Kaggle offers GPU kernels. A GPU-quota-exhausted lane is unavailable
  and the router continues to the next GPU-capable lane in the configured order.
- A bigger-than-`~32 GB` job is inadequate on this lane and falls through.

## Guardrails

- **API token** -- the new single Kaggle API token from the guarded
  `KAGGLE_API_TOKEN` environment projection, injected into the `kaggle` subprocess env, never on argv
  (`/proc/<pid>/cmdline` is world-readable), never logged, never written to a legacy
  `kaggle.json` or a kernel. The legacy `KAGGLE_USERNAME` + `KAGGLE_KEY` pair is not used;
  kagglehub validates the token and yields the username, which the kaggle CLI uses for kernel
  ops. A redaction filter covers all agent-readable output.
- **Weekly GPU-hour gate** -- GPU kernels pass a fail-closed gate that refuses if the job's
  GPU-hour estimate plus the trailing-7-day usage would exceed the weekly cap, then reserves
  the estimate in the local usage ledger before the first push, so concurrent GPU submits
  cannot collectively blow the cap. CPU work never touches this ledger (CPU is free).
- **Concurrency cap** -- a round fans out at most `concurrency` (~5) kernels, respecting
  Kaggle's concurrent-session limit.
- **Loop bound** -- `max_runs` bounds the resume loop, so a looping or crashing agent cannot
  push kernels forever.
- **Confirm** -- `preflight` is free and emits the plan; lifecycle verbs refuse without an
  explicit confirm.
- **No teardown** -- kernels auto-stop at the 12h session cap and cost nothing, so there is no
  reaper and nothing to destroy. This is why Kaggle sits right behind local: free, low-risk,
  and quota-free for CPU.

This lane reuses the broker's `research_compute` routing and ledger code, which installs with
the Modal lane, so install them together (all are in the `full-research` profile).
