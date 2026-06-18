---
name: modal-research-compute
description: Use when a research or engineering task needs automatic heavy-compute routing through the local broker for Modal-backed remote CPU, high-memory CPU, or GPU execution.
metadata:
  short-description: Route heavy compute to Modal or GitHub Actions through the local broker
---

# Modal Research Compute


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. For Codex-only installs the runtime is usually `%USERPROFILE%\.codex\runtime`; for multi-agent installs it is usually `%LOCALAPPDATA%\ai-agents-skills\runtime`. Set `$runtime` to the installed runtime root, then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/modal-research-compute/run_modal_research_compute.bat" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

Use this skill when the task is about:

- exhaustive search
- object enumeration
- counterexample hunting
- large parameter sweeps
- remote execution of generated experiment code
- GPU-suitable document, embedding, reranking, or tensor workloads

This skill is the integration layer for the local `research_compute` broker.

## When to prefer this skill

- the user wants Modal involved automatically
- the local machine is CPU, memory, disk, or GPU constrained for the requested workload
- the workload is long-running enough that remote execution is a better fit

## Core workflow

1. If local resources matter, run `get-available-resources`.
2. Build a broker manifest JSON for the task.
3. Run broker `plan`.
4. If the plan stays within policy, run broker `submit`.
5. Use `wait` and `fetch` to retrieve results and logs back to local storage.

## Runtime commands

Linux (resolve the installed runtime root for the current agent, then call `run_skill.sh`):

```bash
# codex -> ~/.codex/runtime ; claude and other agents -> ~/.local/share/ai-agents-skills/runtime
runtime="${AAS_RUNTIME_ROOT:-}"
if [ -z "$runtime" ]; then
  for candidate in "$HOME/.local/share/ai-agents-skills/runtime" "$HOME/.codex/runtime"; do
    [ -d "$candidate" ] && runtime="$candidate" && break
  done
fi
run() { bash "$runtime/run_skill.sh" skills/modal-research-compute/run_modal_research_compute.sh "$@"; }
```

```bash
run bootstrap                          # one-time: generate config if absent, authenticate gh, check deps, doctor
run doctor                             # broker + config + Modal + (if enabled) GitHub Actions readiness
run plan   /path/to/job.json
run submit /path/to/job.json
run wait   <job_id>
run fetch  <job_id> --dest /path/to/output
```

On the Claude target the documented wrapper forwards to the same runtime, so this is equivalent:

```bash
bash ~/.claude/skills/_run.sh \
  skills/modal-research-compute/run_modal_research_compute.sh doctor
```

Windows:

```powershell
& "$env:USERPROFILE\.codex\runtime\run_skill.bat" `
  "skills\modal-research-compute\run_modal_research_compute.bat" `
  doctor
```

## Operational notes

- The broker is the decision boundary. Do not call Modal directly from the normal Codex flow when the broker can handle the task.
- CPU-heavy combinatorial workloads should default to remote CPU or high-memory CPU, not GPU.
- GPU use should be explicit in the manifest or clearly justified by the workload.
- `doctor` and `plan` work without a deployed Modal app. `submit`, `wait`, `fetch`, and `deploy` need the host to be Modal-ready.
- Linux hosts become Modal-ready after `python3 -m pip install --user --upgrade modal` and `modal token set` or `modal token new`.
- Windows hosts should install `modal` into `%USERPROFILE%\.codex\.venv`; the wrapper adds `%USERPROFILE%\.codex\.venv\Scripts` to `PATH` so broker deploy can find `modal.exe`.
- Broker state persists under the runtime memories tree, while fetched outputs materialize under the caller workspace by default.
- One-time per machine, run `bootstrap`: it generates `research-compute.toml` from the example if absent (never overwriting an existing one), authenticates `gh`, checks deps, and runs `doctor`. Use this to set up a host that does not have the full system installer.
- GitHub Actions ToS compliance: the broker's `gha` lane runs only inside a private research repo, executes that repo's own committed experiment code (parameters are data, never executed), is budget-gated, and is the last automatic backend after local and Modal — never a general compute pool. Configure it under `[gha]` in `research-compute.toml`.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and Modal/GitHub Actions credit-gated heavy-compute offload.
- `engineering-delivery-loop-runbook` -- Bounded build-and-deliver loop runbook: single-path implementation with seen-to-fail proof, cross-agent diff verification, behavior-preserving cleanup, and credit-gated heavy-compute offload.
