# Copilot Target

Generated Copilot artifacts are adapter-only. Copilot is a default install
target for portable skill files and personas, but it does not receive Codex or
Claude instruction blocks, command aliases, templates, or `.github/*`
repository workflow files from this installer.

Copilot skills should stay thin and reference the canonical skill sources.
Runtime-backed behavior must go through the shared runtime files selected by
the installer, and evidence-bearing delegated work must be parent-owned and
validated before it is used in synthesis.

## Native launcher trust contract

The host restoration system owns the native Copilot launcher and npm runtime.
Credential-bearing launches use the single target-scoped authority
`~/.config/ai-agents-skills/providers/copilot.env`; literal environment names
are not recoverable authority paths. The installed launcher must resolve an
immutable content-addressed npm closure of the form
`~/.npm-global/closures/sha256-<source-sha256>-<tree-sha256>/.../npm-loader.js`
and verify the recorded launcher-source, rendered-launcher, Node executable,
npm source, npm tree, and loader SHA-256 evidence immediately before execution.
The compatibility loader under `~/.npm-global/lib/node_modules` is discovery
only and is not an execution authority.

The final Node process environment is built from an empty environment: fixed
`PATH=/usr/local/bin:/usr/bin:/bin`, the explicitly declared non-secret keys in
`manifest/target-state.yaml`, and only the five Copilot credential keys from
the strict authority. The authority pointer itself never reaches Node. An
absent closure is `NOT_CONFIGURED`; any wrapper, executable, loader, or npm-tree
digest drift is `TECHNICAL_FAIL`, never an authentication failure.

## Autonomous loop enforcement

Copilot has no built-in autonomous-loop enforcement: it is adapter-only with no
managed settings/hook surface or headless runner in this installer. Loop
stop-conditions on this target remain policy-based via the installed
`autonomous-loop-enforcement` rule. See the repo
[Architecture](../../docs/architecture.md) for the full per-target matrix.
