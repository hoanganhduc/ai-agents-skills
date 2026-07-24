---
name: autonomous-research-loop-runtime
description: Runtime helper for autonomous-research-loop ledgers plus headless drive and host-owned multi-agent panel phases (--panel on, auto, or off). Use to initialize, append, validate, inspect, smoke-test, drive, or panel-dispatch loop state without requiring ad-hoc nested multi-agent CLIs from the primary agent.
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
| `antigravity` | `agy -p "<prompt>" --dangerously-skip-permissions` (falls back to `gemini --yolo -p "<prompt>"`) |

`<prompt>` is the standard one-iteration contract: read `recovery.md` and the
ledger, execute the single recorded next action under the loop policy, verify
independently, append exactly one iteration record, refresh the recovery files,
exit. Inspect it with `agent-cmd --provider <p> --dir <loop> --print-prompt`.
OpenClaw is not a driver target (no local agent CLI); drive its loops from a
supported provider instead.

Start an unattended run (POSIX):

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/autonomous-research-loop-runtime/run_autonomous_research_loop.sh drive --dir research/run --provider claude
```

On Windows use `%AAS_RUNTIME_ROOT%\run_skill.bat ... run_autonomous_research_loop.bat drive --dir research\run --provider codex`.
Wrap with `nohup`, `systemd-run`, or Task Scheduler for multi-day runs.

Driver behavior:

- Each iteration's output is captured under `<loop>/driver_logs/`.
- Credit/quota outages (rate limit, 429, out of credits, usage limit, billing)
  detected in a FAILED iteration's output do not count as failures: the driver
  pauses `--quota-backoff` seconds (default 900) and retries, honoring the
  pause-and-wait-for-credits policy. `--max-quota-waits N` caps consecutive
  waits (default 0 = wait indefinitely).
- **Operator policy when a primary is known exhausted:** do not rely on infinite
  `quota_wait` if another funded provider can run. Update
  `panel.json` / `standing_orders.panel` with `exclude_until_credit`, stop the
  drive process, and restart with `--provider <funded>`. Full policy:
  instruction `provider-credit-quota.md`.
- Genuine failures stop the run after `--max-failures` consecutive occurrences.
- Stop conditions are re-checked every cycle by the `done` arbiter: iteration
  cap, wall/token/USD budgets, terminal ledger status, `STOP_REQUESTED` and
  `PAUSE` sentinels, and `require_user_stop_only`.
- Exit codes: 0 stopped cleanly (`done`), 3 max failures, 4 runtime error,
  5 quota waits exhausted, 6 provider binary unavailable.
- Overrides: `AAS_AUTOLOOP_BIN_<PROVIDER>` (binary), `AAS_AUTOLOOP_ARGS_<PROVIDER>`
  (argument template; `{prompt}`/`{dir}` placeholders), `AAS_AUTOLOOP_CMD_<PROVIDER>`
  (full shell template; `{prompt}` is inserted shell-quoted and also exported as
  `AUTOLOOP_PROMPT`).

### Host-owned multi-agent panel (hybrid model)

```bash
# Opt-in host panel around each drive iteration (parent-owned; top-level CLIs)
… drive --dir <loop> --provider codex --panel on

# auto: enable only if panel.json / standing_orders.panel / AAS_AUTOLOOP_PANEL=on
… drive --dir <loop> --provider codex --panel auto

# Standalone smoke / phase (does not start drive)
… panel --smoke --root <project>
… panel --dir <loop> --root <project> --phase target_advice
```

When `--panel on` (or auto-enabled), each cycle is:

1. `target_advice` via `panel_parent` → `iterations/iterNNN/panel/01_target_advice/`
2. primary agent (math only; prompt forbids nested panel CLIs)
3. after ledger advances: `result_review` → `panel/03_result_review/`

Config (`<loop>/panel.json` or `loop_state.standing_orders.panel`):

```json
{
  "enabled": true,
  "providers": ["claude", "codex", "codewhale", "kimi"],
  "exclude_until_credit": [],
  "timeout_mode": "adaptive",
  "timeouts": {"target_advice": 600, "result_review": 900},
  "timeouts_by_provider": {"kimi": {"mult": 1.5}},
  "timeout_calc": {"min_s": 120, "max_s": 2400, "size_free": 4000},
  "require_different_family": true,
  "anti_deadlock_math_without_panel": true
}
```

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

### Goal priority (optional path discipline)

Opt-in file `{loop}/goal_priority.json` or `loop_state.standing_orders.goal_priority`
with JSON boolean `"enabled": true` (or env force-on when a config object
exists). Soft v1: injects campaign / goal-EV text into `iteration_prompt` and
panel target briefs; derives local-without-goal-delta streak; emit validate
**warnings** (key always present; never a new stop). Append soft fields:

```bash
… append-iteration --dir <loop> --mode bounded-research --objective "…" --decision continue \
  --goal-contribution advance --campaign-id main
```

Optional: `--local-without-goal-delta`, `--local-without-goal-delta-tag`,
`init --goal-priority-template` to write an example JSON with `enabled: false`
(refuses overwrite unless `--force`). Reference template:
`canonical/templates/goal-priority.md` (workflow template slug `goal-priority`).
Env: `AAS_AUTOLOOP_GOAL_PRIORITY=on|off`. Does **not** execute `success_check` in
`done` and does **not** expand recovery rewrite on append.

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

```bat
%AAS_RUNTIME_ROOT%\run_skill.bat skills/autonomous-research-loop-runtime/run_autonomous_research_loop.bat selftest
```

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
- `goal-priority` -- Optional goal_priority.v1 reference for soft path discipline.
