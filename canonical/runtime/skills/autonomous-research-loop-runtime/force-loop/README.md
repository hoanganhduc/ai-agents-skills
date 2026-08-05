# Force-loop kit (default scripted path)

**Default** cross-platform pack for unattended ARL **force-loop** (supervisor + `drive` with Goal Focus discipline). Not `formal_policy=force` hygiene and not TikZ `force_loop`.

## Defaults applied on bootstrap / apply-defaults

| Pin | Value |
|-----|--------|
| Goal Focus | `enforce` |
| goal_priority | `enabled=true`, `discipline_mode=hard` |
| Notify | `AAS_AUTOLOOP_NOTIFY=auto` (+ standing_orders) |
| Compute (default profiles) | allow `local,kaggle,modal`; forbid `hetzner,github-actions` |
| Formal profile | `formal_policy=on`, typecheck on |

## Platform matrix

| Platform | Bootstrap / apply | Start default | Optional |
|----------|-------------------|---------------|----------|
| Linux | Python CLI + `.sh` | **foreground** | `--backend systemd` if user bus works; `--detach` |
| WSL | same as Linux | **foreground** | avoid assuming full systemd |
| macOS | Python CLI + `.sh` | **foreground** | `--detach` |
| Windows | Python CLI + `.ps1` | **foreground** | no Windows Service in v1 |

## Commands

```text
force-loop bootstrap --loop DIR --root ROOT --profile formal|general [--goal …]
force-loop apply-defaults --loop DIR --profile formal|general
force-loop start --loop DIR --root ROOT          # foreground
force-loop stop|replace|status --loop DIR
force-loop drain --loop DIR [--cancel-dispatch-id ID] [--recover-quarantine]
force-loop smoke --loop DIR [--live]
```

### POSIX

```bash
RUNTIME="${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}"
bash "$RUNTIME/run_skill.sh" \
  skills/autonomous-research-loop-runtime/force-loop/run_force_loop.sh \
  bootstrap --loop "$LOOP" --root "$ROOT" --profile formal --goal "…"
```

### Windows

```powershell
& "$env:AAS_RUNTIME_ROOT\run_skill.ps1" `
  skills\autonomous-research-loop-runtime\force-loop\run_force_loop.ps1 `
  bootstrap --loop $Loop --root $Root --profile formal --goal "…"
```

## Env safety

- **Never** shell-`source` agent-writable loop env files.
- Host pin file: `{loop}/driver/force_loop.env` (strict KEY=VALUE via `load_loop_env.py`).
- Secrets may be exported directly in the launcher process, or loaded from the
  absolute path in launcher variable `AAS_COMPUTE_SECRETS_FILE`. Do not put
  that pointer in `force_loop.env`.
- The compute secrets file must be a bounded, single-link regular file with no
  symlink in its path. On POSIX it must be owned by the effective user and mode
  `0600` (or stricter). Only `HCLOUD_TOKEN`, `HCLOUD_SSH_KEYS`,
  `KAGGLE_API_TOKEN`, and `KAGGLE_CONFIG_DIR` are accepted. The restored class
  replaces stale ambient values and the pointer is removed before child launch;
  values are never included in CLI output.
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
