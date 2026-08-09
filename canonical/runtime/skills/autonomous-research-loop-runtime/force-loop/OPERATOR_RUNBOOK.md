# Force-loop operator runbook

## Bootstrap a new loop

1. Choose project root and loop directory.
2. Run `force-loop bootstrap`; when the loop does not exist yet, both `--goal` and `--success-criteria` are required (init exits 2 without them).
3. Confirm smoke reports `ok: true` (enforce / hard / notify present).
4. Ensure notify secrets and `AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS=allow` are configured if notify must fire (preflight fails closed when required and no channel resolves — configure remote-bridge / notify secrets per ARL docs).
5. `force-loop start` in an interactive session or long-lived terminal (foreground default).

## Day-2 operations

| Need | Command |
|------|---------|
| Re-apply pins after hand edits | `apply-defaults` |
| Escalate / repair Goal Focus mode | `goal-focus set-mode --dir $LOOP --mode enforce --apply` (the kit never rewrites `enforcement_mode` itself) |
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
- The host policy file is fully supported: `Load-LoopEnv.ps1` parses the same
  strict `KEY=VALUE` allowlist as `load_loop_env.py`, so `--policy-file` pins
  (including `AAS_FORCE_LOOP_COMPUTE_LANES`) behave as on POSIX.
- Compute and provider secrets use the same lane-derived pointer contract; path
  checks are ownership/ACL-based rather than mode-`0600`-based.
- A credential-bearing `start` or `replace` requires an attested interpreter, so
  `run_python.ps1` demands **both** `AAS_WINDOWS_PYTHON_SHA256` (64 hex digits,
  the interpreter's SHA-256) and `AAS_WINDOWS_PYTHON_SIGNER_THUMBPRINT` (40–128
  hex digits, its Authenticode signer thumbprint) in the launching shell.
  Without them the launch exits 127 with "requires pinned Python digest and
  signer thumbprint". Pin them once per host:

  ```powershell
  $Python = (Get-Command python.exe).Source
  $env:AAS_WINDOWS_PYTHON_SHA256 = (Get-FileHash -Algorithm SHA256 $Python).Hash
  $env:AAS_WINDOWS_PYTHON_SIGNER_THUMBPRINT = `
      (Get-AuthenticodeSignature $Python).SignerCertificate.Thumbprint
  ```

  Subcommands that carry no credential (`status`, `doctor`, `drain`, `stop`)
  never demand the pins, so an ambient `GITHUB_TOKEN` in the shell cannot make
  them unstartable.
- Foreground only in v1: no Windows Service backend. `--detach` is accepted but
  has no effect, and an explicit `--backend posix_detach` is refused with
  "posix_detach is not available on Windows; use foreground". Supervisor shell
  scripts are POSIX-only, so Windows runs `drive` via Python.
- The exact-generation credential broker is POSIX-only: it speaks over
  `socket.AF_UNIX`, which CPython does not expose on Windows. `broker_active()`
  stays false, so panel and compute launches take the direct execution path and
  read credentials through `load_secret_env.ps1`.
- Failover rotation that depends on `arl_drive_supervisor.sh` is a POSIX
  convenience; Windows operators set `--provider` or failover offline.

## Secrets

- Never create project `force_loop.env` files or backups. They are rejected as
  shadow authority paths even when credential-shaped fields are empty.
- Never put API tokens in project policy, loop JSON, or unit `Environment=`.
- Exporting tokens in the launching shell does **nothing**: the runners scrub
  every provider and compute token before the CLI sees them, so the pointer
  files below are the only credential authority.
- Set `AAS_COMPUTE_SECRETS_FILE` in the launching shell to an absolute path
  outside the loop. The file uses strict `KEY=VALUE` lines and must contain
  exactly the keys of the lanes selected by `AAS_FORCE_LOOP_COMPUTE_LANES` in
  the host policy file — `hetzner` → `HCLOUD_TOKEN`, `HCLOUD_SSH_KEYS`;
  `kaggle` → `KAGGLE_API_TOKEN`, `KAGGLE_CONFIG_DIR`; `modal` →
  `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`. Keys outside the selected lanes are
  rejected, and a pointer with no selected lanes fails `start`. The lane pin is
  read only from the `--policy-file` (an exported shell variable of that name
  is dropped), is preserved across `apply-defaults` re-runs, selects accepted
  secret **names** only, and is independent of `compute_policy.json` backends.
  Windows honours the same lane-derived contract via `run_force_loop.ps1`.
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
- Export `AAS_REMOTE_STRICT_NOTIFY_CHANNEL=zulip` (or `telegram`) in the
  **launching shell** when a campaign must forbid fallback; it is not accepted
  in the host policy file (`load_loop_env` admits only the `AAS_AUTOLOOP_*`
  pins and `AAS_FORCE_LOOP_COMPUTE_LANES`, and one unsupported key fails the
  whole file). Invalid values fail start, and the validated restriction is
  propagated through the private systemd environment file.
- Goal Focus `enforce` — which this kit pins — fail-closes external notify:
  also export `AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS=allow` in the launching
  shell, or no remote notification is sent. `smoke` warns when it is absent.

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
     require `AAS_REMOTE_STRICT_NOTIFY_CHANNEL` and
     `AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS=allow`;
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

## Child-command binding preflight

`start` binds the child command by descriptor before it loads any credential:
`argv[0]` (the interpreter) and, when it is an absolute `.py` or `.sh`,
`argv[1]` (the script) are opened with `O_NOFOLLOW`, and every ancestor
directory must be owned by root or the calling user with no group/other write
bit. Root-owned sticky directories such as `/tmp` are accepted; a
group-writable one is not. The command file itself must additionally be a
regular file with exactly one hard link unless root owns it.

This is the same host-control rule as the attestation preflight above, but here
it applies to **the kit's own tree** rather than to a provider package. A
checkout made under a group-writable umask carries `775` directories and `664`
files, so `start` refuses to launch from it:

```json
{
  "ok": false,
  "error": "child binding failed: child command ancestor /srv/ai-agents-skills is a group- or world-writable directory (mode 0775); run 'chmod go-w /srv/ai-agents-skills'"
}
```

The message names the first offending path, its mode, and the remedy. Clear the
whole chain before the first start, then confirm it:

```bash
find <kit-root> -perm /022     # must print nothing
chmod -R go-w <kit-root>       # content unchanged: sha256 pins still match
namei -m <kit-root>/canonical/runtime/skills/autonomous-research-loop-runtime/autonomous_research_loop_runtime.py
```

`chmod -R` does not touch the directories **above** `<kit-root>`, which the
walk also inspects; `namei -m` prints that full chain so a remaining `775`
parent is visible. The loop directory is not bound this way, so relocating the
loop never clears this error — a loop under an owner-private directory in
`/tmp` starts normally once the command chain is owner-controlled.

## Banked launch presets (claude)

Two tested `AAS_AUTOLOOP_ARGS_CLAUDE` presets are banked as env fragments in
`canonical/templates/sample-arl-headless-driver-with-formal/`:

| | Preset A `hermetic_benchmark_env.inc.sh` | Preset B `production_formalization_env.inc.sh` |
|---|---|---|
| Purpose | Closed-book benchmark (the 2026-08 T0–T4 recipe) | Production formalization |
| Claude args | `--strict-mcp-config --setting-sources project --disallowedTools WebSearch WebFetch Task Agent Skill` | Same, plus `--mcp-config $AAS_ARL_MCP_CONFIG` (curated file) |
| Formal policy | `on`, project `.` | `on`, typecheck `1` |
| F2' library-first | Suspended by goal wording only (no runtime flag; see fragment note) | Active (intended) |
| MCP posture | None beyond project config | Curated operator-owned 0600 config outside the loop tree; server keys only via its per-server `env` block |
| Panel | `--panel off` | `--panel auto` (host-owned panel replaces the disallowed Task/Agent/Skill) |

Launch template (both presets):

```bash
env -i HOME="$HOME" PATH="$PATH" LANG="$LANG" TERM="$TERM" \
    TMPDIR="$TMPDIR" USER="$USER" PYTHONDONTWRITEBYTECODE=1 \
    bash -c 'source <preset>.inc.sh && python3 .../autonomous_research_loop_runtime.py drive --provider claude ...'
```

**Lane matrix — read before sourcing.** `AAS_AUTOLOOP_ARGS_CLAUDE` presets run
ONLY in the strict-isolated, non-attested drive lane:

| Lane | Preset A/B |
|------|-----------|
| Plain `drive`, provider not attested | ✅ works |
| Provider attested (`AAS_AUTOLOOP_ATTESTED_BIN_CLAUDE` pinned) | ❌ refused fail-closed: "a host-attested provider cannot use custom argument overrides" |
| Goal-Focus enforce campaign | ❌ enforce requires trusted-local attestation → same refusal |
| This force-loop kit | ❌ defaults pin Goal-Focus enforce (`apply_force_loop_defaults.py` `verify_effective`), and the policy-file grammar cannot carry an args template; the kit also has no MCP key channel (`run_force_loop.sh` unsets `LEANEXPLORE_API_KEY`) |

A first-class MCP-config knob for the attested/enforce lane (R1) is
**deliberately deferred**: an MCP config grants live tool-server authority to
an unattended agent, so it needs its own adversarial security review —
including server-credential delivery through `build_primary_child_env`'s
allowlists — before it can be added.

## Recovery matrix

| Symptom | Action |
|---------|--------|
| Lock held, no live pid | `stop` then `start`; or remove stale pidfile after confirming no process |
| `loop_state.status` is `running`, no live pid | Normal boundary-stop resume state, not corruption: a terminal status is written only by an iteration's own decision. Trust `status` (lock/pids) and the last `iterations.jsonl` decision; `start` resumes |
| `spent_usd` exceeds `max_usd` = `0.0` | Not a breach: `0`/null is the uncapped sentinel and stop condition (c) never fires on it. Set a positive `max_usd` for a real cap |
| Drive exits `bad_arguments`: "group/other writable" / "not host-controlled" | Attested provider package tree fails host-control; `chmod -R go-w` the package root (see Trusted-local attestation preflight) |
| `start` exits 2: "child binding failed: … is a group- or world-writable directory/regular file" | The kit's own command chain is not owner-controlled; `chmod go-w` the exact path the message names, then re-check the ancestors above it (see Child-command binding preflight). Moving the loop does not help |
| `start` exits 2: "child binding failed: … has N hard links" | The bound interpreter or script is multiply linked, so another link could replace it; copy it to a single-link path or run it from a root-owned location |
| Dispatch pending, PID dead | `drain --cancel-dispatch-id …` (exact id) |
| Quarantine backlog | `drain --recover-quarantine` |
| Soft panel plans / inspect thrash | Re-`apply-defaults`; prefer registry `next_action`; replan under clean authority (`goal-focus replan`) — do not park an active approach only to force replan |
| Sticky compute forbid of `local` | Ensure `compute_policy.json` + standing mirror list `local` (file authoritative) |
| Validation errors on plan | `goal-focus status` / `validate`; fix pins; do not confuse **reconcile** with **replan** |
| Drive exits `runtime_error`: "transaction journal quarantined" | Inspect `$LOOP/.goal_focus_transactions_quarantine/<id>/manifest.json` against the live targets, then re-run `start`; the loop is not wedged |
| Supervisor exits 11 immediately, no drive spawned | All primaries are session-excluded; entries expire after `session_exclude_ttl_s` (default 6 h), or clear now with `rm $LOOP/driver/EXCLUDED` and empty `standing_orders.panel.exclude_until_credit` in `loop_state.json` |
| `iterations.jsonl` looks truncated / iteration numbers restart | Not data loss: the ledger shards at 8 MB. Read `iterations.<n>.jsonl` in numeric order, then the live `iterations.jsonl`; rotation lands inside the finalize transaction, so no record falls between them |
| `smoke` fails: "loop_state.notify_channel is off while the notify pin is auto/on" | `notify_channel: "off"` is decisive and sticky, and the pin alone cannot unmute the loop; set it back to `auto` (or a channel) in `loop_state.json`, then re-run `apply-defaults` |

## Kill switches

- `touch $LOOP/STOP_REQUESTED`
- `touch $LOOP/PAUSE`
- `AUTOLOOP_DISABLE=1` — interactive Stop hook only; no effect on a running
  drive or supervisor. Use `STOP_REQUESTED` or `force-loop stop` instead.
- `force-loop stop`
