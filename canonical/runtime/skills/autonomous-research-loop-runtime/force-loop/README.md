# Force-loop kit (default scripted path)

**Default** cross-platform pack for unattended ARL **force-loop** (supervisor + `drive` with Goal Focus discipline). Not `formal_policy=force` hygiene and not TikZ `force_loop`.

## Defaults applied on bootstrap / apply-defaults

| Pin | Value |
|-----|--------|
| Goal Focus | `enforce` |
| goal_priority | `enabled=true`, `discipline_mode=hard` (warnings + panel goal-EV block; the host path rewrite is inactive under the Goal Focus `enforce` pin above) |
| Notify | `AAS_AUTOLOOP_NOTIFY=auto` (+ standing_orders) |
| Compute (default profiles) | allow `local,kaggle,modal`; forbid `hetzner,github-actions` |
| Compute secret lanes | `AAS_FORCE_LOOP_COMPUTE_LANES` (host policy; selects accepted secret names) |
| Formal profile | `formal_policy=on`, typecheck on |

## Platform matrix

| Platform | Bootstrap / apply | Start default | Optional |
|----------|-------------------|---------------|----------|
| Linux | Python CLI + `.sh` | **foreground** | `--backend systemd` if user bus works; `--detach` |
| WSL | same as Linux | **foreground** | avoid assuming full systemd |
| macOS | Python CLI + `.sh` | **foreground** | `--detach` |
| Windows | Python CLI + `.ps1` | **foreground** | host policy read by `Load-LoopEnv.ps1`; no Windows Service in v1; `--detach` has no effect and `--backend posix_detach` is refused |

## Commands

Set `AAS_FORCE_LOOP_POLICY_FILE` to the absolute host policy path, or pass
`--policy-file` on every command that writes or reads pins: `bootstrap`,
`apply-defaults`, `start`, `replace`, `status`, and `smoke` all exit before
doing any work without it. Only `stop` and `drain` run without a policy path.
`--profile` defaults to `formal` wherever it is accepted.

```text
force-loop bootstrap --loop DIR --root ROOT --profile formal|general [--goal …] --policy-file ABS_PATH
force-loop apply-defaults --loop DIR --profile formal|general --policy-file ABS_PATH
force-loop start --loop DIR --root ROOT --policy-file ABS_PATH     # foreground
force-loop replace --loop DIR --root ROOT --policy-file ABS_PATH   # stop, then start
force-loop status --loop DIR --policy-file ABS_PATH
force-loop stop --loop DIR
force-loop drain --loop DIR [--cancel-dispatch-id ID] [--recover-quarantine]
force-loop smoke --loop DIR [--live] --policy-file ABS_PATH
```

### POSIX

```bash
RUNTIME="${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}"
POLICY=/abs/path/host-policy.env
bash "$RUNTIME/run_skill.sh" \
  skills/autonomous-research-loop-runtime/force-loop/run_force_loop.sh \
  bootstrap --loop "$LOOP" --root "$ROOT" --profile formal --goal "…" \
  --policy-file "$POLICY"
```

### Windows

```powershell
$Policy = "C:\abs\path\host-policy.env"
& "$env:AAS_RUNTIME_ROOT\run_skill.ps1" `
  skills\autonomous-research-loop-runtime\force-loop\run_force_loop.ps1 `
  bootstrap --loop $Loop --root $Root --profile formal --goal "…" `
  --policy-file $Policy
```

## Env safety

- **Never** shell-`source` agent-writable loop env files.
- Host policy is an explicit owner-private file outside the loop tree, passed
  with `--policy-file` and parsed by `load_loop_env.py`.
- `{loop}/driver/force_loop.env` and backup copies are forbidden shadow
  authorities. Defaults may migrate only the strict nonsecret policy allowlist
  and never copy legacy bytes. Any credential-capable field requires redacted
  manual promotion to the canonical provider, compute, or Remote Bridge
  authority before retrying.
- Provider and compute authorities are supplied only to the exact-generation
  launcher; never put their values or pointers in project policy files.
- The compute secrets file must be a bounded, single-link regular file with no
  symlink in its path. On POSIX it must be owned by the effective user and mode
  `0600` (or stricter). The accepted key set is **derived from the lanes**
  selected by `AAS_FORCE_LOOP_COMPUTE_LANES` in the host policy file — a
  comma-separated list:

  | Lane | Keys projected |
  |------|----------------|
  | `hetzner` | `HCLOUD_TOKEN`, `HCLOUD_SSH_KEYS` |
  | `kaggle` | `KAGGLE_API_TOKEN`, `KAGGLE_CONFIG_DIR` |
  | `modal` | `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` |

  A pointer with no selected lanes fails `start`. The lane pin is read only
  from the `--policy-file`; an exported shell variable of the same name is
  dropped. `apply-defaults` preserves it across re-runs. It selects accepted
  secret **names** only and is independent of `compute_policy.json` backends.
  The same lane-derived contract applies on Windows via `run_force_loop.ps1`.
  The restored class replaces stale ambient values and the pointer is removed
  before child launch; values are never included in CLI output.
- Provider fallback credentials use a separate absolute launcher pointer,
  `AAS_PROVIDER_SECRETS_FILE`. Its file has the same strict path, ownership,
  size, syntax, duplicate, and empty-value checks. It accepts only
  `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_API_KEY`,
  `CLAUDE_CODE_OAUTH_TOKEN`, `COPILOT_GITHUB_TOKEN`,
  `COPILOT_PROVIDER_API_KEY`, `COPILOT_PROVIDER_BEARER_TOKEN`, `DEEPSEEK_API_KEY`,
  `GEMINI_API_KEY`, `GH_TOKEN`, `GITHUB_TOKEN`, `GOOGLE_API_KEY`,
  `GROK_API_KEY`, `KIMI_API_KEY`, `MOONSHOT_API_KEY`, `OPENAI_API_KEY`,
  `OPENCODE_API_KEY`, and `XAI_API_KEY`. The restored values override ambient
  values only inside the launcher/child process tree; strict primary and panel
  children receive only the keys explicitly allowed for their selected
  provider. Never put this pointer in `force_loop.env`.
- `AAS_REMOTE_STRICT_NOTIFY_CHANNEL` accepts only `zulip`, `telegram`, or an
  empty value. The start path validates it and includes the normalized policy in
  the private systemd environment file so a detached unit cannot lose the
  campaign-wide send boundary.

## Layout

| File | Role |
|------|------|
| `force_loop_cli.py` | Universal CLI |
| `apply_force_loop_defaults.py` | Pin writer + verify |
| `load_loop_env.py` | Strict env parse |
| `force_loop_process.py` | Portable start/stop/status |
| `run_force_loop.sh` / `.ps1` | Thin runners |
| `defaults/` | Non-secret JSON + env.defaults |
| `profiles/` | formal / general hints |

See `OPERATOR_RUNBOOK.md` and `LESSONS.md`.
