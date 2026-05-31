# Copilot Target

Generated Copilot artifacts are adapter-only. Copilot is a default install
target for portable skill files and personas, but it does not receive Codex or
Claude instruction blocks, command aliases, templates, or `.github/*`
repository workflow files from this installer.

Copilot skills should stay thin and reference the canonical skill sources.
Runtime-backed behavior must go through the shared runtime files selected by
the installer, and evidence-bearing delegated work must be parent-owned and
validated before it is used in synthesis.
