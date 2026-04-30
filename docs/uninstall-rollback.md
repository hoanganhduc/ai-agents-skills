# Uninstall And Rollback

`rollback` restores previous managed state from a recorded run. `uninstall`
removes or restores current managed artifacts according to the install journal.
Both support skill and agent scopes and both support dry-run previews.

Applied uninstall requires an explicit scope: use `--skill`, `--skills`, or
`--artifact`, `--artifacts`, or `--all`. Uninstall acts only on recorded managed
artifacts. It restores backups for replaced pre-install files when the installed
artifact has not changed, deletes files created by the installer when they have
not changed, unmanages adopted files, and removes managed instruction blocks
while preserving surrounding user text. Rollback can target one run, one skill,
multiple skills, one artifact, multiple artifacts, or one agent. If a managed
instruction file was created by the installer and becomes empty after block
removal, it is removed.

Applied uninstall and rollback are interactive and require the same confirmation
phrase as install. Real home-directory writes additionally require
`--real-system`.

Use uninstall when the current installed state is no longer wanted. If you
install and immediately uninstall, the installer restores the pre-install
settings it replaced. If you modify a managed file after installation, uninstall
keeps that changed file and leaves the corresponding state record for review.
Use rollback when you want to reverse a specific recorded run and restore
previous managed content or remove files that were created from an empty state.
Rollback preflights the selected artifacts before mutating anything, so a
conflict in a shared instruction file does not partially remove other files.
Run `verify` after every applied install, uninstall, migration, adoption, or
rollback.

Install mode is not an uninstall input. Uninstall reads the managed-state
journal and uses recorded signatures to decide whether a selected artifact is
unchanged enough to restore or delete. When a later install switches a skill to
`reference`, previously managed support files for that skill are planned as
obsolete removals because reference adapters point at the canonical repo
directory instead of local support-file copies or links. Those obsolete-removal
backups remain available to uninstall through tombstone records. Rollback uses
the recorded run and preserves symlink and legacy-directory backups when
reversing a mode switch or migration.

Dry-run examples:

```bash
make uninstall ARGS="--skill zotero"
make uninstall ARGS="--artifacts entrypoint-alias:zotero"
make rollback ARGS="--skill zotero"
make rollback ARGS="--run 20260429-080620"
```

Windows dry-run examples:

```bat
make.bat uninstall --skill zotero
make.bat uninstall --artifacts entrypoint-alias:zotero
make.bat rollback --skill zotero
make.bat rollback --run 20260429-080620
```

Applied examples:

```bash
make uninstall ARGS="--skill zotero --apply"
make rollback ARGS="--run 20260429-080620 --apply"
make verify ARGS="--root /tmp/aas-fake-home"
```

Windows applied examples:

```bat
make.bat uninstall --skill zotero --apply --root %TEMP%\aas-fake-home
make.bat rollback --run 20260429-080620 --apply --root %TEMP%\aas-fake-home
make.bat verify --root %TEMP%\aas-fake-home
```

Safety rules:

- uninstall never removes or rewinds changed unmanaged/user-owned files
- uninstall restores backups only when the current artifact is missing or still
  matches the installer's recorded installed signature
- uninstall deletes installer-created files only when they still match the
  recorded installed signature
- uninstall removes selected managed instruction blocks only when the block
  still matches the recorded managed block content
- rollback uses the journal for the selected run or scope and restores recorded
  backups where available
- rollback preflights all selected artifacts before mutation and refuses the
  whole rollback when a selected artifact has changed
- rollback refuses artifacts outside the selected root and backups outside the
  installer state backup directory
- `--apply` and `--dry-run` cannot be combined
- instruction files are removed only when the installer created them and they
  become empty after managed block removal

Related pages: [Installation](installation.md), [Verification](verification.md),
[Audit And Migration](audit-and-migration.md), [Agent Locations](agent-locations.md).
