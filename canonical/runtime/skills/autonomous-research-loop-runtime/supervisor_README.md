# ARL drive supervisor (failover pack)

Outer supervisor around stock single-provider `drive`. Installs with the
`autonomous-research-loop-runtime` skill (per-file entries in
`manifest/runtime.yaml`).

**Default multi-OS force-loop:** prefer sibling pack `force-loop/`
(`run_force_loop.sh` / `.ps1`) for bootstrap, applied defaults
(enforce / hard / notify), and portable start/stop/drain. This supervisor pack
is the POSIX failover detail that kit uses when present; direct
`LAUNCH_supervisor.sh` remains an advanced entry.

## Files

| File | Role |
|------|------|
| `arl_drive_supervisor.sh` | Rotate primaries on drive exit 5/6/7; session-exclude |
| `LAUNCH_supervisor.sh` | `start` (exclusive lock) or `replace` (stop prior then start) |
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
# In an ordinary interactive shell, run LAUNCH_supervisor.sh directly. Under a
# managed executor that reaps descendants, start a persistent user service
# through a loop-owned wrapper that loads credentials internally:
systemd-run \
  --user \
  --quiet \
  --collect \
  --no-ask-password \
  --expand-environment=no \
  --unit=aas-autoloop-example.service \
  --service-type=exec \
  --working-directory="$PROJECT_ROOT" \
  --property=KillMode=control-group \
  --property=TimeoutStopSec=20s \
  /path/to/loop-owned-launch-wrapper --foreground
```

The service command and unit properties must contain only non-secret paths and
controls. Do not pass provider or compute tokens through `--setenv`, `env
TOKEN=...`, or unit `Environment=` properties. The wrapper should construct a
minimal environment, source the loop's non-secret policy/pin file, load only
the approved credentials from its protected source, then `exec` this pack's
`LAUNCH_supervisor.sh start` in the foreground.

## Provider ids

Use the same names as `drive --provider`: `claude`, `codex`, `deepseek` (CodeWhale),
`grok`, `kimi`, `opencode`, `copilot`, `antigravity` (antigravity-cli / `agy`).

Default example **drive** order (`failover.example.json`):

`claude → codex → grok → opencode → antigravity → copilot → kimi → deepseek`

**Failover rule:** always drive with the **first available** name in that list
(not session-excluded).

| Drive exit | Meaning | Supervisor |
|------------|---------|------------|
| **5** | `quota_wait_exhausted` after **N** consecutive quota signals (`max_quota_waits`, default **3**), including weekly/usage limits | Session-exclude as temporary **quota_or_credit** (+ panel `exclude_until_credit`); switch to first available |
| **6** | provider binary unavailable | Session-exclude; switch |
| **7** | auth/session dead | Session-exclude; switch |
| **3** | `max_failures` (drive already saw N consecutive non-quota fails, default 3) | Session-exclude immediately; switch |
| **4** | runtime error | After `failures_before_rotate` streak; exclude and switch |

When none remain → exit 11.

Session exclusions are **not** permanent. Each entry in `{loop}/driver/EXCLUDED`
is stored as `name<TAB>epoch` and expires after `session_exclude_ttl_s` seconds
(`failover.json`, default `21600` = 6 h), so a provider that regains credit
re-enters rotation on the next supervisor pass without operator action. Set it
to `0` to disable expiry. A legacy bare-name file is read as excluded from
**now**, so an upgrade costs one more TTL window rather than a permanent
exclusion. To clear immediately, remove the file and empty
`standing_orders.panel.exclude_until_credit` in `loop_state.json`.

The shipped supervisor and `failover.example.json` pass
`drive_defaults.max_failures=3`. Host panel dispatch separately persists a
three-attempt cap per phase and pending iteration, so restarting or rotating a
primary does not restart panel calls indefinitely.

Default **panel** invite order (when loop config omits `providers`):

`codex → claude → codewhale`

The panel id for CodeWhale is `codewhale`; the drive `--provider` id for the
same provider is `deepseek` (both are accepted by panel dispatch).

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

## POSIX portability

Both scripts are POSIX-targeted, not Linux-only, and degrade rather than fail on
hosts missing Linux-specific tooling:

- **`flock(1)`** — absent on macOS/BSD. `LAUNCH_supervisor.sh` falls back to
  `flock(2)` on the inherited lock descriptor, so the lock still belongs to the
  launching shell. A locking failure that is not contention exits **3**, never
  the "lock held" **10**.
- **procfs** — absent on macOS and some containers. `stop`/`replace` scan
  `/proc` when it exists and `ps -ax -o pid=,command=` otherwise.
- **`fuser(1)`** — util-linux/psmisc only. `replace` skips the lock-release wait
  when it is missing instead of reading "not installed" as "lock already free".

## Notify titles

Set `research_title` in `failover.json` so Zulip/Telegram show the research topic,
not a generic “loop” name. Drive also derives a short title from `loop_state.goal`
when unset.

## Secrets

Never load API tokens from files inside these scripts. For a direct
`LAUNCH_supervisor.sh` launch, export tokens in the environment (or via a
loop-owned `with_compute_env.sh`) before launch. Under the force-loop kit (see
above) that advice does **not** apply: the kit scrubs every ambient provider and
compute token before the CLI sees them and takes credentials only through
`AAS_COMPUTE_SECRETS_FILE` / `AAS_PROVIDER_SECRETS_FILE`.

## Compose with formal / compute

```bash
export DRIVE_EXTRA_ARGS="--formal-policy on"
# or source formal_env.inc.sh then append flags
LOOP_DIR=... PROJECT_ROOT=... bash LAUNCH_supervisor.sh replace
```
