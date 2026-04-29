---
name: workspace-rearranger
description: Use when the user wants to organize, rearrange, sort, or clean up workspace files with safe dry-run and explicit apply behavior.
---

# Workspace Rearranger

Use this skill to plan safe file organization. It is intentionally conservative:
preview first, apply only with explicit confirmation, never delete silently, and
never overwrite unmanaged files.

## Workflow

1. Inspect the workspace boundary and any project instructions.
2. Identify generated files, source files, data, logs, build outputs, notes, and
   ambiguous items.
3. Produce a dry-run move plan with source, destination, reason, and conflict
   status.
4. Ask before applying any move plan unless the user already gave explicit
   apply permission.
5. After applying, report moved, skipped, conflicted, and unchanged files.

## Rules

- Do not move files outside the declared workspace.
- Do not delete files unless the user explicitly asks for deletion.
- Do not overwrite an existing file.
- Preserve hidden config files unless the user explicitly includes them.
- Keep git repositories intact; inspect `git status` before moving tracked
  files when the workspace is a repo.
- Prefer directory names that already exist in the project over inventing a new
  taxonomy.

## Dry-Run Table

Use this shape for previews:

| Source | Destination | Action | Reason |
|---|---|---|---|

Mark conflicts as `skip` until the user chooses a resolution.
