# ARL drive supervisor (failover pack)

Outer supervisor around stock single-provider `drive`. Installs with the
`autonomous-research-loop-runtime` skill (per-file entries in
`manifest/runtime.yaml`).

## Files

| File | Role |
|------|------|
| `arl_drive_supervisor.sh` | Rotate primaries on drive exit 5/6/7; session-exclude |
| `LAUNCH_supervisor.sh` | `start` (flock) or `replace` (stop prior then start) |
| `failover.example.json` | Schema example → copy to `{loop}/failover.json` |
| `sync_panel_exclude.py` | Atomic panel exclude_until_credit merge |
| `apply_failover_settings.py` | Write/merge failover.json |

## Quick start

```bash
RUNTIME="${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}"
PACK="$RUNTIME/workspace/skills/autonomous-research-loop-runtime"

python3 "$PACK/apply_failover_settings.py" \
  --dir /path/to/loop \
  --from-json "$PACK/failover.example.json" \
  --research-title "TS_k acyclicity characterization" \
  --primary-order grok,claude,deepseek \
  --max-quota-waits 3 \
  --force

export LOOP_DIR=/path/to/loop
export PROJECT_ROOT=/path/to/project
export AAS_RUNTIME_ROOT="$RUNTIME"
# Optional: pre-export HCLOUD_TOKEN / KAGGLE_API_TOKEN via loop helper
nohup bash "$PACK/LAUNCH_supervisor.sh" replace >>"$LOOP_DIR/driver_logs/launch.out" 2>&1 &
```

## Provider ids

Use the same names as `drive --provider`: `claude`, `codex`, `deepseek` (CodeWhale),
`grok`, `kimi`, `opencode`, `copilot`, `antigravity` (antigravity-cli / `agy`).

Default example **drive** order (`failover.example.json`):

`claude → codex → grok → opencode → antigravity → copilot → kimi → deepseek`

**Failover rule:** always drive with the **first available** name in that list
(not session-excluded). On hard failure (exit 5/6/7) or a soft-failure streak
(exit 3/4 after `failures_before_rotate`), the current primary is session-excluded
and the next cycle picks the new first available entry. When none remain → exit 11.

Default **panel** invite order (when loop config omits `providers`):

`codex → claude → grok → opencode → antigravity → copilot → kimi → deepseek`

## Drive exit codes consumed

| rc | Meaning | Supervisor action |
|----|---------|-------------------|
| 5 | quota_wait_exhausted | session-exclude + rotate |
| 6 | provider_unavailable | session-exclude + rotate |
| 7 | auth_or_session_dead | session-exclude + rotate |
| 3/4 | failures / runtime | streak then rotate (no permanent exclude) |
| 0 | clean | restart same primary if loop not done |

## Supervisor exit codes

| rc | Meaning |
|----|---------|
| 0 | done / STOP |
| 2 | config |
| 10 | lock held (`start` only) |
| 11 | all primaries session-excluded |
| 12 | restart cap |

## Notify titles

Set `research_title` in `failover.json` so Zulip/Telegram show the research topic,
not a generic “loop” name. Drive also derives a short title from `loop_state.goal`
when unset.

## Secrets

Never load API tokens from files inside these scripts. Export tokens in the
environment (or via a loop-owned `with_compute_env.sh`) before launch.

## Compose with formal / compute

```bash
export DRIVE_EXTRA_ARGS="--formal-policy on"
# or source formal_env.inc.sh then append flags
LOOP_DIR=... PROJECT_ROOT=... bash LAUNCH_supervisor.sh replace
```
