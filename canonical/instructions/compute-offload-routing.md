# Compute offload routing (unified)

Umbrella router guidance for heavy compute. The `research_compute` broker chooses a backend
by available resources and safety gates. Its recommended default priority is:

> **local > Kaggle > Modal > Hetzner > GitHub Actions**

Kaggle sits right behind local because its CPU compute is **free** and does **not** consume
the GPU quota, so a CPU job that fits Kaggle's constraints is preferred over the paid/quota'd
lanes (Modal, Hetzner, GitHub Actions).

The broker honors a valid configured `routing_order` or explicit `policy.backend` override;
otherwise it walks the recommended default order and takes the **first backend that is AVAILABLE**
(credentials or credits present, reachable, within budget) **AND ADEQUATE** (resources fit
the job estimate). An explicit override chooses the lane but does not exempt the job from
the adequacy check: forcing a lane the job does not fit is rejected, not dispatched, because
a lane's hard memory ceiling would otherwise kill the job after it was accepted.
`run plan job.json` is the decision boundary; `doctor` warns if
`routing_order` deviates from the recommended order. Provider guards are lazy: a safe local
decision does not contact remote providers, and the ordered walk stops probing after the
first admitted lane. This doc links, and does not replace,
the lane-specific routing contracts:

A valid custom order keeps `local` exactly once in the first position, then may reorder or
omit remote lanes; lane names must be supported and unique. `doctor` reports invalid orders
distinctly, and automatic planning rejects them rather than falling open to an unlisted lane.

- `modal-offload-routing.md` -- when to keep work local vs. route to Modal (remote CPU, high-memory CPU, GPU).
- `github-actions-offload-routing.md` -- when to route to GitHub Actions (private repo, budget-gated, last automatic lane).

The Kaggle and Hetzner lanes' driver and guardrail contracts live with their skills in
`kaggle-research-compute/references/kaggle-offload.md` and
`hetzner-research-compute/references/hetzner-offload.md`.

## Mandatory pre-dispatch sizing gate

Routing decides *which* lane. This gate decides whether the job has been
**characterized** well enough to be routed at all. Work through
`templates/compute-offload-sizing-gate.md` before any `submit`; it is five
steps: characterize the workload by measurement, read the declared lane
capacity, write the manifest in the correct dialect, assert the plan, then
verify what was actually provisioned.

Two rules carry most of the weight:

- **`accepted: true` still reports how it decided.** Every lane now states its
  adequacy against your numbers. Kaggle compares declared RAM against
  `kernel_ram_gb` (`peak_ram 1024GB exceeds kernel RAM 32GB`); Modal compares
  peak RSS against the declared `memory=` of the function the decision maps to
  and rejects with `modal_capacity_exceeded:ram_gb requested=… declared=…`.
  Read the reason: Modal's `cpu=` is a reserved floor rather than a cap, so
  asking for more workers than cores is admitted and reported as
  `cores_oversubscribed=12>4` — the job completes, but at a fraction of the
  throughput the estimate assumed.
- **Size from the declared spec, never from inside the container.**
  `os.cpu_count()` reports the worker host, not the allocation. The cgroup is
  authoritative on Kaggle but **not on Modal**, where the limit is enforced by
  the scheduler outside the gVisor guest and the guest's cgroup reports host
  figures. `modal_backend.FUNCTION_CAPACITY` is the ground truth, and
  `modal_app.py` builds its `@app.function(...)` decorators from it so the two
  cannot drift.

## Manifest resource contract

There are two manifest dialects and they are **not** interchangeable. The broker
**rejects** a job carrying any unrecognized top-level key, naming the offending
key and listing the valid set, because a resource block under the wrong key
would otherwise plan as though nothing was requested and route the job to the
wrong hardware.

| Manifest | Resource block key | Read by |
|---|---|---|
| Broker job (`plan`, `fanout-plan`, `submit`) | top-level **`constraints`** | `research_compute/planner.py` |
| Kaggle bundle `manifest.json` | flat top level: `cores`, `memory_mb`, `total_units`, `checkpoint_glob` | `kaggle_driver.py` |

A rejection carries the `unknown_manifest_key` risk flag. Fix the manifest and
re-plan; do not work around it by deleting the key, which loses the request.

## Lane capacity reference

Declared per-unit capacity, and the aggregate that matters for sizing. Read the
live values from the sources named in the sizing-gate template; the figures
below are the shape of the comparison, not a substitute for reading them.

| Lane | Per unit | Parallel units | Aggregate | Cost |
|---|---|---|---|---|
| Kaggle CPU | `kernel_cores` (4) / `kernel_ram_gb` (32 GB), 12 h session | `concurrency` (5) | **20 vCPU / 160 GB** | free, quota-free |
| Modal `run_cpu_job` | `cpu=4.0`, `memory=8192` | per-call | 4 vCPU / 8 GB | paid |
| Modal `run_highmem_job` | `cpu=16.0`, `memory=65536` | per-call | 16 vCPU / 64 GB | paid |
| Modal `run_gpu_job` | `gpu="L4"`, `cpu=8.0`, `memory=32768` | per-call | 8 vCPU / 32 GB + L4 | paid |

Kaggle's aggregate free capacity exceeds Modal's paid high-memory tier in core
count, which is why Kaggle sits ahead of Modal in the default order. Sizing a
job to one kernel instead of the fan-out understates the free lane by 5x and
misroutes work to a paid lane.

## Keep work local when

- the data is small enough for the current machine and setup overhead would dominate;
- credentials or private data must not leave the machine (secret-locality data is never offloaded);
- local verification is faster than provisioning; and
- the local self-preservation projection says the full run stays under the load ceiling.

## Local self-preservation veto

The local lane is gated so a local run can never trip this host's auto-restart. Each
CPU-bound worker adds about 1.0 to sustained load, and `nice` does not lower loadavg, so the
only control is the worker count.

- **Pre-launch projection.** `w_safe = floor(danger_load_frac*N - loadavg - session_headroom_frac*N)` and `w_needed = ceil(core_hours / local_wall_budget_h)`. Reject and fall back if even one worker is unsafe (`w_safe < 1`) or the wall budget only fits at unsafe parallelism (`w_needed > w_safe`). Otherwise accept as safe throttled-local pinned to `w_eff` workers.
- **Runtime watchdog.** Poll the 1-minute load; a soft breach sheds a worker; a hard breach (still below the measured restart point) checkpoints, aborts, and falls back to the next tier, resuming from the checkpoint.
- **Unfallable hard-stop.** Secret-locality data that is load-unsafe cannot offload (offloading it is the forbidden act) and cannot safely run local -- surface it to the user, never gamble locally.

## Backend selection (non-secret work)

1. **local** -- chosen only when the self-preservation projection proves it stays safe for the whole run.
2. **Kaggle** -- the first offload tier: free Kaggle Kernels for CPU batch (and GPU under a weekly cap). CPU sessions are **free and quota-free**, so a CPU job that fits one kernel's ~32 GB and is chunkable/resumable to <=12h per run is preferred over the paid lanes. A job longer than 12h spans multiple kernel runs (the multi-run resume loop). No cost gate, no teardown -- kernels auto-stop at 12h. Available when the new Kaggle API token (`KAGGLE_API_TOKEN`, or `~/.kaggle/access_token`) is present; kagglehub validates it and the kaggle CLI (>=1.8.0) runs the kernel ops.
3. **Modal** -- the next offload tier: remote CPU, high-memory CPU, or GPU, when the host has authenticated Modal API liveness and the job estimate is within the configured per-job USD cap. Modal is the paid on-demand GPU workhorse (see GPU policy below).
4. **Hetzner** -- the next offload tier after Modal, for CPU / high-memory work, when `HCLOUD_TOKEN` is present and the budget allows. A disposable server runs the portable bundle at full cores, then is destroyed. Hetzner Cloud has no on-demand GPU, so GPU-requested jobs skip it (see GPU policy below).
5. **GitHub Actions** -- the last automatic lane: a private research repo's own committed experiment code, budget-gated on included minutes, proportionate, never a general compute pool.


## User-requested compute resources (strict allowlist)

When the user **specifically names** compute resources (for example "use Hetzner and
Kaggle", "only Modal", "no local residual"), the agent and broker must treat that as a
**hard allowlist**, not a soft preference:

1. **Encode the request** on the job as either:
   - `policy.backend = "<one>"` — single hard pin (existing), or
   - `policy.backends = ["kaggle", "hetzner", ...]` — ordered multi-backend allowlist
     (alias: `policy.preferred_backends`).
2. **Only admit listed lanes.** Unlisted backends — including **local** when omitted —
   must not be used as a silent fallthrough while any listed lane might still be
   available. Parallel fan-out may use several listed lanes at once, but not unlisted ones.
3. **Exhaustion before expansion.** If every listed lane is unavailable, inadequate,
   over budget, or out of credits/quota, the plan is **rejected** (or the loop path is
   blocked / deferred). Do **not** invent another resource the user did not name.
4. **Do not bypass the broker.** Agents must not launch ad-hoc heavy local processes
   (`python … residual …`, unthrottled multi-core sweeps) while a user allowlist or
   offload path is active. Local light work (merge, pin checks, smoke) is fine.
5. **Mutual exclusion.** `policy.backend` and `policy.backends` must not both be set.

This is stronger than the default `routing_order` walk (which still starts with `local`
for automatic routing). User-named resources win until they are all exhausted.

## GPU policy (router-wide)

GPU is enabled on every backend that supports on-demand GPU, and is used when either the
job auto-signals GPU or the user explicitly requests it:

> `gpu_requested = auto_gpu_signal OR policy.gpu`

`auto_gpu_signal` is inferred from the job estimate -- a GPU task-family/type marker or an
explicit `gpu` resource class. `policy.gpu` (or the equivalent `constraints.gpu`) is the
explicit request. Because the trigger is a disjunction, an **explicit request always wins**:
it forces a GPU lane even when auto-detection would classify the job as CPU. A job with no
GPU signal at all stays on a CPU lane and is never given a GPU.

A GPU-requested job walks `routing_order` and takes the **first GPU-capable and available**
lane, so GPU routing is cheapest-first by the same priority as CPU routing:

- **local** -- GPU-capable only when the resource snapshot shows a local GPU.
- **Kaggle** -- GPU-capable and **free**, within a self-imposed weekly GPU-hour cap (12h
  sessions). A GPU-quota-exhausted lane is unavailable and the router falls through.
- **Modal** -- always GPU-capable; the paid on-demand GPU destination.
- **Hetzner** -- never: Hetzner Cloud has no on-demand GPU, so a GPU job always skips it.
- **GitHub Actions** -- GPU only via paid "larger runners" (Team/Enterprise; not free minutes,
  not public repos). Opt-in through `[gha].gpu_enabled`, **off by default**; when on, the lane
  is GPU-capable but still bounded by the cumulative Actions-minutes cap.

With the default order this resolves to **local-GPU (if present) then Kaggle-GPU (free, within
the weekly cap) then Modal-GPU**, with Hetzner skipped and GitHub Actions used only when its
GPU is opted in. If no GPU-capable lane is available -- for example a GPU job when the box has
no GPU, the Kaggle weekly GPU-hour cap is exhausted, and Modal is unavailable -- the job is
rejected rather than silently run on CPU. The Kaggle weekly GPU-hour cap, Modal's USD budget,
and (when opted in) the GitHub Actions minutes cap all still apply, so a GPU choice never
bypasses the budget gate.

## Multi-backend parallel fan-out (v2)

The sections above route ONE job to ONE lane. For a LARGE divisible batch job -- M
independent, resumable chunks (a sweep or enumeration split into shards) -- the v2 fan-out
scheduler instead splits the chunks across SEVERAL lanes AT ONCE (some chunks local, some
on the free lane, some on a paid lane), each lane sized to its spare capacity, to minimise
the makespan (time until every chunk's result is back) while minimising cost. Fan-out is a
scheduler layer ON TOP of the same per-lane probes and drivers; small jobs still use the
single-lane router. It is opt-in (`[fanout].enabled`) and triggers only when the job
declares at least `[fanout].min_chunks` chunks.

- **Objective knob.** Each job carries `speed_cost_weight` in [0, 1]: `0` is cheapest (free
  and cheap lanes only, accept a slower finish), `1` is fastest (recruit paid lanes
  aggressively to cut the makespan), and values between blend the two. The allocator
  minimises `weight * norm(makespan) + (1 - weight) * norm(cost)` over feasible splits by
  water-filling chunks -- free and cheap lanes first, paid lanes added only as far as the
  speed target needs. The default is `0.5`, overridable per job via
  `policy.speed_cost_weight`.
- **Cost model.** Local (Oracle Cloud) is **not** free -- its per-core-hour cost enters the
  objective. Kaggle is the free lane (cost 0). Hetzner is billed in EUR and normalised into
  the objective's USD cost term through `[fanout].usd_per_eur`; GitHub Actions minutes are
  prepaid, so their marginal objective cost is 0 while their consumption stays rail-limited.
- **Hard rails still bind.** The knob only redistributes chunks *within* each lane's
  ceiling; it can never breach a cap. Every rail is enforced as a per-lane `max_chunks`
  ceiling: per-lane budget caps, the <= EUR 3/day auto-approve envelope, the GitHub Actions
  60% cumulative-minutes cap, Kaggle's weekly GPU-hour quota, and local's self-preservation
  load-cap (`w_safe`) and wall budget. A speed-leaning knob uses more *allowed* capacity, not
  more than is allowed. The per-lane budget gates (`budget_gate` / `gpu_budget_gate`) remain
  the fail-closed enforcement at dispatch; the fan-out ceilings are the planning-time
  sizing.
- **Aggregation and fault-tolerance.** Each lane's partial `out/` is merged into one result
  set, preserving the bundle's non-vacuous banked-value guard (a merge in which every chunk
  is empty is refused); a successful retry replaces a stale failed/vacuous duplicate. A
  stalled or failed lane's UNFINISHED chunks are reassigned to a healthy lane, but all work
  that lane already consumed—including completed chunks—continues to count against its
  cumulative quota/cost rail. Because chunks are resumable, no finished work is lost and
  every chunk is covered exactly once.

The scheduler is `research_compute/fanout.py`; its allocator is a pure, deterministic
function (identical lane probes + M + weight give an identical split) separated from all IO.
`run fanout-plan job.json` returns the split, makespan, cost, and each lane's chunk-id range
without dispatching -- execution reuses the per-lane drivers above.

## Budget and teardown discipline

- Every paid/quota'd offload lane is **guarded, fail-closed** before dispatch. Hetzner and GitHub Actions reserve pessimistic worst-case EUR/minutes in the shared ledger, and Kaggle GPU reserves GPU-hours against its weekly cap, so concurrent submissions cannot exceed those configured rails. Modal requires authenticated API liveness and enforces the lower of the job's USD cap and broker per-job USD cap at planning time; it does not claim a shared monthly reservation ledger. **Kaggle CPU is free and quota-free, so it has no cost gate.**
- Within the auto-approve envelope the agent may submit alone (logged); spend above it needs out-of-band human confirmation the agent cannot mint.
- **Teardown is mandatory on Hetzner.** A powered-off server still bills; only DELETE stops it. Teardown must run on every terminal path (success, failure, timeout, boot-fail, push-fail, crash), and failure or timeout paths fetch checkpoints before destroy so the run is resumable. Modal, GitHub Actions, and Kaggle are metered/free per run and need no explicit teardown -- Kaggle kernels auto-stop at the 12h session cap and cost nothing, so Kaggle needs no reaper.
- Never print or copy remote credentials into prompts, logs, docs, or managed repo files. Tokens and API keys are read from the environment, never passed on argv, and never placed on a server or kernel.

## Setup and commands

Each lane installs to the runtime root and runs via `run_skill.sh`. Resolve the runtime root
for the current agent, then use the lane's wrapper:

```bash
# Credential-bearing lanes must launch from a root-owned AAS component
# generation; the per-user runtime copy is refused by the credential gate.
# The generation directory is named for a git commit, so its name carries no
# ordering -- pick the newest publish, which the store records as its mtime.
launcher="${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh"
newest=0
for gen in /usr/local/libexec/coding-system/components/ai-agents-skills/*/; do
  gen="${gen%/}"
  [ -f "$gen/manifest/credential-runtime.json" ] || continue
  [ -x "$gen/canonical/runtime/runners/run_skill.sh" ] || continue
  stamp="$(stat -c %Y "$gen" 2>/dev/null || stat -f %m "$gen" 2>/dev/null)" || continue
  [ "${stamp:-0}" -gt "$newest" ] || continue
  newest="$stamp"
  launcher="$gen/canonical/runtime/runners/run_skill.sh"
done
# router + Modal / GitHub Actions lanes:
run() { bash "$launcher" skills/modal-research-compute/run_modal_research_compute.sh "$@"; }
# Kaggle lane:
kg() { bash "$launcher" skills/kaggle-research-compute/run_kaggle_research_compute.sh "$@"; }
# Hetzner lane:
hz() { bash "$launcher" skills/hetzner-research-compute/run_hetzner_research_compute.sh "$@"; }

run doctor                 # routing_order + Modal / GitHub Actions readiness
run plan job.json          # the router's backend choice
kg preflight --job ./bundle --json   # the Kaggle plan (no kernel)
kg run --job ./bundle --confirm      # multi-run resume loop across concurrent kernels (free CPU)
hz preflight --job ./bundle --json   # the Hetzner plan (no server)
hz oneshot --job ./bundle --confirm  # provision -> run -> fetch -> destroy (teardown guaranteed)
```

When restoration configures `AAS_COMPUTE_SECRETS_FILE`, the managed broker and
Hetzner wrappers strictly load its exact four-key compute authority
(`HCLOUD_TOKEN`, `HCLOUD_SSH_KEYS`, `KAGGLE_API_TOKEN`, `KAGGLE_CONFIG_DIR`)
into only their child process tree. Keep the pointer in the launcher
environment; never copy it or its assignments into a job manifest or
agent-writable loop env.
