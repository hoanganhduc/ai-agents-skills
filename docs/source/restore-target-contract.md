# Restore target contract

`manifest/target-state.yaml` is the target-neutral contract between this skill
installer and a host restoration system.  Version 3 declares, for every
installer target, the agent home, native CLI candidates, logical runtime
requirements, credential authority locations, and readiness evidence expected
after restoration.

One installer target may expose multiple independently verified native CLI
surfaces. In particular, the Antigravity adapter declares separate `agy` and
Gemini CLI probes and separate credential authorities; success of one must not
hide a missing or expired session for the other.

The manifest contains paths and policy only.  Credential values are forbidden.
The `ai-agents-skills` installer continues to own skill rendering and loader
verification; the host restoration system owns operating-system packages,
agent CLI installation, credential recovery, and service activation.

`strict-env-file` authorities name an owner-private restored environment file
plus both the complete authority schema (`allowed_keys`) and the bounded key
names accepted by that target (`keys`). The host must reject assignments
outside the declared schema, validate the whole authority without evaluating
it, and project only the target's declared keys into the native CLI child. In
particular, Copilot may consume the backed GitHub token or its two native BYOK
secret names (`COPILOT_PROVIDER_API_KEY` and
`COPILOT_PROVIDER_BEARER_TOKEN`) from its physically separate
`providers/copilot.env` authority. That authority rejects every non-Copilot
key; it is not a filtered view of a shared multi-provider secret file and does
not authorize exporting provider credentials into every login shell.
The `credential-projection` readiness check is the offline child-scope proof:
it must confirm that at least one declared Copilot token reaches the launcher
child while unrelated provider keys and the authority pointer do not. The
projection contract binds the exact authority, launcher source, immutable npm
closure loader, and pointer name so PATH or ambient pointer state cannot change
what was verified.

For pinned OpenClaw 2026.7.1-2, per-agent provider profiles are DB-first. The
canonical authority is the evidence-bound
`.openclaw/agents/*/agent/openclaw-agent.sqlite` glob; legacy
`auth-profiles.json` is migration input only and is not an authority. The glob
has one path-segment wildcard for the agent ID and must be expanded without
following symlinked agent directories. A generic credential-file parser must
not try to decode the SQLite files as JSON or text.

The `agent-auth-closure` readiness check is satisfied from the freshly
generated full OpenClaw runtime report. The outer report must have
`schema_version: 1`, `profile: full`, and `status: passed`; its `agent_auth`
payload must have schema `openclaw.agent-auth-closure/v2`, runtime version
`2026.7.1-2`, and status `PASS`. Its exact top-level fields are `schema`,
`status`, `runtimeVersion`, `verificationMode`, `openclawExecuted`,
`networkEnabled`, `agents`, `failureCount`, and `failures`.
`verificationMode` must be `offline-structural-only`; OpenClaw execution and
network access must both be false. The restoration verifier must validate the
payload shape and zero-failure result, reject duplicate or unsafe agent IDs,
and bind each existing canonical store reported for an agent to the exact safe
glob path. Every agent record has exactly `agentId`, `status`,
`canonicalStore`, and `reasons`. Every glob match must have one corresponding
passing agent record with successful SQLite integrity and schema-version
metadata. Canonical-store metadata additionally binds app version, auth-store
row and profile counts, configured/authority-JSON/executable-secret-reference
and redaction-sentinel checks, sorted credential source kinds drawn only from
`env` and `file`, plus device, inode, size, modification time, and change time.
The primary store's `appVersion` may be null; a non-null value must equal the
expected runtime version. Replacing or modifying the file before target-state
consumption therefore fails closed. An auxiliary
agent may validly inherit the default agent's store, so a passing agent record
whose `canonicalStore.exists` is false does not itself require another glob
match; the closure helper verifies that inheritance against the default store.

This evidence is metadata-only: it contains schema and provider names, source
kinds, counts, and failure reasons, never credential payloads. The declared
`provider_calls_allowed: false` boundary allows the runtime verifier to run the
pinned local closure helper to produce the embedded payload, but neither the
producer nor the target verifier may contact a provider to establish readiness.

OpenClaw's restricted-target evidence file is intentionally not part of this
generic readiness contract.  It is required only when the dedicated
`ai-agents-skills` OpenClaw apply path is the writer; a restoration system may
instead delegate OpenClaw writes to another reviewed installer without
fabricating `ai-agents-skills` provenance.

A restored target is not ready merely because its skill files exist.  The host
must also prove the locked CLI version, the target's managed skill visibility,
the declared runtime smoke checks, and the applicable structural/native
authentication status.  Provider credit exhaustion is recorded separately and
does not turn a structurally correct target into a technical failure.

Managed-skill visibility is an inventory and provenance check, not a path
existence check. Every skill supported by the target in the pinned
`complete-restore` inventory must have an owner-private installer state record
that exactly matches its per-run receipt, canonical source path and digest,
and current installed signature. OpenClaw uses its dedicated target journal
for every supported non-runtime skill file (runtime-backed skills use their
separate runtime closure); created files and safely adopted byte-identical
files must bind both the
canonical-source digest and rendered-content digest plus the exact installed
file identity. A missing inventory member or an arbitrary pre-existing skill
file is a technical failure.
