---
name: opengauss
description: Use when preparing optional Math Inc. OpenGauss readiness for Lean prove/formalize workflows in the formal research lane. Inert doctor and config guidance only; never auto-installs or claims formal proof from Gauss success.
metadata:
  short-description: Inert OpenGauss readiness and evidence policy for formal lane
---

# OpenGauss (Math, Inc.)

## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/opengauss/run_opengauss.bat" doctor
& "$runtime\run_skill.bat" "skills/opengauss/run_opengauss.ps1" doctor
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

## What this skill is

Optional **inert** helper for the formal lane around [OpenGauss](https://www.math.inc/opengauss) ([GitHub](https://github.com/math-inc/OpenGauss)): a project-scoped Lean workflow orchestrator (`gauss`) that can run prove/draft/formalize workflows via claude-code or codex backends.

This skill:

- reports local readiness (`doctor`) without executing `gauss`, `lake`, or backends
- emits manual install / Morph / WSL snippets (`config-snippet`) with placeholders only
- provides offline smoke (`smoke` / `selftest`)

This skill does **not**:

- install OpenGauss, Lean, Mathlib, or backends
- start `gauss`, tmux sessions, or swarms
- write `~/.gauss` config or read secret values
- promote research claims to “proved” from a Gauss job

Live install is **manual-native**. Unattended auto-launch is **out of scope until** a `headless_qualified` feasibility spike (see plan Phase 1).

## Runtime Helper

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/opengauss/run_opengauss.sh doctor
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/opengauss/run_opengauss.sh config-snippet
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/opengauss/run_opengauss.sh smoke
```

### Live readiness / prove-path smoke (opt-in)

Offline CI stays on `smoke` / `doctor`. Live coverage is **explicit**:

```bash
# 1) Tool/PATH/project readiness (no /prove, no claim-support)
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/opengauss/run_opengauss.sh live-preflight \
  --project-root /path/to/lean-project \
  --run-gauss-doctor

# 2) Backend ping + optional short gauss chat probe (LLM; costs quota)
# NEVER set this in default CI.
export AAS_OPENGAUSS_LIVE_PROVE=1
bash "$AAS_RUNTIME_ROOT/run_skill.sh" \
  skills/opengauss/run_opengauss.sh live-prove-smoke \
  --project-root /path/to/lean-project \
  --backend claude-code \
  --timeout-sec 180
# optional: --attempt-prove
```

Success of `live-prove-smoke` means the **backend path responded**, not that a theorem is proved.
Record as `opengauss_run` provenance only; still require Lake + strict Lean gate for formal claims.

PATH must include `~/.local/bin` (and preferably `~/.npm-global/bin`) so OpenGauss can find `claude`.

## When to use in research

| Situation | Action |
|-----------|--------|
| Literature / discovery only | Stay in source-research / deep-research; may **mention** formal candidates |
| Stable lemma + intake `proceed` + Lake project | Skeleton → optional OpenGauss fill (Phase 1+) → **strict gate** |
| Paper review only | Tag `formal_candidates`; do not launch Gauss unless user asked to formalize |
| Missing `gauss` | `tool_unavailable` / defer — **not** failed theorem evidence |

Pipeline (after live invoke exists):

```text
lean-formalization-intake (proceed)
  → lean-explore-mcp (reuse first)
  → formal-skeleton-helper
  → OpenGauss /prove or /draft (opengauss_run provenance)
  → lean-strict-verification-gate
  → lead/human statement-equivalence for claim support
```

## Evidence policy

- Record harness output as **`opengauss_run`** (provenance only).
- Never treat Gauss success as `formal_check` or claim-support by itself.
- Local formal status uses `lean-strict-verification-gate` and existing deep-research claim-support statuses.
- Forbidden language: “OpenGauss proved …”, “fully formalized” with open sorry/axioms, equating job OK with informal claim C.

See `references/evidence-policy.md`.

## Platform notes

| Host | Skill install | Live Gauss |
|------|---------------|------------|
| Linux | yes | primary (when installed) |
| macOS | yes | experimental until dated evidence |
| WSL | yes | supported Windows path (same distro as AAS+Lean) |
| Native Windows | helper yes | **unsupported** — use WSL2 or Morph |

Details: `references/local-install.md`, `references/windows-wsl.md`, `references/morph-cloud.md`.

## Commands (MVP documented workflows)

After a real OpenGauss install (not this helper):

- Prefer `/prove` and `/draft` for guided work
- Gate `/swarm`, unbounded `/autoprove`, `/autoformalize` behind budgets and later auto policy
- Always run strict verification after harvest

## Supporting references

Open only when needed:

- `references/evidence-policy.md`
- `references/local-install.md`
- `references/windows-wsl.md`
- `references/morph-cloud.md`


## Manual live workflows (Phase 1)

After installing OpenGauss yourself (see `references/local-install.md`):

1. Register a Lake project (`gauss` `/project init` or existing `.gauss/project.yaml`).
2. Prefer `/prove` and `/draft` only for MVP.
3. Harvest Lean paths, then run `lean-strict-verification-gate`.
4. Record `opengauss_run` evidence via the helper harvest shape or handoff-gate JSON — never claim-support alone.

Handoff helpers (offline JSON):

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/opengauss/run_opengauss.sh \
  handoff-intake --claim-id C1 --informal-statement-ref claims/C1.md --project-root /path/to/lean
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/opengauss/run_opengauss.sh \
  handoff-gate --run-id manual-1 --project-root /path/to/lean --workflow prove --gauss-exit success
```

## Feasibility spike (Phase 1)

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/opengauss/run_opengauss.sh \
  spike --work-dir /tmp/og-spike
```

Outcomes: `headless_qualified` | `interactive_only` | `failed`.  
Default probe is **non-executing** and will not invent headless success. Operator-dated headless evidence is required before auto mode.

## Adapter verbs (Phase 3 — fail-closed)

```text
preflight | launch | status | harvest | kill
```

- `preflight` requires `spike_report.json` with `outcome=headless_qualified` and host headroom.
- `launch` **refuses** to spawn gauss until a documented headless driver exists (even if spike is forced).
- Use interactive Gauss manually; use `harvest` to emit provenance-only `opengauss_run` evidence stubs.

Caps for future auto mode: agent-immutable standing auth; wall/concurrency/attempts; USD advisory unless measurable; host load re-check.
