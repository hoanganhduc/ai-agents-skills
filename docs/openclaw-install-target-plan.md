# OpenClaw Install Target Plan

This plan covers OpenClaw as a future install target for canonical
`ai-agents-skills` skills. It is separate from the existing
[OpenClaw Integration Plan](openclaw-integration-plan.md), which treats
OpenClaw as a legacy source for sanitized inventory and migration review.

The current state is planning only. The installer must not claim OpenClaw
native target support until fake-root lifecycle tests and native loader
evidence exist.

## Scope And Evidence

Scope:

- add a general OpenClaw install-target design for any system
- use the maintainer host's OpenClaw layout only as example evidence
- keep OpenClaw-as-source migration separate from OpenClaw-as-target installs
- define gates before real `.openclaw` writes are possible

Evidence inspected for this plan:

- existing installer target code for Codex, Claude, and DeepSeek
- existing OpenClaw source/import pipeline modules and tests
- existing OpenClaw integration and verification documentation
- a sanitized local OpenClaw inventory with deny-by-default content policy
- redacted `openclaw.json` key/type structure only; values were not read
- an observed skill-like directory at `.openclaw/skills/<name>/SKILL.md`
  with helper files

Confirmed from repo inspection:

- supported install targets are currently Codex, Claude, and DeepSeek
- OpenClaw code in this repository is currently a quarantined source/import
  pipeline with explicit roots, sanitized inventories, immutable manifests,
  fake-root apply, evidence recording, and persistence blocking
- default installer behavior must remain unchanged for existing targets

Confirmed from sanitized host evidence:

- OpenClaw homes can contain many sensitive or active state areas, including
  credentials, browser state, memory, logs, hooks, cron, workspaces, backups,
  locks, sandbox state, plugins, and config backups
- `.openclaw/skills/<name>/SKILL.md` is a plausible candidate skill shape

incomplete analysis

Still unchecked before real native support:

- whether OpenClaw loads `.openclaw/skills/<skill>/SKILL.md`
- whether OpenClaw loads support files beside `SKILL.md`
- whether OpenClaw follows symlinks or relative helper paths safely
- precedence among `skills`, `plugin-skills`, `plugins`, `agents`,
  `subagents`, `qmd`, and workspace-local locations
- cross-platform OpenClaw layouts on macOS, Windows, and WSL
- real-system rollback behavior while OpenClaw is running

## Decision

OpenClaw should start as a restricted, explicit-only target class. It should
not be default-detected and should not behave like a normal peer of Codex,
Claude, or DeepSeek until native evidence exists.

Resolve install-mode policy by phase:

- Before native loader evidence: copy-mode may be exercised in fake roots
  only. Real OpenClaw systems remain dry-run only, except for optional inert
  documentation under a quarantined managed namespace.
- After native loader evidence: OpenClaw `auto` may resolve to `copy` because
  support files matter and symlink/reference behavior is unproven.
- Symlink mode stays blocked until separately proven.
- Reference mode is not an active OpenClaw loader mode unless OpenClaw is
  proven to follow reference adapters.

## Target Policy

Target identity:

- target id: `openclaw`
- candidate skill directory after evidence:
  `.openclaw/skills/<skill>/`
- optional inert documentation namespace:
  `.openclaw/ai-agents-skills/...`

Detection policy:

- do not include OpenClaw in default target detection
- require explicit `--agents openclaw`
- require an explicit fake root for early lifecycle tests
- require a future explicit OpenClaw home option or approved manifest for real
  systems

Artifact policy:

- phase-1 MVP allows only skill files and skill support files in fake roots
- no instruction blocks for OpenClaw
- no management notice in `openclaw.json` or a fabricated `AGENTS.md`
- no default optional artifacts under active OpenClaw loader paths
- future inert docs/templates may live only under
  `.openclaw/ai-agents-skills/...`

Runtime policy:

- do not install runtime wrappers into `.openclaw/bin`, hooks, plugins, config,
  or runtime state in the MVP
- prefer repo-managed shared runtime paths outside `.openclaw`
- keep `~/.codex/runtime` independent from OpenClaw target support

Real-system write policy:

- no real `.openclaw/skills` active-loader writes before native evidence
- no real writes without an OpenClaw-specific approval gate
- every write must have a manifest action, precondition, collision policy,
  rollback record, and uninstall check
- existing OpenClaw files are preserve-only by default

## No-Go Surfaces

The installer must not write, adopt, migrate, copy, parse deeply, or import
from these OpenClaw areas by default:

- `openclaw.json`
- secrets, credentials, identity, provider/auth/channel/gateway settings
- logs, sessions, memory databases, browser state
- hooks, cron, shell profiles, schedulers, persistent execution entries
- workspaces, workspace-local state, backups, sync-conflict files
- queues, locks, sandbox and sandboxes
- plugins, plugin-skills, qmd, bin, agents, subagents
- tasks, devices, delivery queues, downloaded runtime data

Any uncertainty about whether a path is config, runtime state, executable,
auto-loaded, synced, or user-owned is a no-go until separately analyzed.

## Implementation Phases

| Phase | Goal | Allowed behavior | Exit gate |
|---|---|---|---|
| 1. Policy and registry | Represent restricted targets explicitly. | Add target capability fields such as `detect_by_default`, `requires_explicit_selection`, `allows_instruction_blocks`, `allowed_artifact_classes`, `real_system_apply_policy`, `default_install_mode`, and `active_loader_writes_allowed`. | Existing Codex/Claude/DeepSeek tests unchanged. |
| 2. Restricted fake-root target | Exercise candidate OpenClaw skill layout without touching real systems. | Add explicit `openclaw` target mapping to `<root>/.openclaw/skills/<skill>/`; copy `SKILL.md` and support files in fake roots only. | Fake-root apply, verify, uninstall, and rollback pass. |
| 3. Quarantined inert docs | Provide review artifacts without active loading. | Optional docs/templates only under `.openclaw/ai-agents-skills/...`; no claim that OpenClaw loads them. | Docs/templates are proven inert and uninstallable. |
| 4. Native evidence | Prove OpenClaw loader behavior before enabling active writes. | Record OpenClaw version, platform, path style, shell, install mode, command summary, artifact hashes, observed behavior, and limitations. | Native-loader or high-fidelity-loader evidence exists for the claimed matrix. |
| 5. Real-system active skills | Enable tightly scoped real installs. | Allow `--real-system` only with an explicit OpenClaw target, explicit target root, reviewed immutable manifest, `--apply`, and an OpenClaw-specific write flag. | Real-system dry-run and one-skill apply/uninstall prove rollback without touching no-go surfaces. |
| 6. Later expansion | Evaluate non-skill OpenClaw surfaces. | Templates, commands, agents/subagents, plugins, hooks, schedules, runtime wrappers, and config each require separate threat models. | Separate approval per surface. |

## Code Design Notes

The target model should separate known targets from default-detected targets.
`openclaw` may be a known target for explicit selection while remaining absent
from the default install set.

Recommended capability fields:

```python
detect_by_default: bool
requires_explicit_selection: bool
allows_instruction_blocks: bool
allowed_artifact_classes: tuple[str, ...]
default_install_mode: str
active_loader_writes_allowed: bool
real_system_apply_policy: str
```

Planner behavior should use these capabilities instead of path-name special
cases:

- skip instruction-block and management-notice planning when
  `allows_instruction_blocks` is false
- reject artifact classes outside `allowed_artifact_classes`
- reject real-system actions when `active_loader_writes_allowed` is false
- degrade or block install modes according to the target capability policy

The fake-root OpenClaw target can use copy mode because copy mode records file
hashes and support files in the existing installer model. That does not imply
real OpenClaw loader support until evidence upgrades the target policy.

## Required Tests

Default and explicit selection:

- default detection ignores real or fake `.openclaw`
- `--agents openclaw` is required for any OpenClaw plan
- `--agents openclaw` without an explicit fake root or future approved real
  gate fails closed

Fake-root lifecycle:

- fake-root OpenClaw copy installs `SKILL.md` and support files
- fake-root OpenClaw install creates no symlinks
- verify checks the copied files
- uninstall returns the fake root to baseline
- changed generated files are preserved and reported as conflicts

Planning safety:

- no OpenClaw instruction-block actions are planned
- `openclaw.json` never appears as a write target
- `backup-replace`, `adopt`, and `migrate` are disabled for OpenClaw MVP
  unless explicitly designed
- unmanaged existing `.openclaw/skills/<skill>/SKILL.md` is skipped by default

Path safety:

- symlinked `.openclaw`, symlinked parents, hardlinks, special files, path
  traversal, case/Unicode collisions, and Windows reserved names fail closed
- no-go fixtures for credentials, logs, memory, hooks, workspaces, plugins,
  bin, and qmd never appear in plan writes

Evidence safety:

- fixture-only, manual-review, and upstream-doc evidence cannot enable active
  OpenClaw writes
- native evidence must be scoped by OpenClaw version, platform, path style,
  shell, install mode, command summary, observed behavior, and limitation
- evidence summaries must distinguish fake-root isolation from native loader
  proof

Real-system gate:

- real home roots remain rejected before the future OpenClaw-specific gate
- approved real-system manifests must match the exact target root and manifest
  hash
- any drift in source evidence, target pre-state, schema version, permissions,
  or action policy fails closed before writes

## Acceptance Criteria

OpenClaw target support is acceptable only when:

- default installer behavior is unchanged for Codex, Claude, and DeepSeek
- OpenClaw is explicit-only and absent from default target discovery
- early writes are fake-root-only
- real active `.openclaw/skills` writes are impossible before native evidence
- `auto` resolves to copy only after the evidence gate
- no instruction block, settings mutation, hook, cron, plugin, runtime, or
  workspace action is planned
- no-go surfaces are enforced by tests
- docs clearly separate OpenClaw-as-source import from
  OpenClaw-as-install-target
- focused OpenClaw target tests, full installer tests, docs generation, and
  fake-root lifecycle checks pass

Related pages: [OpenClaw Integration Plan](openclaw-integration-plan.md),
[Agent Locations](agent-locations.md), [Architecture](architecture.md),
[Verification](verification.md), [Uninstall And Rollback](uninstall-rollback.md).
