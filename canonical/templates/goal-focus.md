# Goal Focus v2

Goal Focus v2 is the enforceable strategy layer for autonomous research loops.
It separates the stable research contract from the mutable plan, keeps a
machine-readable portfolio of approaches, and records every direction decision.

## Authoritative files

| File | Authority |
|---|---|
| `goal_contract.json` | Goal, success criteria, scope, and evidence-backed obligation DAG |
| `approach_registry.json` | Campaigns, approaches, forecasts, blockers, dependencies, and reopen conditions |
| `current_plan.json` | The one active campaign, approach, objective, and next action |
| `direction_decisions.jsonl` | Append-only migration, selection, revision, and outcome audit |

`loop_state.goal`, `loop_state.success_criteria`,
`loop_state.next_preferred_path`, and the managed block in `recovery.md` are
compatibility views. They never override the four files above.

## Modes

- `off` — Goal Focus v2 is not active.
- `monitor` — validate and report inconsistencies without blocking dispatch.
- `enforce` — require a coherent, reviewed, unexpired plan before dispatch.

New v2 loops use `enforce`. Unmigrated `goal_priority.v1` loops retain their
legacy behavior until `goal-focus migrate --apply` succeeds.

Goal governance and provider-process trust are separate. The provider transport
defaults to `strict-isolated`, which denies real enforce-mode providers while
hostile-process containment is unavailable. An operator may explicitly set
`AAS_AUTOLOOP_PROVIDER_TRANSPORT=trusted-local` for attested local CLIs in a
dedicated project. That profile retains all Goal-Focus authority, staging,
review, and banking gates and adds mandatory cgroup/POSIX resource limits,
timeouts, output bounds, and descendant cleanup, but does not claim credential,
filesystem, or network isolation from the selected CLI.

## Progress contract

Every finalized iteration records two independent deltas:

- `campaign_delta`: `none | incremental | substantial | closed`
- `global_delta`: `none | reduced | satisfied`

`global_delta` requires a named obligation transition and evidence. Work under
`scope_lock: encoding_only` is campaign progress unless it discharges a bridge
or terminal obligation.

## Selection contract

The runtime promises the best-supported registered direction under the current
evidence, budget, and policy. It does not claim to know the objectively best
route through an open problem.

At a strategy review the runtime filters ineligible routes, validates structured
panel advice, evaluates lower and upper utility bounds, and commits a single
route only after different-family doubt review. When no route robustly dominates,
it chooses a bounded discriminating experiment from at most three diverse live
approaches. The review and commit bind the same complete goal, registry, and
plan objects using semantic fingerprints plus exact source hashes.

Default utility weights are:

```text
5 goal resolution + 3 information gain + 2 reusable value + 1 diversity
- 2 execution cost - 2 verification cost - 4 bridge debt
- 3 dependency risk - 1 correlated redundancy
```

## Safety invariants

- A campaign/path/recovery/ledger mismatch is an enforce-mode dispatch error.
- Invalid, omitted, or unavailable trusted-local resource controls deny before
  provider spawn; strict-isolated never falls back to trusted-local.
- A blocked route needs a recorded new mechanism and fresh review to reopen.
- Plan, goal, registry, and candidate content use hash/revision compare-and-swap.
- State changes use a recoverable write-ahead transaction, no-follow directory
  traversal, and atomic replace.
- An enforce-mode primary can stage only through its exact live host dispatch.
- A primary result is staged, independently reviewed by exact candidate hash,
  then finalized.
- Accepted obligation transitions bind the exact target and matching staged
  evidence actually inspected by the reviewer; host-derived early success still
  needs a valid proof artifact.
- Supported material claims also bind matching inspected evidence. Evidence ids
  are opaque safe single-component names under the host-created
  `.goal_focus/evidence/<candidate-id>/` directory; hidden/path-like/sensitive
  names are rejected. Complete bounded private UTF-8 content, size, and digest
  are host-snapshotted into the immutable candidate.
- Completed obligations count only with evidence and completed dependencies;
  success-criterion ids are unique.
- Mode changes require a complete decision row bound to the exact plan
  postimage; partial or forged opt-out state fails closed.
- Rejected work consumes its real budget but banks no research claim.
- Result-review unavailability leaves the candidate pending and launches no new
  research iteration.
- The supported enforce completion path is one host-consumed bounded submission.
  Any candidate that exists after timeout, capture failure, output rejection,
  cleanup failure, or another failed host completion gate is atomically moved
  behind `candidate_quarantine.json`; it cannot be reviewed, banked, retried, or
  released without an exact fingerprint-bound operator action. Strict-isolated
  additionally enforces a read-only project/control plane. Trusted-local relies
  on an operator-approved cooperative CLI and does not claim filesystem
  containment.
- Provider identity is a trusted operator assertion over the exact entrypoint,
  dependency closure, upstream family, and model, all revalidated and
  launch-bound immediately before spawn; it is not remote-service
  cryptographic proof.
- External panel and notify egress require their explicit consent variables.
  PII in a panel brief additionally requires approval of the exact prompt
  SHA-256; notification output redacts common explicit PII forms.

## Commands

```bash
run_autonomous_research_loop.sh goal-focus migrate --dir research/run --dry-run
run_autonomous_research_loop.sh goal-focus migrate --dir research/run --apply
run_autonomous_research_loop.sh goal-focus status --dir research/run
run_autonomous_research_loop.sh goal-focus validate --dir research/run
run_autonomous_research_loop.sh goal-focus replan --dir research/run --trigger plateau --dry-run
run_autonomous_research_loop.sh goal-focus reconcile --dir research/run
run_autonomous_research_loop.sh goal-focus recover-dispatch --dir research/run
run_autonomous_research_loop.sh goal-focus recover-quarantine --dir research/run
run_autonomous_research_loop.sh goal-focus recover-quarantine --dir research/run --release --candidate-fingerprint sha256:<exact-digest>
```

Migration preserves all legacy inputs and creates a UUID-suffixed backup plus a
durable manifest containing source digests, transaction binding, and restore
instructions. Ambiguous dynamic campaign signals are never guessed;
the migrated plan remains `needs_replan` until reviewed or explicitly resolved.
Apply refuses a live owning driver and compare-and-swaps the exact legacy source
snapshot used for both the proposal and backup, so concurrent mutation produces
no partial v2 state.
Applied manual replans also require `--primary-provider <known-provider>`.
If status reports an in-flight dispatch after a host interruption, first confirm
the original worker is gone; only then cancel with the exact reported dispatch
id and restart the driver.
If status reports a candidate quarantine, inspect the preserved candidate and
evidence first. Release only with the exact fingerprint printed by status; the
runtime archives the quarantine and leaves budget/ledger claims unchanged.
