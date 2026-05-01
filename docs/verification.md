# Verification

Verification is selective. Only installed and enabled managed artifacts from the
installer state are checked.
If no managed artifacts match the requested scope, `verify` returns
`no-managed-artifacts` instead of `ok`.

`verify` checks installer ownership and file integrity. It does not prove that
an agent runtime has loaded a skill. Use `smoke` for the separate
agent-discovery compatibility check; smoke results can be `ok`, `degraded`,
`unsupported`, or `skipped` with reasons.

Use verification after any applied install, uninstall, migration, adoption, or
rollback. It is intentionally narrower than `precheck`: `precheck` checks
software availability, while `verify` checks whether this installer still owns
the files and managed instruction blocks it recorded. For adopted user-owned
files, verification checks that the file still matches the hash recorded at
adoption time.

Use `lifecycle-test` as the default installer acceptance gate. It creates fake
roots, runs dry-run install, confirms the dry-run did not write files, applies
the install, compares normalized dry-run and applied actions, runs `verify` and
`smoke`, dry-runs uninstall, applies uninstall, and confirms the fake root
returns to its baseline outside installer state, including directories. Fake
roots are deleted after successful cases unless `--keep-fake-roots` is passed.
`fake-root-lifecycle` runs the same checks for a caller-selected install scope.
The matrix treats forced symlink mode as an expected-degraded smoke scenario
when Codex or DeepSeek is included, because those adapters may not load
file-symlinked `SKILL.md` files without native evidence.
Use `--matrix stress` for broader local coverage: all skills, all portable
workflow artifacts with backing skills, individual-agent installs, paths with
spaces, changed managed files, missing managed files, outside-root state
tampering, and corrupt state reporting.

Common commands:

```bash
make lifecycle-test ARGS="--matrix default --platform-shape all"
make lifecycle-test ARGS="--matrix full --platform-shape linux"
make lifecycle-test ARGS="--matrix stress --platform-shape linux"
make fake-root-lifecycle ARGS="--skill zotero --platform-shape linux"
make verify ARGS="--root <fake-or-real-root>"
make verify ARGS="--skill zotero --root <fake-or-real-root>"
make verify ARGS="--skills zotero,docling --root <fake-or-real-root>"
make smoke ARGS="--skill zotero --root <fake-or-real-root>"
```

Result meanings:

- `ok`: all selected managed artifacts passed their checks.
- `no-managed-artifacts`: the selected scope has no installer-managed files to check.
- `missing` or failed checks: a managed file, marker, block, or format-specific condition no longer matches recorded state.

Current skill checks:

- `L1 file-exists`
- `L2 installed-signature-match`
- `L3 metadata-valid`
- `L4 managed-marker` for copy and reference installs
- `L5 symlink`, `source-exists`, and `source-match` for symlink installs
- `L6 no-secret-leak`
- `L7 agent-visible`
- `L8 adopted-hash-match` for adopted user-owned files

Current instruction-block checks:

- `S1 file-exists`
- `S2 managed-block-present`
- `S3 no-secret-leak` for the managed block text only; surrounding user
  instructions are outside installer ownership

Current support-file checks:

- `A1 file-exists`
- `A2 installed-signature-match`
- `A3 managed-marker` for copied support files
- `A4 symlink`, `source-exists`, and `source-match` for symlinked support files
- `A5 no-secret-leak`

Current optional artifact checks:

- `O1 file-exists`
- `O2 installed-signature-match`
- `O3 managed-marker`
- `O4 no-secret-leak`
- `O5 format-specific checks for Codex TOML personas and Claude frontmatter`

The verifier intentionally skips skills and artifacts that were not installed.
Runtime smoke tests, runner-specific `doctor` commands, and direct
`agent-loads-config` checks are not automatic yet; use `precheck` and the
agent's own diagnostics for those layers.

Related pages: [Installation](installation.md), [Audit And Migration](audit-and-migration.md),
[OpenClaw Integration Plan](openclaw-integration-plan.md),
[Uninstall And Rollback](uninstall-rollback.md), [Troubleshooting](troubleshooting.md).
