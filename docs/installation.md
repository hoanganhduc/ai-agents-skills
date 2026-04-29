# Installation

This page describes safe installation flows. The installer is conservative:
planning and dry-run previews are the default workflow, and real home-directory
writes require both `--apply` and `--real-system`.

Use `make precheck` or `make.bat precheck` first when installing on a new
machine. Use `plan` before `install`. Partial installs are first-class: select
`--skill`, `--skills`, or `--profile`. Artifact installs are also partial:
select `--artifact`, `--artifacts`, or `--artifact-profile`.

`doctor` is a quick required-tool check. `precheck` is broader: it detects
required tools, optional tools, Python packages, remote-service configuration
placeholders, detected agents, skipped agents, ignored dependencies, and
Windows/WSL substrate information where possible.
`audit-system` is read-only and compares the selected repo profile with the
current agent homes, managed state, legacy aliases, unmanaged files, dependency
status, and install-plan summaries.

Use `precheck --interactive` for a guided one-by-one pass through missing
dependencies. It does not install packages automatically; it shows the install
hint, lets the user skip or ignore a dependency, and tells them to rerun
`precheck` after installing software.

`install --dry-run` previews the same actions as a default install preview;
`install --apply` is required before any writes occur. Real home-directory
writes additionally require `--real-system`.

## Safe First Install

Linux:

```bash
make doctor
make precheck ARGS="--profile research-core"
make plan ARGS="--profile research-core"
make install ARGS="--profile research-core --dry-run"
```

Windows:

```bat
make.bat doctor
make.bat precheck --profile research-core
make.bat plan --profile research-core
make.bat install --profile research-core --dry-run
```

To test file writes without touching a real agent home, use a fake root:

```bash
make install ARGS="--profile research-core --apply --root /tmp/aas-fake-home"
make verify ARGS="--root /tmp/aas-fake-home"
```

Real-system writes should be a final step after reviewing `plan` output:

```bash
make install ARGS="--profile research-core --apply --real-system"
```

## Selection Model

- `--profile research-core` selects a workflow bundle.
- `--skill zotero` selects one skill.
- `--skills zotero,docling` selects a comma-separated skill set.
- `--no-skills --artifact-profile workflow-templates` installs only optional
  artifacts.
- `--with-deps` lets dependency-bound artifacts bring in their backing skills.

See [Profiles](profiles.md), [Skills](skills.md), and
[Optional Artifacts](artifacts.md) for the available selectors.

## Conflict Modes

- default: create missing managed files and skip unmanaged or legacy files
- `--adopt`: record an existing target file as user-owned managed state
- `--backup-replace`: back up and replace an unmanaged target file
- `--migrate`: copy a detected legacy skill into the canonical target while
  leaving the legacy source in place

Instruction blocks are installed only when the corresponding skill artifact is
actually installed, adopted, updated, already managed, or migrated. A skipped
skill does not receive an `AGENTS.md` or `CLAUDE.md` block.

Optional artifacts are not installed by default. Use `--no-skills` when you
want an artifact-only install. Use `--with-deps` when selected dependency-bound
artifacts should bring in their required backing skills.

For an existing personal system, prefer staged migration:

1. Run `precheck --profile full-research`.
2. Run `audit-system --profile full-research`.
3. Review `plan --profile full-research --migrate` for legacy aliases.
4. Review `plan --profile full-research --adopt` for canonical files that
   already exist but are not managed.
5. Apply one small selected scope at a time, then run `verify`.

Use `--artifact-profile repo-management` when you want a top-level managed
notice in `AGENTS.md` or `CLAUDE.md` without installing every skill.

Scenario summary:

| Scenario | Result |
|---|---|
| Agent home absent | Agent is skipped; its dependencies are not required. |
| Skill absent | Managed skill files and support files are created. |
| Skill already managed | Files are updated or left unchanged according to hashes. |
| Skill exists unmanaged | Default plan skips it; use `--adopt` or `--backup-replace` explicitly. |
| Legacy alias exists | Default plan skips; `--migrate` copies canonical content under the canonical name. |
| Top-level management notice selected | Adds a removable managed block explaining repo/source ownership boundaries. |
| Dependency-bound artifact selected without dependency | Artifact is blocked and skipped until the backing skill is managed or selected with `--with-deps`. |
| Persona selected | Codex gets TOML, Claude gets Markdown frontmatter, DeepSeek gets a reference prompt. |
| Windows SageMath | Prefer WSL-backed detection when native SageMath is absent. |

Related pages: [Dependencies](dependencies.md), [Audit And Migration](audit-and-migration.md),
[Verification](verification.md), [Troubleshooting](troubleshooting.md).
