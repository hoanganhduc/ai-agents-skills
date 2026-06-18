---
name: behavior-preserving-cleanup
description: Use for a clarity-only pass that must not change behavior — simplifying, renaming, de-duplicating, or restructuring code, configs, research scripts, or prose. Gates on understanding the target before touching it and re-verifies after each change so behavior stays fixed.
metadata:
  short-description: Simplify without changing behavior, behind a comprehension gate
---

# Behavior-Preserving Cleanup

For passes whose only goal is clarity — not new behavior or bug fixes. The risk is
"simplifying" something you do not fully understand and silently changing what it
does. Two guards prevent that.

## When to use

- simplifying or restructuring code, a config, a research script, or prose
- removing duplication or dead-looking content
- any edit that should leave behavior unchanged

## When not to use

- feature work or bug fixes (use `engineering-lifecycle` and `delivery-verification-gate`)
- changes that are meant to alter behavior

## Method

1. **Comprehension gate (Chesterton's Fence).** Before changing the target, state:
   its responsibility, who calls or depends on it, its edge and error paths, why it
   may be written the way it is (check history or `git blame` when available), and
   whether a test or runnable check defines its expected behavior. If any answer is
   unknown, STOP and read more — do not edit yet.
2. **Change in small steps.** Make one behavior-preserving edit at a time.
3. **Verify after each change.** Re-run the defining check (or re-read the behavior)
   after each edit; the behavior must be identical. If you cannot confirm it, revert
   that step.

## Guardrails

- never remove content you do not understand on the assumption it is unused
- if no check pins the behavior, establish one before simplifying
- keep behavior identical — a cleanup that changes output is a defect, not a cleanup

## Recommended templates

When this skill is involved, consider these workflow templates (install via
the `workflow-templates` artifact profile, or `--with-deps` to pull backing skills):

- `engineering-delivery-loop-runbook` -- Bounded build-and-deliver loop runbook: single-path implementation with seen-to-fail proof, cross-agent diff verification, behavior-preserving cleanup, and credit-gated heavy-compute offload.
