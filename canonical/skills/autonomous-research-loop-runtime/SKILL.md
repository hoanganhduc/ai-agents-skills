---
name: autonomous-research-loop-runtime
description: Runtime helper for autonomous-research-loop ledgers plus headless drive, host-owned multi-agent panel phases (--panel on, auto, or off), and the default cross-platform force-loop kit (bootstrap/start/drain with enforce/hard/notify defaults). Use to initialize, append, validate, inspect, smoke-test, drive, or panel-dispatch loop state without requiring ad-hoc nested multi-agent CLIs from the primary agent.
---

# Autonomous Research Loop Runtime

This companion skill provides offline helper scripts for the
`autonomous-research-loop` ledger contract.

It is intentionally runtime-backed and should be installed only for targets that
support runtime skill helpers. It is not an OpenClaw skill-file target.

## Commands

From a configured ai-agents-skills runtime, prefer:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh selftest
```

Common commands:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh init --dir research/run --goal "..." --success-criteria "..."
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh append-iteration --dir research/run --mode bounded-research --objective "Check evidence gaps" --decision continue
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh validate --dir research/run
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh status --dir research/run
```

Initialize a new goal-focused loop in enforce mode (the default for new v2
state), or select an explicit compatibility mode:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh init \
  --dir research/run \
  --goal "..." \
  --success-criteria "..." \
  --goal-focus-mode enforce
```

Inspect and migrate an existing `goal_priority.v1` loop explicitly:

```bash
… goal-focus migrate --dir research/run --dry-run
… goal-focus migrate --dir research/run --apply
… goal-focus status --dir research/run
… goal-focus validate --dir research/run
… goal-focus replan --dir research/run --trigger plateau --dry-run
… goal-focus reconcile --dir research/run
… goal-focus recover-quarantine --dir research/run
```

Migration is dry-run first. When dynamic campaign signals disagree, the runtime
refuses to choose among them. Apply may still create a safe v2 state with
`current_plan.state: needs_replan` and no selected direction; enforce mode will
not dispatch until structured strategy review or an explicit reviewed campaign
resolves it.

The helper is authoritative for local ledger and iteration-budget invariants.
It rejects appends after `max_iterations`, rejects continuing decisions on the
final allowed iteration, rejects early `stop` records that lack a valid
proof/success artifact, and validation fails ledgers whose spent iteration
count, iteration records, terminal decisions, and running status disagree.

The runtime also exposes force-management and enforcement subcommands used by the
autoloop wiring (not part of the normal ledger flow): `arm` / `disarm` /
`active` register, deregister, and list an active loop; `done` is the read-only
stop-condition arbiter; `hook-check` is the cross-platform Stop-hook check that
the installed Claude `hooks.Stop` entry invokes directly (it reads the hook JSON
on stdin, honors `AUTOLOOP_DISABLE` / `AUTOLOOP_DRIVER` / the `stop_hook_active`
re-entrancy payload, and exits 2 only when an active loop is unfinished, fail-open
otherwise); `agent-cmd` prints the per-provider headless one-iteration command
(offline PATH probe, no execution); and `drive` is the cross-platform headless
driver that runs one iteration per loop until `done` (the POSIX
`autoloop_driver.sh` is a thin shim that delegates to it).

## Truly autonomous execution on every install target

A chat session cannot run hundreds of loop iterations: context windows and turn
boundaries end it. Unattended execution therefore uses `drive`, which respawns a
FRESH headless agent session per iteration against the on-disk loop files and
owns the stop conditions itself. Exactly one of `--cmd` or `--provider` selects
the iteration command; with `--provider` the runtime builds the standard
one-iteration invocation for that install target:

| Provider (target) | Iteration command built by `agent-cmd` / `drive --provider` |
|---|---|
| `claude` | `claude -p "<prompt>" --dangerously-skip-permissions` |
| `codex` | `codex exec --full-auto "<prompt>"` |
| `deepseek` | `codewhale exec --auto "<prompt>"` (falls back to `codewhale-tui`, `deepseek`) |
| `opencode` | `opencode run "<prompt>"` |
| `copilot` | `copilot -p "<prompt>" --allow-all-tools` |
| `antigravity` | `agy -p "<prompt>" --dangerously-skip-permissions` (falls back to `gemini --yolo -p "<prompt>"`). **Never put flags between `-p` and the prompt** — `-p`/`--print` consumes the next argv as the prompt text (wrong: `agy --print --dangerously-skip-permissions "$PROMPT"`). `agy` does not read the user prompt from stdin. |

`<prompt>` is the standard one-iteration contract: read `recovery.md` and the
ledger, execute the single recorded next action under the loop policy, verify
independently, append exactly one iteration record, refresh the recovery files,
exit. Inspect it with `agent-cmd --provider <p> --dir <loop> --print-prompt`.
OpenClaw is not a driver target (no local agent CLI); drive its loops from a
supported provider instead.

### Default scripted force-loop (all OS)

For unattended **force-loop** (supervisor + drive with Goal Focus discipline),
prefer the installed **force-loop kit** first. It applies notify ON, Goal Focus
**enforce**, and goal_priority **hard**, and works on Linux, macOS, Windows, and
WSL without requiring systemd:

Every command that writes or reads pins needs the host policy file, either as
`--policy-file ABS_PATH` or via `AAS_FORCE_LOOP_POLICY_FILE`:

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/autonomous-research-loop-runtime/force-loop/run_force_loop.sh \
  bootstrap --loop research/run --root "$PWD" --profile formal --goal "..." \
  --policy-file /abs/path/host-policy.env
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/autonomous-research-loop-runtime/force-loop/run_force_loop.sh \
  start --loop research/run --root "$PWD" --provider claude \
  --policy-file /abs/path/host-policy.env
```

Windows (PowerShell only; the same policy file is read by `Load-LoopEnv.ps1`):

```powershell
& "$env:AAS_RUNTIME_ROOT\run_skill.ps1" skills/autonomous-research-loop-runtime/force-loop/run_force_loop.ps1 bootstrap --loop research\run --root $PWD --profile formal --goal "..." --policy-file C:\abs\path\host-policy.env
```

Pack docs: `force-loop/README.md`, `OPERATOR_RUNBOOK.md`. Discovery template:
`arl-scripted-force-loop`. Direct `drive` / `LAUNCH_supervisor.sh` remain
supported as advanced paths.

Start a raw unattended `drive` (POSIX, advanced):

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh drive --dir research/run --provider claude
```

On Windows use `& "$env:AAS_RUNTIME_ROOT\run_skill.ps1" ... run_autonomous_research_loop.ps1 drive --dir research\run --provider codex`.
Wrap with a persistent user service or Task Scheduler for multi-day runs. A
managed executor may reap ordinary `nohup` descendants when its command ends;
on Linux, prefer a `systemd-run --user` service whose command is a loop-owned
wrapper. The managed force-loop backend writes one private mode-`0600`,
allowlisted environment file below `XDG_RUNTIME_DIR`, passes only that file's
path to `systemd-run`, and removes it through `ExecStopPost`. Never put token
values in `--setenv` arguments or unit properties.

The direct `drive` wrappers consume the restored launcher pointers
`AAS_PROVIDER_SECRETS_FILE` and `AAS_COMPUTE_SECRETS_FILE` through the same
strict managed loaders as the default force-loop kit. Provider authority uses
the documented provider allowlist, including `COPILOT_PROVIDER_API_KEY` and
`COPILOT_PROVIDER_BEARER_TOKEN`; compute authority accepts only
`HCLOUD_TOKEN`, `HCLOUD_SSH_KEYS`, `KAGGLE_API_TOKEN`, and
`KAGGLE_CONFIG_DIR`. A protected authority replaces stale ambient values in its
class, and both pointers are removed before the managed drive starts. The
runtime's attested provider/host-pinned lane filters still decide which values
enter each primary or panel child. Never place either pointer or credential
assignment in the loop directory's env file.

Driver behavior:

- Each iteration's output is captured under `<loop>/driver_logs/` (host prompt +
  `# --- END HOST PROMPT ---` sentinel first, then agent stdout/stderr).
- Failure classification on nonzero exit (after stripping the host prompt):
  **AUTH** (token invalidated / 401 Unauthorized / sign-in-again) → immediate
  exit **7** `auth_or_session_dead` (no 900s sleep); **QUOTA** (429, out of
  credits, usage limit, anchored `quota exceeded|limit|…`) →
  `quota_wait` pause; else hard failure. Patterns match provider-error text,
  not bare prompt word `quota`.
- Credit/quota outages (including Claude **weekly limit** / “resets …” phrasing)
  pause and retry. `--max-quota-waits N` caps consecutive quota signals (default
  0 = wait indefinitely). With `N>0`, exit **5** fires when
  `quota_waits >= N` (so **`N=3` means three consecutive quota fails then switch**).
  Hard caps (weekly/monthly/out-of-credits) use a short backoff (~15s) between
  those N signals when a max is set. Outer supervisor treats exit 5 as temporary
  `quota_or_credit` exclude and picks the first available primary.
- **Multi-provider unattended:** stock `drive` stays single `--provider`. Prefer
  the outer supervisor pack (`arl_drive_supervisor.sh` /
  `LAUNCH_supervisor.sh` + `{loop}/failover.json`) which rotates on exit 5/6/7
  and session-excludes dead primaries. See `supervisor_README.md` in this runtime
  skill. Full policy: instruction `provider-credit-quota.md`.
- **Operator policy when a primary is known exhausted and no supervisor:** do not
  leave `max-quota-waits 0` spinning; stop and restart with `--provider <funded>`,
  and set `exclude_until_credit` on the panel roster.
- Genuine failures stop the run after `--max-failures` consecutive occurrences.
  The default and recommended cap is **3**; supervisor configs must not widen it
  to 10.
- Stop conditions are re-checked every cycle by the `done` arbiter: iteration
  cap, wall/token/USD budgets, terminal ledger status, `STOP_REQUESTED` and
  `PAUSE` sentinels, and `require_user_stop_only`.
- Exit codes: 0 stopped cleanly (`done`), 2 bad arguments, 3 max failures,
  4 runtime error, 5 quota waits exhausted, 6 provider binary unavailable,
  7 auth/session dead.
- Overrides: `AAS_AUTOLOOP_BIN_<PROVIDER>` (binary), `AAS_AUTOLOOP_ARGS_<PROVIDER>`
  (argument template; `{prompt}`/`{dir}` placeholders), `AAS_AUTOLOOP_CMD_<PROVIDER>`
  (full shell template; `{prompt}` is inserted shell-quoted and also exported as
  `AUTOLOOP_PROMPT`).

### Host-owned multi-agent panel (hybrid model)

Panel dispatch is capped persistently at **3 attempts per phase per pending
iteration**. Configure `max_attempts` in `panel.json` or
`standing_orders.panel`; the default is 3. Provider rotation reuses the last
panel artifacts after the cap instead of restarting panel calls.

```bash
# Opt-in host panel around each drive iteration (parent-owned; top-level CLIs)
… drive --dir <loop> --provider codex --panel on

# auto: enable only if panel.json / standing_orders.panel / AAS_AUTOLOOP_PANEL=on
… drive --dir <loop> --provider codex --panel auto

# Standalone smoke / phase (does not start drive)
… panel --smoke --root <project>
… panel --dir <loop> --root <project> --phase strategy_review
… panel --dir <loop> --root <project> --phase target_advice
```

When `--panel on` (or auto-enabled), the v2 cycle is:

1. If pre-dispatch validation requires a new direction, `strategy_review` via
   `panel_parent` writes structured `strategy_advice.v1` under
   `iterations/iterNNN/panel/00_strategy_review/`.
2. The host commits exactly one reviewed active plan.
3. One primary agent executes the plan's bounded action (the prompt forbids
   nested panel CLIs).
4. The proposed record is staged as `iteration_candidate.json`; the ledger has
   not advanced yet.
5. `result_review` writes structured `result_review.v1` under
   `panel/03_result_review/` using a reviewer family different from the primary.
6. The host atomically accepts or rejects the candidate, then emits notify from
   finalized state.

Legacy unmigrated loops retain the earlier `target_advice` flow. In enforce
mode, unstructured advice, unavailable required review, same-family-only review,
or substantive result-review disagreement cannot be converted into a banked
success. Each passing reviewer must cover the candidate's complete exact claim
and obligation sets; split coverage across reviewers is not unanimity.

Config (`<loop>/panel.json` or `loop_state.standing_orders.panel`):

```json
{
  "enabled": true,
  "providers": ["claude", "codex", "codewhale"],
  "exclude_until_credit": [],
  "timeout_mode": "adaptive",
  "timeouts": {"target_advice": 600, "result_review": 900},
  "timeouts_by_provider": {"kimi": {"mult": 1.5}},
  "timeout_calc": {"min_s": 120, "max_s": 2400, "size_free": 4000},
  "require_different_family": true,
  "anti_deadlock_math_without_panel": true
}
```

Default panel invite order (when `providers` is omitted):
`codex, claude, codewhale`. `deepseek` remains an alias for the
CodeWhale/DeepSeek CLI. CodeWhale/DeepSeek is pinned to the official DeepSeek
endpoint and Codex to its built-in OpenAI provider; endpoint/provider override
variables invalidate enforce-mode family attribution. Kimi and generic
multi-provider gateways remain unverified.

`timeout_mode` is `adaptive` (default) or `fixed` (legacy: same cap for every
provider). Adaptive budgets scale by prompt size, provider multiplier, and
recent successful `elapsed_s` under the loop dir, then clamp to
`timeout_calc.min_s` / `max_s`. CLI `panel --timeout N` with adaptive mode
raises the phase **base floor** (`base = max(base, N)`), not a hard exclusive
cap. Panel budgets are independent of drive `--iteration-timeout`.

`exclude_until_credit` (and alias `exclude_providers`) names providers the host
panel **must not invite**. Use when a CLI is usage-limit / credit exhausted so
dispatch does not thrash it every cycle. Env
`AAS_AUTOLOOP_PANEL_PROVIDERS=claude,codewhale` still overrides the invite list
for a session.

Env: `AAS_AUTOLOOP_PANEL=on|off`, `AAS_AUTOLOOP_PANEL_PROVIDERS=claude,codex,…`.
Notify remains orthogonal. Banking still requires host evidence gates.
In enforce mode, external panel calls require the separate explicit consent
`AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS=allow`; absent consent produces no provider
spawn. Consent authorizes the complete bounded goal/registry/plan brief and any
candidate evidence included in the requested review to leave the host.

### Notify v2 progress body (drive / watch / supervisor)

New progress events use `aas.autoloop.notify.v2` **schema_version `2.1`**.
Runtime code supplies finalized facts, `notify_v2` validates and renders them,
and `remote-bridge` chooses transport. Notification failure never changes loop
truth. The body layout is **pack-owned** (not a per-loop template file); see
`canonical/templates/arl-notify-v2.md`.

**Profiles** (`presentation.body_profile`, default `operator_full`):

| Profile | Body |
|---|---|
| `operator_full` | Status + Event time early; research sections; trailer; **omit** empty/sentinel fields |
| `operator_compact` | Title, Status, Event time, Completed/Current/Plan, Progress; errors only when present |
| `legacy` | Prior v2.0 lead (Goal/Completed/Current/Plan); omit-empty still applies |

Select profile via `notify.json` `body_profile`, then
`standing_orders.notify.body_profile`, then
`AAS_AUTOLOOP_NOTIFY_BODY_PROFILE`. Stored on the envelope so remote-bridge
re-renders with the same profile. Install **both** ARL and remote-bridge
`notify_v2.py` copies (must hash equal). Wait ticks
(`strategy_review_wait` / `goal_focus_wait` / `result_review_wait`) are local
progress only — not remote. Zulip does not backslash-escape `_`.

Every Markdown, plain-text, Telegram HTML, and compact rendering includes when informative:

| Field | Required meaning |
|---|---|
| Title | Research-specific identity plus iteration/event outcome; do not use a generic `loop` title when a goal/title exists. |
| Status | Separate iteration, result-review, and loop status (promoted early in `operator_full`). |
| Event time | `occurred_at` wall-clock for this notification (always when known). |
| Progress | Iteration budget used/remaining and plain-language goal/obligation progress. |
| Started | `iteration.started_at` only; **omit** if unknown — never invent from finish time. |
| Finished | Finish timestamp and duration for terminal statuses; **omit** for running/waiting/paused. |
| Executor | Primary provider that performed the attempted iteration. |
| Driver agent | Driver agent/provider actually used, with model/family when recorded. |
| Panel agents | Panel agents/providers that actually returned usable work; never just the configured invite list. |
| Other agents | Any additional participating agent roles/providers. |
| Compute | Explicit structured compute provenance when reported. |
| Runtime errors / Review failures | From `issues` tri-state; omit empty or unreported. |
| **Goal** | What the main research problem is (omit if empty). |
| **Completed** | What was finalized when material. |
| **Results** | Banked claim ids/gists when present. |
| **Current** | Where the research stands now. |
| **Decision** / **Decision reason** | Ledger decision when set (omit pending noise on waits). |
| **Plan** | The next bounded action. |

Iteration status is one of `running`, `success`, `failure`, `error`, `waiting`,
`paused`, or `not_applicable`. Review status is `not_required`, `pending`,
`passed`, `failed`, or `error`. Keep operational errors distinct from a
different-family review rejection.

Compute is never inferred from prose, paths, or filenames. An explicit no-compute
event has `compute.reported: true` with an empty `runs` list; a legacy event with
no provenance has `reported: false`. Each run names `local`, `hetzner`, `kaggle`,
`modal`, `github-actions`, or a safe `other:<slug>`, plus status
`succeeded|failed|cancelled|unknown` and optional job/timing details.

Agent provenance is likewise explicit. Each `driver`, `panel`, and `other`
group has `reported` and `agents`: a reported empty list means that role was not
used, while `reported: false` means legacy/unreported.

Research identity resolution still prefers `research_title` (or its aliases)
from loop notification/failover config, then title env, then the goal, and only
then a non-generic directory name. The stable topic slug derives from the
configured job/topic slug or that title; the string
`optional-stable-zulip-topic-id` is a schema placeholder, never an operator
title.

Use `notify-event` for an explicit structured event:

```bash
… notify-event --dir research/run --event iteration_ok \
  --completed "Verified bridge lemma B and banked its evidence." \
  --current "One terminal obligation remains open." \
  --plan "Test the registered falsifier for approach A3." \
  --iteration-status success --review-status passed \
  --provider claude \
  --compute-run '{"service":"hetzner","status":"succeeded","job_ref":"job-123"}'
```

Legacy flat progress fields remain alongside the v2 envelope during migration.
The compatibility converter never invents a banked result or compute usage.
Deduplication uses the structured event and rendered body; remember a delivery
fingerprint only after `remote-bridge` reports success. Telegram rendering is
bounded and escapes HTML without dropping the mandatory blocks. Before any
transport or dry-run output, configured transport secrets and common credential
forms are recursively redacted from every nested event string, including
unrendered extension values and event ids, while retaining non-sensitive
provenance. Secret-bearing event ids become deterministic opaque ids before
transport results or dedupe state are produced. Enforce-mode external delivery
requires `AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS=allow`; otherwise notification is
local-audit only. HTTP transports require HTTPS, reject URL credentials and
redirects, and allow localhost HTTP only behind its explicit development opt-in.
Common explicit PII forms are redacted from notification values. External panel
prompts are instead refused when PII is detected unless
`AAS_AUTOLOOP_EXTERNAL_PII_APPROVAL_SHA256` matches the SHA-256 of the exact
outbound prompt bytes; finding reports name categories, never matched values.

### Primary failover supervisor (optional pack)

**Default multi-OS path:** `force-loop/` kit (`bootstrap` / `start` / `drain`).
The shell supervisor below is the POSIX failover detail used by that kit when
present, and remains available as an advanced direct entry.

For multi-day multi-provider runs, use the runtime support files (installed with
this skill):

- `force-loop/` — **default** scripted force-loop (all OS; enforce/hard/notify)
- `supervisor_README.md` — full compose notes
- `LAUNCH_supervisor.sh start|replace` — flock (`start` refuses if held → exit 10;
  `replace` stops prior supervisor+drive then starts); POSIX advanced
- `arl_drive_supervisor.sh` — rotates on drive exit 5/6/7; session-excludes;
  empty order → exit 11; restart cap → exit 12
- `{loop}/failover.json` from `failover.example.json` (`primary_order`,
  `research_title`, `max_quota_waits_per_primary`; multi-primary + waits 0 refused)

Default example `primary_order` (drive failover):  
`claude, codex, grok, opencode, antigravity, copilot, kimi, deepseek`.

**Failover rule:** the supervisor always uses the **first available** provider in
`primary_order` (skipping session-excluded names). A failed primary is excluded
for the rest of the run; the next start is again the first remaining entry.

Stock `drive` does **not** read `primary_order`; only the supervisor does.

### Formal policy (optional Lean assist)

Default **off** (no formal headings in `iteration_prompt`). Opt in with CLI,
env, `formal/formal_policy.json`, or `loop_state.standing_orders.formal`.

```bash
… init --dir <loop> --goal "…" --success-criteria "…" --formal-policy on --formal-project formal/
… drive --dir <loop> --provider codex --formal-policy on
# host hygiene tick after each ok iteration (non-terminal; scan-first; no OpenGauss spawn):
… drive … --formal-policy force --formal-force-after-iteration
```

| Flag / env | Role |
|---|---|
| `--formal-policy` / `AAS_AUTOLOOP_FORMAL_POLICY` | `off\|mention-only\|auto\|on\|force` |
| `--formal-project` / `AAS_AUTOLOOP_FORMAL_PROJECT` | Lake project path (default `formal/`) |
| `--formal-force-after-iteration` / `AAS_AUTOLOOP_FORMAL_FORCE=1` | enable host tick when policy is `force` |
| `--formal-typecheck` / `AAS_AUTOLOOP_FORMAL_TYPECHECK=1` | opt-in Lake typecheck inside host tick |
| `--formal-force-credits` | credit budget for force tick (default 3) |

At **drive start**, the host resolves policy, writes `formal/host_policy.pin.json`,
persists privileged keys into `standing_orders.formal`, and exports
`AAS_AUTOLOOP_FORMAL_*` into the child env. Prompt order:
`compute_policy` → panel (if) → authoritative `goal_focus` when active (otherwise
legacy `goal_priority`) → `formal_policy` (empty when off).

**`formal_force_tick`** (after `iteration_ok`, only `force` + flag): writes
`formal/force_loop_reports/*`; never sets loop `blocked`/`stopped`; never
launches OpenGauss; never sets `claim_support_status=supported`. Missing Lake
→ `tool_unavailable`; drive continues.

**Glossary:** headless force-driven ARL ≠ `formal_policy=force`. Default
scripted force-loop pack: `force-loop/` (and discovery template
`arl-scripted-force-loop`). Thin formal-env sample only:
`canonical/templates/sample-arl-headless-driver-with-formal/`. Instruction:
`canonical/instructions/autonomous-loop-formal-policy.md`.

### Goal Focus v2 (enforceable direction control)

> **Current deployment status:** authoritative state, migration, selection,
> staging/review/finalization contracts, and both execution profiles are
> available. `AAS_AUTOLOOP_PROVIDER_TRANSPORT=trusted-local` explicitly enables
> attested Claude/Codex primaries and Claude/Codex/CodeWhale panels on a trusted
> project. Every such process is constrained by a dedicated systemd/cgroup
> scope, inherited POSIX limits, bounded output, wall timeout, and descendant
> cleanup. Omission or `strict-isolated` still denies before provider creation;
> strict isolation remains unavailable until credential blindness, filesystem
> allowlisting, and endpoint-constrained egress are implemented. Trusted-local
> makes no hostile-process containment claim.

Goal Focus v2 uses four authoritative loop files:

| File | Runtime role |
|---|---|
| `goal_contract.json` | Revisioned goal, criteria, scope, and obligation DAG. |
| `approach_registry.json` | Revisioned campaigns/approaches, estimates, eligibility, and reopen conditions. |
| `current_plan.json` | One selected bounded direction and its goal/registry/plan revision pins. |
| `direction_decisions.jsonl` | Idempotent append-only decision provenance. |

The selected plan projects into `loop_state.next_preferred_path`,
`loop_state.goal_focus_projection`, and a managed `recovery.md` block for legacy
readers. Those are derived views, never competing sources of truth.

Provider-process trust is a separate axis. `trusted-local` is an explicit
operator opt-in and never follows from `enforce`; invalid or omitted transport
values resolve to `strict-isolated`. Trusted-local requires Linux bubblewrap,
a functional user systemd manager, cgroup memory/swap/task/CPU/runtime limits,
and POSIX address-space/CPU/open-file/file-size/core limits. Invalid settings or
an unavailable backend fail before registration/provider spawn.

Before enabling trusted-local, the operator must pin every provider that may be
used. For each uppercase provider key (`CLAUDE`, `CODEX`, or `CODEWHALE`), set:

```bash
export AAS_AUTOLOOP_ATTESTED_BIN_CLAUDE=/exact/absolute/real/path/to/claude
export AAS_AUTOLOOP_ATTESTED_SHA256_CLAUDE=sha256:<64-lowercase-hex-digest>
export AAS_AUTOLOOP_ATTESTED_UPSTREAM_CLAUDE=anthropic
export AAS_AUTOLOOP_ATTESTED_MODEL_CLAUDE=claude-fable-5
```

Use upstream `openai` for Codex and `deepseek` for CodeWhale. The optional
`AAS_AUTOLOOP_ATTESTED_DEPENDENCY_ROOT_<PROVIDER>` must be an exact absolute
real path containing the executable; otherwise the runtime infers the package
root. Compute the executable digest only after resolving any launcher symlink,
and review/re-pin after every provider update. The runtime hashes the bounded
dependency closure and revalidates the complete identity immediately before
each spawn; a command override or endpoint-family override still fails closed.
The tree must also be host-controlled: the executable and every file under
the dependency root must be owned by the invoking user or root, with no
group/other write bits and no symlinks, and ancestor directories must be
symlink-free and not group/other writable.
npm-installed CLIs commonly ship `775`/`664` modes and fail this gate
("provider executable parent is group/other writable" or "provider dependency
is not host-controlled"); fix with `chmod -R go-w` on the package root, which
leaves the pinned digests unchanged, and re-check after reinstalls.

Trusted-local resource defaults are 4 GiB primary / 3 GiB panel memory, zero
swap, 64 GiB address space, 100% aggregate CPU quota, 128 primary / 64 panel
tasks, 1024 open files, 4 GiB per-file size, zero-byte core dumps, a 16 MB
combined captured-output ceiling, and the phase wall timeout plus a 15-second
scope lifetime margin. Override them only with the validated
`AAS_AUTOLOOP_RESOURCE_*` integer variables. A root-owned pre-exec gate reads
the process's actual cgroup leaf and inherited limits before executing the
provider. Inside containment, the cgroup API, user service manager, container
control sockets, live tmux/Screen control paths, and related launch planes are
masked. Cleanup, complete prompt delivery/capture, timeout, output-size, and
sensitive-output status are host-attested and revalidated before staging or
banking. `RLIMIT_FSIZE` is per file; this profile does not claim an aggregate
disk quota, credential blindness, a host-filesystem allowlist, or constrained
network egress.

`current_plan.enforcement_mode` is `off`, `monitor`, or `enforce`. New v2 loops
default to `enforce`; existing loops stay legacy until explicit migration. In
enforce mode the driver runs a pre-dispatch gate that:

1. recovers `.goal_focus_transactions/` journals;
2. reconciles managed views;
3. validates cross-file revisions and plan/ledger agreement;
4. blocks when `iteration_candidate.json` awaits review; and
5. blocks visibly on an unresolved `iteration_dispatch.json`; and
6. requests structured `strategy_advice.v1` when a replan trigger fires.

Triggers include missing/non-active plans, ineligible selections, trip wires,
panel dissent, plan/estimate expiry, unreviewed counterevidence, three iterations
without global obligation reduction, and three scope-only iterations. Monitor
mode reports ordinary drift/replan signals instead of enforcing either the
active-plan or review-before-bank gate; it preserves legacy banking semantics
and must not be treated as an acceptance guarantee.

The host filters ineligible routes, scores interval estimates, and commits one
reviewed direction with compare-and-swap. A robustly dominant lower bound selects
exploitation; overlapping intervals select a bounded informative experiment
from at most three diverse approaches. Every direction commit, including
retention and campaign/approach switches, requires a different-family review.
The review is bound to complete goal/registry/plan objects, their semantic
fingerprints, and exact source hashes, so same-revision mutation invalidates it.

In enforce mode, `append-iteration` stages the proposed record rather than
directly banking it. A different-family `result_review.v1` must pass before one
atomic transaction appends the accepted row, applies budget/control changes,
archives the candidate, and removes the pending file. If review is unavailable
or operationally errors, the candidate stays pending, no finalized-attempt
budget is charged, and the driver launches no new work.

The driver first persists `iteration_dispatch.json`, binding a known executor
family and candidate id to canonical full-object plan/goal/registry
fingerprints and revisions. Enforce-mode staging requires and consumes that
exact live intent. Provider-family failover requires replan;
unverified/custom primaries and provider command/argument/binary overrides fail
closed. Standard `AAS_*_LATEST_MODEL` and highest-thinking pins remain
supported. Every material result needs an explicit
claim id plus a safe loop-relative evidence path, and reported compute services
are checked against the reviewed plan allow/deny policy. The host no-follow
reads and size-bounds every regular UTF-8 evidence artifact, then embeds its
complete content, path, size, and digest in the immutable candidate; an
identifier alone is not evidence.

Each `result_review.v1` binds the canonical fingerprint of the complete pending
candidate. Accepted obligation reviews must match the exact requested target
status and cite at least one staged evidence id the reviewer lists in
`inspected_paths`; supported claims have the same inspected-evidence
requirement. Finalization rechecks those
bindings and applies hash/revision compare-and-swap, so a reviewer-side or
same-revision authority mutation cannot be banked.

Trusted-local real panels are available only through the explicit transport
opt-in described above. Their provider-native prompt-only flags disable shell,
filesystem, MCP, browser, memory, custom-instruction, and subagent tools, while
the host applies executable attestation, resource limits, bounded capture, and
review-schema validation. This profile still trusts the local CLI and its host
view. The stronger strict-isolated design additionally requires an allowlisted
runtime/provider filesystem, immutable interpreter closure, credential
blindness, and endpoint-constrained egress; because that complete boundary is
not yet implemented, strict-isolated/default real panels fail closed before
spawn. Injected runners remain available only for offline contract tests.

Rejected candidates are finalized as `bank_status: rejected`: their real
iteration/token/USD deltas count against budget and their compute/time/executor
provenance is retained, but claim ids, claims, obligation transitions,
`campaign_delta`, and `global_delta` are cleared. The default next plan state is
`needs_replan`.

Use the `goal-focus` command family for status, validation, reconciliation,
replan, and migration. Migration is non-mutating by default:

```bash
… goal-focus migrate --dir <loop> --dry-run
… goal-focus migrate --dir <loop> --apply
… goal-focus recover-dispatch --dir <loop>
… goal-focus recover-dispatch --dir <loop> --cancel --dispatch-id <exact-id>
… goal-focus recover-quarantine --dir <loop>
… goal-focus recover-quarantine --dir <loop> --release --candidate-fingerprint sha256:<exact-digest>
```

If a candidate exists after a timeout, invalid resource attestation, rejected
capture/output, cleanup failure, or another failed host completion gate, the
runtime atomically quarantines it and the supervisor stops without retry or
failover. Automatic review and banking remain blocked across restart. Inspect
the status output and evidence before using the exact fingerprint-bound release;
release archives the quarantine and does not change the research ledger or
budget.

Dry-run exposes every direction signal and refuses to select a route from
ambiguous current-path/recovery/ledger/audit evidence. Apply may preserve that
ambiguity as an unselected `needs_replan` plan. It first copies existing
`goal_priority.json`, `loop_state.json`, `budget.json`, `iterations.jsonl`, and
`recovery.md` plus any hard-replan audit under timestamped
`.goal_focus_backups/`, then creates the v2 contracts and migration decision
transactionally. Apply refuses a live owning driver, and proposal, backup, and
commit share exact source hashes/absence checks; concurrent mutation returns
`source_changed` before any v2 write. Apply holds an atomic migration claim;
current-runtime drivers check it before and after registration and remove a
raced registration before refusing to start. A safely parsed claim owned by a
dead local PID may be reclaimed. Quiesce a live driver at an iteration boundary,
apply, reconcile, validate, and only then restart.

### Legacy `goal_priority.v1` compatibility

File `{loop}/goal_priority.json` or `loop_state.standing_orders.goal_priority`.
Executable v1 defaults are `"enabled": false` and
`"discipline_mode": "soft"`. Explicit advise/hard mode adds bare-`advance`
warnings, plus v1 goal text on loops that are not Goal Focus `enforce`. Opt out
with `"enabled": false` or
`AAS_AUTOLOOP_GOAL_PRIORITY=off`; env `on` forces enable only when a config
object exists.

V1 injects campaign/goal-EV text, derives local-without-goal-delta streaks, and
emits validation warnings. It does not enforce v2 pre-dispatch consistency or
review-before-bank. Append compatibility fields with:

```bash
… append-iteration --dir <loop> --mode bounded-research --objective "…" --decision continue \
  --goal-contribution advance --campaign-id main
```

Optional: `--local-without-goal-delta`, `--local-without-goal-delta-tag`, and
`init --goal-priority-template` (refuses overwrite unless `--force`). Reference
template: `canonical/templates/goal-priority.md`; v2 reference:
`canonical/templates/goal-focus.md`. V1 does not execute `success_check` in
`done` and does not expand recovery rewrite on append.

The default flag sets grant the agent full tool autonomy, which unattended
research requires; run loops only in workspaces you trust the agent to modify,
and prefer a dedicated project root. Interactive forcing is separate: on Claude
the installed `hooks.Stop` entry blocks turn-end while an ARMED loop (`arm
--dir <loop> --root <project>`) is unfinished; the other targets have no Stop
hook and are governed by the driver alone.

For an early proof/success stop, at least one `--evidence-id ID` must resolve to
`proof_artifacts/ID.json` inside the loop directory. Early proof/success stop
reasons are `success`, `success_criteria_met`, `proof`, `proof_found`,
`found_proof`, and `proved`. The artifact id must be 1-128 characters of
letters, digits, underscore, hyphen, or dot and must start with a letter or
digit. The JSON artifact must include:

```json
{
  "schema_version": "1.0",
  "id": "proof-artifact-1",
  "artifact_type": "lean",
  "machine_checkable": true,
  "target": "the theorem or success target",
  "proof_path": "proofs/theorem.lean",
  "checker": {
    "name": "lean",
    "status": "passed"
  }
}
```

The helper checks that the artifact exists, `id` matches the evidence id,
`schema_version` is `1.0`, `machine_checkable` is `true`, `artifact_type` is
one of `lean`, `coq`, `isabelle`, `agda`, `sagemath`, `python-verifier`, or
`external-verifier`, `checker.name` is non-empty, `checker.status` is `passed`,
`target` is non-empty, and `proof_path` is an existing relative file within
the loop directory. It does not run Lean, Coq, SageMath, or another checker
itself.

On Windows, use the installed runtime runner with the native launcher target:

```powershell
& "$env:AAS_RUNTIME_ROOT\run_skill.ps1" skills/autonomous-research-loop-runtime/run_autonomous_research_loop.ps1 selftest
```

## Guarantees

The helper:

- uses only the Python standard library
- does not require network access for ledger, arbiter, probe, or selftest work
- does not install packages
- does not start servers
- does not write configuration outside the selected loop directory (the driver
  additionally writes iteration logs under the loop's `driver_logs/`)
- ledger subcommands, `done`, `hook-check`, `agent-cmd`, and `selftest` never
  call Codex, Claude, Copilot, DeepSeek, or other provider CLIs; only `drive`
  executes the iteration command the operator selected (via `--cmd` or
  `--provider`), which is the entire point of the headless driver
- when `--panel` is enabled, `drive` and the `panel` subcommand may also invoke
  configured panel provider CLIs as **top-level** host-parent processes (not
  nested under the primary agent sandbox)
- does not spawn unbounded recursive multi-agent trees

Use the canonical `autonomous-research-loop` skill for orchestration policy and
this helper only for local ledger mechanics. This helper validates that an
early proof stop points to a passed machine-checkable proof artifact record; it
does not independently validate the semantic truth of the proof.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
- `autonomous-research-loop-portfolio-runbook` -- Open-problem, portfolio-first variant of the autonomous research-loop runbook: a rigorous definition-of-done with an insufficient-result disqualification list, an approach registry with blocked-route discipline, and an adversarial audit gate with a concrete-deliverable requirement, keeping the same four stop conditions, cross-agent verification, fresh-agent backtracking, and five-lane broker-routed heavy-compute offload with per-lane safety gates.
- `arl-scripted-force-loop` -- discovery doc for the **default** cross-platform force-loop kit (runtime `force-loop/`).
- `goal-focus` -- Goal Focus v2 authoritative-state, strategy-review, stage/review/finalize, migration, and Notify v2 reference.
- `goal-priority` -- legacy `goal_priority.v1` reference (defaults disabled/soft).
- `sample-arl-headless-driver-with-formal` -- thin formal-env layer only (not the force-loop default).
- `informal-to-lean-formalization-runbook` -- F1–F7 positions when formal-track.
