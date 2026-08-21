---
name: hetzner-research-compute
description: Use when a research or engineering task needs automatic heavy-compute routing to a disposable Hetzner Cloud CPU or high-memory server through the local broker, with agent-driven provision, run, collect, and destroy under hard cost caps.
metadata:
  short-description: Route heavy CPU compute to a disposable Hetzner Cloud server through the local broker
---

# Hetzner Research Compute


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" "skills/hetzner-research-compute/run_hetzner_research_compute.ps1" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.
Native Windows supports planning and recovery/teardown, but live `up` and `oneshot` fail closed
in this release because no durable Task Scheduler reaper has been natively attested. Use WSL or
Linux for paid provisioning; do not bypass this gate by invoking the Python module directly.

Use this skill when the task is about:

- exhaustive search
- object enumeration
- counterexample hunting
- large parameter sweeps
- long-running CPU or high-memory batch work that a throttled local run cannot finish in time

This skill is the Hetzner Cloud lane of the local `research_compute` broker. It rents a
disposable server, runs a portable job bundle on it at full cores, fetches the results,
and destroys the server. It is peer to the Kaggle, Modal, and GitHub Actions lanes.

## When to prefer this skill

- the local machine is CPU, memory, or disk constrained for the requested workload, or must stay responsive so a local run would trip the self-preservation veto
- the workload is CPU-heavy or high-memory (GPU work is out of scope in v1, so the router skips Hetzner and continues to the next GPU-capable lane)
- a dedicated, disposable, full-core box is a better fit than a throttled local run
- the recommended routing order is `local > Kaggle > Modal > Hetzner > GitHub Actions`, so Hetzner follows the free Kaggle lane and Modal for non-GPU work when a token and budget are available; a valid custom order keeps local first and may reorder or omit unique remote lanes

## Unified routing

The umbrella doc `compute-offload-routing.md` explains backend selection across the five
lanes (local, Kaggle, Modal, Hetzner, GitHub Actions), the keep-local rules, and the local
self-preservation veto. The per-lane contract for Hetzner — driver verbs, guardrails, the
lifecycle invariant, budget, and teardown — is in `references/hetzner-offload.md`. The
broker router is the decision boundary: `plan` and `doctor` choose the backend; this skill
provisions only after that choice lands on Hetzner.

## Core workflow

Work through `compute-offload-sizing-gate` first. This lane bills per server
hour, so an under-characterized job pays for boot and teardown before failing;
measure the workload and match it to the server type's declared vCPU/RAM before
`up`.

1. If local resources matter, run `get-available-resources` and let the broker apply the self-preservation veto.
2. Build a portable job bundle (`manifest.json`, `worker`, executable `run.sh`, `merge`, writable `out/`) as an immediate child of the absolute operator-approved `[hetzner].bundle_root`. The same bundle runs unchanged on any lane.
3. Run `preflight` (free, no server) to get the plan and exact full `required_bundle_sha256`; review that digest with the cost and placement.
4. If the plan stays within policy, pass that digest unchanged through `--bundle-sha256` to `up` and `push`, or to `oneshot`. Any changed bundle is rejected before create/upload.
5. Use `wait` and `fetch` to poll and copy results back to local storage, verifying they are well formed.
6. `down` DESTROYS the server. A powered-off server still bills; only DELETE stops it, so teardown must run on every terminal path.

## Runtime commands

Linux (resolve the launcher for the current agent — a root-owned component generation when one is installed, otherwise the per-user runtime — then call it):

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
run() { bash "$launcher" skills/hetzner-research-compute/run_hetzner_research_compute.sh "$@"; }
```

```bash
run bootstrap                          # one-time: check hcloud CLI + token, run doctor
run doctor                             # lane + token + hcloud CLI + configured caps (offline)
run preflight --job /path/to/jobdir --json     # record required_bundle_sha256 (no server)
bundle_sha256='<exact-64-hex-from-preflight>'
run up      --job /path/to/jobdir --bundle-sha256 "$bundle_sha256" --confirm
run push    <job_id> --job /path/to/jobdir --bundle-sha256 "$bundle_sha256" --confirm
run run     <job_id>                            # detached, full-core execution
run status  <job_id>
run wait    <job_id>
run fetch   <job_id> --dest /path/to/output
run down    <job_id> --confirm                  # DESTROY (the only thing that stops billing)
run down    <job_id> --confirm --allow-unfetched # ... even though the results were never fetched
run down    --orphans --confirm                 # kill-switch cleanup of stale/expired servers
run down    --all --dry-run                     # read-only inventory + exact target-bound phrase
run down    --all --confirm --confirm-project-wide 'DELETE-AAS-HETZNER project=... count=... digest=...'
run oneshot --job /path/to/jobdir --bundle-sha256 "$bundle_sha256" --confirm
```

Planning verbs (`bootstrap`, `doctor`, `preflight`) are free and never touch a server.
Lifecycle verbs (`up`, `push`, `run`, `status`, `wait`, `fetch`, `down`, `oneshot`) may
hold a paid server and require `HCLOUD_TOKEN` plus an explicit `--confirm`. Use `--dry-run`
on `up`, `down`, and `oneshot` to inspect the plan with no provisioning or deletion.
`down --all --dry-run` intentionally performs a read-only inventory so it can bind the
confirmation phrase to the exact target count and digest.

On targets that install a local skill wrapper, that wrapper should forward to the same
runtime command target.

```bash
skills/hetzner-research-compute/run_hetzner_research_compute.sh doctor
```

Windows:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.ps1" `
  "skills/hetzner-research-compute/run_hetzner_research_compute.ps1" `
  doctor
```

Manual native-Windows reaper/recovery commands use the same strict compute authority loader:

```powershell
& "$runtime\run_skill.ps1" `
  "skills/hetzner-research-compute/run_hetzner_reaper.ps1" `
  reap --dry-run
```

## Agent-loop integration

For autonomous research loops and multi-iteration agents (token inheritance,
broker plan vs `oneshot`, preflight field semantics, allowlist-exhaust
diagnostics, dual-lane with Kaggle), see
`references/agent-loop-integration.md`. That reference is the portable
operational contract; this page remains the skill entrypoint and lifecycle
reference.

## Operational notes

- The broker is the decision boundary for **routing**. Provision on Hetzner only when the router chose this lane (or the loop’s allowlist permits it and plan/preflight agree). Execution is this skill’s `preflight` / `oneshot` (or up/push/run/wait/fetch/down).
- `HCLOUD_TOKEN` is read from the environment at runtime (env-injection, never argv, never logged). The managed POSIX and Windows wrappers consume the restored `AAS_COMPUTE_SECRETS_FILE` launcher pointer through the strict loader, which accepts exactly `HCLOUD_TOKEN`, `HCLOUD_SSH_KEYS`, `KAGGLE_API_TOKEN`, and `KAGGLE_CONFIG_DIR` in the shared compute authority and rejects unsafe files without printing values. The Hetzner driver and reaper receive only the two `HCLOUD_*` values; Kaggle values and the pointer are removed before launch. Live creates require both Hetzner fields. Do not write an `hcloud context` file, put the pointer in agent-writable config, or place the token on a server. Use a dedicated, least-privilege Hetzner project with a project server-limit. Drive supervisors that spawn child agents should load the same protected authority before spawn so eligible children inherit only their policy-allowed compute lane values.
- `[hetzner].bundle_root` is a trusted operator boundary, not an agent-selected convenience path. `up`, `push`, and `oneshot` accept only an immediate child bundle beneath that absolute link-free root, reject links, hard links, special files, unsafe names, authority-like filenames, protected-secret inode overlap, unsafe ownership/modes, oversized/deep trees, and manifest/job mismatches, then hold one bounded private immutable snapshot for the complete operation. `preflight` emits its deterministic full SHA-256; create and upload require the exact approved digest. Servers bind both 32-hex halves in `bundle-sha256-high` and `bundle-sha256-low`, and `push` refetches the exact numeric provider ID and validates both labels immediately before rsync. Custom `--user-data` is unsupported: every create must retain the managed billing dead-man switch. Native Windows supports `preflight` and recovery/teardown only; bundle upload and paid provisioning require WSL/Linux.
- Every new server carries an `install-scope` label derived from `install_id` and the resolved runtime-workspace identity, plus a `project-scope` label derived from the exact configured `[hetzner].project_identity`; raw identities are never placed in labels. Normal job operations require the exact install scope. Reaping and broad deletion span install scopes but require the exact project scope, so a token/config mismatch cannot silently widen deletion to unrelated AAS servers. Orphan-ledger decisions and reservation reconciliation remain limited to the current install scope. Use a dedicated Hetzner project and a stable operator-controlled project identity; missing or invalid identity/config fails closed.
- Live provisioning requires `HCLOUD_SSH_KEYS` as an explicit comma-separated allowlist of safe login-key names. `up` queries the current project, proves every selected name exists, and attaches only that subset; it never attaches every project key or honors the legacy `HETZNER_SSH_KEYS` alias. Separately, each job gets a locally generated Ed25519 **server host identity** embedded in managed cloud-init. All SSH, SCP, and rsync operations require the exact per-job `known_hosts` pin with `StrictHostKeyChecking=yes`, disable global known-hosts and host-key updates, and never use TOFU, `accept-new`, or `/dev/null` as the user known-hosts file.
- Budget caps live under `[hetzner]` in `research-compute.toml`: `max_eur_per_job`, `max_eur_per_day`, `max_server_hours`, `max_concurrent_servers`, and `allowed_locations` / `allowed server types` allow-lists. The gate reserves the pessimistic worst case (`rate x ceil(max_server_hours) x count + IPv4`) before any create, so concurrent submits cannot collectively overspend.
- A `job_id` names the server, so it must be a valid hostname: letters, digits, dots and hyphens, no underscores. `preflight` reports an unnameable id as `invalid_job_id` and `up` refuses before the gate reserves anything. When a create fails anyway, the reservation is released only if no server carries the job-id label, so a machine that may be billing keeps its budget.
- Within the auto-approve envelope (worst case at or below `max_eur_per_job`, at or below `max_concurrent_servers`, allow-listed types) the agent may submit alone; a larger spend needs out-of-band human confirmation the agent cannot mint.
- Teardown must run on every terminal path (success, failure, timeout, boot-fail, push-fail, crash). Failure and timeout paths fetch checkpoints before destroy so work is resumable. Immediately before each delete, the driver/reaper refetches the exact numeric provider ID, re-establishes managed scope, and recomputes the current predicate with a fresh authoritative ledger read where orphan status matters. A detached reaper (systemd timer or cron, never a session child) is the durable billing-stopper on POSIX. After each successful scheduled pass, a root-controlled post-step publishes a short-lived, non-secret `0644` lease beneath a fully root-controlled parent chain. The driver binds that lease to project identity, install scope, scheduler identity, and configuration digest; a static environment assertion or agent-authored marker cannot enable live provisioning. Deployment is documented in `references/reaper-deployment.md`. The Windows reaper target is recovery-only, so native-Windows paid provisioning remains disabled. `oneshot` and `down --orphans` remain in-session controls, and every supported `up` attaches the cloud-init compute backstop.
- Billing-safety guardrails are on by default: a reconcile-before-create runaway-loop guard aborts `up` if project-scoped live servers would exceed `max_concurrent_servers`, and every provision/destroy/reap/kill attempts a redacted append-only audit record (`hetzner-audit.jsonl`). Audit failure is reported but cannot skip reservation reconciliation or later server deletions. Orphan cleanup runs only for the current install scope while an existing readable owner-private ledger is authoritative; missing, linked, broad-permission, changing, or corrupt ledger state disables the orphan reason while project-scoped TTL, powered-off, and stale-heartbeat safeguards continue. `down --all` and `hetzner_reaper kill` first emit an exact inventory-bound phrase from a read-only dry run; live deletion requires that unchanged phrase and a fresh protected reaper lease.
- Do not hand-roll the collection step. Use `fetch` or `oneshot`: they create the destination directory, verify `RESULTS.json` parses, and record the fetch in the audit log. `down <job_id>` then refuses to destroy a server whose results were never fetched, since deletion discards the only copy — override with `--allow-unfetched`. The refusal is scoped to job-id teardown: `--all`, `--orphans`, `--server-id`, and the reaper are never blocked, because stopping billing must always be possible.
- CPU-heavy or high-memory combinatorial workloads are the target. GPU work is out of scope in v1, so the router skips Hetzner and continues to the next GPU-capable lane.
- `doctor` and `preflight` work without a token or a server. `up`, `push`, `run`, `wait`, `fetch`, and `down` need the host to be Hetzner-ready (the `hcloud` CLI installed and `HCLOUD_TOKEN` set).
- One-time per machine, run `bootstrap`: it checks the `hcloud` CLI and token presence and reports `doctor`. It never provisions.

## Recommended templates

When this skill is involved, consider the same workflow templates as the other offload lanes
(install via the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `compute-offload-sizing-gate` -- Pre-dispatch worksheet: measure the workload, read the declared lane capacity, write the manifest in the correct dialect, assert the plan, and verify the realized allocation.
- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
- `engineering-delivery-loop-runbook` -- Bounded build-and-deliver loop runbook: single-path implementation with seen-to-fail proof, cross-agent diff verification, behavior-preserving cleanup, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
