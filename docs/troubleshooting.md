# Troubleshooting

Run `precheck --json` to inspect detected agents, selected tools, optional
packages, skipped agents, missing required dependencies, and degraded optional
capabilities. Use `audit-system --json` to inspect repo-vs-system drift,
managed marker counts, unmanaged files, and legacy aliases. Use `plan` to
preview every file change.

If a plan reports `classification=unmanaged`, the installer found user-owned
content in the target path and will skip it unless `--adopt` or
`--backup-replace` is used. If a plan reports `classification=legacy`, the
installer found a compatibility or alias path and will skip it unless
`--migrate` is used.

Useful inspection commands:

```bash
make precheck ARGS="--profile full-research --json"
make audit-system ARGS="--profile full-research --json"
make plan ARGS="--profile full-research --migrate"
make verify ARGS="--root /tmp/aas-fake-home"
```

Common cases:

| Symptom | Likely meaning | Next step |
|---|---|---|
| Agent is listed under skipped agents | The agent home was not detected under `--root`. | Install that agent first, change `--root`, or ignore it. |
| Required dependency is missing | A selected installed skill needs software that was not found. | Install the package, use an override, or select fewer skills. |
| Dependency is degraded | The tool or install root was found but not fully executable from this substrate. | Re-run precheck from the native substrate, such as Windows or WSL. |
| Plan skips unmanaged files | Existing user-owned content would be overwritten by a naive install. | Review the file, then choose `--adopt` or `--backup-replace` if appropriate. |
| Plan skips legacy aliases | A skill exists under an old or alternate name. | Review `--migrate` output before applying migration. |
| Verify returns `no-managed-artifacts` | The selected scope has no state recorded by this installer. | Run install/adopt/migrate first, or verify a different scope. |

Related pages: [Installation](installation.md), [Dependencies](dependencies.md),
[Audit And Migration](audit-and-migration.md), [Verification](verification.md).
