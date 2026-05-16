---
name: sagemath
description: Use when the user needs SageMath for graph theory, combinatorics, algebra, spectral computations, or mathematical verification beyond what local Python tools can do.
metadata:
  short-description: SageMath execution via Codex runtime
---

# SageMath


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. For Codex-only installs the runtime is usually `%USERPROFILE%\.codex\runtime`; for multi-agent installs it is usually `%LOCALAPPDATA%\ai-agents-skills\runtime`. Set `$runtime` to the installed runtime root, then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } elseif (Test-Path "$env:USERPROFILE\.codex\runtime") { "$env:USERPROFILE\.codex\runtime" } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/sagemath/run_sage.bat" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

This uses the vendored Codex runtime copy of the SageMath workflow.

## When to use

- chromatic polynomial or chromatic number computations on nontrivial graph families
- Tutte polynomial
- automorphism groups and isomorphism-heavy checks
- spectral analysis
- finite fields or polynomial algebra
- exhaustive or batch mathematical verification that is beyond lightweight local Python

For simple checks such as connectivity, bipartiteness, or small ad hoc scripts, prefer local Python first.

## Base path

- `~/.codex/runtime/workspace/skills/sagemath/`

Use the Codex runtime runner rather than invoking `run_sage.sh` directly.

Shared runner:

- `bash ~/.codex/runtime/run_skill.sh`

## Core commands

Use `functions.exec_command`.

```bash
bash ~/.codex/runtime/run_skill.sh skills/sagemath/run_sage.sh "<sage_code>"
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/sagemath/run_sage.sh --timeout 1800 "<sage_code>"
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/sagemath/run_sage.sh --file skills/sagemath/templates/<template>.sage
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/sagemath/run_sage.sh --file skills/sagemath/templates/reconfiguration_check.sage
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/sagemath/run_sage.sh --plot "<sage_code>"
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/sagemath/run_sage.sh --session "<name>" "<sage_code>"
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/sagemath/run_sage.sh --cancel <job_id>
```

## Templates

Common templates in `skills/sagemath/templates/`:

- `enumerate_chromatic.sage`
- `counterexample_search.sage`
- `spectral_analysis.sage`
- `reconfiguration_check.sage`

## Operational notes

- The OpenClaw SageMath job runs inside Docker with no network access.
- Results are returned as JSON.
- Prefer this skill when correctness depends on SageMath-native graph or algebra routines rather than lightweight heuristics.
- Treat this `SKILL.md` and `sage_reference.md` as the primary quick reference for the wrapper; the wrapper’s default interface is execution-oriented rather than documentation-oriented.
