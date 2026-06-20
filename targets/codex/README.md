# Codex Target

Generated Codex artifacts use canonical skill names and the Codex-compatible
`SKILL.md` directory layout. User-level skills target `~/.codex/skills` in this
setup. `.agents/skills` is treated as an optional workspace or compatibility
location when it is explicitly detected or requested.

In auto mode, Codex skill files are rendered as reference adapters by default
because current Codex discovery loads regular user `SKILL.md` files but does
not reliably discover file-symlinked user `SKILL.md` files. Codex personas are
TOML custom-agent files, and entrypoint aliases are installed as reference
documents under `instructions/entrypoints` rather than native slash commands.

Zotero and Calibre integrations must call the shared profile-aware runtime
commands. Codex target files should not hardcode Zotero or Calibre library
paths; they should rely on the selected local-library profile manifest and the
shared safety gates.

## Autonomous loop enforcement

Codex has no interactive Stop-hook merge for autonomous loops: it uses TOML
configuration rather than a JSON `hooks` map. Headless loops are enforced by the
shared `autoloop_driver.sh`, which derives "done" from the runtime and stops on
the stop policy (loops, credit, goal resolved, or user stop) or after repeated
iteration failures. See the repo [Architecture](../../docs/architecture.md) for
the full per-target matrix.
