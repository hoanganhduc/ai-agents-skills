# Explicit executor setup

The verifier does not install tools. After reviewing available CPU, memory and
disk, provision the qualified local Docker executor explicitly:

```bash
python3 canonical/runtime/skills/lax-formalization/provision_executor.py
python3 canonical/runtime/skills/lax-formalization/provision_executor.py --apply
```

These commands are run from the canonical repository. The first previews; the
second builds a local image and the trusted Lax inspector, then records operator
configuration under `~/.config/lax-formalization/config.json`. It needs an existing
Lax 0.1.48 installation and its v4.33.0 warm mathlib store. `lax doctor` prepares
that store only when setup has been requested.

The provisioning step uses pinned official Node 22 Debian images for AMD64 or
ARM64, then records the final local image ID and package content digest. The
upstream Lax hosted-runner digest is AMD64-only; it is not silently emulated on
ARM64. Image manifests were checked against the official Docker registry on
2026-09-27. Requalification is required after changing the tool/package profile.

Validation containers have no network, secrets, agent/engine socket or broad home
mount. They run as the invoking non-root user with capabilities dropped, a
read-only image, bounded temporary space, CPU/memory/PID limits and explicit
read-only trusted inputs. Candidate and checking phases have disjoint writable
outputs. Teardown is confirmed before admitting captured artifacts.

Main-thread verification records TERM/HUP/INT and delivers termination at safe
supervisor checkpoints, after bounded calls return. Process acquisition and
cleanup cannot be interrupted by those handlers; removal requires a successful
daemon query with no matching container. A daemon error is not proof of removal.
An interrupted run without a final receipt is not accepted evidence. Hard kill,
host failure and signal handling for threaded API callers remain outside that
guard; inspect only the job's identified containers before retrying, never stop
unrelated containers. Preserve the failed run and restart into a fresh evidence
directory with the same reviewed inputs.

The config is an operator input, not part of a proof request. Do not put it in an
agent-generated candidate tree. An executor unavailable on this machine is a
blocked check, not permission to execute retrieved Lean directly on the host.
Native Windows must use a qualified Linux/WSL execution path for real builds.

The qualified execution host is non-root Linux (including WSL); the actual native
qualification in this migration used ARM64. Other targets can install the skill
and run offline commands, but installation smoke does not qualify native Lean
execution there. Install `lean-strict-verification-gate` alongside this runtime
(or use the formal-research profile).

Ordinary local Git directories are required. Linked worktrees, alternate object
stores, custom filters/hooks/includes, and other unqualified Git configuration
are refused before host inspection. The official Lax database's promisor/blob:none
settings are permitted, but lazy fetching is blocked by the command environment;
provision required objects separately. Input worktree bytes must equal committed
and exported bytes, including files hidden by assume-unchanged flags.

Provisioning pins the selected Lean toolchain, elan launchers, warm dependency
closure and inspector bytes. Every verification rewalks those inputs and checks
the provisioned fingerprints. A private supervisor cache reuses content hashes
only when device, inode, mode, size, mtime and ctime match; changed files are
rehash-checked. The cache is never mounted into candidate containers and never
replaces the provisioned pins. Host administrator/kernel compromise remains
outside this executor's boundary.

Trusted Node processes disable SIGUSR1 debugging. Compiler descendants are
terminated before bounded collection from tmpfs; only exact inventoried modules
enter the fresh checking process. Source, DB and executor identity changes during
the run invalidate the result.
