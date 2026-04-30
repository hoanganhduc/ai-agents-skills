# Verification

Verification is selective. Only installed and enabled managed artifacts from the
installer state are checked.
If no managed artifacts match the requested scope, `verify` returns
`no-managed-artifacts` instead of `ok`.

Use verification after any applied install, uninstall, migration, adoption, or
rollback. It is intentionally narrower than `precheck`: `precheck` checks
software availability, while `verify` checks whether this installer still owns
the files and managed instruction blocks it recorded. For adopted user-owned
files, verification checks that the file still matches the hash recorded at
adoption time.

Common commands:

```bash
make verify ARGS="--root /tmp/aas-fake-home"
make verify ARGS="--skill zotero --root /tmp/aas-fake-home"
make verify ARGS="--skills zotero,docling --root /tmp/aas-fake-home"
```

Result meanings:

- `ok`: all selected managed artifacts passed their checks.
- `no-managed-artifacts`: the selected scope has no installer-managed files to check.
- `missing` or failed checks: a managed file, marker, block, or format-specific condition no longer matches recorded state.

Current skill checks:

- `L1 file-exists`
- `L2 metadata-valid`
- `L3 managed-marker` for copy and reference installs
- `L4 symlink`, `source-exists`, and `source-match` for symlink installs
- `L5 no-secret-leak`
- `L6 agent-visible`
- `L7 adopted-hash-match` for adopted user-owned files

Current instruction-block checks:

- `S1 file-exists`
- `S2 managed-block-present`
- `S3 no-secret-leak` for the managed block text only; surrounding user
  instructions are outside installer ownership

Current support-file checks:

- `A1 file-exists`
- `A2 managed-marker` for copied support files
- `A3 symlink`, `source-exists`, and `source-match` for symlinked support files
- `A4 no-secret-leak`

Current optional artifact checks:

- `O1 file-exists`
- `O2 managed-marker`
- `O3 no-secret-leak`
- `O4 format-specific checks for Codex TOML personas and Claude frontmatter`

The verifier intentionally skips skills and artifacts that were not installed.
Runtime smoke tests, runner-specific `doctor` commands, and direct
`agent-loads-config` checks are not automatic yet; use `precheck` and the
agent's own diagnostics for those layers.

Related pages: [Installation](installation.md), [Audit And Migration](audit-and-migration.md),
[Uninstall And Rollback](uninstall-rollback.md), [Troubleshooting](troubleshooting.md).
