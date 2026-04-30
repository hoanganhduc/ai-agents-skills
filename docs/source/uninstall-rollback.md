# Uninstall And Rollback

`rollback` restores previous managed state from a recorded run. `uninstall`
removes current managed artifacts. Both support skill and agent scopes and both
support dry-run previews.

Applied uninstall requires an explicit scope: use `--skill`, `--skills`, or
`--artifact`, `--artifacts`, or `--all`. Uninstall removes only managed files
and managed instruction blocks. Rollback can target one run, one skill,
multiple skills, one artifact, multiple artifacts, or one agent. If a managed
instruction file was created by the installer and becomes empty after block
removal, it is removed.

Applied uninstall and rollback are interactive and require the same confirmation
phrase as install. Real home-directory writes additionally require
`--real-system`.

Use uninstall when the current installed state is no longer wanted. Use
rollback when you want to reverse a specific recorded run and restore previous
managed content or remove files that were created from an empty state.

Install mode is not an uninstall input. Uninstall reads the managed-state
journal and removes the selected managed artifacts regardless of whether they
were installed as `auto`, `symlink`, `reference`, or `copy`. When a later
install switches a skill to `reference`, previously managed support files for
that skill are planned as obsolete removals because reference adapters point at
the canonical repo directory instead of local support-file copies or links.
Rollback uses the recorded run and preserves symlink and legacy-directory
backups when reversing a mode switch or migration.

Dry-run examples:

```bash
make uninstall ARGS="--skill zotero"
make uninstall ARGS="--artifacts entrypoint-alias:zotero"
make rollback ARGS="--skill zotero"
make rollback ARGS="--run 20260429-080620"
```

Applied examples:

```bash
make uninstall ARGS="--skill zotero --apply"
make rollback ARGS="--run 20260429-080620 --apply"
```

Safety rules:

- uninstall never removes unmanaged files
- uninstall removes only selected managed file paths and managed instruction
  blocks, then prunes empty directories
- rollback uses the journal for the selected run or scope and restores recorded
  backups where available
- `--apply` and `--dry-run` cannot be combined
- instruction files are removed only when the installer created them and they
  become empty after managed block removal

Related pages: [Installation](installation.md), [Verification](verification.md),
[Audit And Migration](audit-and-migration.md), [Agent Locations](agent-locations.md).
