# AI Agents Skills Documentation

Shared, sanitized skill bodies, settings metadata, and installers for Codex,
Claude, and DeepSeek. The docs describe a research workstation where multiple
agent frontends share one canonical skill repository, while local software such
as Python, TeX, optional SageMath, library tools, document parsers, and public
database clients are detected as external capabilities instead of being
hardcoded.

Latest update: {sub-ref}`today`

## Start Here

- New install: read [Installation](installation.md), then choose a
  [Profile](profiles.md).
- Existing agent setup: run a read-only audit with
  [Audit And Migration](audit-and-migration.md) before installing anything.
- OpenClaw integration work: use the
  [OpenClaw Integration Plan](openclaw-integration-plan.md) before adding
  migration, hook, or runtime behavior.
- Dependency questions: use [Dependencies](dependencies.md), then check the
  platform-specific [Windows](windows.md) or [Linux](linux.md) notes.
- Unsure what will be written: read [Agent Locations](agent-locations.md) and
  [Verification](verification.md).
- Research workflow motivation: start with
  [System And Research Workflow Overview](workflow-overview.md) and
  [Multi-Agent Examples](multi-agent-examples.md).

## Safety Model

The installer is dry-run first. Real home-directory writes require explicit
`--apply --real-system`. Existing unmanaged files are skipped unless the user
chooses `--adopt`, `--backup-replace`, or `--migrate`. Secrets, auth files,
session logs, local paper libraries, and runtime caches are outside the managed
scope.

```{toctree}
:maxdepth: 2
:caption: Contents

overview
workflow-overview
multi-agent-examples
installation
skills
artifacts
profiles
dependencies
system-profile
agent-locations
audit-and-migration
openclaw-integration-plan
verification
architecture
windows
linux
troubleshooting
uninstall-rollback
```
