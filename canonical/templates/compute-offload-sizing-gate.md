# Compute Offload Sizing Gate

Fill this in **before** submitting any job to a remote lane (Kaggle, Modal,
Hetzner, GitHub Actions). Its purpose is to make the dispatch decision on
measured numbers rather than on guesses, so a job is not accepted, booted, and
paid for before it fails.

The broker checks that the job fits the lane's declared RAM and rejects it when
it does not. It cannot check what you never measured: an unmeasured peak RSS or
a guessed `total_units` produces a manifest that fits a lane the real job does
not. Steps 1 and 2 supply the numbers the broker then enforces, and Step 4 is
where you confirm it enforced them against yours.

Do not skip a step because the job "looks small". Step 1 is what tells you
whether it is small.

---

## Step 1 — Characterize the workload

Measure before sizing. A short calibration run at reduced scale is enough, and
it also catches the case where the job is too small to be worth offloading.

| Field | Value |
|---|---|
| What the job computes |  |
| Calibration scale run locally |  |
| Wall time at that scale |  |
| Scaling law (time vs input, measured) |  |
| Extrapolated wall time at full scale |  |
| Peak RSS at calibration scale |  |
| Does peak RSS grow with input? |  |
| Independent units (`total_units`) |  |
| Checkpointable per unit? |  |
| Speedup measured at N workers |  |

**Go/no-go.** Offload only if the extrapolated wall time exceeds what the local
self-preservation veto allows (`w_safe` workers within the wall budget). If the
job fits locally and safely, keep it local and record that here.

Decision: `local` / `offload` — because: ______

---

## Step 2 — Read the declared lane capacity

Never infer an allocation from inside the container. Read the declared spec
from the source of truth below, and treat container self-reports as unverified.

| Lane | Ground truth for its size | Per-unit | Parallel units | Aggregate |
|---|---|---|---|---|
| Kaggle | `[kaggle]` configuration estimates, echoed by `preflight`; qualify the actual account/image separately | `kernel_cores`, `kernel_ram_gb` | 1 for live CPU; configured `concurrency` is planning-only | One kernel live; `aggregate_cores` is planning-only |
| Modal | `modal_backend.FUNCTION_CAPACITY` | declared `cpu`/`memory` | per-call | n/a |
| Hetzner | server type in `[hetzner]` | vCPU / RAM of the type | 1 server | n/a |
| GitHub Actions | runner label | 2–4 vCPU | matrix cells | cells × vCPU |

The Kaggle driver's `preflight` returns `kernel_cores`, `kernel_ram_gb` and
`aggregate_cores`, so a bundled job can be sized without opening the config.

Live Kaggle execution is limited to **one CPU kernel with `total_units = 1`**.
Configured concurrency and aggregate capacity describe the intended multi-run
design only; they are not dispatchable capacity. GPU and multi-run remain
disabled even when a plan reports them as feasible. Configuration values are
estimates, not a guarantee of the current provider allocation.

| Field | Value |
|---|---|
| Lane chosen |  |
| Declared cores per unit |  |
| Declared RAM per unit |  |
| Parallel units available |  |
| Session/timeout ceiling |  |
| Cost per unit |  |
| Does the workload's peak RSS fit the per-unit RAM? |  |
| Does `total_units` match the enabled execution shape (Kaggle live: 1 CPU unit)? |  |

---

## Step 3 — Write the manifest in the correct dialect

The two manifest dialects are **not** interchangeable. The broker rejects a job
carrying any unrecognized top-level key (`unknown_manifest_key`), naming the
offending key and the valid set, so a resource block under the wrong key fails
loudly at plan time instead of planning as if it requested nothing.

| Manifest | Resource block goes under | Read by |
|---|---|---|
| Broker job (`plan` / `submit`) | top-level **`constraints`** | `planner.py` |
| Kaggle bundle `manifest.json` | flat top level (`cores`, `memory_mb`, `total_units`) | `kaggle_driver.py` |

```json
{
  "job_id": "...",
  "constraints": { "cores": 4, "memory_mb": 8192, "core_hours": 0.5, "gpu": false },
  "payload": { "...": "..." }
}
```

Set `cores` / `memory_mb` to the **declared per-unit spec from Step 2**, not to
a round number, and set `total_units` to match the enabled execution shape.
Kaggle live bundles require `total_units = 1` and GPU disabled; fan-out estimates
remain planning-only.

---

## Step 4 — Assert the plan before dispatch

Run `plan` (or `preflight`) and check all five. Any failure means fix the
manifest and re-plan — not submit and see.

| # | Assertion | Pass? |
|---|---|---|
| 1 | The plan echoes a **non-empty** `constraints` block equal to what you wrote |  |
| 2 | `routing_trail` gives a per-lane adequacy reason that **references your numbers** (`modal_capacity_ok:cores=4 ram_gb=8 …`, `peak_ram … kernel RAM …`) |  |
| 3 | No lane in the trail reports `cores_oversubscribed=` — if one does, your worker count exceeds the reserved cores and the runtime estimate is wrong by that ratio |  |
| 4 | Estimates match `total_units` and the enabled execution shape; Kaggle GPU/multi-run estimates do not authorize live dispatch |  |
| 5 | Estimated cost and runtime are within the intended envelope |  |

Record the decision: lane ______, declared size ______, est. cost ______.

For Kaggle, use reviewed one-unit CPU `push` followed by intent-bound
`status`/`wait`/`fetch`; see `kaggle-research-compute` for the exact command path.
Review `--owner` and the dry-run `--bundle-sha256` before push. Do not repush pending
or ambiguously accepted submissions. Internet defaults off; enabling it requires
explicit job opt-in and host policy permission, and applies to the whole kernel.
Live SDK operations require a qualified POSIX controller; native Windows supports
offline planning only.

---

## Step 5 — Verify what you actually got

After the run, compare realized against declared. Report both side by side;
never report a container self-report as if it were the allocation.

| Field | Declared (Step 2) | Observed | Match? |
|---|---|---|---|
| Cores |  |  |  |
| RAM |  |  |  |
| Wall time |  |  |  |
| Cost |  |  |  |

**`os.cpu_count()` is never authoritative** inside a container — it reports the
worker host. The cgroup (`/sys/fs/cgroup/cpu.max`, `memory.max`) is authoritative
on some lanes but **not on Modal**, where the limit is enforced by the scheduler
outside the gVisor guest and the guest's own cgroup reports the host's figures.
Where the two disagree, the declared spec wins and the discrepancy is worth
noting in the run report.

If observed capacity or runtime diverges materially from declared, record it
here and update the Step 2 figures for the next job rather than re-running blind.
