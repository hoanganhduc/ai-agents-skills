---
name: autonomous-research-loop-runtime
description: Runtime helper for autonomous-research-loop ledgers. Use to initialize, append, validate, inspect, or smoke-test autonomous research loop state files without network, package installation, provider CLI calls, or live agent spawning.
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
otherwise); and `drive` is the cross-platform headless driver that runs one
iteration per loop until `done` (the POSIX `autoloop_driver.sh` is a thin shim
that delegates to it).

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
- does not require network access
- does not install packages
- does not start servers
- does not write configuration outside the selected loop directory
- does not call Codex, Claude, Copilot, DeepSeek, or other provider CLIs
- does not spawn subagents

Use the canonical `autonomous-research-loop` skill for orchestration policy and
this helper only for local ledger mechanics. This helper validates that an
early proof stop points to a passed machine-checkable proof artifact record; it
does not independently validate the semantic truth of the proof.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `autonomous-research-loop-runbook` -- Bounded autonomous research-loop runbook with four stop conditions, single-path solving, mandatory cross-agent verification, fresh-agent backtracking, and Modal/GitHub Actions credit-gated heavy-compute offload.
