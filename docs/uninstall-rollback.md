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

Use uninstall when the current installed state is no longer wanted. Use
rollback when you want to reverse a specific recorded run and restore previous
managed content or remove files that were created from an empty state.

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
- uninstall removes only selected managed files and managed instruction blocks
- rollback uses the journal for the selected run or scope
- instruction files are removed only when the installer created them and they
  become empty after managed block removal

Related pages: [Installation](installation.md), [Verification](verification.md),
[Audit And Migration](audit-and-migration.md), [Agent Locations](agent-locations.md).
