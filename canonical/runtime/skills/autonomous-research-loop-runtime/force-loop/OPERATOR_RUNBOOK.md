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

- Use `run_force_loop.ps1` or `.cmd`.
- Foreground only in v1. Supervisor shell scripts are POSIX-only; Windows uses `drive` via Python.
- Failover rotation that depends on `arl_drive_supervisor.sh` is a POSIX convenience; Windows operators set `--provider` or failover offline.

## Secrets

- Never put API tokens in `force_loop.env`, loop JSON, or unit `Environment=`.
- Export tokens in the launching shell, or point `AAS_COMPUTE_SECRETS_FILE` at a host-owned file outside the loop.
- systemd-run invocations must not pass tokens via `--setenv`.

## Recovery matrix

| Symptom | Action |
|---------|--------|
| Lock held, no live pid | `stop` then `start`; or remove stale pidfile after confirming no process |
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
