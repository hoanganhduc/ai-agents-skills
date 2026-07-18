# GitHub Actions offload contract

Battle-tested patterns for sharding heavy research computation (enumeration,
certificate suites, censuses) onto GitHub Actions runners. Derived from a
long-running research computation campaign (60+ sharded suites, ~10^9 objects
enumerated, zero lost results after adopting this contract). Use these as the default
implementation whenever a research loop offloads to Actions.

## Preconditions

- Check billing ONCE per session before the first launch:
  `gh api /users/<owner>/settings/billing/usage` (net amounts; private repos
  draw included minutes). Size shard counts to stay inside included minutes.
- Offload branch: a dedicated branch (e.g. `research-compute`) with a `compute/`
  bundle. `workflow_dispatch` only works once the workflow file exists on the
  default branch; add `on: push: branches: [<branch>] paths: [compute/**, .github/workflows/<name>.yml]`
  so pushes to the offload branch trigger directly. Narrow `paths:` per
  workflow — otherwise unrelated pushes re-fire old workflows.

## Driver contract (`compute/actions_<suite>.py`)

- Signature: `<suite> <shard_idx> <shard_count> <budget_s> <out.json>`.
- Per-config hard cap: `signal.signal(signal.SIGALRM, ...)` + `signal.alarm(cap)`
  around each config; a pathological config must never hang a shard.
- NO `multiprocessing.Pool` on 2-core runners: `Pool.terminate` hangs have lost
  results; run sequentially under the alarm.
- Vendor the import closure byte-identical into `compute/` (trace real imports;
  some research modules parse `sys.argv` at import — sanitize argv first).
- Output JSON per shard: `{suite, shard, done: [(config_id, status, stats)],
  failures: [...]}` with status in PASS/FAIL/TIMEOUT/ERROR. FAIL is a signal
  (potential counterexample lead), never a crash.

## Workflow contract (`.github/workflows/<name>.yml`)

- Matrix over `(suite, shard)`; `timeout-minutes` per job slightly above the
  driver budget; `fail-fast: false`.
- Upload one artifact per shard with a stable name (`<suite>-<shard>`).
- The merge job MUST declare `needs: [<all shard jobs>]`. A merge that runs
  without its shards reads zero artifacts and, without controls, passes
  vacuously — the worst silent failure.
- The merge job MUST carry NON-VACUOUS CONTROLS: assert banked known values
  (e.g. "control suite m8 must reproduce 111/111") so an empty or partial
  merge FAILS LOUDLY (exit 1) instead of passing. This is the vacuity lesson:
  empty inputs must never look like success.
- The merge job exits 1 on ANY FAIL row, so genuine certificate failures
  surface as red runs. Expect and document "failure by design": a red run can
  mean the alarm worked, not that the infrastructure broke — read the merge
  log before reacting.

## Launch discipline

- Do not push many new workflow files at once: simultaneous workflows queue
  behind org/repo concurrency limits and merges can fire against missing
  artifacts. Prefer ONE workflow with a suite matrix, or staged pushes.
- Smoke-test the driver locally on a 3-5 config slice per suite BEFORE pushing
  (proves the import closure and the spec path); never run full suites locally.

## Merge-back and resume

- `gh run download <RUN_ID> -R <repo> -D <dir>` then a merge script that
  tallies per-suite counts and writes a consolidated JSON into the research
  tree (provenance: record run IDs in the deliverable).
- Resume pattern: regenerate pending lists as grid-minus-PASSes from the
  merged JSON and re-push only the remainder.
- The runner logs themselves are an independent execution record — quote the
  merge log lines (not the agent's summary) when banking results.

## Verification interplay

- Same-script re-runs cannot catch SPEC bugs; for load-bearing certificates,
  require an independent reimplementation of the checker (locally, small) in
  the verification gate.
- A red run with 0 artifacts = wiring bug (fix `needs:`/artifact names).
  A red run with FAIL rows = mathematics (profile the config, recompute the
  hosting object's exact status before any claim).
