# Scripted force-loop (discovery)

**Primary install path:** runtime pack under

`skills/autonomous-research-loop-runtime/force-loop/`

This markdown file is **discovery only**. The multi-file kit installs via
`manifest/runtime.yaml` runtime-files with the ARL runtime skill (all install
targets; Linux / macOS / Windows / WSL).

## Default operator entry

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/autonomous-research-loop-runtime/force-loop/run_force_loop.sh \
  bootstrap --loop "$LOOP" --root "$ROOT" --profile formal --goal "…"
```

Windows: `run_force_loop.ps1` via `run_skill.ps1`.

## Defaults

- Goal Focus **enforce**
- goal_priority **hard**
- **notify** auto/on
- Foreground start on all OS

See the pack `README.md` and `OPERATOR_RUNBOOK.md`.

## Not this kit

| Term | Meaning |
|------|---------|
| `formal_policy=force` | Host formal hygiene tick — different |
| TikZ `force_loop` | Figure repair credits — different |
| Thin formal sample | `sample-arl-headless-driver-with-formal` — env layer only |
