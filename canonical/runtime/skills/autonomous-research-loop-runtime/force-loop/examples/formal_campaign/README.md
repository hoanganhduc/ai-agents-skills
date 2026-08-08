# Example: formal campaign overlay

Use after `force-loop bootstrap --profile formal`.

1. Edit `goal_priority.json` campaign registry for your paper artifact.
2. Set `notify.json` research_title / job_slug.
3. Set panel providers in `panel.json` or standing_orders.panel.
4. `force-loop start --loop … --root … --provider <primary> --policy-file <abs_path>`
   (`--policy-file` may be replaced by `AAS_FORCE_LOOP_POLICY_FILE`; the same
   host policy must have been passed to `bootstrap`).
