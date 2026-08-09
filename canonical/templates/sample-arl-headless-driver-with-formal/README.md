# Sample: headless ARL + formal tools (thin env pack)

This directory is a **thin sample**, not a second ARL supervisor.

**Default force-loop path:** use the runtime pack
`autonomous-research-loop-runtime/force-loop/` (discovery:
`arl-scripted-force-loop`) for bootstrap/start with enforce, hard goal_priority,
and notify ON on all supported OSes. Keep this directory for formal-env
fragments only.

## Glossary (do not conflate)

| Term | Meaning |
|------|---------|
| **Headless / force-driven ARL** | Unattended `drive` (supervisor / LAUNCH.sh). |
| **`formal_policy=force`** | Bounded **host formal hygiene** tick after ok iterations. |
| **TikZ `force_loop`** | Unrelated figure-repair credits; pattern only. |

## What this pack provides

| File | Role |
|------|------|
| `formal_env.inc.sh` | Non-secret exports only (`AAS_AUTOLOOP_FORMAL_*`). |
| `formal_policy.example.json` | Schema example for `<loop>/formal/formal_policy.json`. |
| `apply_formal_settings.py` | Schema-validated JSON writer (not sed). |
| `LAUNCH_with_formal_env.sh` | Optional: source env, then exec **existing** supervisor. |
| `hermetic_benchmark_env.inc.sh` | Preset A: closed-book benchmark recipe (claude, non-attested drive lane only). |
| `production_formalization_env.inc.sh` | Preset B: production lane with curated MCP config (claude, non-attested drive lane only). |
| `curated_mcp.claude.example.json` | Preset B curated MCP config example (copy operator-owned, 0600, outside loop trees). |
| `README.md` | This file. |

Preset lane rules and the enforce/force-loop compatibility matrix live in
`autonomous-research-loop-runtime/force-loop/OPERATOR_RUNBOOK.md` ("Banked
launch presets (claude)"). Benchmark-set selection for closed-book runs:
`docs/lean-formalization-benchmarks.md` (repo root).

## Default sample policy

**`on`** — iteration prompt gets formal binding (F1–F7 positions when path is formal-track). Formal tools **assist** stable lemmas; they are **not** the default discovery primary.

Aggressive host hygiene:

```bash
export AAS_AUTOLOOP_FORMAL_POLICY=force
export AAS_AUTOLOOP_FORMAL_FORCE=1
# optional typecheck inside host tick (scan-only is default)
# export AAS_AUTOLOOP_FORMAL_TYPECHECK=1
```

## Wire into an existing supervisor

**Prefer trusted paths.** Source only an install-owned or operator-owned fragment
(not agent-writable loop trees). Prefer JSON + CLI over `source` under `$LOOP_DIR`:

```bash
# Safer: schema-validated policy file (agent may propose values; host writes)
python3 /path/to/sample-arl-headless-driver-with-formal/apply_formal_settings.py \
  --dir "$LOOP_DIR" --from-json formal_policy.example.json --policy on

# Env fragment: source from install/template path, NOT from agent-writable loop dir
FORMAL_ENV="${AAS_FORMAL_ENV_INC:-$HOME/.local/share/ai-agents-skills/templates/sample-arl-headless-driver-with-formal/formal_env.inc.sh}"
# shellcheck source=/dev/null
source "$FORMAL_ENV"

# Explicit boolean checks — do NOT use ${AAS_AUTOLOOP_FORMAL_FORCE:+flag}
# (bash treats FORCE=0 as "set", which would incorrectly pass the flag).
formal_flags=(--formal-policy "${AAS_AUTOLOOP_FORMAL_POLICY:-on}")
if [[ "${AAS_AUTOLOOP_FORMAL_FORCE:-0}" == "1" || "${AAS_AUTOLOOP_FORMAL_FORCE:-}" == "true" ]]; then
  formal_flags+=(--formal-force-after-iteration)
fi
if [[ "${AAS_AUTOLOOP_FORMAL_TYPECHECK:-0}" == "1" || "${AAS_AUTOLOOP_FORMAL_TYPECHECK:-}" == "true" ]]; then
  formal_flags+=(--formal-typecheck)
fi

bash "$RUNTIME" drive \
  --dir "$LOOP_DIR" \
  --root "$PROJECT_ROOT" \
  --provider "$PROVIDER" \
  "${formal_flags[@]}"
```

Prefer `$AAS_RUNTIME_ROOT` / installed runtime paths — not hardcoded clone paths.
If you keep a copy under `$LOOP_DIR/driver/`, treat it as **host-owned** (mode
not agent-writable, or re-copy from install each launch).

## Init with formal

```bash
… run_autonomous_research_loop.sh init \
  --dir research_loop \
  --goal "…" --success-criteria "…" \
  --formal-policy on \
  --formal-project formal/
```

## Secrets

- **Never** `source ~/.config/lean-explore/env` from samples.
- Operator pre-exports `LEANEXPLORE_API_KEY` if Explore is needed.
- Force tick redacts Bearer / api_key shapes in reports.

## Correct tool positions

```text
Each ARL iteration:
  P1 path-select (recovery / hard replan)
  P2 resources / compute policy
  P3 solve THAT path
     if formal-track: F1 intake → F2 Explore → F2' library reuse gate
        → F3 skeleton → F4a fill → F4b OpenGauss interactive only
        → F5 gate → F6 review → F7 accept → F7' intake proposal (user-gated)
  P4 panel / independent verify
  P6 ledger + recovery

After iteration_ok (only formal_policy=force + force flag):
  formal_force_tick → reports/advisory notes (non-terminal)
```

## Non-goals (enforced)

- No OpenGauss auto-spawn from host tick.
- No `claim_support=supported` from force tick.
- No loop stop when Lake/Explore missing (`tool_unavailable` report only).
- Path steal default false (MVP refuses writes).

## Related docs

- `canonical/instructions/autonomous-loop-formal-policy.md`
- `canonical/templates/informal-to-lean-formalization-runbook.md` (F1–F7)
- ARL skill + portfolio runbook P3b
