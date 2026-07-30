# Goal priority (`goal_priority.v1` + soft v2 fields)

Optional loop-local discipline so each primary path advances `loop_state.goal`
and `success_criteria`, instead of unbounded local residual sampling.

**Does not change stop conditions.** See `autonomous-loop-enforcement.md`.
**Never** writes `loop_state.status`. **Never** fail-closes `append-iteration`
for vocabulary (hard mode may warn/coerce only).

This file lives under **`canonical/templates/`** (not the policy skill directory)
so OpenClaw can still install the ARL policy `SKILL.md`.

## Enable

Active when the merged config has `"enabled": true`, or when a
config object exists and `AAS_AUTOLOOP_GOAL_PRIORITY=on` forces enable. Set
`"enabled": false` or `AAS_AUTOLOOP_GOAL_PRIORITY=off` to opt out.

This is the legacy v1 compatibility contract. Its executable defaults are
`"enabled": false` and `"discipline_mode": "soft"`. New loops should use Goal
Focus v2 in `enforce` mode; see the `goal-focus` template.

- File: `{loop_dir}/goal_priority.json`
- Or: `loop_state.standing_orders.goal_priority`
- Env: `AAS_AUTOLOOP_GOAL_PRIORITY=on|off|1|0|true|false|yes|no`

Merge order: defaults → file → standing_orders (standing wins) → env (enabled only).

## Discipline modes

| Mode | Behavior |
|------|----------|
| `soft` (default) | v1 soft text + optional fields; no advance-deprecation warn |
| `advise` | + host warnings for bare `advance`; host local streak; REPLAN text |
| `hard` | advise + **rewrites** `next_preferred_path` and recovery **Next safe action** when REPLAN_REQUIRED (closed residual targeted, or local streak at cap). **Must not** refuse append or write `loop_state.status` |

Set `"discipline_mode": "soft"|"advise"|"hard"` in `goal_priority.json`.
Defaults are opt-in: `"enabled": false`, `"discipline_mode": "soft"`.

## Soft ledger fields

`append-iteration` optional flags:

- `--goal-contribution` (recommended vocabulary below)
- `--goal-contribution-detail`
- `--campaign-id`
- `--residual-id`
- `--scope-lock` (`encoding_only` | `goal_sc` | `manuscript` | `mixed`)
- `--local-without-goal-delta`
- `--local-without-goal-delta-tag`

## Recommended `goal_contribution` vocabulary

- `eliminate` — kill a candidate / no-go
- `construct` — new witness / lock / gadget
- `scope_lift` — strictly larger class closed
- `bridge` / `separate` — encoding ↔ goal membership
- `verify_trust` — dual-engine / independent audit
- `replan` — campaign/path change
- `formalize` — Lean/formal gate progress
- `operational` — infra only
- `advance` — allowed; discouraged as sole label in advise+

## Residual inventory (optional)

File: `{loop_dir}/residual_inventory.json`

```json
{
  "schema_version": "residual_inventory.v1",
  "host_signal_epoch_iteration": 248,
  "leaves": [
    {
      "id": "k2_lr",
      "campaign_id": "A2",
      "status": "open",
      "scope_lock": "encoding_only",
      "max_iterations_before_replan": null,
      "recovery_aliases": ["k2_lr"]
    }
  ]
}
```

- `host_signal_epoch_iteration`: rows before this iteration are not host-counted.
- Open leaves are listed in the drive/panel prompt when present.
- The merged machine campaign order uses defaults → `goal_priority.json` →
  `loop_state.standing_orders.goal_priority`; standing orders win. Markdown
  (OPEN_QUESTION, APPROACH_REGISTRY) remains advisory.

## Scope: encoding vs goal

After an encoding/GOAL separation, residual work with
`scope_lock: encoding_only` is **campaign** progress, not full goal resolution.

## Stop safety

REPLAN_REQUIRED text must never authorize `--decision stop|blocked`. The
headless driver owns stop conditions. Goal priority must not write
`loop_state.status`.

## Activation boundary (streak)

Streak counting starts at the first ledger record that sets any of
`goal_contribution`, `campaign_id`, or `local_without_goal_delta`. Hitting the
cap injects `REPLAN_REQUIRED` text; it does **not** stop the loop.

## Soft vs strict

v1/v2 soft injects prompt/panel text and validate **warnings**. It does not stop
the loop or hard-fail append.

## Example

See `goal-priority.example.json` next to this file (or `init --goal-priority-template`).
