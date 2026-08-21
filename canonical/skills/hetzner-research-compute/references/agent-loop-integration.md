# Agent-loop integration (Hetzner)

Operational contract for autonomous research loops and similar multi-iteration
agents. Complements `hetzner-offload.md` (driver/budget/teardown) and the
umbrella `compute-offload-routing.md`.

This document generalizes patterns observed in successful host-local loops
(export token in the drive supervisor, portable bundle, lane `preflight` then
`oneshot`, optional dual-lane with Kaggle on **disjoint** shards, host-verify
fetched results) and in failures that stopped at broker `run plan` rejection
without completing a token-aware lane recheck.

## Environment: token is process-local

The Hetzner driver reads **`HCLOUD_TOKEN` from the environment only**. The
managed skill wrapper now resolves that environment from the protected
`AAS_COMPUTE_SECRETS_FILE` launcher pointer when configured; the driver itself
still never parses arbitrary secret files.

- Lifecycle verbs (`up`, `push`, `run`, `wait`, `fetch`, `down`, `oneshot`) require
  the token and `--confirm`.
- `doctor` and `preflight` may run **without** a token (see skill operational
  notes). Therefore a preflight `available: false` / `api_unreachable` recorded
  without proving `HCLOUD_TOKEN` was set in that process is a **diagnostic
  signal**, not a conclusive “Hetzner is down” proof.
- Never print the token, pass it on argv, log it, write an `hcloud` context file,
  or place it on a rented server.

**How to load (portable rule):** set `AAS_COMPUTE_SECRETS_FILE` in the launcher
environment to the restored, absolute private compute authority and invoke the
managed runtime wrapper. Its strict loader accepts only `HCLOUD_TOKEN`,
`HCLOUD_SSH_KEYS`, `KAGGLE_API_TOKEN`, and `KAGGLE_CONFIG_DIR`, rejects linked,
public, malformed, or oversized files without printing values, and projects
only `HCLOUD_TOKEN` and `HCLOUD_SSH_KEYS` into this lane. It removes Kaggle
values and the pointer before launch. Direct ambient export remains supported,
but never put the pointer or token assignment in an agent-writable loop env file.

Drive/supervisor processes that spawn per-iteration agents should export the
token **before** spawn so children inherit it.

## Broker plan vs lane execution

| Step | Owner | Purpose |
|------|--------|---------|
| `run plan` / broker routing | research_compute broker | Choose among **permitted** lanes; adequacy + budget |
| `preflight --job DIR` | **this skill** | Free plan: type, EUR estimate, `available` / `budget_verdict`, and exact `required_bundle_sha256` |
| `oneshot --job DIR --bundle-sha256 HEX --confirm` | **this skill** | Approved immutable bundle: `up → push → run → wait → fetch → down` with teardown on every exit |

`run plan` does **not** provision a server. After a plan selects Hetzner (or the
loop allowlists Hetzner and the job is adequate), agents must call the lane skill.

Prefer:

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
het() { bash "$launcher" skills/hetzner-research-compute/run_hetzner_research_compute.sh "$@"; }

# free: inspect the JSON and approve its exact required_bundle_sha256
het preflight --job /path/to/bundle --json
bundle_sha256='<approved required_bundle_sha256>'
# paid lifecycle with guaranteed teardown for that exact immutable snapshot
het oneshot --job /path/to/bundle --bundle-sha256 "$bundle_sha256" --confirm
# if an iteration crashed mid-lifecycle
het down --orphans --confirm
```

## Preflight field semantics

A single JSON preflight may report combinations that look contradictory if only
`ok` is read. Typical fields:

| Field | Meaning |
|-------|---------|
| `ok` | Preflight command completed / structure valid |
| `adequate` | Resources fit the job estimate |
| `available` | Lane can accept work **now** (credentials, API, stock, etc.) |
| `within_auto_approve` | Worst-case EUR within auto-approve envelope |
| `budget_verdict` | e.g. `auto_approve` / `blocked` |
| `required_bundle_sha256` | Exact full SHA-256 that must be approved and supplied to lifecycle verbs |
| `reason` | Machine-readable explanation when not available |
| `provisioned` | Whether a server was created (false for preflight-only) |

**Rule:** `ok: true` is **not** availability. Require `available` (and a
non-blocked budget verdict for live runs) before `oneshot`.

## Allowlist exhaustion → diagnostic recheck

When the user or loop sets `policy.backends` to a strict list (e.g.
`["kaggle","hetzner"]`) and the broker reports `backends_allowlist_exhausted`:

1. Do **not** silently expand the allowlist or substitute unlisted local heavy compute.
2. Do **not** only re-plan and re-document the same blocker for many iterations
   without a fresh same-bundle lane check.
3. From a process with credentials loaded, re-run **lane** `preflight` on the
   **same** job bundle and record `adequate` / `available` / `budget_verdict` /
   `reason` / `required_bundle_sha256`.
4. If the lane is now available, re-enter normal routing/dispatch (plan if
   required, then `oneshot` with that exact approved digest / Kaggle `run`). The
   recheck does not itself bypass routing policy, and a changed bundle requires a
   new preflight and approval.
5. Only after a token-aware recheck still fails should a multi-iteration
   infrastructure blocker be banked.

## Dual-lane parallel (optional, when shardable)

When both Hetzner and Kaggle are permitted and the job has many independent units:

- Split into **disjoint** partitions (e.g. even → Hetzner, odd → Kaggle).
- Use **separate bundle directories** (or parameters) so units are not double-run.
- Launch both lanes in the same iteration when wall-time matters.
- Merge and **host-verify** returned unit files / certificates independently of
  the producer code when the loop’s evidence gates require it.

Hetzner is a good fit for a wide-core, must-finish partition under the EUR caps.
Kaggle free CPU fits long chunkable tails (session and concurrency limits apply;
see the Kaggle skill).

## Portable job bundle (reminder)

Backend-agnostic shape: `manifest.json`, `worker`, `run.sh`, `merge`, writable
`out/`. Prefer writing a `RESULTS.json` (or the skill’s expected success marker)
so `wait` / `fetch` complete. Selftest the worker briefly before remote dispatch.

## What not to promote into canonical text

- Project-specific mathematical residuals, theorem numbers, or domain thresholds
- A single secrets file path as the only valid credential store
- Claims that a particular historical `URLError` was **exclusively** caused by a
  missing token unless that preflight record also documents token presence

## Related

- `references/hetzner-offload.md` — driver contract, budget, reaper
- `references/reaper-deployment.md` — detached billing stopper
- ARL injected rules: `autonomous-research-loop-runtime` `compute_policy.py`
