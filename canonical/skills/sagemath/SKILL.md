---
name: sagemath
description: Use when the user needs SageMath for graph theory, combinatorics, algebra, spectral computations, or mathematical verification beyond what local Python tools can do.
metadata:
  short-description: SageMath execution via managed runtime
---

# SageMath


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/sagemath/run_sage.bat" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

This uses the managed ai-agents-skills runtime copy of the SageMath workflow.

If `sage` is not on the non-interactive WSL `PATH`, set the executable path
explicitly before running the wrapper. Bash aliases in `~/.bashrc` are not
visible to the Windows runtime wrapper.

```powershell
$env:AAS_SAGE_WSL_DISTRO = "Ubuntu-24.04"
$env:AAS_SAGE_BIN = "/path/to/sage-10.4/sage"
& "$runtime\run_skill.bat" "skills/sagemath/run_sage.bat" "print(2+2)"
```

Alternatively, make `sage` a real executable in WSL:

```powershell
wsl.exe -d Ubuntu-24.04 -e bash -lc 'sudo ln -sfn "$HOME/sage-10.4/sage" /usr/local/bin/sage && sage --version'
```

## When to use

- chromatic polynomial or chromatic number computations on nontrivial graph families
- Tutte polynomial
- automorphism groups and isomorphism-heavy checks
- spectral analysis
- finite fields or polynomial algebra
- exhaustive or batch mathematical verification that is beyond lightweight local Python

For simple checks such as connectivity, bipartiteness, or small ad hoc scripts, prefer local Python first.

## Base path

- `$AAS_RUNTIME_WORKSPACE/skills/sagemath/`

Use the managed runtime runner rather than invoking `run_sage.sh` directly.

Shared runner:

- `bash "$AAS_RUNTIME_ROOT/run_skill.sh"`

## Core commands

Use `functions.exec_command`.

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/sagemath/run_sage.sh "<sage_code>"
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/sagemath/run_sage.sh --timeout 1800 "<sage_code>"
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/sagemath/run_sage.sh --file skills/sagemath/templates/<template>.sage
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/sagemath/run_sage.sh --file skills/sagemath/templates/reconfiguration_check.sage
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/sagemath/run_sage.sh --plot "<sage_code>"
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/sagemath/run_sage.sh --session "<name>" "<sage_code>"
```

```bash
bash "$AAS_RUNTIME_ROOT/run_skill.sh" skills/sagemath/run_sage.sh --cancel <job_id>
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

## Recommended templates

When this skill is involved, consider this workflow template (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `tikz-figure-verification-runbook` -- Bounded draw-compile-verify-redraw loop for a TikZ figure that guarantees it is free of overlap, wrong meaning, and bad layout, with Sage-assisted graph realization and fresh-agent visual confirmation before the strict approval gate.
