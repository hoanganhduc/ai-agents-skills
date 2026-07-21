# GitHub Actions offload routing (research_compute)

Sibling to `compute-offload-routing.md`. The `research_compute` broker can route a compute job
to **GitHub Actions** after considering the local, Kaggle, Modal, and Hetzner lanes.

> **GitHub Actions ToS compliance.** GitHub's Additional Product Terms restrict Actions on
> GitHub‑hosted runners to *"the production, testing, deployment, or publication of the
> software project associated with the repository,"* and forbid *"disproportionate"* /
> serverless burden; misuse can disable the repo or account. Here, Actions compute runs **only
> inside a private research repo**, executes **that repo's own committed experiment code**
> (params are DATA, never executed), as that project's own validation, **budget‑gated** and
> kept **proportionate**, and is the **last** backend in the recommended automatic order
> (after local, Kaggle, Modal, and Hetzner). It is never a general compute pool. Source: GitHub *Terms for Additional Products and Features →
> GitHub Actions*.

## Rules
1. Recommended automatic order is `local → kaggle → modal → hetzner → gha`; a valid configured
   `routing_order` keeps local first and may reorder/omit unique remote lanes, while a valid
   `policy.backend` override is a hard pin. `gha` is off until
   `[gha].enabled = true` and broker `doctor` reports it ready (classic PAT with billing-read; GitHub
   budget set > $0 with stop-usage; target repos private).
2. The target must be a **private** repo registered in `[gha.repos.<key>]` with an in‑repo
   `experiment.yml`. Its `run-name` and result artifact must use the opaque `job_id` input
   (`exp-<job_id>` and `result-<job_id>`). The broker supplies an attempt-unique value,
   passes only `params_json` (data), and never sends code.
3. Every submit is **budget‑gated, fail‑closed**: `github_actions_backend.budget_gate`
   reserves `ceil(timeout) × OS‑multiplier × cells` worst‑case minutes in the reservation
   ledger and refuses if it would exceed remaining included minutes / the per‑repo cap.
   Supported `runner_os` values are exactly `linux`, `ubuntu`, `windows`, and `macos`;
   unknown labels reject rather than falling back to the Linux multiplier. Correlation and
   download bind to the persisted unique dispatch id and exact run id. Unverifiable timing
   keeps the worst-case reservation active; verified actual usage remains locally accrued
   through the UTC billing month so a lagging provider usage API cannot reopen headroom.
4. Work that is unsuitable for or disproportionate on GHA is re-planned onto another
   adequate permitted lane or rejected; never expand GHA into a general compute pool.

## Setup & commands
The broker installs to each target's runtime root (single source of truth: `~/ai-agents-skills`)
and runs via `run_skill.sh`. Resolve the runtime root for the current agent, then `bootstrap`
once (generate config if absent, authenticate `gh`, check deps, run `doctor`):
```
# codex -> ~/.codex/runtime ; claude and others -> ~/.local/share/ai-agents-skills/runtime
runtime="${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}"; [ -d "$runtime" ] || runtime="$HOME/.codex/runtime"
run() { bash "$runtime/run_skill.sh" skills/modal-research-compute/run_modal_research_compute.sh "$@"; }

run bootstrap                  # one-time setup (config + gh auth + deps + doctor)
run doctor                     # routing_order + gha readiness
run submit job.json --wait     # {"gha_target":"…","policy":{"backend":"gha"},"payload":{"parameters":{…}}}
run fetch <job_id> --dest ./out
```
On the Claude target the documented `~/.claude/skills/_run.sh skills/modal-research-compute/…`
wrapper forwards to the same runtime. Implementation: `github_actions_backend.py`
(dispatch/correlate/wait/exact-run fetch + budget), `budget_ledger.py` (reservations and
current-cycle accrued usage), `planner.py`
(`routing_order` + override), `cli.py` (`bootstrap`). Full design:
`coding-system-rebuild/docs/github-actions-experiment-runner-plan.md`.
