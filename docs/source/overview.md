# Overview

AI Agents Skills is an experimental, personal-use repository for sharing
research-oriented agent skills and settings across Codex, Claude, and DeepSeek.
It is designed for combinatorics and graph theory workflows, but the installer
and documentation are written so other users can inspect, dry-run, and install
only the parts that fit their own machines.

The repository is a generator and installer, not a copied dotfiles folder. It
keeps reusable skill bodies, dependency metadata, profiles, optional artifacts,
and target-specific rendering logic in one source tree. Agent homes such as
`~/.codex`, `~/.claude`, and `~/.deepseek` are runtime targets.

## Main Ideas

- Canonical skills live under `canonical/skills/`.
- Profiles in `manifest/profiles.yaml` select useful skill bundles.
- Optional artifacts add templates, personas, instruction docs, entrypoint
  aliases, and management notices outside normal skill directories.
- `precheck` detects tools and Python packages from the current substrate.
- `plan` and `install --dry-run` preview writes before anything is changed.
- Real home-directory writes require explicit `--apply --real-system`.
- Verification checks only installed managed artifacts.

## Typical Workflow

```bash
make doctor
make precheck ARGS="--profile research-core"
make audit-system ARGS="--profile research-core"
make plan ARGS="--profile research-core"
make install ARGS="--profile research-core --dry-run"
```

Use a fake root for write testing:

```bash
make install ARGS="--profile research-core --apply --root /tmp/aas-fake-home"
make verify ARGS="--root /tmp/aas-fake-home"
```

## Where To Go Next

- [Installation](installation.md): safe install, dry-run, conflict, and
  migration flows.
- [Skills](skills.md): canonical skill catalog.
- [Profiles](profiles.md): workflow bundles such as `research-core`,
  `library`, `math`, and `full-research`.
- [Optional Artifacts](artifacts.md): templates, personas, instruction docs,
  entrypoints, and management notices.
- [Dependencies](dependencies.md): logical tools, Python packages, and
  platform-specific detection behavior.
- [Workflow Overview](workflow-overview.md): how the research stack connects
  agents, skills, runtimes, and external software.
- [Audit And Migration](audit-and-migration.md): how to inspect an existing
  setup before adopting or migrating files.
- [Verification](verification.md): what installed managed artifacts are
  checked after install, uninstall, or rollback.
