---
name: formal-skeleton-helper
description: Use when the user wants a minimal Lean-style theorem skeleton, namespace wrapper, or generated formal statement stub.
---

# Formal Skeleton Helper


## Windows Runtime Commands

On native Windows, use the managed Windows runner and the native runtime command target. Set `$runtime` to the installed runtime root. Multi-agent installs usually use `%LOCALAPPDATA%\ai-agents-skills\runtime`. Then run:

```powershell
$runtime = if ($env:AAS_RUNTIME_ROOT) { $env:AAS_RUNTIME_ROOT } else { "$env:LOCALAPPDATA\ai-agents-skills\runtime" }
& "$runtime\run_skill.bat" "skills/formal-skeleton-helper/run_formal_skeleton.bat" <args>
```

POSIX examples below use `run_skill.sh` and `.sh` command targets; use the Windows command target above on native Windows.

Use this skill to turn an informal theorem, lemma, definition, or proof target
into a small formalization scaffold. The goal is a useful skeleton, not a
claimed complete proof.

## Workflow

1. Extract the intended claim name, imports, namespace, variables, hypotheses,
   conclusion, and any preferred notation.
2. State assumptions before generating the skeleton when mathematical types or
   libraries are ambiguous.
3. Produce the smallest useful Lean-style scaffold:
   - imports
   - namespace
   - variables
   - theorem or lemma statement
   - placeholder proof such as `by sorry`
4. Separately list blockers, missing definitions, likely library lemmas, and
   mathematical ambiguities.

## Output Rules

- Use stable names and avoid inventing large surrounding APIs.
- Prefer a conservative statement over an overfit one.
- Do not claim the code compiles unless it was actually checked.
- If Lean is unavailable, label the output as an unchecked skeleton.

## Verification

When a Lean environment is available and the user wants a checked artifact, run
the project-local Lean command or ask for the project build command. Otherwise
return the skeleton with explicit unchecked status.

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `informal-to-lean-formalization-runbook` -- Local-first intake mapping an informal proof to Lean declarations with a scanner-first verification gate separating typecheck status from claim support.
