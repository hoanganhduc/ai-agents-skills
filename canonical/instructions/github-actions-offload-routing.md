# GitHub Actions offload routing (research_compute)

Sibling to `modal-offload-routing.md`. The `research_compute` broker can route a compute job
to **GitHub Actions** in addition to local and Modal.

> **GitHub Actions ToS compliance.** GitHub's Additional Product Terms restrict Actions on
> GitHub‑hosted runners to *"the production, testing, deployment, or publication of the
> software project associated with the repository,"* and forbid *"disproportionate"* /
> serverless burden; misuse can disable the repo or account. Here, Actions compute runs **only
> inside a private research repo**, executes **that repo's own committed experiment code**
> (params are DATA, never executed), as that project's own validation, **budget‑gated** and
> kept **proportionate**, and is the **last** automatic backend (after local and Modal). It is
> never a general compute pool. Source: GitHub *Terms for Additional Products and Features →
> GitHub Actions*.

## Rules
1. Automatic order `local → Modal → gha`; `policy.backend` overrides; `gha` is off until
   `[gha].enabled = true` and `gha doctor` passes (classic PAT with billing‑read; GitHub
   budget set > $0 with stop‑usage; target repos private).
2. The target must be a **private** repo registered in `[gha.repos.<key>]` with an in‑repo
   `experiment.yml`. The broker passes only `params_json` (data); it never sends code.
3. Every submit is **budget‑gated, fail‑closed**: `github_actions_backend.budget_gate`
   reserves `ceil(timeout) × OS‑multiplier × cells` worst‑case minutes in the reservation
   ledger and refuses if it would exceed remaining included minutes / the per‑repo cap.
4. Heavier‑than‑GHA work (GPU, > 6 h, ≫ included minutes) goes to Modal/local — never expand
   GHA into a general compute pool.

## Commands
```
… research_compute doctor                 # shows routing_order + gha readiness
… research_compute submit job.json        # job: {"gha_target":"…","policy":{"backend":"gha"},"payload":{"parameters":{…}}}
… research_compute wait <job_id> ; … fetch <job_id>
```
Implementation: `github_actions_backend.py` (dispatch/correlate/wait/fetch + budget),
`budget_ledger.py` (reservations), `planner.py` (`routing_order` + override). Full design:
`coding-system-rebuild/docs/github-actions-experiment-runner-plan.md`.
