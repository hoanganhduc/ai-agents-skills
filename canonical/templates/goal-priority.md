# Goal priority (`goal_priority.v1`)

Optional loop-local discipline so each primary path advances `loop_state.goal`
and `success_criteria`, instead of unbounded local residual sampling.

**Does not change stop conditions.** See `autonomous-loop-enforcement.md`.

This file lives under **`canonical/templates/`** (not the policy skill directory)
so OpenClaw can still install the ARL policy `SKILL.md`. After install, look for
workflow templates or the source repo copy.

## Enable

Active only after merge when `"enabled"` is **JSON boolean `true`**, or when a
config object exists and `AAS_AUTOLOOP_GOAL_PRIORITY=on` forces enable.

- File: `{loop_dir}/goal_priority.json`
- Or: `loop_state.standing_orders.goal_priority`
- Env: `AAS_AUTOLOOP_GOAL_PRIORITY=on|off|1|0|true|false|yes|no`  
  (`on` without config is inert + warn; `on` with config activates even if
  `enabled` was missing or false)

Merge order: defaults → file → standing_orders (standing wins; lists/maps
replaced wholesale) → env (enabled only).

## Soft ledger fields

`append-iteration` optional flags:

- `--goal-contribution` (open string; see vocabulary below)
- `--campaign-id`
- `--local-without-goal-delta`
- `--local-without-goal-delta-tag`

When active and `require_goal_contribution_in_ledger` is true (default), omission
of `goal_contribution` counts toward the local-without-goal-delta streak
(warn-only). Set it to `false` for self-report mode (only explicit local flags
count).

## Activation boundary (streak)

Streak counting starts at the first ledger record that sets any of
`goal_contribution`, `campaign_id`, or `local_without_goal_delta`. All later
records count. Hitting the cap injects `REPLAN_REQUIRED` text; it does **not**
stop the loop.

## Recommended `goal_contribution` vocabulary

Open strings. Suggested:

- `advance` — progress toward the exact claim
- `eliminate` — kill a candidate approach / hypothesis
- `verify` — trust / independence / dual-check
- `replan` — change campaign after closed stratum or streak
- `operational` — infra only (panel/broker); not sole long-run primary
- Research examples: `bridge`, `hardness`, `algorithm`, `counterexample`, `trust_gate`

## Recommended `local_without_goal_delta` tags

Advisory vocabulary (unknown tags warn only):

- `finite_sample_only`
- `bookkeeping`
- `special_case_only`
- `uncertified_counterexample`
- `elegant_reduction`
- `local_refinement_only`
- `closed_campaign_sample`

## Soft vs strict

v1 injects prompt/panel text and validate **warnings**. It does not stop the
loop or hard-fail append. Strict mode is out of scope for this delivery.

## Example

See `goal-priority.example.json` next to this file (or `init --goal-priority-template`).
