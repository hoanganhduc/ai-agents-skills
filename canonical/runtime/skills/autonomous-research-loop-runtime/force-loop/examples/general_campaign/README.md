# Example: general (non-formal) campaign

```bash
POLICY=/abs/path/host-policy.env    # or export AAS_FORCE_LOOP_POLICY_FILE
force-loop bootstrap --loop "$LOOP" --root "$ROOT" --profile general \
  --goal "…" --success-criteria "…" --policy-file "$POLICY"
force-loop start --loop "$LOOP" --root "$ROOT" --policy-file "$POLICY"
```

Formal pins are left off; enforce / hard / notify remain on.
