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
`--migrate` is used. A reviewed `--migrate` plan installs the canonical target
and removes the legacy alias directory.

Default installs use `--install-mode auto`, resolved per agent. Claude receives
symlinked skill files when the filesystem supports them. Codex, DeepSeek, and
Copilot receive reference adapters by default because their symlinked skill
loading is not assumed. OpenCode and Antigravity receive copied regular files;
Antigravity uses documented flat global Markdown skill files under
`~/.gemini/antigravity-cli/skills/`. Use
`--install-mode symlink` only when you intentionally want to force links for
every agent. Use `--install-mode reference` to force adapters for every agent.
If an agent requires regular files in its settings directory, use
`--install-mode copy`.

Useful inspection commands:

```bash
make precheck ARGS="--profile full-research --json"
make audit-system ARGS="--profile full-research --migration-report --json"
make plan ARGS="--profile full-research --migrate"
make lifecycle-test ARGS="--matrix full --platform-shape all"
make lifecycle-test ARGS="--matrix stress --platform-shape linux"
make fake-root-lifecycle ARGS="--profile full-research --platform-shape all"
```

Common cases:

| Symptom | Likely meaning | Next step |
|---|---|---|
| Agent is listed under skipped agents | The agent home was not detected under `--root`. | Install that agent first, change `--root`, or ignore it. |
| Required dependency is missing | A selected installed skill needs software that was not found. | Install the package, use an override, or select fewer skills. |
| Dependency is degraded | The tool or install root was found but not fully executable from this substrate. | Re-run precheck from the native substrate, such as Windows or WSL. |
| Plan skips unmanaged files | Existing user-owned content would be overwritten by a naive install. | Review the file, then choose `--adopt` or `--backup-replace` if appropriate. |
| Plan skips legacy aliases | A skill exists under an old or alternate name. | Review `--migrate` output before applying migration. |
| Agent does not load symlinked skills | The filesystem or agent loader does not follow symlinks. Codex is handled this way by default. | Reinstall that scope with `--install-mode reference`; use `copy` only if the adapter is insufficient. |
| Windows wrapper reports no PowerShell runtime | `make.bat` could not find `pwsh` or `powershell.exe`. | Install PowerShell, run from a shell where it is on PATH, or use the POSIX bootstrap script from a compatible environment. |
| Fake-root install has no actions | The fake root does not contain any detected agent homes such as `.codex`, `.claude`, `.deepseek`, `.copilot`, `.config/opencode`, or `.gemini/antigravity-cli`. | Create the agent homes you want to test under the fake root, or use `lifecycle-test` to create managed fake roots automatically. |
| Docs freshness check fails in CI | Generated docs are stale. | Edit `installer/ai_agents_skills/docs.py` or manifests, run `make docs`, and commit the resulting `README.md` and `docs/` changes. |
| Forced symlink smoke is degraded for Codex or DeepSeek | Current loader evidence does not prove file-symlinked `SKILL.md` loading for those agents. | Use default auto mode or reference mode unless intentionally testing loader behavior. |
| Verify returns `no-managed-artifacts` | The selected scope has no state recorded by this installer. | Run install/adopt/migrate first, or verify a different scope. |

Related pages: [Installation](installation.md), [Dependencies](dependencies.md),
[Audit And Migration](audit-and-migration.md), [Verification](verification.md).
