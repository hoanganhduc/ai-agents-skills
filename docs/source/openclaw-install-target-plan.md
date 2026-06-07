# OpenClaw Install Target Plan

This plan covers OpenClaw as a future install target for canonical
`ai-agents-skills` skills. It is separate from the existing
[OpenClaw Integration Plan](openclaw-integration-plan.md), which treats
OpenClaw as a legacy source for sanitized inventory and migration review.

The current implemented scope is restricted fake-root target support only. The
installer can recognize explicit `--agents openclaw` in fake roots and copy
eligible `SKILL.md` files to `.openclaw/skills/<skill>/SKILL.md`, but it must
not claim OpenClaw native target support until fake-root lifecycle tests,
native loader evidence, and native inertness evidence exist.

Implemented fail-closed behavior:

- OpenClaw is not default-detected
- explicit OpenClaw requests without an `.openclaw` home report the missing
  target and create no directories or files
- real-system OpenClaw plans, applies, uninstalls, and rollbacks are blocked
  before native target evidence
- OpenClaw instruction blocks and management notices are not generated
- symlink and reference install modes are blocked for OpenClaw
- manifest runtime-backed skills are blocked until neutral runtime evidence
  and rendering exist
- support files are skipped unless backed by
  `manifest/schema/openclaw/target-support-file.schema.json` metadata

## Scope And Evidence

Scope:

- add a general OpenClaw install-target design for any system
- use the maintainer host's OpenClaw layout only as example evidence
- keep OpenClaw-as-source migration separate from OpenClaw-as-target installs
- define gates before real `.openclaw` writes are possible

Evidence inspected for this plan:

- existing installer target code for Codex, Claude, DeepSeek, Copilot, OpenCode, and Antigravity
- existing OpenClaw source/import pipeline modules and tests
- existing OpenClaw integration and verification documentation
- a sanitized local OpenClaw inventory with deny-by-default content policy
- redacted `openclaw.json` key/type structure only; values were not read
- an observed skill-like directory at `.openclaw/skills/<name>/SKILL.md`
  with helper files

Concrete repo artifacts inspected:

- `installer/ai_agents_skills/docs.py`
- `installer/ai_agents_skills/agents.py`
- `installer/ai_agents_skills/planner.py`
- `installer/ai_agents_skills/runtime.py`
- `installer/ai_agents_skills/openclaw_inventory.py`
- `installer/ai_agents_skills/openclaw_evidence.py`
- `installer/ai_agents_skills/openclaw_apply.py`
- `tests/test_installer.py`
- `tests/test_runtime_integration.py`
- `tests/test_openclaw_phase0.py`
- `tests/test_openclaw_inventory.py`
- `tests/test_openclaw_manifest.py`
- `tests/test_openclaw_apply.py`
- `tests/test_openclaw_evidence.py`
- `tests/test_openclaw_persistence.py`

Confirmed from repo inspection:

- default install targets are currently Codex, Claude, DeepSeek, Copilot, OpenCode, and Antigravity
- OpenClaw is a known explicit target for restricted fake-root layout tests
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
- how OpenClaw invokes helper scripts, sets working directories, and passes
  environment variables to runtime-backed skills
- whether any candidate documentation namespace under `.openclaw` is ignored
  by loaders, indexers, sync, command discovery, plugin discovery, runtime path
  scans, and active context construction
- precedence among `skills`, `plugin-skills`, `plugins`, `agents`,
  `subagents`, `qmd`, and workspace-local locations
- cross-platform OpenClaw layouts on macOS, Windows, and WSL
- real-system rollback behavior while OpenClaw is running

## Decision

OpenClaw should start as a restricted, explicit-only target class. It should
not be default-detected and should not behave like a normal peer of Codex,
Claude, or DeepSeek until native loader and native inertness evidence exist.

Resolve install-mode policy by phase:

- Before native loader and native inertness evidence: copy-mode may be
  exercised in fake roots only. Real OpenClaw systems remain dry-run only; no
  files may be written anywhere under a real `.openclaw` tree, including
  `.openclaw/ai-agents-skills/...`.
- Native loader evidence is necessary but not sufficient for real writes.
  After native loader evidence, OpenClaw `auto` may resolve to `copy` only
  within scopes otherwise allowed by current evidence and gates. Real
  active-loader copy still requires native inertness evidence, an approved
  real-system gate, and artifact-specific evidence where applicable.
- Symlink mode stays blocked until separately proven.
- Reference mode is not an active OpenClaw loader mode unless OpenClaw is
  proven to follow reference adapters.

## Target Policy

Target identity:

- target id: `openclaw`
- candidate skill directory after evidence:
  `.openclaw/skills/<skill>/`
- future candidate inert documentation namespace after native inertness
  evidence:
  `.openclaw/ai-agents-skills/...`

Detection policy:

- do not include OpenClaw in default target detection
- require explicit `--agents openclaw`
- require an explicit fake root for early lifecycle tests
- require a future explicit OpenClaw home option or approved manifest for real
  systems

Artifact policy:

- early fake-root MVP allows only skill files and skill support files in fake
  roots
- every OpenClaw support-file action must be backed by explicit manifest
  metadata from `manifest/schema/openclaw/target-support-file.schema.json`
  for artifact class, execution role, compatibility tuple, platforms, path
  styles, shell family, wrapper/runtime class, newline policy, mode policy,
  text/binary policy, and any helper-evidence requirement
- unclassified OpenClaw support files fail closed; filesystem discovery alone
  is not enough to make a support file installable for OpenClaw
- no instruction blocks for OpenClaw
- no management notice in `openclaw.json` or a fabricated `AGENTS.md`
- no default optional artifacts under active OpenClaw loader paths
- inert docs/templates are fake-root-only until scoped native inertness
  evidence proves the target namespace is not loaded, executed, synced,
  indexed into active context, or otherwise behavior-affecting
- `.openclaw/ai-agents-skills/...` is a candidate quarantined namespace only;
  it is not exempt from the no-real-write rule

Runtime policy:

- do not install runtime wrappers into `.openclaw/bin`, hooks, plugins, config,
  or runtime state in the MVP
- use a validated neutral `ai-agents-skills` runtime root outside known agent
  home trees and active loader/config/runtime areas for any OpenClaw target
- keep `~/.codex/runtime` independent from OpenClaw target support
- treat runtime files as their own root-scoped artifacts, not as OpenClaw
  settings files
- reject runtime roots resolving under `.openclaw`, `.codex`, `.claude`,
  `.deepseek`, workspace-local agent directories, hooks, plugins, qmd, bin,
  commands, config, cache/state, symlinked parents, traversal, Windows reserved
  names, or case/Unicode collisions
- reject runtime roots that lack a canonical realpath, have symlinked parents,
  are not owned by the current user, are group/world-writable, live under unsafe
  mounts, synced folders, workspace roots, or the repository checkout unless
  explicitly approved, or are not bound into the approval manifest
- plan only runtime and support files whose manifest compatibility tuple
  matches every target dimension: platform, path style, shell family,
  wrapper/runtime class, newline policy, and mode policy; unknown or mixed
  dimensions fail closed

Runtime script classes:

| Class | Examples | MVP policy |
|---|---|---|
| Text support files | Markdown, JSON, YAML, Python modules read as text, reference files shipped beside `SKILL.md` | May be copied as support files in fake roots with normal managed-file verification. |
| Executable helper scripts | `scripts/*.sh`, executable Python helpers, shell launchers | Fake-root only until helper-invocation evidence exists; preserve bytes, newline form, shebang, and executable mode instead of prepending managed headers. Real installs require helper-invocation evidence scoped by OpenClaw version, platform/path style, shell, cwd, argv, env allowlist, executable mode, line endings, support-file visibility, and paths with spaces. |
| Binary support files | Images, compiled helpers, archives, opaque assets | Blocked until a binary artifact policy and verification model exist. |
| Shared runtime files | `run_skill.sh`, `run_skill.ps1`, runtime workspace helpers, portable libraries | May be installed only to a repo-managed shared runtime root outside known agent homes and active loader/config/runtime areas; OpenClaw skills must not point at `~/.codex/runtime` or any Codex runtime path. |
| OpenClaw-native execution surfaces | `.openclaw/bin`, hooks, plugins, shell profiles, schedulers, command launchers | Blocked until a separate threat model and approval gate exist. |

Runtime-backed skill design:

- generated OpenClaw skill material must not hardcode Codex runtime paths
- the preferred runtime root for OpenClaw-only or mixed installs is a shared
  `ai-agents-skills` runtime outside `.openclaw`
- runtime wrappers must accept only workspace-relative skill commands and
  reject absolute paths and `..` traversal
- production runtime wrappers and tests must use an allowlisted environment and
  must not inherit OpenClaw provider/config/model/shell/`.env` state by default
- helper-invocation evidence must show whether OpenClaw runs helper scripts
  directly, whether the current working directory is the skill directory, what
  `argv` and shell are used, whether support files are visible at runtime, what
  environment is inherited, whether executable modes and line endings are
  preserved, and how paths with spaces behave
- helper-invocation evidence must be reproducible: record a native probe skill
  with declared helper files and path-with-spaces fixtures, probe source
  commit, OpenClaw version, platform/path/shell matrix, raw command transcript
  hash, stdout/stderr hashes, env allowlist diff, artifact hashes, and known
  limitations

Real-system write policy:

- before native loader evidence, native inertness evidence, artifact-specific
  evidence where required, and an OpenClaw-specific real-system approval gate,
  the installer must not plan or apply any write under a real `.openclaw` tree,
  including `.openclaw/skills`,
  `.openclaw/ai-agents-skills`, config, runtime, hooks, plugins, cache/state,
  or any descendant
- the only allowed OpenClaw target writes before that gate are fake-root
  lifecycle fixtures
- OpenClaw-associated shared runtime-root writes are also fake-root-only until
  a dedicated real-system runtime approval gate exists; those manifests must
  bind target realpath, runtime realpath, source commit, artifact hashes,
  evidence IDs, pre-state hashes, and action classes
- before Phase 8, real-system OpenClaw dry-run output may contain only
  non-actionable diagnostics or explicitly rejected proposals, not manifest
  actions, action IDs, approval hashes, or approval-eligible real `.openclaw`
  write records
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
- cache and caches, history, tokens, snapshots, private state
- `.env`, `.git`, MCP/server config, tool shims
- commands, aliases, entrypoints, instructions, templates

Any uncertainty about whether a path is config, runtime state, executable,
auto-loaded, synced, or user-owned is a no-go until separately analyzed.

## Implementation Phases

| Phase | Goal | Allowed behavior | Exit gate |
|---|---|---|---|
| 1. Policy and registry | Represent restricted targets explicitly. | Add scoped target capability fields; do not enable OpenClaw manifest eligibility or writes. | Existing Codex/Claude/DeepSeek tests unchanged. |
| 2. OpenClaw-target evidence model | Separate source-import evidence from install-target evidence. | Add target evidence records for OpenClaw loader, helper execution, runtime root, and inertness observations; existing `openclaw.evidence.v1` source/import evidence cannot approve OpenClaw target writes without new target-evidence types and fields. | Fixture/manual/upstream evidence cannot enable real OpenClaw writes. |
| 3. Manifest and rendering eligibility | Decide which skills can be rendered for OpenClaw. | Add an OpenClaw compatibility layer or explicit manifest support entries; runtime-backed skills require neutral rendering first. | Unsupported skills produce no OpenClaw write actions. |
| 4. Restricted fake-root target | Exercise candidate OpenClaw skill layout without touching real systems. | Add explicit `openclaw` target mapping to `<root>/.openclaw/skills/<skill>/`; copy only non-runtime-backed or runtime-neutral `SKILL.md` files and support files in fake roots. | Fake-root apply, verify, uninstall, and rollback pass. |
| 5. Candidate inert namespace | Design quarantined docs/templates without real writes. | Treat `.openclaw/ai-agents-skills/...` as fake-root-only and define the candidate namespace contract without claiming native inertness. | Candidate namespace contract, no-real-write planner checks, and docs/tests exist. |
| 6. Shared runtime contract | Support runtime-backed skills without depending on Codex or OpenClaw runtime state. | Install root-scoped runtime files only under a validated neutral runtime root outside known agent homes and active loader/config/runtime areas; platform-filter runtime and support files. | Runtime fake-root lifecycle and OpenClaw-shaped runtime smoke tests pass. |
| 7. Native loader, inertness, and helper evidence | Prove active and inactive OpenClaw surfaces. | Record OpenClaw version, platform, path style, shell, install mode, command summary, artifact hashes, working directory, argv, helper visibility, environment handling, inert namespace behavior, helper-invocation behavior, and limitations. | Native-loader or high-fidelity-loader evidence, native inertness evidence, and any required helper-invocation evidence exist for the claimed matrix. |
| 8. Real-system dry-run gate | Inspect real targets without side effects. | Produce reviewed dry-run manifests only; bind evidence IDs, target realpath, source commit, artifact hashes, runtime root, pre-state hash, quiescence/lock status, and action classes. | Dry-run manifest is approved and still matches target pre-state. |
| 9. Real-system active skills | Enable tightly scoped real installs. | Allow `--real-system` only with explicit OpenClaw target, explicit target root, reviewed immutable manifest, `--apply`, OpenClaw-specific write flag, matching evidence, and quiescence/lock preflight. | One-skill apply/uninstall proves rollback without touching no-go surfaces. |
| 10. Later expansion | Evaluate non-skill OpenClaw surfaces. | Templates, commands, agents/subagents, plugins, hooks, schedules, OpenClaw-native runtime wrappers, and config each require separate threat models. | Separate approval per surface. |

## Code Design Notes

The target model should separate known targets from default-detected targets.
`openclaw` may be a known target for explicit selection while remaining absent
from the default install set.

Recommended capability fields:

```python
known_target: bool
detect_by_default: bool
requires_explicit_selection: bool
allows_instruction_blocks: bool
allowed_artifact_classes_by_phase: dict[str, tuple[str, ...]]
allowed_install_modes_by_scope: dict[str, tuple[str, ...]]
requires_native_loader_evidence: bool
requires_native_inertness_evidence: bool
real_openclaw_writes_allowed: bool
required_apply_flags: tuple[str, ...]
```

Planner behavior should use these capabilities instead of path-name special
cases:

- skip instruction-block and management-notice planning when
  `allows_instruction_blocks` is false
- reject artifact classes outside the current phase and evidence scope
- reject real-system actions when `real_openclaw_writes_allowed` is false
- degrade or block install modes according to the target capability policy
- distinguish fake-root copy from real active-loader copy
- distinguish native loader evidence from native inertness evidence

The fake-root OpenClaw target can use copy mode because copy mode records file
hashes and support files in the existing installer model. That does not imply
real OpenClaw loader support until evidence upgrades the target policy.

Runtime-file planning must stay root-scoped. A runtime-backed OpenClaw skill
may require shared runtime files, but those files must be planned under the
runtime root, not inside `.openclaw`. Runtime support is acceptable only when
the generated OpenClaw skill text and helper scripts avoid Codex-specific
absolute paths, especially `~/.codex/runtime`.

Approval manifests for any future real OpenClaw target write must bind the
manifest hash, source repo commit, native evidence IDs, OpenClaw version,
platform, path style, shell, target canonical realpath, target pre-state hash,
artifact hashes, runtime root, and allowed action classes. Target pre-state,
path safety, permissions, and write policy must be rechecked immediately before
apply and uninstall.

## Implementation Issue Breakdown

Recommended implementation issues:

1. Target capability model and explicit-only OpenClaw registry entry.
2. OpenClaw-target evidence schema with separate loader and inertness records.
3. Skill compatibility and manifest eligibility layer for OpenClaw.
4. OpenClaw support-file manifest metadata and fail-closed classification via
   `manifest/schema/openclaw/target-support-file.schema.json`.
5. Runtime-neutral OpenClaw rendering for eligible skills.
6. Fake-root OpenClaw skill layout planner and lifecycle tests.
7. No-go path fixture expansion and path-safety preflight.
8. Shared runtime-root contract, real-system runtime approval gate, and
   compatibility-tuple-filtered runtime planning.
9. Executable and binary support-file policy.
10. Helper-invocation evidence gate for runtime-backed and executable-helper
   skills.
11. Generated-doc contract test for this plan.
12. Real-system dry-run manifest gate.
13. Real-system quiescence/lock and write-time pre-state recheck gate.
14. Real-system apply/uninstall gate after native loader evidence, native
   inertness evidence, required artifact-specific evidence, and real-system
   approval.

## Required Tests

Default and explicit selection:

- default detection ignores real or fake `.openclaw`
- `--agents openclaw` is required for any OpenClaw plan
- `--agents openclaw` without an explicit fake root or future approved real
  gate fails closed
- real `.openclaw` paths never appear in write actions before native loader
  evidence, native inertness evidence, and an approved real-system gate

Fake-root lifecycle:

- fake-root OpenClaw copy installs non-runtime-backed or runtime-neutral
  `SKILL.md` and text support files
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
- table-driven no-go fixtures cover every category listed in
  `## No-Go Surfaces`, including credentials, sessions, browser state, memory,
  logs, hooks, cron, shell profiles, schedulers, workspaces, backups, queues,
  locks, sandbox state, plugins, plugin-skills, qmd, bin, agents, subagents,
  tasks, devices, cache/history/tokens/snapshots, `.env`, `.git`, MCP/server
  config, tool shims, commands, aliases, entrypoints, instructions, and
  templates; none may appear in plan writes
- path fixtures cover reparse points, junctions, mount escapes, alternate data
  streams, trailing dot/space names, 8.3 aliases, ownership/permission drift,
  and write-time TOCTOU rechecks

Runtime safety:

- runtime-backed fake-root OpenClaw skills copy declared per-skill helper files
  as support files, but do not execute them during install
- OpenClaw support-file planning validates explicit manifest metadata for
  artifact class, execution role, compatibility tuple, platforms, path styles,
  shell family, wrapper/runtime class, newline policy, mode policy, text/binary
  policy, and helper-evidence requirement from
  `manifest/schema/openclaw/target-support-file.schema.json`; unclassified
  support files fail closed
- OpenClaw runtime-file actions target a validated neutral shared runtime root
  outside `.openclaw`, `.codex`, `.claude`, `.deepseek`, workspace-local agent
  directories, and active loader/config/runtime areas
- neutral runtime-root validation checks canonical realpath, symlink parents,
  owner, group/world-writable modes, unsafe mounts, synced/workspace roots,
  repository checkout location unless explicitly approved, stable per-platform
  defaults, and approval-manifest binding
- generated OpenClaw skill content and helper scripts contain no
  `~/.codex/runtime`, `.codex/runtime`, `$CODEX_HOME`,
  `%USERPROFILE%\.codex`, or absolute `.codex` runtime dependency
- no OpenClaw-native runtime action writes to `.openclaw/bin`, hooks, plugins,
  shell profiles, schedulers, qmd, or OpenClaw config
- compatibility-tuple-filtered runtime and support-file actions are tested
  across Linux, macOS, Windows, WSL-native, and mounted-Windows path styles;
  platform, path style, shell family, wrapper/runtime class, newline policy,
  and mode policy must all match
- executable helper support files preserve bytes, newlines, shebangs, and
  executable modes
- real runtime-backed or executable-helper installs fail closed unless
  helper-invocation evidence exists for the artifact class and target matrix
- helper-invocation evidence tests require reproducible native probe artifacts,
  raw transcript hashes, stdout/stderr hashes, env allowlist diffs, artifact
  hashes, OpenClaw version, platform/path/shell metadata, and probe source
  commit
- planner/runtime behavior tests reject Codex runtime paths in rendered
  OpenClaw artifacts, unclassified support files, compatibility tuple
  mismatches, invalid neutral runtime roots, missing executable-helper gates,
  real `.openclaw` write actions before gates, and pre-Phase-8 approval
  actions
- OpenClaw-associated shared runtime-root writes remain fake-root-only until
  the dedicated real-system runtime approval gate is implemented and tested
- runtime wrappers reject absolute command paths and `..` traversal
- runtime smoke tests distinguish fake-root runtime isolation from native
  OpenClaw execution proof

Evidence safety:

- fixture-only, manual-review, and upstream-doc evidence cannot enable active
  or inert real OpenClaw writes
- OpenClaw-source evidence and OpenClaw-target evidence are separate
- existing `openclaw.evidence.v1` source/import evidence cannot approve
  OpenClaw target writes; target approval requires new target evidence types
  and fields for native loader, native inertness, helper invocation, and
  runtime-root observations
- native target evidence must be scoped by OpenClaw version, platform, path
  style, shell, install mode, command summary, cwd, argv, environment policy,
  helper visibility, observed behavior, and limitation
- native inertness evidence must prove candidate namespaces are not loaded,
  executed, indexed into active context, synced as command/config, or otherwise
  behavior-affecting
- evidence summaries must distinguish fake-root isolation from native loader
  proof

Docs contract:

- generated docs include this install-target plan
- the plan says no real `.openclaw` writes before native inertness evidence
- the central real-system write policy names native loader evidence and native
  inertness evidence separately
- pre-Phase-8 real-system dry-run output is non-actionable and not
  approval-eligible
- OpenClaw support-file actions require explicit manifest metadata, and
  unclassified support files fail closed
- docs/source OpenClaw install-target plan output matches the root docs copy
- docs tests assert key safety claims in their normative sections, not only in
  checklists
- docs tests assert the full negative sentence, including `no files may be
  written anywhere under a real .openclaw tree`
- `.openclaw/ai-agents-skills/...` is not exempt from the no-real-write rule
- fake-root copy is not native loader proof
- shared runtime root is outside known agent homes and active
  loader/config/runtime areas
- generated OpenClaw skill material must not depend on any Codex runtime path
- the former optional-inert-docs exception is absent
- stale phrases from prior plans are absent, including older inert-docs
  exceptions and loader-evidence-only real copy wording

Real-system gate:

- real home roots remain rejected before the future OpenClaw-specific gate
- OpenClaw-associated shared runtime-root writes remain rejected before a
  dedicated real-system runtime approval gate
- pre-Phase-8 real-system dry-run output may contain only non-actionable
  diagnostics or explicitly rejected proposals, not approval-eligible action
  manifests
- approved real-system manifests must match the exact target realpath, manifest
  hash, source repo commit, artifact hashes, native evidence IDs, runtime root,
  pre-state hash, and allowed action classes
- any drift in source evidence, target pre-state, schema version, permissions,
  or action policy fails closed before writes
- real apply, uninstall, and rollback must fail closed unless OpenClaw is
  stopped, locked, or otherwise quiescent; target pre-state and write policy
  must be rechecked after lock acquisition and immediately before writes

## Acceptance Criteria

OpenClaw target support is acceptable only when:

- default installer behavior is unchanged for Codex, Claude, DeepSeek, Copilot, OpenCode, and Antigravity
- OpenClaw is explicit-only and absent from default target discovery
- early OpenClaw writes are fake-root-only
- all real `.openclaw` writes, including `.openclaw/ai-agents-skills`, are
  impossible before native loader evidence, native inertness evidence,
  artifact-specific evidence where required, and an OpenClaw-specific
  real-system approval gate
- fake-root copy may be exercised before native evidence, but real active-loader
  copy is allowed only after native loader evidence, native inertness evidence,
  artifact-specific evidence where required, and the real-system approval gate
- runtime-backed skills use a shared runtime root outside known agent homes and
  active loader/config/runtime areas and do not depend on any Codex runtime
  path
- shared runtime-file actions are allowed only under a validated neutral
  runtime root outside `.openclaw`, `.codex`, `.claude`, `.deepseek`,
  workspace-local agent directories, and active loader/config/runtime areas
  and only after the dedicated real-system runtime approval gate for real
  OpenClaw-associated runtime writes
- no OpenClaw-native runtime, workspace, command, hook, plugin, qmd, bin,
  scheduler, shell-profile, settings mutation, or config action is planned
  before a separate evidence and approval gate
- no-go surfaces are enforced by tests
- docs clearly separate OpenClaw-as-source import from
  OpenClaw-as-install-target
- focused OpenClaw target tests, full installer tests, docs generation, and
  fake-root lifecycle checks pass

Related pages: [OpenClaw Integration Plan](openclaw-integration-plan.md),
[Agent Locations](agent-locations.md), [Architecture](architecture.md),
[Verification](verification.md), [Uninstall And Rollback](uninstall-rollback.md).
