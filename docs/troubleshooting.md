# Troubleshooting

Run `precheck --json` to inspect detected agents, selected tools, optional
packages, skipped agents, missing required dependencies, and degraded optional
capabilities. Use `plan` to preview every file change.

If a plan reports `classification=unmanaged`, the installer found user-owned
content in the target path and will skip it unless `--adopt` or
`--backup-replace` is used. If a plan reports `classification=legacy`, the
installer found a compatibility or alias path and will skip it unless
`--migrate` is used.
