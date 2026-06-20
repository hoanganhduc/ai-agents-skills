# Spec: Force-Managed Autonomous Loop Enforcement

## Objective

- Make autonomous-loop stop-condition enforcement a built-in, reusable, and
  automatically-activated behavior, instead of a hand-crafted per-project hook.
- Encode one authoritative stop policy: user requirements override everything;
  otherwise stop only on loops-reached, credit-out, goal-resolved, or a user
  stop message. Nothing else is a valid stop.
- Provide a single derived arbiter (`done`) that both an interactive Stop hook
  and a headless driver consult, so the same policy governs both paths.
- Guarantee the enforcer is fail-open and always escapable, so a broken or stuck
  enforcer can never trap a session.
- Keep this implementation repo-only. Do not write to live agent homes during
  this phase.

## Assumptions

1. `~/ai-agents-skills` remains the source of truth for reusable skills,
   runtime, instruction docs, generated docs, manifests, templates, and tests.
2. Live agent homes such as `~/.claude`, `~/.codex`, and `~/.config/opencode`
   are installation targets only and are not modified in this phase.
3. The runtime helper already manages `loop_state.json`, `budget.json`,
   `iterations.jsonl`, and `recovery.md`, and already enforces `max_iterations`,
   terminal statuses, and machine-checkable proof artifacts on append.
4. Targets differ in headless and hook capability; the support matrix must state
   each target honestly rather than claim uniform built-in enforcement.
5. The installer has no JSON-merge-into-settings surface yet; merging a managed
   Stop hook into a populated `settings.json` is net-new work, not a reuse of the
   Markdown managed-block primitive.

## Scope

- Runtime arm/disarm/active/done commands plus `stop_conditions`,
  `success_check`, spend/wall enforcement, liveness, and a read-only `done`
  arbiter with strict precedence.
- A canonical instruction rule that installs the stop policy as agent behavior.
- A JSON-merge installer surface that idempotently upserts one tagged managed
  Stop-hook entry and round-trips on uninstall.
- A fail-open Stop-hook template and a generic headless driver with launchers.
- Per-target artifact dirs, manifest artifact kind, generated docs, and target
  READMEs reflecting the honest support matrix.
- Lifecycle artifacts in `SPEC.md`, `tasks/plan.md`, and `tasks/todo.md`.

## Out Of Scope

- Applying changes to live agent homes in this phase.
- Proving provider-native Stop-hook reload behavior on real systems.
- Reimplementing the existing iteration/budget/proof-artifact machinery.
- Auditing secrets, local libraries, sessions, logs, caches, or databases.

## Acceptance Criteria

- [x] Runtime exposes `arm`, `disarm`, `active`, `done`, and `hook-check`, with
      `done` read-only/derived and strict precedence (user stop > credit/spend >
      wall > count > success).
- [x] Hook path is fail-open on every error, missing-state, and re-entrant case,
      and honors `AUTOLOOP_DISABLE`, registry removal, `STOP_REQUESTED`, and
      `PAUSE`.
- [x] Enforcement tests cover arm/block, kill switch, unrelated root, corrupt
      state fail-open, user-stop override, and pause.
- [x] A canonical instruction rule encodes the stop policy as installed behavior.
- [ ] Shipped plateau/blocker/evidence-gap stops are subordinated under the
      policy so they do not fire by default, with a negative test.
- [ ] A `settings-json-merge` installer surface upserts one tagged managed Stop
      hook and round-trips on a populated `settings.json`.
- [ ] Hook-capable targets gain settings/hook artifact dirs, a manifest JSON-hook
      artifact kind, and renderer support.
- [ ] A fail-open Stop-hook template and a generic driver with `.sh/.ps1/.bat`
      launchers exist, with an `AUTOLOOP_DRIVER` exemption and least-privilege
      headless flags.
- [ ] Docs and all target READMEs state the honest per-target support matrix;
      Sphinx is rebuilt.

## Verification

- Enforcement unit tests: `python3 -m pytest -q tests/test_autonomous_research_loop.py`
- Installer manifest/render tests: `python3 -m unittest tests.test_installer`
- Round-trip: apply + uninstall the managed hook on a populated fake
  `settings.json`; `plan` and `audit-system` show the exact diff.
- Generated docs: `make docs-check`.
- Repo-only dry run: installer `plan` and `audit-system`; show the `plan` diff
  before any `apply`.
