# Compute Offload Sizing Gate

Fill this in **before** submitting any job to a remote lane (Kaggle, Modal,
Hetzner, GitHub Actions). Its purpose is to make the dispatch decision on
measured numbers rather than on guesses, so a job is not accepted, booted, and
paid for before it fails.

A plan that returns `accepted: true` is **not** evidence that the job fits. The
lanes differ in how much they check: Kaggle compares your declared RAM against
the kernel ceiling, while Modal's adequacy verdict
(`modal_authenticated_api_usable`) is an API **liveness probe only** and never
compares your request against the target function's declared `cpu=`/`memory=`.
Steps 2 and 4 exist because the broker will not do that comparison for you.

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
| Kaggle | `config/research-compute.toml` `[kaggle]` | `kernel_cores`, `kernel_ram_gb` | `concurrency` | cores × concurrency |
| Modal | `@app.function(cpu=, memory=)` in `research_compute/modal_app.py` | declared `cpu`/`memory` | per-call | n/a |
| Hetzner | server type in `[hetzner]` | vCPU / RAM of the type | 1 server | n/a |
| GitHub Actions | runner label | 2–4 vCPU | matrix cells | cells × vCPU |

Kaggle capacity is **per-kernel × concurrency**, not per-kernel. Quoting only
the per-kernel figure understates the free lane badly and misroutes work to a
paid one.

| Field | Value |
|---|---|
| Lane chosen |  |
| Declared cores per unit |  |
| Declared RAM per unit |  |
| Parallel units available |  |
| Session/timeout ceiling |  |
| Cost per unit |  |
| Does the workload's peak RSS fit the per-unit RAM? |  |
| Does `total_units` use the available fan-out? |  |

---

## Step 3 — Write the manifest in the correct dialect

The two manifest dialects are **not** interchangeable, and an unrecognized
top-level key is dropped **silently** — no warning, no error. A manifest whose
resource block lands under the wrong key plans as if it requested nothing.

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
a round number, and set `total_units` to match the fan-out.

---

## Step 4 — Assert the plan before dispatch

Run `plan` (or `preflight`) and check all four. Any failure means fix the
manifest and re-plan — not submit and see.

| # | Assertion | Pass? |
|---|---|---|
| 1 | The plan echoes a **non-empty** `constraints` block equal to what you wrote (proves the key was read, not dropped) |  |
| 2 | `routing_trail` gives a per-lane adequacy reason that **references your numbers** |  |
| 3 | The chosen lane's declared capacity ≥ your Step 1 peak RSS and core need — **check this yourself for Modal**, whose adequacy is liveness-only |  |
| 4 | Estimated cost and runtime are within the intended envelope |  |

Record the decision: lane ______, declared size ______, est. cost ______.

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
