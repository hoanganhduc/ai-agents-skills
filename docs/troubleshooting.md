# Troubleshooting

Run `doctor --json` to inspect detected agents, selected tools, skipped agents,
and degraded optional capabilities. Use `plan` to preview every file change.
If a plan reports `classification=legacy`, the installer found a skill in an
older or agent-specific location and will skip it unless `--migrate` is used.
