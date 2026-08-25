# External Dependency Provisioning

`provision-external` is an opt-in host-local bootstrap for the application
dependencies behind the course-management and VNU eOffice skills. It is not a
normal skill install and does not copy application code, credentials, or
configuration into agent homes.

## Target Boundary

One provisioned host generation is usable by native targets that execute as the
same local user: Codex, Claude, DeepSeek, Copilot, OpenCode, Antigravity, Grok,
Kimi, and ChatGPT Local Coder. The standard skill installer still controls
which of those targets receives each skill body.

OpenClaw is deliberately excluded from automatic provisioning and verification.
Its locked sandbox uses an image-local environment and must be checked or
rebuilt through its own evidence-gated process. A host `~/.course_venv` or
`~/.vnu-eoffice_venv` is never evidence that OpenClaw can import the package.

Native Windows apply is currently blocked by the installer-wide handle-bound
mutation gate. A native Windows dry run is useful planning evidence, not proof
of executable support.

## What It Provisions

The allowlisted manifest pins these sources:

| Bundle | Repository revision | Stable native pointer |
|---|---|---|
| `course-management` | `course_management_toolkit` at `b3f8f647d4329d212958641f9ab18ecb154a21a8` | `~/.course_venv` |
| `vnu-eoffice` | `vnu-eoffice` at `66d3ab694654bc5b11ca5c8253afeec1f0f00fae` | `~/.vnu-eoffice_venv` |

The command clones only those fixed HTTPS repositories, verifies the full Git
commit and tree, builds a non-editable wheel from an archived source snapshot,
and acquires third-party wheels only from the committed fully transitive hash
lock. Acquisition uses the fixed PyPI HTTPS index, wheel-only artifacts,
`--no-deps`, and `--require-hashes`; it then installs the runtime offline from
that wheelhouse. It verifies package metadata, imports, `pip check`,
installed-wheel `RECORD` hashes, and each agent-safe module's `--help` before
switching the stable pointer. An existing active generation is rechecked before
the command accepts it as active.

The generated receipt records the approved build-input digest, reviewed lock
hash/platform/path, package/wheel hashes, source tree/archive hashes, runtime
`RECORD` integrity data, and paths under
`~/.ai-agents-skills/external-dependencies.json`. The launcher does not record
subprocess output, credentials, or environment values.

The reviewed lock currently covers `linux-aarch64` only. A dry run on another
native platform reports the missing lock; apply fails before provisioning
mutation. Updating a lock is an explicit, reviewable source change rather than
a fresh dependency resolution. The lock fixes accepted artifact bytes, but it
does not independently attest to the quality or intent of third-party code.
Automatic pin updates and deletion of prior generations are intentionally
outside this command.

## Dry Run Then Apply

First obtain the no-network, no-write plan and retain its digest:

```bash
make provision-external ARGS="--bundles course-management,vnu-eoffice --json"
```

To apply, pass exactly the emitted `plan_digest` and bind the confirmation to
that same value. Apply also requires an explicit acknowledgement that build
code is not OS-sandboxed:

```bash
plan_digest=<plan_digest-from-dry-run>
AAS_EXTERNAL_PROVISION_CONFIRM="I approve external dependency plan $plan_digest" \
  AAS_EXTERNAL_EXECUTION_RISK_CONFIRM="I understand pinned external build code is not sandboxed for plan $plan_digest" \
  make provision-external ARGS="--apply --real-system --plan-digest $plan_digest --json"
```

While holding its private provision lock, apply rebuilds the plan and refuses a
changed tracked path or receipt pre-state, unsafe/symlinked parents, an
unmanaged stable venv pointer, a dirty or wrong-remote checkout, and a
pre-existing unmanaged generation. It never runs `git reset`, `git clean`, or
recursive cleanup against an existing managed source/venv.

Before a new generation is created, apply writes a private transaction journal.
If an apply is interrupted, the next confirmed apply either recognizes the
completed receipt/pointer switch or restores the prior pointer and receipt and
removes only the journal-listed new generation. It preserves pre-existing
managed generations. Recovery changes the observed pre-state, so obtain a new
dry-run digest before retrying.

After a successful provision, install or refresh the five selected skill bodies
with the ordinary installer, then rerun the focused precheck:

```bash
AAS_INSTALL_CONFIRM="I understand the installation and uninstall process" \
  make install ARGS="--skills classroom50,course-canvas,course-google-classroom,course-db,vnu-eoffice --backup-replace --apply --real-system --post-install-smoke verify"
make precheck ARGS="--skills classroom50,course-canvas,course-google-classroom,course-db,vnu-eoffice --json"
```

The launcher deliberately does not search for, copy, or record credential
values, and it runs child processes from a private empty working directory with
a scrubbed home, configuration, standard system `PATH`, and isolated Python
mode. This is not an OS-level sandbox: pinned source and build-backend code
still runs as the invoking user and could access files or network services
available to that user. Review the pins and locks and run it only in an account
environment you are willing to expose to that code. Course operations still
require their documented allowlists and service authentication; VNU eOffice
credentials remain configured separately.
