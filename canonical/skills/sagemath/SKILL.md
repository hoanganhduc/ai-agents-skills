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
& "$runtime\run_skill.ps1" "skills/sagemath/run_sage.ps1" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

This uses the managed ai-agents-skills runtime copy of the SageMath workflow.

If `sage` is not on the non-interactive WSL `PATH`, set the executable path
explicitly before running the wrapper. Bash aliases in `~/.bashrc` are not
visible to the Windows runtime wrapper.

```powershell
$env:AAS_SAGE_WSL_DISTRO = "Ubuntu-24.04"
$env:AAS_SAGE_BIN = "/path/to/sage-10.4/sage"
& "$runtime\run_skill.ps1" "skills/sagemath/run_sage.ps1" "print(2+2)"
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

- `bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh"`

## Core commands

Use `functions.exec_command`.

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/sagemath/run_sage.sh "<sage_code>"
```

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/sagemath/run_sage.sh --timeout 1800 "<sage_code>"
```

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/sagemath/run_sage.sh --file skills/sagemath/templates/<template>.sage
```

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/sagemath/run_sage.sh --file skills/sagemath/templates/reconfiguration_check.sage
```

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/sagemath/run_sage.sh --plot "<sage_code>"
```

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/sagemath/run_sage.sh --session "<name>" "<sage_code>"
```

```bash
bash "${AAS_RUNTIME_ROOT:-$HOME/.local/share/ai-agents-skills/runtime}/run_skill.sh" skills/sagemath/run_sage.sh --cancel <job_id>
```

## Templates

Common templates in `skills/sagemath/templates/`:

- `enumerate_chromatic.sage`
- `counterexample_search.sage`
- `spectral_analysis.sage`
- `reconfiguration_check.sage`

### Counterexample search output

`counterexample_search.sage` reads `SAGE_PARAM_N_MAX` (default 7) and
`SAGE_PARAM_CONJECTURE`, a Python expression in `G`, from the environment of
the `--file` command above. It re-enumerates every graph Sage's `graphs(n)`
yields for `n` from 1 to `n_max`, one per isomorphism class, and evaluates the
conjecture on each; it does not check a given witness graph. The conjecture is
evaluated by Python `eval` without Sage's preparser, so write `**` for powers
(`^` is XOR).

The JSON it prints, with exit 0, reports:

- `total_enumerated`: the graphs `graphs(n)` yielded.
- `total_evaluated`: the graphs on which the conjecture evaluated without an
  exception. `evaluation_errors` counts the others, so
  `total_evaluated + evaluation_errors == total_enumerated`. A graph whose
  evaluation raised is neither a pass nor a counterexample.
- `evaluation_error_samples`: every evaluation error message,
  `n=<n>: <Type>: <message>`, whole.
- `counterexamples_found`: every graph on which the conjecture evaluated false.
  `counterexamples` lists the first 20, each with `n`, `edges`,
  `chromatic_number` and `clique_number`. When computing those two numbers
  raises, the witness is still listed, with each number not computed left
  `null` and the error in `witness_detail_error`. `counterexamples_truncated`
  is true when more than 20 were found.
- `total_checked`: a deprecated alias of `total_evaluated`, kept for one
  release. It used to count every enumerated graph, including those whose
  evaluation raised.
- `computation_evidence`: a record of the run in the form deep-research
  computation evidence uses. It carries `tool_name`, `tool_version` (Sage's
  `version()`), `input_encoding_ref` (the sha256 of the conjecture text),
  `checked_domain`, `graph_model_assumptions`, `resource_bounds`,
  `enumeration_method`, `timeout_status`, `result_status`, `coverage_status`,
  `evaluated_predicate` (the conjecture text), a one-line `outcome` and
  `limitations` (the bound, one representative per isomorphism class, the
  `eval` semantics, and any truncation or errors).

The block's statuses follow these rules:

- `result_status` is `failed` when any counterexample was found (the `outcome`
  then starts with "conjecture refuted"), `passed` when none was found and no
  evaluation raised, and `partial` otherwise, including a run that enumerated
  no graph because `n_max` is below 1.
- `coverage_status` is `bounded_complete` when no evaluation raised and at
  least one graph was enumerated; the block then also carries an
  `exhaustiveness_argument`. Otherwise it is `partial`.
- A run stopped by the wrapper's `--timeout` prints no JSON and no block.
  Report it as no verdict, with coverage `timed_out`, never as a pass or a
  refutation.

## Operational notes

- The OpenClaw SageMath job runs inside Docker with no network access.
- Results are returned as JSON.
- Prefer this skill when correctness depends on SageMath-native graph or algebra routines rather than lightweight heuristics.
- Treat this `SKILL.md` and `sage_reference.md` as the primary quick reference for the wrapper; the wrapper’s default interface is execution-oriented rather than documentation-oriented.

## Recommended templates

When this skill is involved, consider this workflow template (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `tikz-figure-verification-runbook` -- Bounded draw-compile-verify-redraw loop for a TikZ figure that guarantees it is free of overlap, wrong meaning, and bad layout, with Sage-assisted graph realization and fresh-agent visual confirmation before the strict approval gate.
