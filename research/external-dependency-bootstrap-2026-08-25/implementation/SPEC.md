# External Dependency Bootstrap Specification

## Goal

Add an opt-in, host-level provisioning workflow for the Course Management
Toolkit and VNU eOffice. It must make the existing native-target skills usable
without copying application code or credentials into individual agent homes.

## Scope

- In scope:
  - A manifest-backed `provision-external` CLI command with dry-run as the
    default and fixed bundle names only.
  - Pinned source revisions: Course Management Toolkit `b3f8f647d4329d212958641f9ab18ecb154a21a8`
    (package `course-hoanganhduc` 0.1.4) and VNU eOffice
    `66d3ab694654bc5b11ca5c8253afeec1f0f00fae` (package `vnu-eoffice` 0.1.0).
  - Managed, content-addressed source and virtual-environment generations;
    stable `~/.course_venv` and `~/.vnu-eoffice_venv` pointers for native host
    skills.
  - A scrubbed subprocess environment and private empty child working directory,
    non-interactive isolated Python/Git/pip execution, reviewed wheel-only
    hash-locked dependency acquisition, local offline installation,
    transaction-backed generation publication, and bounded receipts without
    launcher-captured secrets or raw subprocess output.
  - Import, distribution metadata, `pip check`, and agent-safe `--help`
    verification before a pointer is activated.
  - Propagation of the five dependent skills to supported detected native
    targets using the existing installer after provisioning.
  - A separate read-only OpenClaw verification after native provisioning.
- Out of scope:
  - OpenClaw provisioning or real `.openclaw` writes.
  - Native Windows mutation, which remains blocked by the existing
    handle-bound mutation gate.
  - Credential creation, discovery, projection, copying, or validation.
  - Automatically updating pins, deleting old generations, or repairing an
    unmanaged/dirty checkout or venv.
  - Automatic generation of dependency locks or support for an unreviewed host
    platform/architecture.

## Assumptions

- The user authorized the two named public repositories and the pinned commits
  above. Source revisions are verified by full Git object ID before build.
- Source and package-build code is intentionally executed with a scrubbed
  environment, private empty working directory, isolated Python mode, and
  standard system `PATH`; it is not OS-sandboxed and can still reach files or
  network services available to the invoking user.
- The committed `linux-aarch64` lock is the reviewed exact third-party artifact
  set. Other host platforms report no lock and cannot apply until a separately
  reviewed lock is added.
- Agent targets that execute commands as this host user share the stable venv
  pointers. OpenClaw has its own image-local environment and is excluded.
- Same-UID filesystem races and a malicious local package index are outside
  this initial host-local threat boundary.

## Interfaces

- `manifest/external-dependencies.yaml`
- `installer/ai_agents_skills/external_dependencies.py`
- `installer/ai_agents_skills/cli.py`, `installer/ai_agents_skills/manifest.py`,
  and `Makefile`
- `docs/source/external-dependencies.md` and generated documentation
- `tests/test_external_dependencies.py`
- Existing course-management and VNU eOffice skill contracts and dependency
  prechecks.

## Acceptance Criteria

- A dry run performs no network access or filesystem mutation and returns a
  plan digest that binds selected bundles, source revisions, target paths,
  declared requirements, resolver/build-tool policy, exact reviewed wheel
  hashes, and tracked entry/receipt pre-state.
- Apply requires `--apply`, the plan digest, an explicit plan confirmation, an
  explicit unsandboxed-build acknowledgement, and `--real-system` for a real
  home root. It recomputes the plan under the provision lock; unknown bundles
  or mismatched/stale plan digests fail before new provisioning mutation.
- Source checkout, source generation, state receipts, and stable pointers
  reject symlinks, escapes, wrong remotes, dirty checkouts, unmanaged targets,
  and unsafe parent paths.
- The launcher uses argument vectors and a scrubbed environment with a private
  empty working directory, isolated Python mode, and standard system `PATH`; it
  disables prompts, user-site packages, Git
  configuration/hooks/submodules/LFS, and pip configuration. It does not
  intentionally read, copy, or record credential values, but cannot constrain
  what fetched build code can access without an OS-level sandbox.
- Third-party wheels are downloaded only against the committed exact-hash lock
  with dependency resolution disabled, then installed offline. The built local
  project wheel comes from the verified pinned checkout, not an editable source
  path, and the receipt records the bound build-input and lock identities.
- A private transaction journal records newly absent generation paths before
  build. The stable pointer is activated only after verification passes; an
  interrupted activation is recovered on the next confirmed apply by either
  recognizing the completed receipt/pointer switch or restoring the prior
  receipt/pointer and deleting only journal-listed new generations.
- The post-provisioning precheck reports both Python package requirements as
  available, and the five selected skill bodies are installed for all eligible
  native targets.
- OpenClaw is verified separately as present/absent/blocked for each package;
  no host venv is presented as OpenClaw evidence.

## Verification

- Focused unit tests for manifest validation, dry-run purity, source/venv path
  admission, stale-plan gating, subprocess argument/environment construction,
  wheel-lock/runtime-RECORD verification, transaction rollback/retry, and
  receipts.
- Existing course/VNU dependency and skill-contract tests.
- Static, documentation, sanitization, and full test suites.
- A real-host dry run, then confirmed provision, import/precheck checks, and a
  read-only OpenClaw probe.

## Risks

- The course dependency graph is large and includes native/ML-adjacent wheels;
  an all-wheel resolution may fail safely on an unsupported platform.
- The current exact third-party lock is specific to `linux-aarch64`; a missing
  reviewed lock is a fail-closed provisioning condition on another platform.
- The command reduces accidental credential/config inheritance but does not
  sandbox fetched build code. Its execution-risk acknowledgement makes that
  residual boundary explicit rather than treating it as a credential guarantee.
- OpenClaw may have a separately baked environment whose status cannot be
  inferred from host paths.
