---
name: formal-skeleton-helper
description: Use when the user wants a minimal Lean-style theorem skeleton, namespace wrapper, or generated formal statement stub.
---

# Formal Skeleton Helper

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
