# Uninstall And Rollback

`rollback` restores previous managed state from a recorded run. `uninstall`
removes current managed artifacts. Both support skill and agent scopes and both
support dry-run previews.

Applied uninstall requires an explicit scope: use `--skill`, `--skills`, or
`--all`. Uninstall removes only managed files and managed instruction blocks.
Rollback can target one run, one skill, multiple skills, or one agent. If a
managed instruction file was created by the installer and becomes empty after
block removal, it is removed.
