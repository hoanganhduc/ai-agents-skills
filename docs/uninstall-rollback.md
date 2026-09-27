# Uninstall And Rollback

`rollback` restores previous managed state from a recorded run. `uninstall`
removes or restores current managed artifacts according to the install journal.
Both support skill and agent scopes and both support dry-run previews.

Applied uninstall requires an explicit scope: use `--skill`, `--skills`,
`--artifact`, `--artifacts`, or `--all`. Applied rollback also requires an
explicit scope: use `--run`, `--skill`, `--skills`, `--artifact`,
`--artifacts`, or `--all`. Uninstall acts only on recorded managed artifacts.
It restores backups for replaced pre-install files when the installed artifact
has not changed, deletes files created by the installer when they have not
changed, unmanages adopted files, and removes managed instruction blocks while
preserving surrounding user text. Rollback can target one run, one skill,
multiple skills, all managed artifacts, one artifact, multiple artifacts, or
one agent. If a managed instruction file was created by the installer and
becomes empty after block removal, it is removed.

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
./make.ps1 uninstall --skill zotero
./make.ps1 uninstall --artifacts entrypoint-alias:zotero
./make.ps1 rollback --skill zotero
./make.ps1 rollback --run 20260429-080620
```

Applied examples:

```bash
make uninstall ARGS="--skill zotero --apply --root <fake-root>"
make rollback ARGS="--run 20260429-080620 --apply --root <fake-root>"
make verify ARGS="--root <fake-or-real-root>"
```

Applied real-system examples:

```bash
make uninstall ARGS="--skill zotero --apply --real-system"
make rollback ARGS="--run 20260429-080620 --apply --real-system"
make verify ARGS="--root <real-root>"
```

Native Windows applied lifecycle commands intentionally fail closed until
mutation is handle-bound. Use the dry-run commands above; do not treat a
mounted-profile test from Linux/WSL as native Windows mutation proof.

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

## Retired Skills And The Lax Migration

An exact skill name removed from the current catalog can still be selected by
`uninstall` or `rollback` when the selected root/agent's managed journal records
it. It is not accepted as an ordinary `plan`, `install` or `verify` selection.
An unknown retired name is not permission to delete a matching directory.

For a former personal-library installation, preview both the replacement and
the exact retired scope from the checkout:

```bash
make audit-system ARGS="--profile formal-research"
make plan ARGS="--no-skills --artifact template:lax-paper-artifact --with-deps --runtime-profile auto"
./installer/bootstrap.sh --agents codex uninstall --skill lean-research-library --dry-run
```

Replace `codex` with the actual target and select the correct root if necessary.
The retired name above is only an uninstall example, not an available skill.
Inspect the preview and recorded backups before applying through the scoped
commands above. Back up installer state, and keep the run ID for recovery.

Uninstall may restore pre-install backups or preserve edited files. Afterwards,
inspect agent discovery paths, manual instruction blocks and runtime settings
for old library routing. Rules outside managed blocks and user-owned configs
require a separate reviewed edit; uninstall does not rewrite them. Disable only
the obsolete staging/intake settings and preserve unrelated user configuration.
Do not delete existing HoangMathLib repositories, Lean sources or Git history.

Install the reviewed replacement, run managed `verify` and the offline Lax and
Zenodo doctors, then check that the agent discovers the intended skills. These
checks validate installation/readiness, not any paper proof. Keep historical
plans and generic Lean skills; the replacement workflow is described in
[Lax Formalization And Zenodo Archival](lax-formalization.md).

Related pages: [Installation](installation.md), [Verification](verification.md),
[Audit And Migration](audit-and-migration.md), [Agent Locations](agent-locations.md).
