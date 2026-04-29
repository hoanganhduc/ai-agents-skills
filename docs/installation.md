# Installation

Use `make doctor` or `make.bat doctor` first. Use `plan` before `install`.
Partial installs are first-class: select `--skill`, `--skills`, or `--profile`.
`install --dry-run` previews the same actions as a default install preview;
`install --apply` is required before any writes occur.
Conflict modes:

- default: create missing managed files and skip unmanaged or legacy files
- `--adopt`: record an existing target file as user-owned managed state
- `--backup-replace`: back up and replace an unmanaged target file
- `--migrate`: copy a detected legacy skill into the canonical target while
  leaving the legacy source in place
