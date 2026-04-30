# Audit And Migration

`audit-system` is a read-only comparison between the selected repo profile and
the current agent homes. It is intended for existing systems where skills may
already exist under canonical names, legacy aliases, or local-only names.

Use this page before touching an existing personal setup. Audit output helps
separate files this repo can safely manage from local experiments, legacy
aliases, and user-owned settings that should stay outside the repo.

Common commands:

```bash
make audit-system ARGS="--profile full-research"
make audit-system ARGS="--profile full-research --json"
make plan ARGS="--profile full-research --migrate"
make plan ARGS="--profile full-research --adopt"
```

The audit reports:

- detected and skipped agents
- managed state counts from `.ai-agents-skills/state.json`
- instruction-file managed marker counts
- canonical skills present, missing, managed, unmanaged, and legacy aliases
- extra local skills outside this repo's canonical catalog in the primary
  agent skills directory
- default, adopt, migrate, and adopt+migrate plan summaries
- dependency status and selected skills related to each dependency

Recommended staged migration for a current personal system:

1. Run `precheck --profile full-research` and resolve missing required
   dependencies.
2. Run `audit-system --profile full-research` and review unmanaged and legacy
   counts.
3. Run `plan --profile full-research --migrate` and migrate only reviewed
   legacy aliases, such as underscore-to-hyphen skill names.
4. Run `plan --profile full-research --adopt` and adopt only canonical files
   that should remain user-owned but tracked by this installer.
5. Install missing skills in small batches, then run `verify`.
6. Add optional artifacts such as `repo-management`, `workflow-templates`, or
   `research-entrypoints` only after the backing skill state is clear.

The repo intentionally does not manage every local skill found in Codex or
Claude. Local engineering workflows, one-off experiments, downloaded runtime
state, provider config, secrets, and session/history databases should remain
outside the repo unless they are deliberately promoted into a canonical skill or
artifact.

Audit `extra_local` coverage is limited to the primary agent skills directory,
such as `~/.codex/skills` or `~/.claude/skills`. Compatibility and workspace
skill directories are used for legacy detection during planning, but are not
reported as primary extra-local inventory.

When auditing a mounted Windows profile from Linux or WSL, native `.exe`
programs can often be found but not safely executed. Treat degraded Windows
tool results as presence checks only. To fully verify Windows-native tools, run
the same `make.bat precheck --profile ...` command from a native Windows shell
and compare the output with the mounted-profile audit.

Related pages: [Installation](installation.md), [Dependencies](dependencies.md),
[Agent Locations](agent-locations.md), [Uninstall And Rollback](uninstall-rollback.md).
