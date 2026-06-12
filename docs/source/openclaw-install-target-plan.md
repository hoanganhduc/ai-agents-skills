# OpenClaw Install Target Plan

This plan covers OpenClaw as a restricted install target for canonical
`ai-agents-skills` skills. It is separate from the existing
[OpenClaw Integration Plan](openclaw-integration-plan.md), which treats
OpenClaw as a legacy source for sanitized inventory and migration review.

The current implemented scope has two layers. Normal installer support remains
restricted fake-root target support: the installer includes OpenClaw in the
default target set, detects eligible fake-root `.openclaw` homes, and can copy
eligible `SKILL.md` files to `.openclaw/skills/<skill>/SKILL.md`, but normal
`plan`, `install`, `uninstall`, and `rollback` flows still must not write under
a real `.openclaw` tree. A separate `openclaw-target-*` command family now
implements a narrow reviewed v2 real-system path for
`.openclaw/skills/<skill>/SKILL.md` only.

Implemented fail-closed behavior:

- OpenClaw participates in default target detection when an eligible fake-root
  `.openclaw` home exists
- default or explicit OpenClaw requests without an `.openclaw` home report the
  missing target and create no directories or files
- normal real-system OpenClaw plans, applies, uninstalls, and rollbacks remain
  blocked outside the explicit `openclaw-target-*` path
- the Phase 1 OpenClaw target gate reports blocked real-system decisions
  without enabling normal installer write eligibility
- `openclaw.target-evidence.v1` and `openclaw.target-manifest.v1` schemas are
  non-authorizing scaffolds; they cannot emit approval-eligible real-path
  action IDs, approval hashes, or write records
- `openclaw.target-evidence.v2` and `openclaw.target-manifest.v2` can authorize
  only reviewed `copy` writes to `skills/<skill>/SKILL.md` under an existing
  `.openclaw/skills` root
- `openclaw-target-apply-manifest` requires an approved immutable v2 manifest,
  immediate target pre-state recheck, an OpenClaw-specific confirmation phrase,
  and `--real-system` for real home roots
- v2 uninstall deletes only unchanged files recorded by
  `.ai-agents-skills/openclaw-target-state.json`
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
- local native OpenClaw command behavior for `openclaw --version`,
  `openclaw skills --help`, and `openclaw skills list --json`
- installed native OpenClaw loader code showing managed skills are loaded from
  `CONFIG_DIR/skills` as `<skill>/SKILL.md`

Concrete repo artifacts inspected:

- `installer/ai_agents_skills/docs.py`
- `installer/ai_agents_skills/agents.py`
- `installer/ai_agents_skills/planner.py`
- `installer/ai_agents_skills/runtime.py`
- `installer/ai_agents_skills/openclaw_inventory.py`
- `installer/ai_agents_skills/openclaw_evidence.py`
- `installer/ai_agents_skills/openclaw_apply.py`
- `installer/ai_agents_skills/openclaw_target_gate.py`
- `installer/ai_agents_skills/openclaw_target_evidence.py`
- `installer/ai_agents_skills/openclaw_target_manifest.py`
- `installer/ai_agents_skills/openclaw_target_paths.py`
- `installer/ai_agents_skills/openclaw_target_apply.py`
- `tests/test_installer.py`
- `tests/test_runtime_integration.py`
- `tests/test_openclaw_phase0.py`
- `tests/test_openclaw_target_phase1.py`
- `tests/test_openclaw_target_v2.py`
- `tests/test_openclaw_inventory.py`
- `tests/test_openclaw_manifest.py`
- `tests/test_openclaw_apply.py`
- `tests/test_openclaw_evidence.py`
- `tests/test_openclaw_persistence.py`

Confirmed from repo inspection:

- default install targets are currently Codex, Claude, DeepSeek, Copilot, OpenCode, Antigravity, and OpenClaw
- OpenClaw is a default target for restricted fake-root layout tests
- OpenClaw has a Phase 1 target capability record and central target gate that
  preserves normal installer real-system denials
- OpenClaw has a separate v2 target-evidence, target-manifest, apply, and
  uninstall path for reviewed real-system skill-file writes only
- OpenClaw code in this repository is currently a quarantined source/import
  pipeline with explicit roots, sanitized inventories, immutable manifests,
  fake-root apply, evidence recording, and persistence blocking
- default installer behavior must remain unchanged for existing targets
- default OpenClaw installer behavior must remain fake-root-only unless the
  explicit `openclaw-target-*` command path is used

Confirmed from sanitized host evidence:

- OpenClaw homes can contain many sensitive or active state areas, including
  credentials, browser state, memory, logs, hooks, cron, workspaces, backups,
  locks, sandbox state, plugins, and config backups
- `.openclaw/skills/<name>/SKILL.md` is a plausible candidate skill shape

incomplete analysis

Still unchecked before expanding beyond the implemented v2 skill-file path:

- live canary visibility on this host for a newly written managed skill
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
- live rollback behavior while OpenClaw is running

## Decision

OpenClaw should start as a restricted default target class. It may participate
in default fake-root target detection, but it should not behave like a normal
real-system peer of Codex, Claude, or DeepSeek. Real-system writes are
available only through the explicit reviewed v2 OpenClaw target manifest path,
and only for managed `SKILL.md` files.

Resolve install-mode policy by phase:

- Normal installer copy-mode may be exercised in fake roots only. Normal
  `plan`, `install`, `uninstall`, and `rollback` flows must not write anywhere
  under a real `.openclaw` tree, including `.openclaw/ai-agents-skills/...`.
- The implemented v2 real-system gate can copy only
  `.openclaw/skills/<skill>/SKILL.md`. It requires native loader evidence,
  native managed-skill-root evidence, target pre-state evidence,
  quiescence/lock evidence, an approved content-addressed target manifest, an
  OpenClaw-specific confirmation phrase, and `--real-system` for real home
  roots. Managed non-canary skill writes also require a native managed canary
  evidence record.
- Native loader evidence is necessary but not sufficient for broader real
  writes. Support files, runtime-backed skills, symlinks, reference adapters,
  instruction files, plugins, hooks, config, and runtime surfaces remain
  blocked until separate evidence and approval gates exist.
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

- include OpenClaw in default target detection when an eligible `.openclaw`
  fake-root home exists
- never create `.openclaw` only because OpenClaw is in the default target set
- allow explicit `--agents openclaw` for targeted prechecks and plans
- require a fake root for normal lifecycle writes
- require an approved v2 OpenClaw target manifest for the narrow real-system
  `SKILL.md` path

Artifact policy:

- normal fake-root MVP allows only eligible skill files in fake roots
- v2 real-system target manifests allow only `skills/<skill>/SKILL.md` with
  action class `canary-skill-file` or `managed-skill-file`
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
  it is not part of the v2 real-system write exception

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
| Text support files | Markdown, JSON, YAML, Python modules read as text, reference files shipped beside `SKILL.md` | Fake-root only when explicit OpenClaw support-file metadata exists; unclassified support files fail closed. |
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

- normal installer flows must not plan or apply any write under a real
  `.openclaw` tree, including `.openclaw/skills`,
  `.openclaw/ai-agents-skills`, config, runtime, hooks, plugins, cache/state,
  or any descendant
- the only implemented real-system exception is the explicit
  `openclaw-target-apply-manifest` path for approved v2
  `skills/<skill>/SKILL.md` copy actions
- the v2 gate requires existing `.openclaw` and `.openclaw/skills`
  directories, target path shape `skills/<skill>/SKILL.md`, no symlinked
  target parents, managed OpenClaw skill content, no Codex runtime paths,
  native-loader evidence, native managed-skill-root evidence, target-pre-state
  evidence, quiescence evidence, and canary evidence for non-canary managed
  skill writes
- OpenClaw-associated shared runtime-root writes are also fake-root-only until
  a dedicated real-system runtime approval gate exists; those manifests must
  bind target realpath, runtime realpath, source commit, artifact hashes,
  evidence IDs, pre-state hashes, and action classes
- v1 target-evidence and target-manifest records remain non-actionable
  diagnostics and cannot authorize real `.openclaw` write records
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

Implemented Phase 1 scaffolding:

- `openclaw_target_gate.py` is the central non-authorizing policy evaluator for
  OpenClaw target detection, planning, apply preflight, runtime preflight,
  uninstall, and rollback. It returns structured blocked or fake-root-only
  decisions and never returns real-write eligibility in Phase 1.
- `openclaw_target_evidence.py` validates `openclaw.target-evidence.v1`
  records. These records must set `authorizes_real_writes: false` and
  `approval_eligible: false`; existing `openclaw.evidence.v1` source/import
  evidence is rejected for target authorization.
- `openclaw_target_manifest.py` validates `openclaw.target-manifest.v1`
  diagnostic manifests. These manifests must keep `real_write_status:
  blocked`, `authorizes_real_writes: false`, and `approval_eligible: false`;
  existing `openclaw.apply-manifest.v1` source/import manifests are rejected
  for OpenClaw-as-target authorization.

Implemented v2 real-system skill-file path:

- `openclaw_target_evidence.py` also validates `openclaw.target-evidence.v2`
  records from native probes. Authorizing evidence must be limitation-free,
  content-addressed, bound to one target realpath and managed skills realpath,
  and sourced from `native-probe`.
- `openclaw_target_manifest.py` also validates `openclaw.target-manifest.v2`
  records. A v2 manifest is not authorizing until its approval record has
  `review_status: approved` and `approval_hash` equal to the immutable
  `manifest_id`.
- `openclaw_target_paths.py` limits real-system OpenClaw target paths to
  `skills/<skill>/SKILL.md` and requires canonical kebab-case skill names.
- `openclaw_target_apply.py` is the only implementation that writes through
  the v2 path. It records a transaction before mutation, rechecks target
  pre-state immediately before each write, writes only managed skill content,
  runs native post-apply checks for real home roots, and uninstalls only files
  whose current hash still matches the recorded install hash.
- Normal `plan`, `install`, `uninstall`, and `rollback` still use the Phase 1
  gate and remain blocked for real-system OpenClaw writes.

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

1. Target capability model and default OpenClaw registry entry.
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
12. Real-system v2 target evidence and manifest gate for skill files.
13. Real-system quiescence/lock and write-time pre-state recheck gate.
14. Real-system apply/uninstall gate for approved `SKILL.md` copy actions.
15. Later support-file, runtime-backed skill, symlink/reference, inert
   namespace, and OpenClaw-native execution surface gates.

## Required Tests

Default and explicit selection:

- default detection includes eligible fake-root `.openclaw` homes
- default detection does not create `.openclaw` when it is absent
- explicit `--agents openclaw` remains available for targeted checks
- default or explicit OpenClaw selection without an existing fake-root
  `.openclaw` home fails closed
- normal installer real `.openclaw` paths never appear in write actions; the
  only real-system write actions are produced by approved v2
  `openclaw-target-*` manifests

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
- v2 target manifest generation refuses existing unmanaged
  `.openclaw/skills/<skill>/SKILL.md`

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
  real `.openclaw` write actions outside the v2 skill-file gate, and
  approval-eligible v1 target records
- OpenClaw-associated shared runtime-root writes remain fake-root-only until
  the dedicated real-system runtime approval gate is implemented and tested
- runtime wrappers reject absolute command paths and `..` traversal
- runtime smoke tests distinguish fake-root runtime isolation from native
  OpenClaw execution proof

Evidence safety:

- fixture-only, manual-review, and upstream-doc evidence cannot enable active
  or inert real OpenClaw writes
- OpenClaw-source evidence and OpenClaw-target evidence are separate
- `manifest/schema/openclaw/target-evidence.schema.json` and
  `manifest/schema/openclaw/target-manifest.schema.json` require
  non-authorizing Phase 1 records and authorizing Phase 2 records with
  distinct schema versions
- existing `openclaw.evidence.v1` source/import evidence cannot approve
  OpenClaw target writes; v2 skill-file target approval requires native
  loader, native managed-skill-root, target pre-state, quiescence, and, for
  non-canary managed writes, native managed-canary evidence
- v2 native target evidence must be scoped by OpenClaw version, platform, path
  style, target realpath, managed skills realpath, observed behavior, and
  limitation-free probe checks
- future inert namespace evidence must prove candidate namespaces are not
  loaded, executed, indexed into active context, synced as command/config, or
  otherwise behavior-affecting
- evidence summaries must distinguish fake-root isolation from native loader
  proof

Docs contract:

- generated docs include this install-target plan
- generated docs name the Phase 1 target gate, target evidence schema, and
  target manifest schema as non-authorizing scaffolding
- the plan says normal installer flows still cannot write real `.openclaw`
  paths and that v2 real-system writes are limited to
  `skills/<skill>/SKILL.md`
- the central real-system write policy names native loader evidence and native
  managed-skill-root evidence separately
- v1 target-evidence and target-manifest output is non-actionable and not
  approval-eligible
- v2 target manifests require approval before apply
- OpenClaw support-file actions require explicit manifest metadata, and
  unclassified support files fail closed
- docs/source OpenClaw install-target plan output matches the root docs copy
- docs tests assert key safety claims in their normative sections, not only in
  checklists
- docs tests assert the full normal-installer negative sentence and the exact
  v2 exception path
- `.openclaw/ai-agents-skills/...` is not part of the v2 real-system write
  exception
- fake-root copy is not native loader proof
- shared runtime root is outside known agent homes and active
  loader/config/runtime areas
- generated OpenClaw skill material must not depend on any Codex runtime path
- the former optional-inert-docs exception is absent
- stale phrases from prior plans are absent, including older inert-docs
  exceptions and loader-evidence-only real copy wording

Real-system gate:

- normal installer flows reject real home roots for OpenClaw writes
- OpenClaw-associated shared runtime-root writes remain rejected before a
  dedicated real-system runtime approval gate
- v2 real-system dry-run output may contain only `skills/<skill>/SKILL.md`
  actions with action class `canary-skill-file` or `managed-skill-file`
- approved v2 real-system manifests must match the exact target realpath,
  managed skills realpath, manifest hash, artifact hash, native evidence IDs,
  pre-state signature, and allowed action class
- any drift in source evidence, target pre-state, schema version, permissions,
  or action policy fails closed before writes
- real apply and uninstall must fail closed unless OpenClaw is stopped, locked,
  or otherwise quiescent; target pre-state and write policy must be rechecked
  immediately before writes
- v2 uninstall deletes only unchanged files recorded by the OpenClaw target
  state journal and cleans only recorded empty parent directories

## Acceptance Criteria

OpenClaw target support is acceptable only when:

- default installer behavior is unchanged for Codex, Claude, DeepSeek, Copilot, OpenCode, and Antigravity
- OpenClaw participates in default target discovery when an eligible fake-root
  `.openclaw` home exists
- normal OpenClaw installer writes are fake-root-only
- real-system OpenClaw writes are possible only through approved v2
  `openclaw-target-*` manifests for `skills/<skill>/SKILL.md`
- all other real `.openclaw` writes, including `.openclaw/ai-agents-skills`,
  support files, config, runtime, hooks, plugins, and cache/state, are
  impossible before separate evidence and approval gates
- fake-root copy may be exercised before native evidence, but real active-loader
  copy is limited to the v2 skill-file gate
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
