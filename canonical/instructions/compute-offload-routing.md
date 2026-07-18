# Compute offload routing (unified)

Umbrella router guidance for heavy compute. The `research_compute` broker chooses a backend
by available resources and credits, in this strict priority:

> **local > Kaggle > Modal > Hetzner > GitHub Actions**

Kaggle sits right behind local because its CPU compute is **free** and does **not** consume
the GPU quota, so a CPU job that fits Kaggle's constraints is preferred over the paid/quota'd
lanes (Modal, Hetzner, GitHub Actions).

The broker walks the routing order and takes the **first backend that is AVAILABLE**
(credentials or credits present, reachable, within budget) **AND ADEQUATE** (resources fit
the job estimate). `run plan job.json` and `run doctor` are the actual router; `doctor` warns
if `routing_order` deviates from this priority. This doc links, and does not replace, the two
per-backend routing docs:

- `modal-offload-routing.md` -- when to keep work local vs. route to Modal (remote CPU, high-memory CPU, GPU).
- `github-actions-offload-routing.md` -- when to route to GitHub Actions (private repo, budget-gated, last automatic lane).

The Kaggle and Hetzner lanes' driver and guardrail contracts live with their skills in
`kaggle-research-compute/references/kaggle-offload.md` and
`hetzner-research-compute/references/hetzner-offload.md`.

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
3. **Modal** -- the next offload tier: remote CPU, high-memory CPU, or GPU, when the host is Modal-ready and within the USD budget. Modal is the paid on-demand GPU workhorse (see GPU policy below).
4. **Hetzner** -- the next offload tier after Modal, for CPU / high-memory work, when `HCLOUD_TOKEN` is present and the budget allows. A disposable server runs the portable bundle at full cores, then is destroyed. Hetzner Cloud has no on-demand GPU, so GPU-requested jobs skip it (see GPU policy below).
5. **GitHub Actions** -- the last automatic lane: a private research repo's own committed experiment code, budget-gated on included minutes, proportionate, never a general compute pool.

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

## Budget and teardown discipline

- Every paid/quota'd offload lane is **budget-gated, fail-closed**: the broker reserves the pessimistic worst case in the shared append-only ledger before dispatch, so concurrent submits cannot collectively overspend. Hetzner reserves EUR, GitHub Actions reserves minutes, Modal reserves USD, and Kaggle GPU reserves GPU-hours against a self-imposed weekly cap. **Kaggle CPU is free and quota-free, so it has no cost gate.**
- Within the auto-approve envelope the agent may submit alone (logged); spend above it needs out-of-band human confirmation the agent cannot mint.
- **Teardown is mandatory on Hetzner.** A powered-off server still bills; only DELETE stops it. Teardown must run on every terminal path (success, failure, timeout, boot-fail, push-fail, crash), and failure or timeout paths fetch checkpoints before destroy so the run is resumable. Modal, GitHub Actions, and Kaggle are metered/free per run and need no explicit teardown -- Kaggle kernels auto-stop at the 12h session cap and cost nothing, so Kaggle needs no reaper.
- Never print or copy remote credentials into prompts, logs, docs, or managed repo files. Tokens and API keys are read from the environment, never passed on argv, and never placed on a server or kernel.

## Setup and commands

Each lane installs to the runtime root and runs via `run_skill.sh`. Resolve the runtime root
for the current agent, then use the lane's wrapper:

```bash
runtime="${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}"
# router + Modal / GitHub Actions lanes:
run() { bash "$runtime/run_skill.sh" skills/modal-research-compute/run_modal_research_compute.sh "$@"; }
# Kaggle lane:
kg() { bash "$runtime/run_skill.sh" skills/kaggle-research-compute/run_kaggle_research_compute.sh "$@"; }
# Hetzner lane:
hz() { bash "$runtime/run_skill.sh" skills/hetzner-research-compute/run_hetzner_research_compute.sh "$@"; }

run doctor                 # routing_order + Modal / GitHub Actions readiness
run plan job.json          # the router's backend choice
kg preflight --job ./bundle --json   # the Kaggle plan (no kernel)
kg run --job ./bundle --confirm      # multi-run resume loop across concurrent kernels (free CPU)
hz preflight --job ./bundle --json   # the Hetzner plan (no server)
hz oneshot --job ./bundle --confirm  # provision -> run -> fetch -> destroy (teardown guaranteed)
```
