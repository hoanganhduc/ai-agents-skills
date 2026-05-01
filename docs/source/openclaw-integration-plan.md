# OpenClaw Integration Plan

This plan is the safety contract for future OpenClaw import work. OpenClaw is
treated as a legacy source that may contain reusable ideas, not as a runtime
dependency or a trusted install source.

Phase 0 was contracts only: documentation, schemas, fake-root fixtures, tests,
and threat modeling. Phase 1 added a read-only sanitized inventory scanner for
explicit OpenClaw source roots. Phase 2 added immutable dry-run manifest
generation from a saved sanitized inventory and an explicit target root. Phase
3 added approved-manifest apply and uninstall for explicit non-real fake
target roots only. Phase 4 added explicit native-evidence recording and
validation. The current implemented gate is Phase 5: inert persistence checks
for hooks and schedules. Phase 5 does not install hooks, write schedules, edit
shell profiles, import providers, or depend on `~/.openclaw` at runtime.

## Scope

In scope:

- strict versioned schemas for future inventory, denylist, redaction, alias,
  evidence, and dry-run/apply manifests
- fake OpenClaw source roots and fake agent target homes for fixture design
- static and dynamic leakage tests using canary values
- read-only sanitized inventory generation from explicit source roots
- immutable dry-run manifest generation from saved sanitized inventories
- approved-manifest apply and uninstall against explicit fake target roots
- explicit native-evidence records for scoped support claims
- inert persistence checks for hook and schedule material
- dry-run review UX and named gates
- native evidence requirements for Codex, Claude, and DeepSeek
- inert documentation and templates only

Out of scope by default:

- secrets, credentials, auth files, provider config, channels, logs, memory
  databases, browser or gateway state, cron state, private workspaces, and
  downloaded runtime data
- provider defaults, model-provider adoption, copied API settings, MCP/plugin
  config, shell aliases, shell profiles, memory/session history, and workspace
  state
- real-system apply/uninstall behavior, real hook/scheduler writes, or real
  agent-home adoption until later gates pass
- any runtime dependency on `~/.openclaw`

## Named Gates

| Gate | Required evidence |
|---|---|
| Safety-spec approval | Threat model, schemas, fixture catalog, redaction policy, and UX gates reviewed. |
| Sanitized inventory review | Inventory output uses allowlisted metadata only and passes canary leakage tests. |
| Immutable manifest review | Candidate manifest is content-addressed, human-reviewed, and still has status `approved`. |
| Fake-root verification | Fake source root is unchanged; fake target home returns to baseline after uninstall. |
| Native evidence review | Dated evidence exists for each claimed agent, platform, path style, shell, and install mode. |
| Explicit apply confirmation | User approves the exact reviewed manifest and target environment. |

## Phases

| Phase | Goal | Allowed changes | Exit gate |
|---|---|---|---|
| 0. Safety specification only | Define contracts before code depends on OpenClaw. | Docs, threat model, strict schemas, denylist/redaction policy, alias model, native-evidence model, fake-root fixture catalog, CI tier plan, UX gates. | Safety-spec approval. |
| 1. Read-only sanitized inventory | Inspect explicit source roots without creating install input. | Bounded scanner that emits allowlisted sanitized metadata only. | Sanitized inventory review. |
| 2. Immutable dry-run manifest | Describe candidate target actions without applying them. | Strict content-addressed candidate manifest with stable action IDs and source/target separation. | Immutable manifest review. |
| 3. Gated apply and uninstall | Apply only exact reviewed manifests. | Journaled fake-root and later real-target apply/uninstall guarded by drift checks and rollback. | Fake-root verification plus explicit apply confirmation. |
| 4. Native agent support | Prove loaders before claiming support. | Dated evidence for Codex, Claude, and DeepSeek by platform, path style, shell, runtime, and install mode. | Native evidence review. |
| 5. Hooks and schedules | Add persistent execution only after a separate threat model. | Inert docs/templates first; persistent execution only behind explicit separate approval. | Persistence-specific approval and rollback evidence. |

## Phase 0 Deliverables

Phase 0 creates contracts only:

- `manifest/schema/openclaw/inventory.schema.json`
- `manifest/schema/openclaw/denylist.schema.json`
- `manifest/schema/openclaw/redaction.schema.json`
- `manifest/schema/openclaw/alias.schema.json`
- `manifest/schema/openclaw/evidence.schema.json`
- `manifest/schema/openclaw/apply-manifest.schema.json`
- `tests/fixtures/openclaw/adversarial-fixtures.json`

These artifacts are intentionally non-installing. They do not scan real
OpenClaw data, write agent homes, or change runtime behavior.

## Phase 1 Deliverables

Phase 1 adds a review-only scanner:

- `installer/ai_agents_skills/openclaw_inventory.py`
- CLI command: `openclaw-inventory --source-root <path>`
- `tests/test_openclaw_inventory.py`

The scanner:

- requires an explicit `--source-root`; it does not default to `~/.openclaw`
- emits `openclaw.inventory.v1`
- uses tokenized roots such as `<OPENCLAW_ROOT>/...`
- reads only filesystem metadata through `lstat`
- skips private categories as counts with reason codes
- detects hooks as metadata only and never imports or executes them
- denies symlinked source roots, symlink traversal, hardlinks, special files,
  reserved names, case/Unicode collisions, and bounded-scan overflow
- ignores hostile `OPENCLAW_*`, `DEEPSEEK_*`, provider, shell, and `.env`
  environment variables
- remains disconnected from install, migration, apply, and uninstall logic

Example commands:

```bash
make openclaw-inventory ARGS="--source-root <fake-openclaw-root> --json"
```

```bat
make.bat openclaw-inventory --source-root <fake-openclaw-root> --json
```

## Phase 2 Deliverables

Phase 2 adds a review-only manifest builder:

- `installer/ai_agents_skills/openclaw_manifest.py`
- CLI command: `openclaw-dry-run-manifest --inventory <file> --target-root <path>`
- `tests/test_openclaw_manifest.py`

The manifest builder:

- consumes a saved `openclaw.inventory.v1` JSON file instead of rescanning
  OpenClaw during manifest construction
- requires an explicit `--target-root`; it does not default to real agent homes
- inspects target pre-state read-only and records only relative target paths
- emits `openclaw.apply-manifest.v1` with stable action IDs
- marks manifests as `approval.review_status = unreviewed`
- keeps `apply_policy.no_recompute`,
  `apply_policy.fail_closed_on_drift`, and
  `apply_policy.content_addressed` set to `true`
- converts existing target collisions to `no-op` actions with
  `skip-report`
- refuses unsafe inventories with raw paths, unsafe content-read policy,
  unsupported schema versions, non-explicit source roots, critical denial
  categories, special-file items, or non-tokenized source path references
- remains disconnected from install, migration, apply, and uninstall logic

Example commands:

```bash
make openclaw-dry-run-manifest ARGS="--inventory <inventory.json> --target-root <fake-home-root> --target-agents codex,claude --json"
```

```bat
make.bat openclaw-dry-run-manifest --inventory <inventory.json> --target-root <fake-home-root> --target-agents codex,claude --json
```

## Phase 3 Deliverables

Phase 3 adds fake-root apply and uninstall:

- `installer/ai_agents_skills/openclaw_apply.py`
- CLI command: `openclaw-approve-manifest --manifest <file> --reviewer <name>`
- CLI command: `openclaw-apply-manifest --manifest <file> --target-root <fake-home-root>`
- CLI command: `openclaw-uninstall-manifest --target-root <fake-home-root>`
- `tests/test_openclaw_apply.py`

The Phase 3 apply path:

- applies only manifests whose `approval.review_status` is `approved`
- requires `approval.approval_hash` to match the immutable `manifest_id`
- refuses real-system target roots, including real home directories
- dry-runs by default; `--apply` is required for fake-root writes
- preflights every action and fails closed on target drift before any write
- supports only create-style review artifacts and `no-op` actions
- writes deterministic review files from sanitized manifest metadata only
- records an OpenClaw-specific journal under the fake target root
- uninstalls only unchanged files recorded in that journal
- preserves changed generated files as `skip-conflict`
- removes only directories recorded as created by this OpenClaw apply path

Example commands:

```bash
make openclaw-approve-manifest ARGS="--manifest <manifest.json> --reviewer <name> --json"
make openclaw-apply-manifest ARGS="--manifest <approved.json> --target-root <fake-home-root> --json"
make openclaw-apply-manifest ARGS="--manifest <approved.json> --target-root <fake-home-root> --apply --json"
make openclaw-uninstall-manifest ARGS="--target-root <fake-home-root> --manifest-id <manifest_id> --apply --json"
```

```bat
make.bat openclaw-approve-manifest --manifest <manifest.json> --reviewer <name> --json
make.bat openclaw-apply-manifest --manifest <approved.json> --target-root <fake-home-root> --apply --json
make.bat openclaw-uninstall-manifest --target-root <fake-home-root> --manifest-id <manifest_id> --apply --json
```

## Phase 4 Deliverables

Phase 4 adds evidence recording:

- `installer/ai_agents_skills/openclaw_evidence.py`
- CLI command: `openclaw-record-evidence`
- CLI command: `openclaw-validate-evidence`
- `tests/test_openclaw_evidence.py`

The Phase 4 evidence gate:

- records explicit `openclaw.evidence.v1` objects only
- content-addresses each evidence object with a stable `evidence_id`
- distinguishes fixture, CI, native-loader, high-fidelity-loader, upstream-doc,
  and manual-review evidence
- requires native-loader and high-fidelity-loader evidence to include an
  `agent_version`
- summarizes native support claims only for evidence records that actually use
  native-loader or high-fidelity-loader evidence types
- keeps agents without native evidence in a reference-only bucket
- does not inspect real agent homes automatically
- does not change install policy for Codex, Claude, or DeepSeek

Example commands:

```bash
make openclaw-record-evidence ARGS="--evidence-type fixture-only --evidence-agent deepseek --evidence-platform ci-container --install-mode reference --path-style posix --observed-behavior 'fixture reference docs only' --limitation 'not native loader evidence' --json"
make openclaw-validate-evidence ARGS="--evidence <evidence.json> --json"
```

```bat
make.bat openclaw-record-evidence --evidence-type fixture-only --evidence-agent deepseek --evidence-platform ci-container --install-mode reference --path-style posix --observed-behavior "fixture reference docs only" --limitation "not native loader evidence" --json
make.bat openclaw-validate-evidence --evidence <evidence.json> --json
```

## Phase 5 Deliverables

Phase 5 adds persistence blocking:

- `installer/ai_agents_skills/openclaw_persistence.py`
- CLI command: `openclaw-persistence-check --manifest <file>`
- `tests/test_openclaw_persistence.py`

The Phase 5 persistence gate:

- treats hook, scheduler, cron, launchd, systemd, and shell-profile material as
  inert unless it is represented by `no-op`
- returns `inert-only` when a manifest has no enabled persistence actions
- returns `blocked` if a manifest tries to write persistent execution material
- does not create persistence manifests
- does not write hook files, scheduler entries, shell profiles, wrapper
  scripts, agent hooks, or project hooks

Example commands:

```bash
make openclaw-persistence-check ARGS="--manifest <manifest.json> --json"
```

```bat
make.bat openclaw-persistence-check --manifest <manifest.json> --json
```

## Risk Fixes

| Risk | Required fix |
|---|---|
| Metadata leakage | Inventory defaults to no file-content reads. Output uses allowlisted fields, tokenized roots, sanitized reason codes, and no raw private paths. Denied categories are not parsed, copied, cached, indexed, or content-hashed. |
| Path escape | Source roots must be explicit and canonicalized with platform-native rules. Symlinked prefixes, symlink traversal, hardlinks, reparse points, special files, mount escapes, traversal, Unicode/case ambiguity, and reserved path names fail closed. |
| Weak schemas | Schemas are strict, versioned, use stable IDs, use canonical serialization where relevant, and separate OpenClaw source data from agent target actions. |
| Unsafe apply | Apply consumes the exact approved content-addressed manifest. It never recomputes actions. Schema, denylist, source, target, pre-state, permissions, file type, or environment drift fails closed before writes. |
| Rollback overclaim | Rollback scope is declared in the manifest. Supported metadata is restored; unsupported metadata must fail preflight or be declared out of scope. Failure injection must test backup, apply, rollback, uninstall, and drift failures. |
| Alias ambiguity | Alias records are typed as display alias, agent-id alias, skill alias, command alias, path alias, unsupported, or excluded-private. Collisions default to non-success skip/report unless explicitly approved. |
| False portability claims | Fake-root evidence proves isolation only. Native support claims require dated evidence by agent, agent version, platform, install mode, path style, shell, and runtime behavior. |
| Runtime contamination | Inherited external `OPENCLAW_*`, `DEEPSEEK_*`, provider API keys, base URLs, model variables, shell env files, and agent config env are scrubbed or fixture-controlled. Codex may set OpenClaw-compatible variables only to Codex-owned runtime paths. |
| Persistent execution | Hooks, schedulers, shell profile edits, crontab, systemd, launchd, Task Scheduler, agent hooks, wrapper scripts, and project hooks remain inert docs/templates unless a separate persistence manifest is approved. |

## Agent-Specific Requirements

Codex:

- validate `SKILL.md` frontmatter, unique skill names, descriptions, templates,
  personas, `AGENTS.md` effects, and runtime-backed skill separation
- use Codex-owned paths only
- ignore inherited external `OPENCLAW_*`; compatibility variables may point
  only to Codex-owned runtime paths

Claude:

- model command files, skill metadata, `CLAUDE.md` sections, settings hooks,
  aliases, and runner scripts separately
- detect existing legacy hooks read-only and redacted; never import or execute
  them
- no Claude artifact may symlink to, execute, or depend on `~/.openclaw`

DeepSeek:

- model `.env`, `DEEPSEEK_*`, managed config, requirements, MCP, hooks, memory,
  notes, tasks, snapshots, and workspace-local skill shadowing
- generated DeepSeek artifacts must not claim Codex or Claude semantics are
  enforced by DeepSeek
- DeepSeek remains reference-only until native loader evidence proves another
  mode

## Testing Mechanism

Testing uses separate fake roots:

- fake OpenClaw source root
- fake target agent home

Inventory tests prove no writes occur and no canary values leak. Dry-run
manifest tests use a saved fake-root inventory plus a separate fake target
home and prove that source and target trees remain unchanged. Apply and
uninstall tests run only against fake target homes; real-system target roots
remain refused.

Required fixture classes:

- absent, empty, custom, malformed, sensitive, and large OpenClaw roots
- symlink loops, symlink escapes, hardlinks, reparse points, special files,
  mount-like paths, and traversal attempts
- hostile `OPENCLAW_*`, `DEEPSEEK_*`, provider, shell, and `.env` variables
- all agents, individual agents, and no detected agents
- Linux, macOS, Windows drive, Windows UNC, WSL-native, and WSL-mounted paths
- case collisions, Unicode confusables, reserved names, CRLF/LF files, spaces
  in paths
- every legacy alias, canonical plus legacy conflicts, duplicate aliases, and
  divergent support files
- poisoned manifests, permission failures, partial apply failures, backup
  failures, rollback failures, concurrent drift, and outside-root write attempts

Current Phase 1 test coverage:

- fake source root remains byte-for-byte unchanged after inventory
- inventory output omits source-root absolute paths and canary secrets
- absent roots produce sanitized denial records only
- symlinked source roots are denied without traversal
- hostile environment variables do not appear in output
- scan bounds produce `max-entries-exceeded` instead of walking unbounded trees

Current Phase 2 test coverage:

- manifest generation leaves fake source and fake target roots unchanged
- manifest output omits source-root paths, target-root paths, and canary values
- fixed inputs produce identical manifests and stable action IDs
- existing target collisions become `no-op` plus `skip-report`
- unsafe inventories fail closed before manifest generation
- CLI manifest generation reads a saved inventory file and stays unreviewed

Current Phase 3 test coverage:

- unapproved manifests can dry-run but cannot apply
- approved fake-root apply writes only deterministic review files
- fake source roots remain unchanged during apply and uninstall
- fake target roots return to baseline after uninstall
- target drift fails closed before any write
- changed generated files are preserved during uninstall
- CLI approval, apply, and uninstall complete a fake-root lifecycle

Current Phase 4 test coverage:

- fixture evidence records do not create native support claims
- native-loader evidence requires `agent_version`
- native support summaries are scoped by agent, platform, install mode, and
  path style
- CLI evidence recording and validation operate on explicit evidence files

Current Phase 5 test coverage:

- hook metadata produced by inventory and manifest generation stays `no-op`
- persistence checks report inert manifests as `inert-only`
- persistence checks block manifests that try to write hook material
- CLI persistence checks operate on explicit manifest files

CI tiers:

- PR-blocking: schema validation, static forbidden-pattern checks, core
  fake-root smoke
- release-blocking: canary redaction, dry-run manifest contract, selected
  apply/rollback lifecycle
- scheduled: extended OS/path/shell matrix and native-loader evidence refresh

## Acceptance Criteria

Phase 0 is complete when:

- schema artifacts parse as JSON and pass Phase 0 structural tests
- fixture catalog covers the required adversarial classes
- documentation labels fake-root evidence as isolation evidence only
- documentation states that native support requires separate dated loader
  evidence

Phase 1 is complete when:

- `openclaw-inventory` requires explicit `--source-root`
- the scanner emits only schema-allowlisted inventory metadata
- source-root absolute paths, file contents, environment variables, and canary
  secrets do not appear in inventory output
- denied categories are reported as counts and reason codes only
- inventory output is not accepted by install, migrate, apply, or uninstall
- focused inventory tests and the full installer test suite pass

Phase 2 is complete when:

- `openclaw-dry-run-manifest` requires explicit `--inventory` and
  `--target-root`
- generated manifests are schema-versioned, content-addressed, and unreviewed
- action IDs are stable for fixed inputs
- target paths remain relative and contained under the explicit target root
- collisions are skipped and reported instead of overwritten
- unsafe or incomplete inventories fail closed
- manifest output is not accepted by install, migrate, apply, or uninstall
- focused manifest tests and the full installer test suite pass

Phase 3 is complete when:

- approved-manifest apply refuses real-system target roots
- unapproved manifests cannot be applied
- apply preflight detects target drift before writing
- uninstall deletes only unchanged journaled OpenClaw review artifacts
- changed generated files are preserved and reported as conflicts
- fake target roots return to baseline after apply/uninstall
- focused apply tests and the full installer test suite pass

Phase 4 is complete when:

- evidence records are schema-versioned and content-addressed
- native evidence requires agent version and scoped observed behavior
- fixture-only and CI evidence cannot produce native support claims
- DeepSeek remains reference-only unless native DeepSeek evidence exists
- evidence validation is explicit and file-based
- focused evidence tests and the full installer test suite pass

Phase 5 is complete when:

- hook and scheduler metadata remains inert
- any enabled persistence write is blocked by a dedicated check
- no hook, scheduler, shell profile, wrapper, agent hook, or project hook write
  path exists in this OpenClaw pipeline
- focused persistence tests and the full installer test suite pass

Related pages: [Audit And Migration](audit-and-migration.md),
[Verification](verification.md), [Architecture](architecture.md).
