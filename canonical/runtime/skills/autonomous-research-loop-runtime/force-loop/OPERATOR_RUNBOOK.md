# Force-loop operator runbook

## Bootstrap a new loop

1. Choose project root and loop directory.
2. Run `force-loop bootstrap` with `--goal` (and criteria) if the loop does not exist yet.
3. Confirm smoke reports `ok: true` (enforce / hard / notify present).
4. Ensure notify secrets / egress are configured if notify must fire (preflight fails closed when required and no channel resolves — configure remote-bridge / notify secrets per ARL docs).
5. `force-loop start` in an interactive session or long-lived terminal (foreground default).

## Day-2 operations

| Need | Command |
|------|---------|
| Re-apply pins after hand edits | `apply-defaults` |
| See lock / pids / GF status | `status` |
| Stuck dispatch (dead PID) | `drain --cancel-dispatch-id <exact-id>` |
| Quarantine | `drain --recover-quarantine` |
| Soft stop | `touch $LOOP/STOP_REQUESTED` then wait |
| Hard stop | `force-loop stop` |
| Replace running supervisor | `force-loop replace` |

## OS notes

### Linux

- Default: foreground CLI (holds lock while supervisor/drive runs).
- Optional: `--backend systemd` only when `systemctl --user` works.
- Optional: `--detach` for posix background (pidfile under `driver/`).

### WSL

- Treat like Linux. Do not assume systemd is enabled; prefer foreground.

### macOS

- Foreground default. `--detach` available. No systemd.

### Windows

- Use `run_force_loop.ps1`.
- Foreground only in v1. Supervisor shell scripts are POSIX-only; Windows uses `drive` via Python.
- Failover rotation that depends on `arl_drive_supervisor.sh` is a POSIX convenience; Windows operators set `--provider` or failover offline.

## Secrets

- Never put API tokens in `force_loop.env`, loop JSON, or unit `Environment=`.
- Export tokens in the launching shell, or set `AAS_COMPUTE_SECRETS_FILE` in
  that shell to an absolute path outside the loop. The file uses strict
  `KEY=VALUE` lines and may contain only `HCLOUD_TOKEN`, `HCLOUD_SSH_KEYS`,
  `KAGGLE_API_TOKEN`, and `KAGGLE_CONFIG_DIR`.
- Protect the file with `chmod 600`: it must be current-user owned, single-link,
  and reachable without symlinks. The launcher rejects invalid files without
  printing their values.
- Put provider fallback credentials in a separate private file and export its
  absolute path as `AAS_PROVIDER_SECRETS_FILE` in the launching shell. The
  accepted names are `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`,
  `CLAUDE_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `COPILOT_GITHUB_TOKEN`,
  `COPILOT_PROVIDER_API_KEY`, `COPILOT_PROVIDER_BEARER_TOKEN`,
  `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`, `GH_TOKEN`, `GITHUB_TOKEN`,
  `GOOGLE_API_KEY`, `GROK_API_KEY`, `KIMI_API_KEY`, `MOONSHOT_API_KEY`,
  `OPENAI_API_KEY`, `OPENCODE_API_KEY`, and `XAI_API_KEY`. Do not place this
  pointer in `force_loop.env`; the launcher projects only the selected
  provider's keys into attested primary and panel children.
- systemd-run invocations must not pass tokens via `--setenv`.
- Set `AAS_REMOTE_STRICT_NOTIFY_CHANNEL=zulip` (or `telegram`) in the host-owned
  loop env when a campaign must forbid fallback. Invalid values fail start, and
  the validated restriction is propagated through the private systemd
  environment file.

## Notify channel and launch preflight

- Zulip notifications go to the stream named by `zulip.control_stream` in the
  remote-bridge secrets file. The bridge only **sends** to that stream — it
  never creates one — so point it at an **existing** channel, and verify
  existence first with a read-only `GET /api/v1/get_stream_id?stream=<name>`.
  A missing stream fails every send.
- Structural checks catch a missing channel configuration but not credentials
  that resolve and then fail authentication. For a campaign whose launch must
  fail closed on notify, run a launch preflight before `start`:
  1. build the launch env exactly as `start` will see it (the launching shell
     plus the host policy file named by `AAS_FORCE_LOOP_POLICY_FILE`) and
     require the strict-channel and egress variables;
  2. `remote-bridge` config resolution must yield the complete credential set
     for the required channel;
  3. dry-run the notification (`send --dry-run` semantics) and require the
     resolved channel order to equal the required one exactly;
  4. optionally verify live auth with a read-only identity call
     (Zulip `GET /api/v1/users/me`) — the only step that catches a bad key;
  5. wrap steps 1–4 in the launch script so a failure exits before any
     research process starts.

## Trusted-local attestation preflight

Attestation covers more than the executable hash: the attested binary and
**every file and directory in the dependency root** (the inferred npm package
root, or the pinned `AAS_AUTOLOOP_ATTESTED_DEPENDENCY_ROOT_<P>`) must be owned
by the invoking user or root, carry no group/other write bits, and contain no
symlinks; ancestor directories must likewise be symlink-free and not
group/other writable.
npm-installed CLIs commonly violate this out of the box (`775` directories,
`664` files), which fails drive with `bad_arguments`
("provider executable parent is group/other writable" /
"provider dependency is not host-controlled"). Check and fix before start:

```bash
find <package-root> -perm /022        # must print nothing
chmod -R go-w <package-root>          # content unchanged: sha256 pins still match
```

Re-check after every provider update; a package manager may restore group-write
permissions when it reinstalls.

## Recovery matrix

| Symptom | Action |
|---------|--------|
| Lock held, no live pid | `stop` then `start`; or remove stale pidfile after confirming no process |
| `loop_state.status` is `running`, no live pid | Normal boundary-stop resume state, not corruption: a terminal status is written only by an iteration's own decision. Trust `status` (lock/pids) and the last `iterations.jsonl` decision; `start` resumes |
| `spent_usd` exceeds `max_usd` = `0.0` | Not a breach: `0`/null is the uncapped sentinel and stop condition (c) never fires on it. Set a positive `max_usd` for a real cap |
| Drive exits `bad_arguments`: "group/other writable" / "not host-controlled" | Attested provider package tree fails host-control; `chmod -R go-w` the package root (see Trusted-local attestation preflight) |
| Dispatch pending, PID dead | `drain --cancel-dispatch-id …` (exact id) |
| Quarantine backlog | `drain --recover-quarantine` |
| Soft panel plans / inspect thrash | Re-`apply-defaults`; prefer registry `next_action`; replan under clean authority (`goal-focus replan`) — do not park an active approach only to force replan |
| Sticky compute forbid of `local` | Ensure `compute_policy.json` + standing mirror list `local` (file authoritative) |
| Validation errors on plan | `goal-focus status` / `validate`; fix pins; do not confuse **reconcile** with **replan** |

## Kill switches

- `touch $LOOP/STOP_REQUESTED`
- `touch $LOOP/PAUSE`
- `AUTOLOOP_DISABLE=1`
- `force-loop stop`
